# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Coherent-deletion test for US-088 volet 5.

Deleting a document must purge it from EVERY remaining store, the
chunk-level table included. Before this fix, ``delete_document`` removed the
Meilisearch document but left the ``chunks`` rows behind — orphan chunks
that the RAG engine (which reads ``chunks``) could still resurface. These
tests pin the contract: the chunk store is asked to drop the doc's chunks,
and a chunk purge failure makes the whole deletion report failure.

ÉPIC-31 (US-113): LanceDB is no longer part of the write/delete path (v4.0
ships fusion-only, decision D1) — ``DocumentIndexer.delete_document`` only
ever touches Meilisearch + the chunk store now.
"""

from unittest.mock import MagicMock

from aitao.indexation.indexer import DocumentIndexer, generate_doc_id


def _indexer_with_mocks():
    """Build an indexer whose two remaining stores are mocks (no real model / IO)."""
    meili = MagicMock()
    chunks = MagicMock()
    idx = DocumentIndexer(
        meilisearch_client=meili,
        chunk_store=chunks,
    )
    return idx, meili, chunks


class TestDeletePurgesChunks:
    def test_delete_document_purges_chunk_table(self):
        idx, meili, chunks = _indexer_with_mocks()
        path = "/some/doc.pdf"
        doc_id = generate_doc_id(path)

        ok, msg = idx.delete_document(path)

        assert ok, msg
        meili.delete.assert_called_once_with(doc_id)
        chunks.delete_by_doc_id.assert_called_once_with(doc_id)

    def test_chunk_purge_failure_fails_the_delete(self):
        idx, _meili, chunks = _indexer_with_mocks()
        chunks.delete_by_doc_id.side_effect = RuntimeError("boom")

        ok, msg = idx.delete_document("/some/doc.pdf")

        assert ok is False
        assert "ChunkStore" in msg

    def test_skip_chunking_does_not_touch_chunk_store(self):
        # When chunking is disabled, there is no chunk store to purge.
        meili = MagicMock()
        idx = DocumentIndexer(
            meilisearch_client=meili,
            skip_chunking=True,
        )

        ok, _msg = idx.delete_document("/some/doc.pdf")

        assert ok is True
        assert idx.chunk_store is None
