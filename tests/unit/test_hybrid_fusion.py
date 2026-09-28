# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_hybrid_fusion.py — unit tests for the ÉPIC-31 (US-110/US-113) document-
# stage fusion search path (HybridSearchEngine + search/hybrid_fusion.py) —
# the only search path since v4.0 (decision D1, no more "rrf" engine).
#
# Fully mocked (no live Meilisearch/embedding model): injects fake lancedb_repo
# / meilisearch_repo clients and a fake config exposing
# search.semantic_ratio_documents, then asserts the SHAPE of the single native
# hybrid call — semanticRatio per mode, vector present, no fan-out.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search.hybrid_engine import HybridSearchEngine  # noqa: E402


class _FakeSearchSection:
    def __init__(self, semantic_ratio_documents: float = 0.5):
        self.semantic_ratio_documents = semantic_ratio_documents


class _FakeConfig:
    """Duck-typed stand-in for ConfigManager — only `.search.*` is read by
    HybridFusionMixin, so nothing else needs implementing."""

    def __init__(self, semantic_ratio_documents: float = 0.5):
        self.search = _FakeSearchSection(semantic_ratio_documents)


def _mock_lance(vector=None):
    client = Mock()
    client._embed_text = Mock(return_value=vector or [0.1, 0.2, 0.3])
    return client


def _mock_meili(hits=None):
    client = Mock()
    client.search_hybrid = Mock(return_value=hits or [])
    client.search = Mock(return_value=[])  # would be used by the rrf path
    return client


class TestFusionEngineHybridCallShape:
    """ONE call to meilisearch_client.search_hybrid() — the only search path."""

    def test_hybrid_mode_uses_semantic_ratio_documents(self):
        lance = _mock_lance(vector=[0.4, 0.5, 0.6])
        meili = _mock_meili(hits=[{
            "id": "doc1", "path": "/a.md", "title": "A", "content": "hello",
            "category": None, "language": None, "file_size": None,
            "created_at": None, "_score": 0.87,
        }])
        cfg = _FakeConfig(semantic_ratio_documents=0.42)
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=cfg)

        response = engine.search_sync("chiffre d'affaires", limit=5)

        assert meili.search_hybrid.called
        kwargs = meili.search_hybrid.call_args.kwargs
        assert kwargs["semantic_ratio"] == 0.42
        assert kwargs["vector"] == [0.4, 0.5, 0.6]
        assert kwargs["query"] == "chiffre d'affaires"

        # No parallel fan-out — lance/meili's plain .search() must never run.
        lance.search.assert_not_called()
        meili.search.assert_not_called()

        assert len(response.results) == 1
        assert response.results[0].id == "doc1"
        assert response.results[0].score == pytest.approx(0.87)
        assert response.lancedb_count == 0
        assert response.meilisearch_count == 1

    def test_semantic_mode_forces_ratio_1(self):
        lance = _mock_lance()
        meili = _mock_meili()
        cfg = _FakeConfig(semantic_ratio_documents=0.5)
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=cfg)

        engine.search_sync("query", mode="semantic")

        assert meili.search_hybrid.call_args.kwargs["semantic_ratio"] == 1.0

    def test_fulltext_mode_forces_ratio_0(self):
        lance = _mock_lance()
        meili = _mock_meili()
        cfg = _FakeConfig(semantic_ratio_documents=0.5)
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=cfg)

        engine.search_sync("query", mode="fulltext")

        assert meili.search_hybrid.call_args.kwargs["semantic_ratio"] == 0.0

    def test_query_vector_comes_from_shared_embedding_path(self):
        """US-110 point 4: no new embedding stack — the query vector is
        whatever lancedb_client._embed_text() (the existing bge-m3 path)
        returns, passed through verbatim."""
        lance = _mock_lance(vector=[9.9, 8.8])
        meili = _mock_meili()
        cfg = _FakeConfig()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=cfg)

        engine.search_sync("query")

        lance._embed_text.assert_called_once()
        assert meili.search_hybrid.call_args.kwargs["vector"] == [9.9, 8.8]
