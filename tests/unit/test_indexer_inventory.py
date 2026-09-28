# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Index-inventory test for US-086 volet 1.

``DocumentIndexer.indexed_doc_ids`` is the oracle the scanner uses to spot
orphan files (seen but never stored). It degrades gracefully if the store
query fails, returning an empty set rather than raising.

ÉPIC-31 (US-113): LanceDB is no longer a live store (v4.0 ships fusion-only,
decision D1) — Meilisearch is the ONLY backend ``indexed_doc_ids``/
``healthy_doc_ids`` read from now (no more union across two backends).
"""

from unittest.mock import MagicMock

from aitao.indexation.indexer import DocumentIndexer


def _indexer(meili=None):
    return DocumentIndexer(
        meilisearch_client=meili,
        skip_chunking=True,
    )


class TestIndexedDocIds:
    def test_returns_meilisearch_doc_ids(self):
        meili = MagicMock()
        meili.all_doc_ids.return_value = {"a", "b", "c"}

        ids = _indexer(meili).indexed_doc_ids()

        assert ids == {"a", "b", "c"}

    def test_failing_store_returns_empty(self):
        meili = MagicMock()
        meili.all_doc_ids.side_effect = RuntimeError("down")

        ids = _indexer(meili).indexed_doc_ids()

        assert ids == set()

    def test_no_store_returns_empty(self):
        idx = DocumentIndexer(
            skip_meilisearch=True,
            skip_chunking=True,
        )
        assert idx.indexed_doc_ids() == set()


class TestHealthyDocIds:
    def test_uses_meilisearch_healthy_doc_ids(self):
        # A title-only doc (empty OCR) is present in Meili but has no real
        # content, so it must be excluded — the store tracks this explicitly
        # via has_content/healthy_doc_ids() since LanceDB (which used to
        # reject empty-content writes for free) is gone (US-086 v1b, US-113).
        meili = MagicMock()
        meili.healthy_doc_ids.return_value = {"healthy1", "healthy2"}

        ids = _indexer(meili).healthy_doc_ids()

        assert ids == {"healthy1", "healthy2"}
        meili.healthy_doc_ids.assert_called_once()

    def test_no_meilisearch_returns_empty(self):
        idx = DocumentIndexer(
            skip_meilisearch=True,
            skip_chunking=True,
        )
        assert idx.healthy_doc_ids() == set()

    def test_failing_meilisearch_is_swallowed(self):
        meili = MagicMock()
        meili.healthy_doc_ids.side_effect = RuntimeError("down")

        assert _indexer(meili).healthy_doc_ids() == set()
