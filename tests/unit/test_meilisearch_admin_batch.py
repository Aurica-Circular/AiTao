# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_meilisearch_admin_batch.py — unit tests for
# MeilisearchAdminMixin.add_documents_batch() (ÉPIC-31, US-111, absorbs
# US-093): the ONE-task batched document write, now actually wired into
# production (previously dead code — see meilisearch_admin.py:add_documents_batch).
#
# Covers the two behaviour changes over the pre-US-111 version:
# - metadata (mtime, pages...) is merged into each record, matching
#   add_document() — previously silently dropped here, defeating the US-17
#   mtime staleness check for anything indexed through this path.
# - a per-document `vectors` list threads the real fusion doc-level vector
#   (or falls back to the US-110 null opt-out) via _fusion_vectors_payload().
#   Fusion is the only engine since v4.0 (US-113, decision D1): every write
#   always carries a _vectors field, never omitted.
#
# Fully mocked meilisearch.Client — no live server needed.

import sys
from pathlib import Path
from unittest.mock import Mock

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


def _make_client(monkeypatch):
    mock_index = Mock()
    mock_index.update_settings = Mock(return_value=_fake_task())
    mock_index.add_documents = Mock(return_value=_fake_task())

    mock_client = Mock()
    mock_client.health = Mock(return_value={"status": "available"})
    mock_client.get_index = Mock(return_value=mock_index)
    mock_client.wait_for_task = Mock(return_value={"status": "succeeded"})

    monkeypatch.setattr(mcmod, "Client", Mock(return_value=mock_client))

    cfg = _FakeConfig()
    client = mcmod.MeilisearchClient(host="http://localhost:7700", index_name="test_docs", config=cfg)
    return client, mock_index


class TestAddDocumentsBatchMetadataMerge:
    def test_metadata_is_merged_into_each_record(self, monkeypatch):
        client, mock_index = _make_client(monkeypatch)
        mock_index.update_settings.reset_mock()

        doc_ids = client.add_documents_batch([
            {"path": "/a.txt", "title": "A", "content": "hello", "metadata": {"mtime": 123.456}},
        ])

        assert len(doc_ids) == 1
        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["mtime"] == 123.456

    def test_metadata_never_overrides_a_core_field(self, monkeypatch):
        client, mock_index = _make_client(monkeypatch)

        client.add_documents_batch([
            {"path": "/a.txt", "title": "A", "content": "hello",
             "metadata": {"title": "should not win", "path": "/should/not/win"}},
        ])

        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["title"] == "A"
        assert records[0]["path"] == "/a.txt"


class TestAddDocumentsBatchVectors:
    """Since v4.0 fusion is the only engine (ÉPIC-31, US-113): every document
    write always carries a ``_vectors`` field — the real vector when given,
    else the US-110 explicit null opt-out. There is no more "rrf" mode that
    omits ``_vectors`` entirely (that used to be its own test here)."""

    def test_uses_real_vector_when_given(self, monkeypatch):
        client, mock_index = _make_client(monkeypatch)
        mock_index.update_settings.reset_mock()

        client.add_documents_batch(
            [{"path": "/a.txt", "title": "A", "content": "hello"}],
            vectors=[[0.1, 0.2, 0.3, 0.4]],
        )

        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["_vectors"] == {"default": [0.1, 0.2, 0.3, 0.4]}

    def test_falls_back_to_null_optout_without_a_vector(self, monkeypatch):
        client, mock_index = _make_client(monkeypatch)
        mock_index.update_settings.reset_mock()

        client.add_documents_batch([{"path": "/a.txt", "title": "A", "content": "hello"}])

        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["_vectors"] == {"default": None}

    def test_vectors_align_positionally_with_documents(self, monkeypatch):
        client, mock_index = _make_client(monkeypatch)
        mock_index.update_settings.reset_mock()

        client.add_documents_batch(
            [
                {"path": "/a.txt", "title": "A", "content": "a"},
                {"path": "/b.txt", "title": "B", "content": "b"},
            ],
            vectors=[[1.0, 0.0, 0.0, 0.0], None],
        )

        records = mock_index.add_documents.call_args.args[0]
        assert records[0]["_vectors"] == {"default": [1.0, 0.0, 0.0, 0.0]}
        assert records[1]["_vectors"] == {"default": None}
