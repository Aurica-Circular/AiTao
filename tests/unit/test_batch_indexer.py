# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_batch_indexer.py — unit tests for the ÉPIC-31 (US-111, absorbs
# US-093) grouped-write batching: DocumentIndexer.index_files_batched().
#
# Fully mocked Meilisearch/chunk-store clients + a REAL (lightweight,
# pure-Python) ChunkingPipeline — no live services, no embedding model.
# LanceDB is no longer part of the write path (ÉPIC-31, US-113, decision D1):
# DocumentIndexer has no lancedb_client constructor parameter any more.

import sys
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation.indexer import DocumentIndexer  # noqa: E402
from aitao.indexation.text_extractor import ExtractionResult  # noqa: E402


class _FakeChunkingSection:
    enabled = True
    chunk_size = 512
    chunk_overlap = 50
    min_chunk_size = 100
    max_chunk_size = 1024
    split_on_sentences = True
    embedding_model = "BAAI/bge-m3"


class _FakeIndexingSection:
    def __init__(self, batch_size: int = 1):
        self.batch_size = batch_size
        self.extraction_timeout_s = 300


class _FakeConfig:
    def __init__(self, batch_size: int = 1):
        self.chunking = _FakeChunkingSection()
        self.indexing = _FakeIndexingSection(batch_size)


@pytest.fixture
def temp_files():
    paths: List[Path] = []
    for i in range(3):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(f"Document number {i} has some unique content to chunk and embed.")
        f.close()
        paths.append(Path(f.name))
    yield paths
    for p in paths:
        p.unlink(missing_ok=True)


@pytest.fixture
def mock_meilisearch():
    client = MagicMock()
    client.index_document.return_value = "doc-id"
    client.add_documents_batch.return_value = ["a", "b", "c"]
    client.get_document.return_value = None
    return client


@pytest.fixture
def mock_chunk_store():
    store = MagicMock()
    store.embed_document.return_value = [0.1, 0.2, 0.3, 0.4]
    store.add_chunks.return_value = 3
    store.delete_by_doc_ids.return_value = 0
    store.delete_by_doc_id.return_value = 0
    return store


def _make_indexer(mock_meilisearch, mock_chunk_store, batch_size=1):
    return DocumentIndexer(
        meilisearch_client=mock_meilisearch,
        chunk_store=mock_chunk_store,
        config=_FakeConfig(batch_size=batch_size),
        allow_temp_paths=True,
    )


class TestBatchSizeOneIsBitIdentical:
    def test_batch_size_1_never_calls_the_batch_write_path(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)
        calls = []
        original_index_file = indexer.index_file

        def spy_index_file(path, force=False):
            calls.append(str(path))
            return original_index_file(path, force=force)

        indexer.index_file = spy_index_file

        result = indexer.index_files_batched([str(p) for p in temp_files], batch_size=1)

        assert len(calls) == len(temp_files)
        assert result.total == len(temp_files)
        assert result.successful == len(temp_files)
        mock_meilisearch.add_documents_batch.assert_not_called()

    def test_no_batch_size_arg_falls_back_to_config_indexing_batch_size(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        """index_files_batched() with NO explicit batch_size reads
        [indexing] batch_size from the injected config — here 1, so it must
        NOT engage the grouped-write path even with 3 files queued."""
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store, batch_size=1)

        result = indexer.index_files_batched([str(p) for p in temp_files])

        assert result.successful == 3
        mock_meilisearch.add_documents_batch.assert_not_called()

    def test_no_batch_size_arg_honours_configured_group_size(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store, batch_size=10)

        result = indexer.index_files_batched([str(p) for p in temp_files])

        assert result.successful == 3
        mock_meilisearch.add_documents_batch.assert_called_once()


class TestGroupedWrites:
    def test_group_writes_one_meilisearch_batch_call(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)

        result = indexer.index_files_batched([str(p) for p in temp_files], batch_size=10)

        assert result.total == 3
        assert result.successful == 3
        mock_meilisearch.add_documents_batch.assert_called_once()
        records, kwargs = mock_meilisearch.add_documents_batch.call_args
        assert len(records[0]) == 3
        assert len(kwargs["vectors"]) == 3

        mock_chunk_store.delete_by_doc_ids.assert_called_once()
        deleted_ids = mock_chunk_store.delete_by_doc_ids.call_args[0][0]
        assert len(deleted_ids) == 3

        mock_chunk_store.add_chunks.assert_called_once()
        all_chunks = mock_chunk_store.add_chunks.call_args[0][0]
        assert len(all_chunks) >= 3  # at least 1 chunk per document

    def test_results_are_positional_one_per_requested_path(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)
        paths = [str(p) for p in temp_files]

        result = indexer.index_files_batched(paths, batch_size=10)

        assert len(result.results) == len(paths)
        for path, r in zip(paths, result.results):
            assert r.path == path

    def test_doc_vectors_passed_through_to_meilisearch_batch(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        """The real doc-level vector (from chunk_store.embed_document, US-111)
        flows into add_documents_batch's ``vectors`` kwarg unconditionally —
        there is no more "rrf" engine that would skip it (ÉPIC-31, US-113)."""
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)

        indexer.index_files_batched([str(p) for p in temp_files], batch_size=10)

        _records, kwargs = mock_meilisearch.add_documents_batch.call_args
        assert all(v == [0.1, 0.2, 0.3, 0.4] for v in kwargs["vectors"])


class TestPerFileFailureDoesNotAbortBatch:
    def test_one_extraction_failure_does_not_block_the_others(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)
        failing_path = str(temp_files[1])

        real_extractor = indexer.text_extractor

        def flaky_extract(path):
            if str(path) == failing_path:
                return ExtractionResult(text="", success=False, error="boom")
            return real_extractor.extract(path)

        indexer.text_extractor = MagicMock()
        indexer.text_extractor.extract.side_effect = flaky_extract

        result = indexer.index_files_batched([str(p) for p in temp_files], batch_size=10)

        assert result.total == 3
        assert result.failed == 1
        assert result.successful == 2

        # Only the 2 successful documents reach the Meilisearch batch call.
        records, _kwargs = mock_meilisearch.add_documents_batch.call_args
        assert len(records[0]) == 2

        failed_result = next(r for r in result.results if r.path == failing_path)
        assert failed_result.success is False
        assert "Extraction failed" in failed_result.error


class TestOneFileOneStateRule:
    def test_chunk_delete_failure_excludes_whole_group_from_add(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        """If the chunk store's grouped delete fails, the grouped add must
        NEVER be attempted — otherwise a file could end up with old chunks
        never removed PLUS new chunks added (mixed state)."""
        mock_chunk_store.delete_by_doc_ids.side_effect = RuntimeError("meili down")
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)

        result = indexer.index_files_batched([str(p) for p in temp_files], batch_size=10)

        mock_chunk_store.add_chunks.assert_not_called()
        for r in result.results:
            assert r.chunks_indexed == 0
            assert "ChunkStore batch" in (r.error or "")

    def test_meilisearch_batch_failure_marks_whole_group_failed(
        self, mock_meilisearch, mock_chunk_store, temp_files,
    ):
        mock_meilisearch.add_documents_batch.side_effect = RuntimeError("network error")
        indexer = _make_indexer(mock_meilisearch, mock_chunk_store)

        result = indexer.index_files_batched([str(p) for p in temp_files], batch_size=10)

        assert result.failed == 3
        for r in result.results:
            assert r.success is False
            assert "Meilisearch batch" in (r.error or "")
