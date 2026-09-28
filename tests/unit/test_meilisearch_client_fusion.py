# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_meilisearch_client_fusion.py — unit tests for the ÉPIC-31 (US-110)
# document-stage fusion additions to MeilisearchClient: search_hybrid() call
# shape, and the userProvided embedder configured on the document index.
#
# Since v4.0 (US-113, decision D1) fusion is the ONLY engine: every
# construction configures the embedder, EXCEPT ``ensure_embedder=False`` — the
# one remaining opt-out, used solely by the US-112 migration tool to read the
# LIVE pre-migration index without the side effect of mutating its settings
# (see search/meilisearch_fusion.py). There is no more "rrf" engine to gate on.
#
# Fully mocked meilisearch.Client — no live server needed.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aitao.search.meilisearch_client as mcmod  # noqa: E402


def _fake_task(uid=1):
    return Mock(task_uid=uid)


class _FakeEmbeddingSection:
    def __init__(self, dimension=4):
        self.dimension = dimension


class _FakeSearchSection:
    def __init__(self, dimension=4):
        self.embedding = _FakeEmbeddingSection(dimension)
        self.meilisearch = Mock(url="http://localhost:7700", api_key="", index_name="test_docs")


class _FakeConfig:
    def __init__(self):
        self.search = _FakeSearchSection()


def _make_client(monkeypatch, ensure_embedder: bool = True):
    """Build a MeilisearchClient with a mocked meilisearch.Client, index
    already "existing" (get_index succeeds immediately) so DEFAULT_SETTINGS
    creation noise doesn't interfere with the embedder-gating assertions."""
    mock_index = Mock()
    mock_index.update_settings = Mock(return_value=_fake_task())

    mock_client = Mock()
    mock_client.health = Mock(return_value={"status": "available"})
    mock_client.get_index = Mock(return_value=mock_index)
    mock_client.wait_for_task = Mock(return_value={"status": "succeeded"})

    monkeypatch.setattr(mcmod, "Client", Mock(return_value=mock_client))

    cfg = _FakeConfig()
    client = mcmod.MeilisearchClient(
        host="http://localhost:7700", index_name="test_docs", config=cfg,
        ensure_embedder=ensure_embedder,
    )
    return client, mock_client, mock_index


class TestDocumentIndexEmbedderGating:
    """MeilisearchClient._ensure_index() always configures a userProvided
    embedder on construction — the ONE opt-out is ``ensure_embedder=False``
    (the US-112 migration tool's read-only view of the live pre-migration
    index, which must never mutate its settings)."""

    def test_configures_userprovided_embedder_by_default(self, monkeypatch):
        _client, _mock_client, mock_index = _make_client(monkeypatch)

        mock_index.update_settings.assert_called_once()
        settings = mock_index.update_settings.call_args.args[0]
        assert settings == {
            "embedders": {"default": {"source": "userProvided", "dimensions": 4}},
        }

    def test_ensure_embedder_false_never_touches_embedder_settings(self, monkeypatch):
        _client, _mock_client, mock_index = _make_client(monkeypatch, ensure_embedder=False)

        mock_index.update_settings.assert_not_called()


class TestSearchHybridCallShape:
    def test_search_hybrid_call_shape_and_result_score(self, monkeypatch):
        client, _mock_client, mock_index = _make_client(monkeypatch)
        mock_index.update_settings.reset_mock()  # only interested in search() below
        mock_index.search = Mock(return_value={
            "hits": [{
                "id": "doc1", "path": "/a.md", "title": "A", "content": "hello",
                "category": None, "language": None, "file_type": None,
                "file_size": None, "created_at": None, "_rankingScore": 0.63,
            }],
        })

        results = client.search_hybrid("query", vector=[0.1, 0.2], limit=7, semantic_ratio=0.42)

        query_arg, opts = mock_index.search.call_args.args
        assert query_arg == "query"
        assert opts["hybrid"] == {"semanticRatio": 0.42, "embedder": "default"}
        assert opts["vector"] == [0.1, 0.2]
        assert opts["limit"] == 7

        assert len(results) == 1
        assert results[0]["id"] == "doc1"
        assert results[0]["_score"] == pytest.approx(0.63)

    def test_search_hybrid_applies_category_and_language_filters(self, monkeypatch):
        client, _mock_client, mock_index = _make_client(monkeypatch)
        mock_index.search = Mock(return_value={"hits": []})

        client.search_hybrid(
            "query", vector=[0.1], filter_category="finance", filter_language="fr",
        )

        _query_arg, opts = mock_index.search.call_args.args
        assert opts["filter"] == 'category = "finance" AND language = "fr"'
