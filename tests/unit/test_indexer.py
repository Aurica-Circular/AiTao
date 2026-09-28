# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for DocumentIndexer module.

Tests the document indexing pipeline:
- Single file indexing
- Batch indexing
- Deduplication
- Error handling

ÉPIC-31 (US-113): fusion is the only search engine since v4.0 — LanceDB was
removed from the live path entirely (decision D1). ``DocumentIndexer`` no
longer accepts a ``lancedb_client``/``skip_lancedb`` argument, and
``IndexResult`` no longer carries a ``lancedb_indexed`` field.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation.indexer import (
    DocumentIndexer,
    IndexResult,
    BatchIndexResult,
    index_file,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_meilisearch():
    """Create a mock Meilisearch client."""
    client = MagicMock()
    client.index_document.return_value = "test_doc_id"
    client.get_document.return_value = None
    client.is_healthy.return_value = True
    client.get_stats.return_value = {"document_count": 0, "index_name": "test"}
    client.delete.return_value = True
    return client


@pytest.fixture
def mock_text_extractor():
    """Create a mock TextExtractor."""
    from aitao.indexation.text_extractor import ExtractionResult

    extractor = MagicMock()
    extractor.extract.return_value = ExtractionResult(
        text="This is test content for indexing.",
        metadata={
            "word_count": 6,
            "language": "en",
            "file_type": "txt",
        },
        success=True,
    )
    extractor.get_supported_extensions.return_value = {".txt", ".pdf", ".md"}
    return extractor


@pytest.fixture
def indexer(mock_meilisearch, mock_text_extractor):
    """Create a DocumentIndexer with mocked dependencies."""
    return DocumentIndexer(
        meilisearch_client=mock_meilisearch,
        text_extractor=mock_text_extractor,
        skip_chunking=True,
        allow_temp_paths=True,  # tests index files under tmp_path (US-088 guard opt-in)
    )


@pytest.fixture
def temp_txt_file():
    """Create a temporary text file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello, this is a test document for indexing.\nIt has two lines.")
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def temp_dir_with_files():
    """Create a temporary directory with test files."""
    tmpdir = tempfile.mkdtemp()

    # Create test files
    (Path(tmpdir) / "test1.txt").write_text("Test document one")
    (Path(tmpdir) / "test2.txt").write_text("Test document two")
    (Path(tmpdir) / "test3.md").write_text("# Test markdown")
    (Path(tmpdir) / "ignored.xyz").write_text("Should be ignored")

    # Create subdirectory
    subdir = Path(tmpdir) / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Nested document")

    yield Path(tmpdir)

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)


# =============================================================================
# IndexResult Tests
# =============================================================================

class TestIndexResult:
    """Tests for IndexResult dataclass."""

    def test_successful_result(self):
        """Test creating a successful index result."""
        result = IndexResult(
            path="/test/file.txt",
            doc_id="abc123",
            success=True,
            meilisearch_indexed=True,
            word_count=100,
            language="en",
        )
        assert result.success
        assert result.meilisearch_indexed
        assert result.word_count == 100

    def test_failed_result(self):
        """Test creating a failed index result."""
        result = IndexResult(
            path="/test/file.txt",
            doc_id="abc123",
            success=False,
            error="Extraction failed",
        )
        assert not result.success
        assert result.error == "Extraction failed"

    def test_total_time(self):
        """Test total_time_ms property."""
        result = IndexResult(
            path="/test/file.txt",
            doc_id="abc123",
            extraction_time_ms=100.5,
            indexing_time_ms=50.3,
        )
        assert result.total_time_ms == pytest.approx(150.8)


class TestBatchIndexResult:
    """Tests for BatchIndexResult dataclass."""

    def test_empty_batch(self):
        """Test empty batch result."""
        result = BatchIndexResult()
        assert result.total == 0
        assert result.success_rate == 0.0

    def test_success_rate(self):
        """Test success rate calculation."""
        result = BatchIndexResult(
            total=10,
            successful=8,
            failed=2,
        )
        assert result.success_rate == 80.0

    def test_partial_success(self):
        """Test batch with mixed results."""
        result = BatchIndexResult(
            total=5,
            successful=3,
            failed=1,
            skipped=1,
        )
        assert result.total == 5
        assert result.success_rate == 60.0


# =============================================================================
# DocumentIndexer Tests
# =============================================================================

class TestDocumentIndexer:
    """Tests for DocumentIndexer class."""

    def test_init_with_mocks(self, indexer):
        """Test initialization with mocked clients."""
        assert indexer is not None
        assert indexer.meilisearch is not None

    def test_init_skip_clients(self):
        """Test initialization with skipped clients."""
        indexer = DocumentIndexer(
            skip_meilisearch=True,
        )
        assert indexer.meilisearch is None

    def test_generate_id(self, indexer):
        """Test document ID generation."""
        id1 = indexer._generate_id("/path/to/file.txt")
        id2 = indexer._generate_id("/path/to/file.txt")
        id3 = indexer._generate_id("/path/to/other.txt")

        assert id1 == id2  # Same path = same ID
        assert id1 != id3  # Different path = different ID
        assert len(id1) == 64  # SHA256 hex length

    def test_generate_id_nfc_nfd_equal(self, indexer):
        """NFD and NFC forms of one filename must share an ID (US-RAG-name).

        macOS stores filenames decomposed (NFD); the same name pasted elsewhere
        is composed (NFC). Both must resolve to one document, not two.
        """
        import unicodedata
        nfc = "/vol/Assurance Maladie taiwan_Création.pdf"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfd != nfc  # genuinely different byte sequences
        assert indexer._generate_id(nfd) == indexer._generate_id(nfc)

    def test_get_category_document(self, indexer):
        """Test category detection for documents."""
        assert indexer._get_category(Path("/test/file.pdf")) == "document"
        assert indexer._get_category(Path("/test/file.docx")) == "document"
        assert indexer._get_category(Path("/test/file.odt")) == "document"

    def test_get_category_code(self, indexer):
        """Test category detection for code files."""
        assert indexer._get_category(Path("/test/file.py")) == "code"
        assert indexer._get_category(Path("/test/file.js")) == "code"
        assert indexer._get_category(Path("/test/file.ts")) == "code"

    def test_get_category_spreadsheet(self, indexer):
        """Test category detection for spreadsheets."""
        assert indexer._get_category(Path("/test/file.xlsx")) == "spreadsheet"
        assert indexer._get_category(Path("/test/file.csv")) == "spreadsheet"

    def test_get_category_other(self, indexer):
        """Test category detection for unknown types."""
        assert indexer._get_category(Path("/test/file.xyz")) == "other"


class TestIndexFile:
    """Tests for index_file method."""

    def test_index_success(self, indexer, temp_txt_file):
        """Test successful file indexing."""
        result = indexer.index_file(temp_txt_file)

        assert result.success
        assert result.meilisearch_indexed
        assert result.doc_id
        assert result.extraction_time_ms > 0
        assert result.indexing_time_ms > 0

    def test_index_nonexistent_file(self, indexer):
        """Test indexing nonexistent file."""
        result = indexer.index_file("/nonexistent/file.txt")

        assert not result.success
        assert "File not found" in result.error

    def test_index_directory(self, indexer, temp_dir_with_files):
        """Test indexing a directory (should fail)."""
        result = indexer.index_file(temp_dir_with_files)

        assert not result.success
        assert "Not a file" in result.error

    def test_index_extraction_failure(self, indexer, temp_txt_file):
        """Test handling of extraction failure."""
        from aitao.indexation.text_extractor import ExtractionResult

        indexer.text_extractor.extract.return_value = ExtractionResult(
            text="",
            success=False,
            error="Extraction failed",
        )

        result = indexer.index_file(temp_txt_file)

        assert not result.success
        assert "Extraction failed" in result.error

    def test_index_already_indexed(self, indexer, temp_txt_file, mock_meilisearch):
        """Test skipping already indexed file."""
        # Simulate document exists
        mock_meilisearch.get_document.return_value = {"id": "existing"}

        result = indexer.index_file(temp_txt_file)

        assert result.success
        assert "Already indexed" in result.error

    def test_index_force_reindex(self, indexer, temp_txt_file, mock_meilisearch):
        """Test force re-indexing."""
        # Simulate document exists
        mock_meilisearch.get_document.return_value = {"id": "existing"}

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        assert result.meilisearch_indexed

    def test_index_file_publishes_document_indexed(self, indexer, temp_txt_file, monkeypatch):
        """A subscriber on document.indexed runs when a file is indexed — with no
        change to DocumentIndexer (the US-26 acceptance criterion)."""
        from aitao.core.events import EventBus, DOCUMENT_INDEXED

        bus = EventBus()
        monkeypatch.setattr("aitao.indexation.indexer.event_bus", bus)
        received = []
        bus.subscribe(DOCUMENT_INDEXED, lambda **p: received.append(p))

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success
        assert len(received) == 1
        assert received[0]["doc_id"] == result.doc_id
        assert received[0]["path"] == str(temp_txt_file)

    def test_failing_subscriber_does_not_break_indexing(self, indexer, temp_txt_file, monkeypatch):
        """A subscriber that raises must not break the indexing pipeline."""
        from aitao.core.events import EventBus, DOCUMENT_INDEXED

        bus = EventBus()
        monkeypatch.setattr("aitao.indexation.indexer.event_bus", bus)

        def boom(**_):
            raise RuntimeError("subscriber failure")

        bus.subscribe(DOCUMENT_INDEXED, boom)

        result = indexer.index_file(temp_txt_file, force=True)

        assert result.success  # indexing succeeded despite the bad subscriber

    def test_index_meilisearch_failure(self, indexer, temp_txt_file, mock_meilisearch):
        """Test handling of Meilisearch failure."""
        from aitao.search.meilisearch_client import MeilisearchError

        mock_meilisearch.index_document.side_effect = MeilisearchError("Connection failed")

        result = indexer.index_file(temp_txt_file)

        assert not result.success
        assert not result.meilisearch_indexed
        assert "Meilisearch" in result.error


class TestIndexFiles:
    """Tests for index_files batch method."""

    def test_batch_index_success(self, indexer, temp_dir_with_files):
        """Test batch indexing multiple files."""
        files = list(temp_dir_with_files.glob("*.txt"))

        result = indexer.index_files(files)

        assert result.total == len(files)
        assert result.successful == len(files)
        assert result.failed == 0

    def test_batch_with_failures(self, indexer, temp_dir_with_files):
        """Test batch indexing with some failures."""
        files = list(temp_dir_with_files.glob("*.txt"))
        files.append(Path("/nonexistent/file.txt"))

        result = indexer.index_files(files)

        assert result.total == len(files)
        assert result.failed == 1

    def test_batch_progress_callback(self, indexer, temp_dir_with_files):
        """Test progress callback during batch indexing."""
        files = list(temp_dir_with_files.glob("*.txt"))
        progress_calls = []

        def on_progress(current, total, result):
            progress_calls.append((current, total, result.success))

        indexer.index_files(files, on_progress=on_progress)

        assert len(progress_calls) == len(files)
        assert progress_calls[-1][0] == len(files)


class TestIndexDirectory:
    """Tests for index_directory method."""

    def test_index_directory(self, indexer, temp_dir_with_files):
        """Test indexing a directory."""
        result = indexer.index_directory(temp_dir_with_files, recursive=False)

        # Should find txt and md files, ignore xyz
        assert result.total == 3  # test1.txt, test2.txt, test3.md

    def test_index_directory_recursive(self, indexer, temp_dir_with_files):
        """Test recursive directory indexing."""
        result = indexer.index_directory(temp_dir_with_files, recursive=True)

        # Should find all txt and md files including nested
        assert result.total == 4  # test1.txt, test2.txt, test3.md, nested.txt

    def test_index_nonexistent_directory(self, indexer):
        """Test indexing nonexistent directory."""
        result = indexer.index_directory("/nonexistent/dir")

        assert result.total == 0
        assert len(result.results) == 1
        assert "Directory not found" in result.results[0].error


class TestDeleteDocument:
    """Tests for delete_document method."""

    def test_delete_success(self, indexer, temp_txt_file):
        """Test successful document deletion."""
        success, message = indexer.delete_document(temp_txt_file)

        assert success
        assert "Deleted" in message

    def test_delete_failure(self, indexer, temp_txt_file, mock_meilisearch):
        """Test deletion failure handling."""
        mock_meilisearch.delete.side_effect = Exception("Delete failed")

        success, message = indexer.delete_document(temp_txt_file)

        assert not success
        assert "Meilisearch" in message


class TestGetStats:
    """Tests for get_stats method."""

    def test_get_stats(self, indexer):
        """Test getting indexing statistics.

        ``lancedb`` stays in the dict (Optional, always None — ÉPIC-31,
        US-113: LanceDB is no longer a live store) for backward compatibility.
        """
        stats = indexer.get_stats()

        assert "lancedb" in stats
        assert stats["lancedb"] is None
        assert "meilisearch" in stats
        assert stats["meilisearch"]["document_count"] == 0


# =============================================================================
# Convenience Function Tests
# =============================================================================

class TestConvenienceFunction:
    """Tests for index_file convenience function."""

    @patch("aitao.indexation.indexer.DocumentIndexer")
    def test_index_file_function(self, mock_indexer_class):
        """Test index_file convenience function."""
        mock_indexer = MagicMock()
        mock_indexer.index_file.return_value = IndexResult(
            path="/test/file.txt",
            doc_id="abc123",
            success=True,
        )
        mock_indexer_class.return_value = mock_indexer

        result = index_file("/test/file.txt")

        assert result.success
        mock_indexer.index_file.assert_called_once()


# =============================================================================
# Integration Tests (with real components, mocked search clients)
# =============================================================================

class TestIntegration:
    """Integration tests with real TextExtractor."""

    def test_real_extraction_mock_indexing(self, mock_meilisearch, temp_txt_file):
        """Test with real extraction but mocked indexing."""
        from aitao.indexation.text_extractor import TextExtractor

        indexer = DocumentIndexer(
            meilisearch_client=mock_meilisearch,
            text_extractor=TextExtractor(),
            skip_chunking=True,
            allow_temp_paths=True,
        )

        result = indexer.index_file(temp_txt_file)

        assert result.success
        assert result.word_count > 0
        assert result.language is not None

    def test_skip_meilisearch(self, temp_txt_file):
        """Test with the Meilisearch client skipped (extraction only)."""
        from aitao.indexation.text_extractor import TextExtractor

        indexer = DocumentIndexer(
            skip_meilisearch=True,
            text_extractor=TextExtractor(),
            skip_chunking=True,
            allow_temp_paths=True,
        )

        result = indexer.index_file(temp_txt_file)

        assert result.success
        assert not result.meilisearch_indexed


# =============================================================================
# Modified-file re-indexing (US-17 hotfix)
# =============================================================================

class TestModifiedFileReindex:
    """A file modified since its last indexing must be re-indexed.

    The stored ``mtime`` is compared with the file on disk; the scanner was
    already detecting changes but the indexer skipped every known doc_id.
    """

    def test_skips_unchanged_file(self, indexer, mock_meilisearch, temp_txt_file):
        mock_meilisearch.get_document.return_value = {
            "id": "x", "mtime": temp_txt_file.stat().st_mtime,
        }
        result = indexer.index_file(temp_txt_file)
        assert result.success
        assert "Already indexed" in (result.error or "")
        mock_meilisearch.index_document.assert_not_called()

    def test_reindexes_modified_file(self, indexer, mock_meilisearch, temp_txt_file):
        mock_meilisearch.get_document.return_value = {
            "id": "x", "mtime": temp_txt_file.stat().st_mtime - 3600,
        }
        result = indexer.index_file(temp_txt_file)
        assert result.success
        assert result.error is None
        mock_meilisearch.index_document.assert_called_once()

    def test_legacy_doc_without_mtime_is_skipped(
        self, indexer, mock_meilisearch, temp_txt_file
    ):
        # Pre-mtime documents must not trigger a mass re-indexing
        mock_meilisearch.get_document.return_value = {"id": "x"}
        result = indexer.index_file(temp_txt_file)
        assert "Already indexed" in (result.error or "")

    def test_force_still_wins(self, indexer, mock_meilisearch, temp_txt_file):
        mock_meilisearch.get_document.return_value = {
            "id": "x", "mtime": temp_txt_file.stat().st_mtime,
        }
        result = indexer.index_file(temp_txt_file, force=True)
        assert result.error is None
        mock_meilisearch.index_document.assert_called_once()

    def test_is_indexed_uses_path_doc_id(
        self, indexer, mock_meilisearch, temp_txt_file
    ):
        from aitao.indexation.indexer_helpers import generate_doc_id

        mock_meilisearch.get_document.return_value = {
            "id": "x", "mtime": temp_txt_file.stat().st_mtime,
        }
        assert indexer.is_indexed(str(temp_txt_file)) is True
        mock_meilisearch.get_document.assert_called_with(
            generate_doc_id(str(temp_txt_file))
        )

    def test_is_indexed_false_for_modified_file(
        self, indexer, mock_meilisearch, temp_txt_file
    ):
        mock_meilisearch.get_document.return_value = {
            "id": "x", "mtime": temp_txt_file.stat().st_mtime - 3600,
        }
        assert indexer.is_indexed(str(temp_txt_file)) is False


# =============================================================================
# Document title = filename (US-20 — "noms faux" fix)
# =============================================================================

class TestDocumentTitle:
    """Title must be the filename, never the unreliable embedded metadata."""

    def _extraction(self, **meta):
        from aitao.indexation.text_extractor import ExtractionResult
        return ExtractionResult(text="x", metadata=meta, success=True)

    def test_title_is_filename_stem(self):
        from aitao.indexation.indexer_helpers import get_document_title
        path = Path("/docs/Attestation AMELI 2025.pdf")
        title = get_document_title(path, self._extraction())
        assert title == "Attestation AMELI 2025"

    def test_embedded_pdf_title_is_ignored(self):
        # The driver_out.pdf bug: bogus embedded /Title must not win
        from aitao.indexation.indexer_helpers import get_document_title
        path = Path("/docs/Attestation AMELI 2025.pdf")
        title = get_document_title(path, self._extraction(title="driver_out"))
        assert title == "Attestation AMELI 2025"
        assert title != "driver_out"


class TestPremiumFormatGate:
    """Freemium scope (PRD §5): advanced formats are Premium at ingestion,
    the RAG/chat engine and text formats stay Core."""

    @staticmethod
    def _make_file(suffix: str) -> Path:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(b"PK\x03\x04 dummy")
            return Path(f.name)

    def test_docx_skipped_in_core_edition(self, indexer, monkeypatch):
        monkeypatch.setattr(
            "aitao.core.license.LicenseManager.is_premium", lambda self: False
        )
        path = self._make_file(".docx")
        try:
            result = indexer.index_file(str(path))
        finally:
            path.unlink(missing_ok=True)
        assert not result.success
        assert "Premium format" in (result.error or "")
        indexer.text_extractor.extract.assert_not_called()  # early return

    def test_docx_proceeds_when_premium(self, indexer, monkeypatch):
        monkeypatch.setattr(
            "aitao.core.license.LicenseManager.is_premium", lambda self: True
        )
        path = self._make_file(".docx")
        try:
            indexer.index_file(str(path))
        finally:
            path.unlink(missing_ok=True)
        indexer.text_extractor.extract.assert_called_once()  # gate let it through

    def test_text_format_indexes_in_core(self, indexer, temp_txt_file, monkeypatch):
        monkeypatch.setattr(
            "aitao.core.license.LicenseManager.is_premium", lambda self: False
        )
        indexer.index_file(str(temp_txt_file))
        indexer.text_extractor.extract.assert_called()  # never gated
