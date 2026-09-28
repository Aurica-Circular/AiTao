# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for HybridSearchEngine (ÉPIC-31, US-113 — fusion-only).

Tests cover:
- SearchFilter/SearchResult/HybridSearchResponse model shape
- Engine initialization
- Repository injection (US-24) and the single fusion hybrid call

The parallel LanceDB+Meilisearch fan-out + Reciprocal Rank Fusion merge this
file used to test (TestResultMerging, TestParallelSearch, _merge_results,
_normalize_score) is gone along with that code path — v4.0 is fusion-only
(decision D1). The fusion call SHAPE itself (semanticRatio per mode, vector
present, no fan-out) is covered in depth by test_hybrid_fusion.py; this file
sticks to models + engine plumbing so the two files don't duplicate.
"""

from datetime import datetime
from unittest.mock import Mock, MagicMock
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ============================================================================
# Mock Setup (before importing HybridSearchEngine)
# We save and restore sys.modules to avoid polluting other tests
# ============================================================================

mock_logger = Mock()
mock_logger.info = Mock()
mock_logger.warning = Mock()
mock_logger.debug = Mock()
mock_logger.error = Mock()

mock_get_logger = Mock(return_value=mock_logger)

mock_logger_module = Mock()
mock_logger_module.get_logger = mock_get_logger
mock_logger_module.StructuredLogger = Mock
mock_logger_module.JSONFormatter = Mock
mock_logger_module.HumanReadableFormatter = Mock

_original_logger_module = sys.modules.get("core.logger")
sys.modules["core.logger"] = mock_logger_module


# ============================================================================
# Import after mocking
# ============================================================================

from aitao.search.hybrid_engine import HybridSearchEngine  # noqa: E402
from aitao.search.search_models import (  # noqa: E402
    HybridSearchResponse,
    SearchFilter,
    SearchResult,
)

if _original_logger_module is not None:
    sys.modules["core.logger"] = _original_logger_module
else:
    del sys.modules["core.logger"]


# ============================================================================
# SearchFilter Tests
# ============================================================================

class TestSearchFilter:
    """Tests for SearchFilter dataclass."""

    def test_default_values(self):
        f = SearchFilter()
        assert f.path_contains is None
        assert f.category is None
        assert f.language is None
        assert f.date_after is None
        assert f.date_before is None
        assert f.file_types is None

    def test_custom_values(self):
        now = datetime.now()
        f = SearchFilter(
            path_contains="documents",
            category="finance",
            language="fr",
            date_after=now,
            file_types=[".pdf", ".docx"],
        )
        assert f.path_contains == "documents"
        assert f.category == "finance"
        assert f.language == "fr"
        assert f.date_after == now
        assert f.file_types == [".pdf", ".docx"]


# ============================================================================
# SearchResult Tests
# ============================================================================

class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_minimal_result(self):
        r = SearchResult(
            id="abc123",
            path="/docs/test.pdf",
            title="Test Document",
            content="Test content...",
            score=0.85,
        )
        assert r.id == "abc123"
        assert r.score == 0.85
        assert r.semantic_score == 0.0
        assert r.fulltext_score == 0.0

    def test_full_result(self):
        now = datetime.now()
        r = SearchResult(
            id="abc123",
            path="/docs/test.pdf",
            title="Test Document",
            content="Test content...",
            score=0.85,
            semantic_score=0.9,
            fulltext_score=0.7,
            category="test",
            language="en",
            file_size=1024,
            modified_at=now,
            metadata={"key": "value"},
        )
        assert r.semantic_score == 0.9
        assert r.fulltext_score == 0.7
        assert r.metadata == {"key": "value"}


# ============================================================================
# HybridSearchEngine Initialization Tests
# ============================================================================

class TestHybridSearchEngineInit:
    """Tests for engine initialization."""

    def test_defaults(self):
        eng = HybridSearchEngine()
        assert eng.max_workers == 2
        assert eng.enable_query_expansion is True

    def test_max_workers(self):
        eng = HybridSearchEngine(max_workers=4)
        assert eng.max_workers == 4

    def test_cleanup(self):
        eng = HybridSearchEngine()
        eng.close()  # should not raise


# ============================================================================
# Response Structure Tests
# ============================================================================

class TestHybridSearchResponse:
    """Tests for HybridSearchResponse structure."""

    def test_response_fields(self):
        response = HybridSearchResponse(
            query="test query",
            results=[],
            total=0,
        )

        assert response.query == "test query"
        assert response.results == []
        assert response.total == 0
        assert response.meilisearch_count == 0
        assert response.search_time_ms == 0.0
        assert response.mode == "hybrid"

    def test_response_with_results(self):
        results = [
            SearchResult(
                id="abc",
                path="/test",
                title="Test",
                content="...",
                score=0.9,
            )
        ]

        response = HybridSearchResponse(
            query="test",
            results=results,
            total=1,
            meilisearch_count=1,
            search_time_ms=50.0,
            meilisearch_time_ms=45.0,
            mode="semantic",
        )

        assert len(response.results) == 1
        assert response.meilisearch_time_ms == 45.0


# ============================================================================
# Full search Tests (fusion — single native Meilisearch hybrid call)
# ============================================================================

class TestFullSearch:
    """Tests for the complete (fusion) search workflow."""

    def _engine(self, hits=None):
        meili = MagicMock()
        meili.search_hybrid = Mock(return_value=hits or [])
        lance = MagicMock()
        lance._embed_text = Mock(return_value=[0.1, 0.2, 0.3])
        return HybridSearchEngine(
            enable_query_expansion=False, lancedb_repo=lance, meilisearch_repo=meili,
        ), meili, lance

    def test_search_empty_query(self):
        engine, _, _ = self._engine()
        response = engine.search_sync(query="", limit=10)

        assert response.query == ""
        assert response.total == 0
        assert response.results == []

    def test_search_whitespace_query(self):
        engine, _, _ = self._engine()
        response = engine.search_sync(query="   ", limit=10)

        assert response.total == 0

    def test_search_with_results(self):
        hits = [{
            "id": "d1", "path": "/docs/invoice.pdf", "title": "Invoice", "content": "hello",
            "category": "finance", "language": "en", "file_size": None,
            "created_at": None, "_score": 0.9,
        }]
        engine, meili, _ = self._engine(hits=hits)

        response = engine.search_sync(query="invoice", limit=10)

        assert response.query == "invoice"
        assert response.total == 1
        assert response.meilisearch_count == 1
        assert response.mode == "hybrid"
        assert response.search_time_ms > 0
        assert meili.search_hybrid.called

    def test_search_pagination(self):
        hits = [{
            "id": f"d{i}", "path": f"/docs/{i}.pdf", "title": f"Doc {i}", "content": "x",
            "category": None, "language": None, "file_size": None,
            "created_at": None, "_score": 1.0 - i * 0.1,
        } for i in range(5)]
        engine, _, _ = self._engine(hits=hits)

        response = engine.search_sync(query="test", limit=1, offset=1)

        assert len(response.results) <= 1

    def test_search_mode_semantic(self):
        engine, meili, _ = self._engine()
        engine.search_sync(query="test", limit=10, mode="semantic")
        assert meili.search_hybrid.call_args.kwargs["semantic_ratio"] == 1.0

    def test_search_mode_fulltext(self):
        engine, meili, _ = self._engine()
        engine.search_sync(query="test", limit=10, mode="fulltext")
        assert meili.search_hybrid.call_args.kwargs["semantic_ratio"] == 0.0


# ============================================================================
# Repository injection (US-24) — swap the storage backend for a test double
# ============================================================================

def _meili_with_hit(doc_id, path):
    """A stand-in Meilisearch repository returning one canned hybrid hit."""
    repo = MagicMock()
    repo.search_hybrid.return_value = [{
        "id": doc_id, "path": path, "title": "T", "content": "hello world",
        "category": "text", "language": "en", "file_size": None,
        "created_at": None, "_score": 0.9,
    }]
    return repo


def _embedder():
    repo = MagicMock()
    repo._embed_text = Mock(return_value=[0.1, 0.2, 0.3])
    return repo


def test_engine_uses_injected_repositories():
    """Injected repositories are used as-is — no real backend is built."""
    ldb = _embedder()
    meili = _meili_with_hit("d2", "/docs/b.txt")

    engine = HybridSearchEngine(
        enable_query_expansion=False, lancedb_repo=ldb, meilisearch_repo=meili
    )

    assert engine.lancedb_client is ldb
    assert engine.meilisearch_client is meili


def test_search_runs_entirely_on_injected_doubles():
    """The whole search pipeline runs against test doubles (US-24 AC)."""
    ldb = _embedder()
    meili = _meili_with_hit("d2", "/docs/b.txt")

    engine = HybridSearchEngine(
        enable_query_expansion=False, lancedb_repo=ldb, meilisearch_repo=meili
    )
    response = engine.search_sync("hello", limit=5)

    assert isinstance(response, HybridSearchResponse)
    assert ldb._embed_text.called
    assert meili.search_hybrid.called
    assert len(response.results) == 1
    assert response.results[0].id == "d2"
