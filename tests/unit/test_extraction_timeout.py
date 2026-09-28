# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_extraction_timeout.py — unit tests for the ÉPIC-31 (US-113 volet 2)
# per-file extraction timeout: a fake extractor that blocks forever must be
# aborted after the configured timeout with an explicit error, and the
# indexer must move on to the next file instead of hanging (the real
# incident: a text-less vector-drawing PDF froze the worker 1h22, zero CPU).

import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation.extraction_timeout import (  # noqa: E402
    ExtractionTimeoutError,
    extract_with_timeout,
)
from aitao.indexation.indexer import DocumentIndexer  # noqa: E402


class _FakeChunkingSection:
    enabled = True
    chunk_size = 512
    chunk_overlap = 50
    min_chunk_size = 100
    max_chunk_size = 1024
    split_on_sentences = True
    embedding_model = "BAAI/bge-m3"


class _FakeIndexingSection:
    def __init__(self, timeout_s: float):
        self.extraction_timeout_s = timeout_s


class _FakeConfig:
    """Minimal ConfigManager stand-in — only `.indexing.extraction_timeout_s`
    and `.chunking` are read on this path."""

    def __init__(self, timeout_s: float):
        self.indexing = _FakeIndexingSection(timeout_s)
        self.chunking = _FakeChunkingSection()


class _BlockingExtractor:
    """A text_extractor.extract() double that blocks until released — stands
    in for a pathological file (vector-only PDF, un-materialized cloud file)
    whose extraction never returns on its own."""

    def __init__(self):
        self.released = threading.Event()
        self.started = threading.Event()

    def extract(self, path):
        self.started.set()
        self.released.wait()  # blocks "forever" unless the test releases it
        from aitao.indexation.text_extractor import ExtractionResult
        return ExtractionResult(text="too late", success=True)

    def get_supported_extensions(self):
        return {".pdf", ".txt"}


class _FastExtractor:
    """A normal, instantaneous extractor — the "next file" the worker must
    still be able to process right after a timeout."""

    def extract(self, path):
        from aitao.indexation.text_extractor import ExtractionResult
        return ExtractionResult(text="hello world", metadata={"word_count": 2, "language": "en"}, success=True)

    def get_supported_extensions(self):
        return {".pdf", ".txt"}


@pytest.fixture
def temp_txt_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Some content.")
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


class TestExtractWithTimeoutLowLevel:
    """Direct unit tests of the extract_with_timeout() helper."""

    def test_fast_extraction_returns_normally(self, temp_txt_file):
        extractor = _FastExtractor()
        result = extract_with_timeout(extractor, temp_txt_file, timeout_s=5.0)
        assert result.success
        assert result.text == "hello world"

    def test_blocking_extraction_raises_after_timeout(self, temp_txt_file):
        extractor = _BlockingExtractor()
        start = time.perf_counter()
        with pytest.raises(ExtractionTimeoutError):
            extract_with_timeout(extractor, temp_txt_file, timeout_s=0.1)
        elapsed = time.perf_counter() - start

        # Must return close to the timeout, never wait for the block to lift.
        assert elapsed < 2.0, f"took {elapsed}s — did not honour the timeout"
        extractor.released.set()  # let the zombie thread finish, tidy shutdown

    def test_timeout_error_message_names_the_path(self, temp_txt_file):
        extractor = _BlockingExtractor()
        with pytest.raises(ExtractionTimeoutError) as exc_info:
            extract_with_timeout(extractor, temp_txt_file, timeout_s=0.05)
        assert str(temp_txt_file) in str(exc_info.value)
        extractor.released.set()


class TestIndexFileTimeoutIntegration:
    """DocumentIndexer.index_file() surfaces a clean failure on timeout, and
    the worker (a fresh index_file() call right after) is NOT stuck."""

    def _make_indexer(self, extractor, timeout_s=0.1):
        return DocumentIndexer(
            meilisearch_client=MagicMock(),
            text_extractor=extractor,
            skip_chunking=True,
            config=_FakeConfig(timeout_s),
            allow_temp_paths=True,
        )

    def test_blocking_file_fails_cleanly_with_explicit_error(self, temp_txt_file):
        extractor = _BlockingExtractor()
        indexer = self._make_indexer(extractor)

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success is False
        assert "timeout" in (result.error or "").lower()
        extractor.released.set()

    def test_worker_continues_to_next_file_after_a_timeout(self, temp_txt_file, tmp_path):
        # First file: pathological, blocks past the timeout.
        blocking_extractor = _BlockingExtractor()
        indexer = self._make_indexer(blocking_extractor)
        stuck_result = indexer.index_file(temp_txt_file, force=True)
        assert stuck_result.success is False
        blocking_extractor.released.set()

        # Second file: a normal file, indexed right after — the worker must
        # not be stuck waiting on the first one (this is the actual incident:
        # one bad file freezing everything that comes after it).
        indexer.text_extractor = _FastExtractor()
        other_file = tmp_path / "ok.txt"
        other_file.write_text("fine")
        start = time.perf_counter()
        ok_result = indexer.index_file(other_file, force=True)
        elapsed = time.perf_counter() - start

        assert ok_result.success is True
        assert elapsed < 2.0
