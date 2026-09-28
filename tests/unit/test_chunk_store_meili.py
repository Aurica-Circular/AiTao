# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_chunk_store_meili.py — unit tests for MeiliChunkStore (ÉPIC-31, US-110
# excerpt-stage fusion engine) and MeilisearchAdminMixin.configure_embedder().
#
# Fully mocked: no live Meilisearch server, no real embedding model load — the
# `meilisearch.Client` and `sentence_transformers.SentenceTransformer` symbols
# imported by indexation.chunk_store_meili are monkeypatched. Asserts the
# SHAPE of what US-110 actually promises: a userProvided embedder configured
# at index creation, and search() issuing ONE native hybrid call carrying both
# the lexical query and a locally computed vector.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aitao.indexation.chunk_store_meili as csm  # noqa: E402


class _FakeApiError(Exception):
    """Stand-in for meilisearch.errors.MeilisearchApiError — its real
    constructor needs a requests.Response, overkill for a unit test; only the
    string content ("index_not_found") is inspected by the code under test."""


def _fake_task(uid=1):
    return Mock(task_uid=uid)


@pytest.fixture(autouse=True)
def _no_real_backends(monkeypatch):
    """Every test in this file mocks the two heavy dependencies itself
    (Client, SentenceTransformer); this fixture only patches the shared
    exception type so index_not_found handling works without a real server."""
    monkeypatch.setattr(csm, "MeilisearchApiError", _FakeApiError)


def _make_store(monkeypatch, index_exists: bool):
    """Build a MeiliChunkStore with every backend mocked.

    Returns (store, mock_client, mock_index). ``index_exists=False`` exercises
    the creation path (settings + embedder applied); ``True`` exercises reuse
    (nothing (re)configured, matching MeilisearchClient's own _ensure_index).
    """
    mock_index = Mock()
    mock_index.update_settings = Mock(return_value=_fake_task())

    mock_client = Mock()
    if index_exists:
        mock_client.get_index = Mock(return_value=mock_index)
    else:
        mock_client.get_index = Mock(side_effect=[_FakeApiError("index_not_found"), mock_index])
    mock_client.create_index = Mock(return_value=_fake_task())
    mock_client.wait_for_task = Mock(return_value={"status": "succeeded"})

    monkeypatch.setattr(csm, "Client", Mock(return_value=mock_client))

    mock_model = Mock()
    mock_model.encode = Mock(return_value=Mock(tolist=lambda: [0.1, 0.2, 0.3, 0.4]))
    monkeypatch.setattr(csm, "SentenceTransformer", Mock(return_value=mock_model))

    store = csm.MeiliChunkStore(
        url="http://localhost:7700",
        index_name="test_chunks_unit",
        config=None,
        dimension=4,
        embedding_model="fake-model",
        semantic_ratio=0.5,
    )
    return store, mock_client, mock_index


class TestEmbedderConfiguredOnCreation:
    def test_new_index_gets_userprovided_embedder(self, monkeypatch):
        store, mock_client, mock_index = _make_store(monkeypatch, index_exists=False)

        mock_client.create_index.assert_called_once()
        # Two update_settings calls at creation: searchable/filterable attrs,
        # then the embedder (configure_embedder -> update_settings).
        assert mock_index.update_settings.call_count == 2
        embedder_call = mock_index.update_settings.call_args_list[-1].args[0]
        assert embedder_call == {
            "embedders": {
                store.EMBEDDER_NAME: {"source": "userProvided", "dimensions": 4},
            }
        }

    def test_existing_index_is_reused_without_reconfiguring(self, monkeypatch):
        store, mock_client, mock_index = _make_store(monkeypatch, index_exists=True)

        mock_client.create_index.assert_not_called()
        mock_index.update_settings.assert_not_called()


class TestChunkHybridSearchCallShape:
    def test_search_issues_one_native_hybrid_call(self, monkeypatch):
        store, _client, mock_index = _make_store(monkeypatch, index_exists=True)
        mock_index.search = Mock(return_value={
            "hits": [{
                "chunk_id": "c1", "doc_id": "d1", "path": "/a.md", "title": "A",
                "content": "hello world", "chunk_index": 0, "total_chunks": 1,
                "_rankingScore": 0.77,
            }],
        })

        results = store.search("query text", limit=5)

        mock_index.search.assert_called_once()
        args, kwargs = mock_index.search.call_args
        assert args[0] == "query text"
        opts = args[1]
        assert opts["hybrid"] == {"semanticRatio": 0.5, "embedder": store.EMBEDDER_NAME}
        assert opts["vector"] == [0.1, 0.2, 0.3, 0.4]
        assert opts["limit"] == 5

        assert len(results) == 1
        chunk, score = results[0]
        assert chunk.chunk_id == "c1"
        assert score == pytest.approx(0.77)

    def test_search_uses_configured_semantic_ratio(self, monkeypatch):
        store, _client, mock_index = _make_store(monkeypatch, index_exists=True)
        mock_index.search = Mock(return_value={"hits": []})
        store._semantic_ratio = 0.8

        store.search("query")

        opts = mock_index.search.call_args.args[1]
        assert opts["hybrid"]["semanticRatio"] == 0.8

    def test_min_score_filters_low_ranking_hits(self, monkeypatch):
        store, _client, mock_index = _make_store(monkeypatch, index_exists=True)
        mock_index.search = Mock(return_value={
            "hits": [
                {"chunk_id": "low", "doc_id": "d", "path": "/a.md", "title": "A",
                 "content": "x", "chunk_index": 0, "total_chunks": 1, "_rankingScore": 0.1},
                {"chunk_id": "high", "doc_id": "d", "path": "/a.md", "title": "A",
                 "content": "y", "chunk_index": 1, "total_chunks": 1, "_rankingScore": 0.9},
            ],
        })

        results = store.search("query", min_score=0.5)

        assert [c.chunk_id for c, _ in results] == ["high"]


class TestAddChunksEmbedsVerbatimVectors:
    def test_add_chunks_pushes_userprovided_vectors(self, monkeypatch):
        from aitao.indexation.interfaces import Chunk

        store, _client, mock_index = _make_store(monkeypatch, index_exists=True)
        mock_index.add_documents = Mock(return_value=_fake_task())

        chunk = Chunk(
            chunk_id="c1", doc_id="d1", path="/a.md", title="A", content="hello",
            chunk_index=0, total_chunks=1, offset_start=0, offset_end=5,
        )
        count = store.add_chunks([chunk])

        assert count == 1
        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["chunk_id"] == "c1"
        assert records[0]["_vectors"] == {store.EMBEDDER_NAME: [0.1, 0.2, 0.3, 0.4]}
