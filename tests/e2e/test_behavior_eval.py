# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
US-17d — Behavioral evaluation harness (demo gate).

Replays the manual anti-hallucination checklist of US-17 against the REAL
stack: running API (port 8200), real indexes, real LLM — zero mock. Run it
before any demo:

    uv run pytest tests/e2e/test_behavior_eval.py -v

Requirements: `./aitao.sh start` (API + Meilisearch) and Ollama with the
target model pulled. Model = `llm.default_model`, overridable:

    AITAO_EVAL_MODEL=granite4:latest uv run pytest tests/e2e/test_behavior_eval.py -v

Assertions are deliberately deterministic (dates, gate messages, citation
audit) — never the model's prose, which varies run to run.
"""

import os
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

API_URL = os.environ.get("AITAO_EVAL_API", "http://localhost:8200")
# LLM calls through a 12B-class local model can take minutes
REQUEST_TIMEOUT = 360.0


def _api_available() -> bool:
    try:
        return httpx.get(f"{API_URL}/api/health", timeout=3.0).status_code == 200
    except Exception:
        return False


def _eval_model() -> str:
    if os.environ.get("AITAO_EVAL_MODEL"):
        return os.environ["AITAO_EVAL_MODEL"]
    from aitao.core.config import get_config

    return get_config().llm.default_model


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _api_available(),
        reason=f"AiTao API not running at {API_URL} — start with ./aitao.sh start",
    ),
]


def _chat(question: str) -> dict:
    """One-shot chat call (fresh conversation, non-streaming, RAG on)."""
    response = httpx.post(
        f"{API_URL}/api/chat",
        json={
            "model": _eval_model(),
            "messages": [{"role": "user", "content": question}],
            "stream": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "content": data.get("message", {}).get("content", ""),
        "rag_context": data.get("rag_context") or [],
        "context_source": data.get("context_source"),
    }


def _fabricated_citations(content: str, rag_context: list) -> list:
    """Audit answer citations against the actually retrieved sources."""
    from aitao.llm.citation_guard import find_fabricated_citations

    docs = [
        SimpleNamespace(path=doc.get("path", ""), title=doc.get("title", ""))
        for doc in rag_context
    ]
    return find_fabricated_citations(content, docs)


class TestTier0SystemFacts:
    """Origin of US-17: 'Quel jour sommes-nous ?' answered '21 octobre 2023'
    with a fabricated citation. Must return the system date, zero citation."""

    def test_system_date_without_greeting(self):
        result = _chat("Quel jour sommes-nous ?")
        today = datetime.now()
        assert str(today.year) in result["content"], result["content"]
        assert str(today.day) in result["content"], result["content"]
        assert _fabricated_citations(result["content"], []) == []

    def test_system_date_with_greeting_prefix(self):
        result = _chat("Bonjour, quel jour sommes-nous ?")
        assert str(datetime.now().year) in result["content"], result["content"]
        assert _fabricated_citations(result["content"], []) == []


class TestConversationalGate:
    """US-17b — greetings stay greetings, questions behind them get through."""

    def test_pure_greeting_not_refused(self):
        from aitao.llm.context_adequacy import REFUSAL_MESSAGE

        result = _chat("Bonjour AiTao !")
        assert result["content"].strip(), "empty answer to a greeting"
        assert REFUSAL_MESSAGE not in result["content"]
        assert "aucun document suffisamment pertinent" not in result["content"]


class TestOffCorpusReformulation:
    """US-17c — a question sharing nothing with the corpus must get the
    instant reformulation message (no LLM call), never an invented answer."""

    def test_off_corpus_returns_reformulation(self):
        start = time.time()
        result = _chat("Quel est le budget du projet Hyperloop Mars ?")
        elapsed = time.time() - start
        assert result["content"].startswith(
            "Je n'ai trouvé aucun document suffisamment pertinent"
        ), result["content"]
        # Gate answers without the LLM — generation latency means regression
        assert elapsed < 60, f"reformulation took {elapsed:.0f}s (LLM called?)"

    def test_off_corpus_cites_nothing(self):
        result = _chat("Que dit le rapport Zorglub sur la fusion froide ?")
        assert _fabricated_citations(result["content"], []) == []


class TestCitationDiscipline:
    """US-17c — every file cited in an answer must be a retrieved source."""

    def test_corpus_question_cites_only_retrieved_sources(self):
        result = _chat("Quelles sont les échéances du contrat de location ?")
        assert result["content"].strip()
        fabricated = _fabricated_citations(
            result["content"], result["rag_context"]
        )
        assert fabricated == [], f"fabricated citations: {fabricated}"

    def test_absent_year_question_cites_only_retrieved_sources(self):
        # The '2031' demo case: grounded refusal or closest-info answer is
        # fine — fabricated sources are not.
        result = _chat("Bonjour, quelles sont les charges de copropriété 2031 ?")
        fabricated = _fabricated_citations(
            result["content"], result["rag_context"]
        )
        assert fabricated == [], f"fabricated citations: {fabricated}"
