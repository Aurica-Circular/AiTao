# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_index_rebuild.py — unit tests for search.index_rebuild.IndexRebuilder
# (ÉPIC-31, US-111 — socle for the real US-112 migration): rebuild a
# "<name>_next" index with target settings/embedder, populate it, then swap
# it onto the live index name; rollback = re-swap.
#
# Fully mocked meilisearch.Client — no live server needed.

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search.index_rebuild import IndexRebuildError, IndexRebuilder  # noqa: E402


def _fake_task(uid=1):
    return Mock(task_uid=uid)


def _make_client():
    mock_index = Mock()
    mock_index.update_settings = Mock(return_value=_fake_task(1))
    mock_index.add_documents = Mock(return_value=_fake_task(2))

    mock_client = Mock()
    mock_client.create_index = Mock(return_value=_fake_task(0))
    mock_client.index = Mock(return_value=mock_index)
    mock_client.delete_index = Mock()
    mock_client.swap_indexes = Mock(return_value=_fake_task(3))
    mock_client.wait_for_task = Mock(return_value={"status": "succeeded"})
    return mock_client, mock_index


class TestRebuildHappyPath:
    def test_rebuild_creates_settings_populates_and_swaps(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        docs = [{"id": "1", "title": "a"}, {"id": "2", "title": "b"}]
        result = rebuilder.rebuild(
            "aitao_documents", settings={"searchableAttributes": ["title"]},
            documents=docs, batch_size=10,
        )

        client.delete_index.assert_called_once_with("aitao_documents_next")
        client.create_index.assert_called_once_with(
            "aitao_documents_next", {"primaryKey": "id"}
        )
        mock_index.update_settings.assert_called_once_with(
            {"searchableAttributes": ["title"]}
        )
        mock_index.add_documents.assert_called_once_with(docs)
        client.swap_indexes.assert_called_once_with(
            [{"indexes": ["aitao_documents", "aitao_documents_next"]}]
        )

        assert result.next_index_name == "aitao_documents_next"
        assert result.documents_written == 2
        assert result.swapped is True

    def test_documents_are_batched(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        docs = [{"id": str(i)} for i in range(25)]
        result = rebuilder.rebuild(
            "aitao_documents", settings={}, documents=docs, batch_size=10,
        )

        assert mock_index.add_documents.call_count == 3  # 10 + 10 + 5
        assert result.documents_written == 25

    def test_embedder_is_configured(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        rebuilder.rebuild(
            "aitao_chunks", settings={"searchableAttributes": ["content"]},
            embedder=("default", 1024),
        )

        settings_arg = mock_index.update_settings.call_args.args[0]
        assert settings_arg["embedders"] == {
            "default": {"source": "userProvided", "dimensions": 1024},
        }

    def test_no_documents_still_creates_and_swaps(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        result = rebuilder.rebuild("aitao_documents", settings={})

        mock_index.add_documents.assert_not_called()
        assert result.documents_written == 0
        assert result.swapped is True

    def test_swap_false_populates_without_swapping(self):
        client, _mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        result = rebuilder.rebuild("aitao_documents", settings={}, swap=False)

        client.swap_indexes.assert_not_called()
        assert result.swapped is False

    def test_custom_next_index_name(self):
        client, _mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        result = rebuilder.rebuild(
            "aitao_documents", settings={}, next_index_name="aitao_documents_v2",
        )

        client.create_index.assert_called_once_with(
            "aitao_documents_v2", {"primaryKey": "id"}
        )
        assert result.next_index_name == "aitao_documents_v2"


class TestRebuildFailurePaths:
    def test_settings_failure_raises_and_never_swaps(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)
        client.wait_for_task = Mock(side_effect=[
            {"status": "succeeded"},  # create_index
            {"status": "failed", "error": "bad settings"},  # update_settings
        ])

        with pytest.raises(IndexRebuildError):
            rebuilder.rebuild("aitao_documents", settings={"bad": "value"})

        client.swap_indexes.assert_not_called()

    def test_populate_failure_raises_and_never_swaps(self):
        client, mock_index = _make_client()
        rebuilder = IndexRebuilder(client)
        client.wait_for_task = Mock(side_effect=[
            {"status": "succeeded"},  # create_index
            {"status": "succeeded"},  # update_settings
            {"status": "failed", "error": "bad document"},  # add_documents
        ])

        with pytest.raises(IndexRebuildError):
            rebuilder.rebuild("aitao_documents", settings={}, documents=[{"id": "1"}])

        client.swap_indexes.assert_not_called()

    def test_swap_failure_raises(self):
        client, _mock_index = _make_client()
        rebuilder = IndexRebuilder(client)
        client.wait_for_task = Mock(side_effect=[
            {"status": "succeeded"},  # create_index
            {"status": "succeeded"},  # update_settings
            {"status": "failed", "error": "swap rejected"},  # swap_indexes
        ])

        with pytest.raises(IndexRebuildError):
            rebuilder.rebuild("aitao_documents", settings={})


class TestSwapBack:
    def test_swap_back_swaps_the_same_pair_again(self):
        client, _mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        rebuilder.swap_back("aitao_documents")

        client.swap_indexes.assert_called_once_with(
            [{"indexes": ["aitao_documents", "aitao_documents_next"]}]
        )

    def test_swap_back_custom_next_name(self):
        client, _mock_index = _make_client()
        rebuilder = IndexRebuilder(client)

        rebuilder.swap_back("aitao_documents", next_index_name="aitao_documents_v2")

        client.swap_indexes.assert_called_once_with(
            [{"indexes": ["aitao_documents", "aitao_documents_v2"]}]
        )
