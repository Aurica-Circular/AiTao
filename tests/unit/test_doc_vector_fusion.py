# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_doc_vector_fusion.py — unit tests for the ÉPIC-31 (US-111/US-113)
# doc-level vector reuse: DocumentIndexer computes the document's
# whole-content embedding ONCE (via the chunk store's already loaded model)
# and passes it to the Meilisearch document write's `_vectors.default`.
#
# US-113 finding (a): this used to be gated behind
# ``DocumentIndexer._is_fusion_engine()``, which read ``self.config`` — an
# attribute NOT set by any real production call site (worker.py's
# `_default_handler` builds a bare ``DocumentIndexer()``, and so do
# worker_batch.py, api/routes/ingest.py, every CLI command). Since fusion is
# now the only engine (the "[search] engine" flag is gone), the gate was
# removed outright — the tests below prove the vector is computed from every
# real call shape, config or no config, batched or not.
#
# Fully mocked: no real Meilisearch/embedding model.

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation.indexer import DocumentIndexer  # noqa: E402


@pytest.fixture
def mock_meilisearch():
    client = MagicMock()
    client.index_document.return_value = "doc-id"
    client.get_document.return_value = None
    return client


@pytest.fixture
def mock_text_extractor():
    from aitao.indexation.text_extractor import ExtractionResult

    extractor = MagicMock()
    extractor.extract.return_value = ExtractionResult(
        text="Some document content to embed.",
        metadata={"word_count": 5, "language": "en"},
        success=True,
    )
    return extractor


@pytest.fixture
def mock_chunk_store():
    """A fake MeiliChunkStore-like store exposing embed_document()."""
    store = MagicMock()
    store.embed_document.return_value = [0.1, 0.2, 0.3, 0.4]
    store.add_chunks.return_value = 1
    store.delete_by_doc_id.return_value = 0
    return store


@pytest.fixture
def temp_txt_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Some document content to embed.")
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


class TestDocVectorAlwaysComputed:
    def test_vector_computed_and_reused_for_meilisearch_write(
        self, mock_meilisearch, mock_text_extractor, mock_chunk_store, temp_txt_file,
    ):
        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=mock_text_extractor,
            chunk_store=mock_chunk_store,
            allow_temp_paths=True,
        )

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        # embed_document() was called exactly once with the doc content.
        mock_chunk_store.embed_document.assert_called_once_with(
            "Some document content to embed."
        )
        expected_vector = [0.1, 0.2, 0.3, 0.4]
        _, meili_kwargs = mock_meilisearch.index_document.call_args
        assert meili_kwargs.get("vector") == expected_vector

    def test_no_config_still_computes_a_real_vector(
        self, mock_meilisearch, mock_text_extractor, mock_chunk_store, temp_txt_file,
    ):
        """US-113 finding (a), the actual production bug: DocumentIndexer()
        built with NO config at all — exactly how worker.py's
        _default_handler, worker_batch.py, api/routes/ingest.py and every CLI
        command construct it — must still compute and send a real vector,
        not the US-110 null opt-out. Before the fix, ``_is_fusion_engine()``
        read ``self.config`` (None here) and always returned False, so this
        exact call shape silently produced ``_vectors: {"default": null}``
        in production regardless of engine/config on disk.
        """
        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=mock_text_extractor,
            chunk_store=mock_chunk_store,
            allow_temp_paths=True,
            # No `config=` argument at all.
        )

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        mock_chunk_store.embed_document.assert_called_once()
        _, meili_kwargs = mock_meilisearch.index_document.call_args
        assert meili_kwargs.get("vector") == [0.1, 0.2, 0.3, 0.4]

    def test_batched_path_also_computes_the_vector(
        self, mock_meilisearch, mock_text_extractor, mock_chunk_store, tmp_path,
    ):
        """Parity check: the grouped-write path (index_files_batched, used
        when [indexing] batch_size > 1) must compute the vector exactly like
        the per-file path — both share the same prepare_item()."""
        mock_meilisearch.add_documents_batch = MagicMock(return_value=["id1", "id2"])
        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=mock_text_extractor,
            chunk_store=mock_chunk_store,
            allow_temp_paths=True,
        )

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a")
        f2.write_text("b")

        result = indexer.index_files_batched([str(f1), str(f2)], force=True, batch_size=2)

        assert result.successful == 2
        _, batch_kwargs = mock_meilisearch.add_documents_batch.call_args
        vectors = batch_kwargs.get("vectors")
        assert vectors == [[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]]

    def test_without_embed_document_falls_back_to_none(
        self, mock_meilisearch, mock_text_extractor, temp_txt_file,
    ):
        """A chunk store that doesn't expose embed_document() must not
        crash — the vector stays None and the write falls back to the
        US-110 null opt-out."""
        store_without_embed = MagicMock(spec=["add_chunks", "delete_by_doc_id"])
        store_without_embed.add_chunks.return_value = 1
        store_without_embed.delete_by_doc_id.return_value = 0
        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=mock_text_extractor,
            chunk_store=store_without_embed,
            allow_temp_paths=True,
        )

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        _, meili_kwargs = mock_meilisearch.index_document.call_args
        assert meili_kwargs.get("vector") is None

    def test_no_chunk_store_at_all_is_safe(
        self, mock_meilisearch, mock_text_extractor, temp_txt_file,
    ):
        """skip_chunking=True (no chunk store available) must not crash —
        vector stays None."""
        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=mock_text_extractor,
            skip_chunking=True,
            allow_temp_paths=True,
        )

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        _, meili_kwargs = mock_meilisearch.index_document.call_args
        assert meili_kwargs.get("vector") is None
