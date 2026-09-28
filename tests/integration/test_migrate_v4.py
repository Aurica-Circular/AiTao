# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_migrate_v4.py — integration test for the US-112 v4 migration
# (ÉPIC-31): exercises the REAL migration pipeline (search.migrate_v4) against
# a live (local) Meilisearch + a temporary LanceDB, using the committed golden
# corpus as source data — no mocks on the classification/rebuild/swap path
# (unlike tests/unit/test_migrate_v4.py, which is fully mocked).
#
# Proves, end to end:
#   - dry-run migrates the golden corpus's documents+chunks into "_next"
#     indices with zero live-index side effects: identical document/chunk
#     counts, sampled vectors present, and an INDEPENDENT hybrid search
#     against the migrated "_next" index retrieves an expected golden doc;
#   - the CJK-gluing detection (US-111/89-6) correctly routes a document
#     whose LIVE content predates the glue fix to the "reindex" population
#     (glued content pushed, vector dropped) instead of "copy" — a synthetic
#     document is injected directly into the live index (bypassing the
#     indexer's own glue step) since the committed corpus, indexed through
#     today's pipeline, never produces one naturally;
#   - swap() then rollback() atomically flip the live indices and back,
#     verified by re-reading the SAME live index name/client across phases.
#
# Isolation: dedicated LanceDB temp dir + test_migrate_v4_* Meilisearch
# indices (never touches aitao_documents/aitao_chunks/~/.aitao) — pattern
# from tests/golden/corpus_fixture.py. A Mock() task_queue is injected so
# swap()'s post-swap requeue never touches the REAL task queue file
# (indexation.queue.TaskQueue()'s production default path — see
# core.pathmanager.path_manager.get_queue_file()).
#
# Requires Meilisearch on :7700 + the bge-m3 embedding model -> slow +
# requires_meilisearch, same as the golden suites; excluded from the fast
# release gate.

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
# `golden` is a plain (non-test) package under tests/ — not on pythonpath
# (pyproject only adds "src"), mirrors test_golden_retrieval.py/test_golden_conversations.py.
sys.path.insert(0, str(Path(__file__).parent.parent))

from aitao.core.cjk_glue import glue_cjk_content  # noqa: E402
from aitao.indexation.indexer_helpers import generate_doc_id  # noqa: E402
from aitao.search.embedding_source import get_shared_embedder  # noqa: E402
from aitao.search.meilisearch_client import MeilisearchClient  # noqa: E402
from aitao.search.migrate_v4 import MigrationV4Runner  # noqa: E402
from aitao.search.migrate_v4_source import read_live_documents  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.requires_meilisearch]

_CORPUS = Path(__file__).parent.parent / "fixtures" / "golden_corpus"
_DOCS_INDEX = "test_migrate_v4_docs"
_CHUNKS_INDEX = "test_migrate_v4_chunks"

# (query, expected file stem) — reused from the proven golden-retrieval pair
# (tests/integration/test_golden_retrieval.py's GOLDEN list) so a failure here
# means the MIGRATED index regressed, not that the probe query itself is bad.
_HYBRID_PROBE = ("Où est le contrat de location de Jean Dupont ?", "fr_bail")


@pytest.fixture(scope="module")
def migration_env(tmp_path_factory, meilisearch_test_available):
    """Seed the pre-4.0-shaped source state the migration reads from.

    ÉPIC-31 (US-113): fusion is the only engine since v4.0 — there is no more
    "[search] engine" flag to gate on, and the production write path no
    longer touches LanceDB at all (LanceDBClient/ChunkStore were removed), so
    ``golden.corpus_fixture.index_corpus`` (shared with the other golden
    suites, deliberately LanceDB-free) cannot build the migration's "before"
    state any more. This fixture builds it directly instead:
      - the LIVE Meilisearch documents index, via the real (fusion) indexer —
        exactly what ``read_live_documents`` reads today;
      - a SYNTHETIC pre-4.0 LanceDB store (raw ``lancedb`` package — no
        production class left writes this) with the two tables the old
        "rrf" engine used to populate: ``aitao_embeddings`` (doc-level
        vectors) and ``chunks`` (excerpt vectors), embedded with the SAME
        shared bge-m3 model (search.embedding_source) production code uses,
        so the migration's own vector-based sample checks (a real hybrid
        search against "_next") exercise real, meaningful vectors.

    The dry-run -> swap -> rollback lifecycle itself lives in the test body
    (it needs ordered assertions BETWEEN phases); this fixture only builds the
    pre-migration state and tears down every test_migrate_v4_* index after.
    """
    if not meilisearch_test_available:
        pytest.skip("Meilisearch not available")

    from golden.corpus_fixture import index_corpus, reset_meilisearch_index

    meili, _chunk_store, files = index_corpus(_CORPUS, _DOCS_INDEX)

    import lancedb

    db_path = str(tmp_path_factory.mktemp("migrate_v4_lancedb"))
    db = lancedb.connect(db_path)
    embedder = get_shared_embedder()

    doc_rows = []
    chunk_rows = []
    for path in files:
        content = path.read_text(encoding="utf-8")
        doc_id = generate_doc_id(str(path))
        vector = embedder._embed_text(content)
        doc_rows.append({"id": doc_id, "vector": vector})
        chunk_rows.append({
            "chunk_id": f"{doc_id}_0", "doc_id": doc_id, "path": str(path),
            "title": path.stem, "content": content,
            "chunk_index": 0, "total_chunks": 1, "vector": vector,
        })

    db.create_table("aitao_embeddings", data=doc_rows)
    chunks_table = db.create_table("chunks", data=chunk_rows)

    yield {
        "db_path": db_path,
        "meili": meili,
        "chunks_table": chunks_table,
        "files": files,
        "tmp_path_factory": tmp_path_factory,
    }

    for name in (_DOCS_INDEX, f"{_DOCS_INDEX}_next", _CHUNKS_INDEX, f"{_CHUNKS_INDEX}_next"):
        reset_meilisearch_index(name)


class TestMigrationLifecycle:
    """One ordered walk through dry-run -> swap -> rollback (ÉPIC-31, US-112).

    A single test method: the phases are inherently sequential (swap depends
    on a fresh dry-run build, rollback depends on a prior swap) — splitting
    them into independent test_* methods would rely on pytest's incidental
    top-to-bottom ordering instead of stating the dependency explicitly.
    """

    def test_dry_run_swap_rollback(self, migration_env):
        env = migration_env
        db_path = env["db_path"]
        meili = env["meili"]
        chunks_table = env["chunks_table"]
        files = env["files"]
        tmp_path_factory = env["tmp_path_factory"]

        # ------------------------------------------------------------
        # CJK detection setup — inject a synthetic pre-glue-fix document
        # directly into the LIVE index (via add_document(), which never
        # glues — only the extraction pipeline does) ONLY IF the corpus
        # itself doesn't already exercise the "reindex" population (it never
        # does today: ingestion glues CJK content before it is ever stored).
        # ------------------------------------------------------------
        live_docs_before = read_live_documents(meili)
        assert len(live_docs_before) == len(files)  # ground truth: doc count pre-migration

        naturally_gapped = [
            d for d in live_docs_before
            if glue_cjk_content(d.get("content") or "") != (d.get("content") or "")
        ]

        synthetic_path = None
        synthetic_id = None
        if not naturally_gapped:
            synth_dir = tmp_path_factory.mktemp("migrate_v4_synthetic")
            synthetic_path = synth_dir / "synthetic_zh_gap.md"
            synthetic_path.write_text("承 擔", encoding="utf-8")
            synthetic_id = meili.add_document(
                path=str(synthetic_path),
                title="Synthetic CJK gap (US-112 integration test)",
                content="承 擔",  # NOT glued — simulates a doc stored before US-111
                category="test",
                language="zh",
            )

        expected_docs_total = len(files) + (1 if synthetic_path else 0)
        expected_reindex_count = 1 if synthetic_path else len(naturally_gapped)
        assert meili.count("") == expected_docs_total

        # ------------------------------------------------------------
        # Dry-run
        # ------------------------------------------------------------
        task_queue = Mock()  # must NEVER touch the real production queue file
        runner = MigrationV4Runner(
            docs_index_name=_DOCS_INDEX,
            chunks_index_name=_CHUNKS_INDEX,
            lancedb_path=db_path,
            lancedb_table_name="aitao_embeddings",
            task_queue=task_queue,
            sample_size=8,
        )

        report = runner.dry_run()

        assert report.mode == "dry_run"
        assert report.swapped is False
        # Documents: identical count, correctly split copy/reindex.
        assert report.docs_total == expected_docs_total
        assert report.docs_needing_reindex == expected_reindex_count
        assert report.docs_copied == expected_docs_total - expected_reindex_count
        task_queue.add_task.assert_not_called()  # dry-run NEVER requeues

        # Excerpts (chunks): identical count against an INDEPENDENT ground
        # truth (counting the synthetic LanceDB "chunks" table directly — a
        # different code path than the one under test). The synthetic
        # CJK-gap doc has no LanceDB chunk row at all, so nothing extra
        # should be excluded because of it.
        source_chunk_count = len(chunks_table.to_pandas())
        assert source_chunk_count > 0
        assert report.chunks_total_source == source_chunk_count
        if synthetic_path:
            assert report.chunks_excluded_pending_reindex == 0
            assert report.chunks_copied == source_chunk_count

        # Vector sampling (sondage): every sampled "copy" doc carries its
        # vector into "_next" AND is found back by a hybrid search.
        assert len(report.sample_checks) > 0
        for check in report.sample_checks:
            assert check.present, f"{check.doc_id} missing from {report.next_docs_index}"
            assert check.has_vector
            assert check.search_found, f"{check.doc_id} ({check.path}) not found by hybrid search"

        # Independent hybrid search against the migrated "_next" index (not
        # just the runner's own self-check) — proves a real query retrieves
        # an expected golden document.
        query, expected_stem = _HYBRID_PROBE
        vector = get_shared_embedder()._embed_text(query)
        next_docs_client = MeilisearchClient(index_name=report.next_docs_index)
        hits = next_docs_client.search_hybrid(query, vector, limit=5)
        assert any(expected_stem in (h.get("path") or "") for h in hits), (
            f"{query!r}: {expected_stem} not found in {report.next_docs_index} — "
            f"got {[h.get('path') for h in hits]}"
        )

        # CJK detection: the gapped document is REINDEX, not copied — its
        # "_next" record carries the GLUED content (best-available text),
        # not the stale one, and it is queued for a real reindex.
        if synthetic_path:
            assert str(synthetic_path) in report.reindex_paths
            next_record = next_docs_client.get_document(synthetic_id)
            assert next_record["content"] == "承擔"  # glued, gap removed

        # ------------------------------------------------------------
        # Swap
        # ------------------------------------------------------------
        swap_report = runner.swap(confirmed=True)

        assert swap_report.mode == "swap"
        assert swap_report.swapped is True
        if synthetic_path:
            task_queue.add_task.assert_called_once()
            args, kwargs = task_queue.add_task.call_args
            assert args[0] == str(synthetic_path)
            assert kwargs["metadata"]["force"] is True

        # The LIVE index (same name, same client) now serves the migrated
        # content: total count unchanged, synthetic doc's content is glued.
        assert meili.count("") == expected_docs_total
        if synthetic_path:
            live_record = meili.get_document(synthetic_id)
            assert live_record["content"] == "承擔"

        # ------------------------------------------------------------
        # Rollback
        # ------------------------------------------------------------
        rollback_report = runner.rollback(confirmed=True)

        assert rollback_report.mode == "rollback"
        assert rollback_report.swapped is True

        # The LIVE index is back to its PRE-migration content: same total
        # count, and the synthetic doc's content is fragmented again — proof
        # rollback restored the true prior state, not just "some" state.
        assert meili.count("") == expected_docs_total
        if synthetic_path:
            reverted_record = meili.get_document(synthetic_id)
            assert reverted_record["content"] == "承 擔"


class TestProdStoreGuardCoversMigration:
    """ÉPIC-31 (US-112) — the migration path must be covered by the SAME
    prod-store guard as the rest of the store-building surface (US-088).
    tests/conftest.py already patches MigrationV4Runner.__init__ AND
    lancedb.connect (guarded_migration_init/guarded_lancedb_connect) — this
    is a live-suite-flavoured re-check of the unit-tested guard
    (tests/unit/test_migrate_v4.py::TestProdStoreGuard), specifically for the
    acceptance criterion's exact wording: "a test pointing the migration at
    aitao_documents must fail". No live Meilisearch call actually happens —
    the guard raises before any network I/O.
    """

    def test_pointing_docs_index_at_prod_is_refused(self, tmp_path):
        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            MigrationV4Runner(
                docs_index_name="aitao_documents",
                chunks_index_name="test_migrate_v4_guard_chunks",
                lancedb_path=str(tmp_path),
            )
