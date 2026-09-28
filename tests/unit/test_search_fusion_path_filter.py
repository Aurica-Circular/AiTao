# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_search_fusion_path_filter.py -- US-125 regression tests: the folder
# filter (--path CLI / path_contains API+RAG) must actually restrict fusion
# document search results, in every mode (semantic / hybrid / fulltext).
#
# Same mocking style as test_hybrid_fusion.py: fake lancedb_repo/meilisearch_repo
# injected into HybridSearchEngine, no live Meilisearch/embedding model.
# search_hybrid() itself is mocked to return a mix of in-path and out-of-path
# hits -- MeilisearchClient cannot filter a path substring natively (no
# CONTAINS filter, `path` isn't even a filterable attribute), so the fix
# under test is the Python post-filter applied in search.search_executors
# after the mocked call returns.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search.hybrid_engine import HybridSearchEngine  # noqa: E402
from aitao.search.path_filter import PATH_FILTER_OVERFETCH_FACTOR  # noqa: E402
from aitao.search.search_models import SearchFilter  # noqa: E402


class _FakeSearchSection:
    def __init__(self, semantic_ratio_documents: float = 0.5):
        self.semantic_ratio_documents = semantic_ratio_documents


class _FakeConfig:
    def __init__(self, semantic_ratio_documents: float = 0.5):
        self.search = _FakeSearchSection(semantic_ratio_documents)


def _mock_lance(vector=None):
    client = Mock()
    client._embed_text = Mock(return_value=vector or [0.1, 0.2, 0.3])
    return client


def _hit(doc_id, path, score=0.9):
    return {
        "id": doc_id, "path": path, "title": doc_id, "content": "text",
        "category": None, "language": None, "file_size": None,
        "created_at": None, "_score": score,
    }


# A mix of hits inside and outside the "factures" folder being filtered on.
MIXED_HITS = [
    _hit("in1", "/data/factures/2024/janvier.pdf", score=0.95),
    _hit("out1", "/data/contrats/bail.pdf", score=0.90),
    _hit("in2", "/data/factures/2023/decembre.pdf", score=0.85),
    _hit("out2", "/data/notes/divers.md", score=0.80),
]


def _mock_meili(hits=None):
    client = Mock()
    client.search_hybrid = Mock(return_value=hits if hits is not None else list(MIXED_HITS))
    return client


class TestPathFilterAppliedAcrossModes:
    """A path_contains filter must yield ONLY in-path hits, identically in
    semantic / hybrid / fulltext mode (US-125 priority: reliability)."""

    @pytest.mark.parametrize("mode", ["semantic", "hybrid", "fulltext"])
    def test_only_in_path_hits_come_back(self, mode):
        lance = _mock_lance()
        meili = _mock_meili()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=_FakeConfig())

        response = engine.search_sync(
            "query", limit=10, filters=SearchFilter(path_contains="factures"), mode=mode,
        )

        paths = {r.path for r in response.results}
        assert paths == {"/data/factures/2024/janvier.pdf", "/data/factures/2023/decembre.pdf"}
        assert all("factures" in p for p in paths)

    def test_truncates_to_requested_limit_after_filtering(self):
        lance = _mock_lance()
        meili = _mock_meili()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=_FakeConfig())

        response = engine.search_sync(
            "query", limit=1, filters=SearchFilter(path_contains="factures"), mode="hybrid",
        )

        assert len(response.results) == 1
        assert "factures" in response.results[0].path

    def test_overfetch_requested_when_path_filter_active(self):
        lance = _mock_lance()
        meili = _mock_meili()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=_FakeConfig())

        engine.search_sync("query", limit=5, filters=SearchFilter(path_contains="factures"))

        requested_limit = meili.search_hybrid.call_args.kwargs["limit"]
        assert requested_limit == 5 * PATH_FILTER_OVERFETCH_FACTOR
        assert requested_limit > 5


class TestNoPathFilterIsANoOp:
    """US-125 point 5: path_contains empty/None must not change behaviour at
    all -- same limit sent to Meilisearch, no hit discarded."""

    def test_default_limit_unchanged_and_no_hits_dropped(self):
        lance = _mock_lance()
        meili = _mock_meili()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=_FakeConfig())

        response = engine.search_sync("query", limit=4, filters=SearchFilter())

        requested_limit = meili.search_hybrid.call_args.kwargs["limit"]
        assert requested_limit == 4  # no over-fetch
        assert len(response.results) == 4  # all hits kept, none filtered out

    def test_no_filters_object_at_all_unchanged(self):
        lance = _mock_lance()
        meili = _mock_meili()
        engine = HybridSearchEngine(lancedb_repo=lance, meilisearch_repo=meili, config=_FakeConfig())

        response = engine.search_sync("query", limit=4)

        requested_limit = meili.search_hybrid.call_args.kwargs["limit"]
        assert requested_limit == 4
        assert len(response.results) == 4
