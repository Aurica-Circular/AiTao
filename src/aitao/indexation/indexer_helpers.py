# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Indexer helper functions and data models.

Provides reusable helpers for the DocumentIndexer:
- IndexResult / BatchIndexResult data classes
- Document ID generation (SHA256)
- Title extraction, category detection
- Metadata preparation
- Per-engine indexing wrappers (LanceDB, Meilisearch, chunking)
"""

import hashlib
import logging
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from aitao.core.models import Document
from aitao.indexation.chunk_store_meili import MeiliChunkStoreError as ChunkStoreError
from aitao.indexation.text_extractor import ExtractionResult
from aitao.search.meilisearch_client import MeilisearchError


# -- Data models -------------------------------------------------------------

class _Base(BaseModel):
    """Tolerant of surplus keys, strict on missing required fields."""

    model_config = ConfigDict(extra="ignore")


class IndexResult(_Base):
    """Result of indexing a single document."""

    path: str
    """Path to the indexed document."""

    doc_id: str
    """SHA256-based document ID."""

    success: bool = True
    """Whether indexing was successful."""

    meilisearch_indexed: bool = False
    """Whether document was indexed in Meilisearch."""

    chunks_indexed: int = 0
    """Number of chunks indexed for this document."""

    error: Optional[str] = None
    """Error message if indexing failed."""

    extraction_time_ms: float = 0
    """Time taken for text extraction in milliseconds."""

    indexing_time_ms: float = 0
    """Time taken for indexing in milliseconds."""

    word_count: int = 0
    """Word count of extracted text."""

    language: Optional[str] = None
    """Detected language."""

    @property
    def total_time_ms(self) -> float:
        """Total processing time in milliseconds."""
        return self.extraction_time_ms + self.indexing_time_ms


class BatchIndexResult(_Base):
    """Result of batch indexing multiple documents."""

    total: int = 0
    """Total number of documents processed."""

    successful: int = 0
    """Number of successfully indexed documents."""

    failed: int = 0
    """Number of failed documents."""

    skipped: int = 0
    """Number of skipped documents (already indexed)."""

    results: List[IndexResult] = Field(default_factory=list)
    """Individual results for each document."""

    total_time_ms: float = 0
    """Total processing time in milliseconds."""

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        if self.total == 0:
            return 0.0
        return (self.successful / self.total) * 100


# -- Extension → category mapping -------------------------------------------

_EXTENSION_CATEGORIES: Dict[str, str] = {
    # Documents
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".odt": "document", ".rtf": "document",
    # Spreadsheets
    ".xls": "spreadsheet", ".xlsx": "spreadsheet",
    ".ods": "spreadsheet", ".csv": "spreadsheet",
    # Presentations
    ".ppt": "presentation", ".pptx": "presentation", ".odp": "presentation",
    # Images
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".bmp": "image", ".webp": "image", ".tiff": "image",
    # Code
    ".py": "code", ".js": "code", ".ts": "code", ".java": "code",
    ".c": "code", ".cpp": "code", ".go": "code", ".rs": "code",
    # Markdown / text
    ".md": "text", ".markdown": "text", ".txt": "text", ".rst": "text",
    # Config / data
    ".json": "config", ".yaml": "config", ".yml": "config",
    ".toml": "config", ".xml": "config",
    # Web
    ".html": "web", ".htm": "web", ".css": "web",
}


# -- Pure helper functions ---------------------------------------------------

def normalize_path(path: str) -> str:
    """NFC-normalize a path string so one file yields one stable identity.

    macOS (APFS/HFS+) stores filenames in decomposed form (NFD: ``e`` + combining
    acute), while the same name typed/pasted elsewhere is usually composed (NFC).
    Hashing the raw bytes would then index the *same* file twice under two IDs.
    NFC folds both forms together; it is a no-op on Linux/Windows (already NFC),
    so this is a single cross-platform fix, not a per-OS special case. File access
    is unaffected — macOS resolves NFC and NFD to the same directory entry.
    """
    return unicodedata.normalize("NFC", path)


def generate_doc_id(path: str) -> str:
    """Generate document ID from a path (NFC-normalized) using SHA256."""
    return hashlib.sha256(normalize_path(path).encode()).hexdigest()


def get_document_title(path: Path, extraction: ExtractionResult) -> str:
    """Document title = filename (without extension).

    Embedded metadata titles (e.g. a PDF's ``/Title``) are unreliable — they
    often carry a template or software name ("driver_out" on an AMELI PDF),
    so the user could not recognise their own documents (US-20). The filename
    is what the user actually calls the file, so it is the title. The embedded
    title remains available in ``extraction.metadata['title']`` if ever needed.
    """
    return path.stem


def get_document_category(path: Path) -> str:
    """Determine document category from file extension."""
    return _EXTENSION_CATEGORIES.get(path.suffix.lower(), "other")


def prepare_document_metadata(
    path: Path, extraction: ExtractionResult,
) -> Dict[str, Any]:
    """Build metadata dict from file path and extraction result."""
    metadata = {
        "mtime": path.stat().st_mtime,
        "pages": extraction.pages,
        "line_count": extraction.metadata.get("line_count"),
        "encoding": extraction.metadata.get("encoding"),
    }
    return {k: v for k, v in metadata.items() if v is not None}


# -- Per-engine indexing wrappers -------------------------------------------

def index_in_meilisearch(
    client: Any,
    document: Document,
    logger: logging.Logger,
    vector: Optional[List[float]] = None,
) -> Tuple[bool, Optional[str]]:
    """Index a Document in Meilisearch. Returns (success, error_msg).

    ``vector`` (ÉPIC-31, US-111/US-113): precomputed doc-level embedding,
    reused for the fusion engine's ``_vectors.default`` (see
    ``indexation.item_preparer.prepare_item``) — passed through as a keyword
    so a caller with nothing to give (empty content) still gets a clean
    ``client.index_document(document)`` call.
    """
    try:
        if vector is not None:
            client.index_document(document, vector=vector)
        else:
            client.index_document(document)
        logger.debug(f"Meilisearch indexed: {Path(document.path).name}")
        return True, None
    except MeilisearchError as e:
        logger.error(f"Meilisearch indexing failed for {document.path}: {e}")
        return False, f"Meilisearch: {e}"


def chunk_and_store(
    pipeline: Any,
    store: Any,
    doc_id: str,
    path: Path,
    title: str,
    text: str,
    category: str,
    language: str,
    file_type: str,
    logger: logging.Logger,
) -> Tuple[int, Optional[str]]:
    """Chunk a document and persist the chunks. Returns (count, error_msg)."""
    try:
        store.delete_by_doc_id(doc_id)

        result = pipeline.chunk_document(
            text=text, doc_id=doc_id, path=str(path), title=title,
            metadata={
                "category": category,
                "language": language,
                "file_type": file_type,
            },
        )

        if result.success and result.chunks:
            count = store.add_chunks(result.chunks)
            logger.debug(f"Chunked {path.name}: {count} chunks")
            return count, None
        if not result.success:
            return 0, f"Chunking: {result.error}"
        return 0, None
    except ChunkStoreError as e:
        logger.error(f"Chunk storage failed for {path}: {e}")
        return 0, f"ChunkStore: {e}"
    except Exception as e:
        logger.error(f"Chunking failed for {path}: {e}")
        return 0, f"Chunking: {e}"
