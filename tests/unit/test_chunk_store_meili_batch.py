# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_chunk_store_meili_batch.py — unit tests for the ÉPIC-31 (US-111,
# absorbs US-093) additions to MeiliChunkStore: embed_document() (public
# doc-level embedding hook, reused by the indexer instead of loading a
# second SentenceTransformer) and delete_by_doc_ids() (batched delete, the
# "1 file = 1 state" precondition for a grouped chunk add).
#
# Fully mocked: no live Meilisearch server, no real embedding model load —
# mirrors tests/unit/test_chunk_store_meili.py's own mocking style.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aitao.indexation.chunk_store_meili as csm  # noqa: E402


class _FakeApiError(Exception):
    pass


def _fake_task(uid=1):
    return Mock(task_uid=uid)


@pytest.fixture(autouse=True)
def _no_real_backends(monkeypatch):
    monkeypatch.setattr(csm, "MeilisearchApiError", _FakeApiError)


def _make_store(monkeypatch):
    mock_index = Mock()
    mock_index.update_settings = Mock(return_value=_fake_task())

    mock_client = Mock()
    mock_client.get_index = Mock(return_value=mock_index)  # index "exists" already
    mock_client.wait_for_task = Mock(return_value={"status": "succeeded"})

    monkeypatch.setattr(csm, "Client", Mock(return_value=mock_client))

    mock_model = Mock()
    mock_model.encode = Mock(return_value=Mock(tolist=lambda: [0.1, 0.2, 0.3, 0.4]))
    monkeypatch.setattr(csm, "SentenceTransformer", Mock(return_value=mock_model))

    store = csm.MeiliChunkStore(
        url="http://localhost:7700", index_name="test_chunks_unit", config=None,
        dimension=4, embedding_model="fake-model",
    )
    return store, mock_index


class TestEmbedDocument:
    def test_embed_document_returns_a_vector(self, monkeypatch):
        store, _mock_index = _make_store(monkeypatch)

        vector = store.embed_document("some whole-document text")

        assert vector == [0.1, 0.2, 0.3, 0.4]

    def test_embed_document_on_empty_text_returns_zero_vector(self, monkeypatch):
        store, _mock_index = _make_store(monkeypatch)

        vector = store.embed_document("   ")

        assert vector == [0.0, 0.0, 0.0, 0.0]


class TestDeleteByDocIds:
    def test_deletes_with_an_in_filter_in_one_task(self, monkeypatch):
        store, mock_index = _make_store(monkeypatch)
        mock_index.delete_documents = Mock(return_value=_fake_task())

        store.delete_by_doc_ids(["d1", "d2", "d3"])

        mock_index.delete_documents.assert_called_once()
        _args, kwargs = mock_index.delete_documents.call_args
        assert kwargs["filter"] == 'doc_id IN ["d1", "d2", "d3"]'

    def test_empty_list_is_a_no_op(self, monkeypatch):
        store, mock_index = _make_store(monkeypatch)
        mock_index.delete_documents = Mock(return_value=_fake_task())

        store.delete_by_doc_ids([])

        mock_index.delete_documents.assert_not_called()

    def test_raises_when_the_delete_task_fails(self, monkeypatch):
        """A failed (not just erroring) delete task must raise so the caller
        (indexation.batch_indexer) excludes the whole group from the add step
        instead of proceeding as if the old chunks were gone."""
        store, mock_index = _make_store(monkeypatch)
        mock_index.delete_documents = Mock(return_value=_fake_task())
        # wait_for_task_status swallows exceptions and returns a plain dict;
        # simulate a Meilisearch-reported failure (not a communication error).
        monkeypatch.setattr(
            csm, "wait_for_task_status",
            lambda *a, **k: {"status": "failed", "error": "index locked"},
        )

        with pytest.raises(csm.MeiliChunkStoreError):
            store.delete_by_doc_ids(["d1"])
