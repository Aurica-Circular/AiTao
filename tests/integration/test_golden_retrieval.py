# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_golden_retrieval.py — Golden non-regression suite (US-89, point (c)).
#
# A tiny COMMITTED multilingual corpus is indexed into ISOLATED stores (a
# dedicated test_* Meilisearch documents index + a dedicated Meilisearch
# chunk index, ÉPIC-31 US-113 fusion-only — no more LanceDB), then a fixed
# set of "golden" questions is replayed through the real retrieval + gate
# (RAGEngine.enrich_messages) with NO LLM — assertions are deterministic.
#
# Each case guards a behaviour we fixed/observed in production:
#   - the RIGHT document is retrieved and anchored (89-4 CJK bigram anchoring),
#   - an off-topic document is rejected,
#   - an ABSENT term yields an honest refusal (anti-hallucination).
#
# Requires Meilisearch + the embedding model → marked slow + requires_meilisearch;
# excluded from the light release gate, run by the dedicated golden CI job.

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.requires_meilisearch]

_CORPUS = Path(__file__).parent.parent / "fixtures" / "golden_corpus"
_MEILI_INDEX = "test_golden_documents"

# (question, expected file stem)  —  expected=None asserts an honest refusal.
GOLDEN = [
    ("事假扣薪", "zh_attendance"),                                   # CJK anchoring on parts
    ("外籍從業人員管理辦法", "zh_foreign"),                          # exact-name retrieval
    ("歲末感恩暨迎新年", "zh_event"),                                # event flyer; must not refuse
    ("Où est le contrat de location de Jean Dupont ?", "fr_bail"),  # FR — must not over-refuse
    ("generative AI software development", "en_ai"),                # EN topical
    # billing@acme.example (in en_invoice AND en_receipt) is covered separately
    # by test_exact_token_pins_all_email_docs — top-1 is ambiguous with two docs,
    # the point of US-89-2 is that ALL bearers are pinned.
    ("粒米女性經理人聯誼會", None),                                  # absent → honest refusal
]


@pytest.fixture(scope="module")
def golden_rag(meilisearch_test_available):
    """Index the golden corpus into isolated stores and yield a RAGEngine."""
    if not meilisearch_test_available:
        pytest.skip("Meilisearch not available")

    import sys

    import meilisearch

    from aitao.core.config import get_config
    from aitao.core.logger import get_logger
    from aitao.indexation.indexer import DocumentIndexer
    from aitao.llm.rag_engine import RAGEngine
    from aitao.search.hybrid_engine import HybridSearchEngine
    from aitao.storage.repository import make_meilisearch_client

    # `golden` is a plain (non-test) package under tests/ — not on pythonpath
    # (pyproject only adds "src"); mirrors test_golden_conversations.py.
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from golden.corpus_fixture import make_chunk_store  # noqa: E402

    # Clean slate for the test Meilisearch index (auto-purged by conftest too).
    raw = meilisearch.Client("http://localhost:7700")
    try:
        raw.delete_index(_MEILI_INDEX)
    except Exception:
        pass

    meili = make_meilisearch_client(index_name=_MEILI_INDEX)
    # ÉPIC-31 (US-113): MeiliChunkStore — dedicated Meili chunk index, real
    # userProvided vectors, the only chunk backend since v4.0 (fusion-only).
    chunks = make_chunk_store(_MEILI_INDEX)

    indexer = DocumentIndexer(meilisearch_client=meili, chunk_store=chunks)
    files = sorted(_CORPUS.glob("*.md"))
    for path in files:
        indexer.index_file(str(path), force=True)

    # Meilisearch indexing is async — wait until all docs are searchable.
    import time
    for _ in range(40):
        if meili.count("") >= len(files):
            break
        time.sleep(0.25)

    engine = HybridSearchEngine(meilisearch_repo=meili)
    engine._chunk_store = chunks  # isolated chunks

    rag = RAGEngine(get_config(), get_logger("golden"))
    rag._search_engine = engine
    # IDF distillation (89-1) is meaningless on a 6-doc corpus (every word is
    # "frequent"), so disable it here — this suite guards retrieval + CJK anchoring
    # (89-4) + the gate. Distillation is covered by tests/unit/test_query_distiller.
    rag.distill_query = False

    yield rag


def _top_anchored_path(context) -> str:
    anchored = [d for d in context if float(getattr(d, "score", 0) or 0) > 0]
    return str(getattr(anchored[0], "path", "")) if anchored else ""


class TestGoldenRetrieval:
    @pytest.mark.parametrize("question,expected", GOLDEN)
    def test_golden_case(self, golden_rag, question, expected):
        from aitao.llm.context_adequacy import evaluate_refusal

        _, context, _ = golden_rag.enrich_messages(
            [{"role": "user", "content": question}]
        )
        refused = evaluate_refusal(question, context) is not None

        if expected is None:
            assert refused, (
                f"{question!r}: expected an honest refusal (term absent from corpus), "
                f"but the gate answered. Anchored: {_top_anchored_path(context)!r}"
            )
            return

        assert not refused, f"{question!r}: wrongly refused — the answer IS in the corpus."
        top = _top_anchored_path(context)
        assert expected in top, (
            f"{question!r}: top anchored doc is {top!r}, expected to contain {expected!r}."
        )

    def test_exact_token_pins_all_email_docs(self, golden_rag):
        """US-89-2 — an email present in several docs pins ALL of them.

        billing@acme.example lives in both en_invoice and en_receipt; exact-token
        pinning must surface both (the "where does this email appear?" case),
        not just the top semantic hit.
        """
        _, context, _ = golden_rag.enrich_messages(
            [{"role": "user", "content": "billing@acme.example"}]
        )
        anchored = {
            Path(str(getattr(d, "path", ""))).stem
            for d in context
            if float(getattr(d, "score", 0) or 0) > 0
        }
        assert {"en_invoice", "en_receipt"} <= anchored, (
            f"expected both email-bearing docs pinned, got anchored stems: {anchored}"
        )

    # Rare-term recall gate for EPIC-31 fusion (US-109, D3). 範例玻璃 / zh_glass
    # is the one case where the US-106 study found opposite behaviour across
    # engine configs: plain RRF (today's architecture, exercised by this test)
    # and montage A/B(0.0) keep it at rank 1, but native hybrid B(0.5)/B(0.8)
    # lost it entirely at the excerpt stage. US-30 exact-token pinning
    # (_exact_token_docs, rag_engine.py:593, CJK run >= 2 chars) is the safety
    # net expected to catch it once fusion replaces RRF (US-110) — this test
    # must stay green under fusion, or the pinning hypothesis needs
    # reinforcing before merge. Green here today just proves the harness
    # measures the right thing. Distinct from the i11_*.yaml scenarios, which
    # test conversational referent tracking (US-102), not rare-term recall.
    @pytest.mark.parametrize(
        "question",
        [
            "範例玻璃",
            "Quel est le chiffre d'affaires de 範例玻璃 ?",
        ],
    )
    def test_rare_term_recall(self, golden_rag, question):
        _, context, _ = golden_rag.enrich_messages(
            [{"role": "user", "content": question}]
        )
        anchored = {
            Path(str(getattr(d, "path", ""))).stem
            for d in context
            if float(getattr(d, "score", 0) or 0) > 0
        }
        assert "zh_glass" in anchored, (
            f"{question!r}: zh_glass not found among anchored docs: {anchored}"
        )
