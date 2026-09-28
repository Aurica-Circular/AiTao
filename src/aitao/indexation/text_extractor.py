# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
TextExtractor - Extract text content from various document formats.

This module provides text extraction capabilities for document indexing.
Supports PDF, TXT, MD, JSON, TOML, and source code files natively; DOCX,
PPTX, XLSX and ODF are supported too when the separately distributed AiTao
Premium module is installed (US-138-1 — see the ``_init_extractors`` note
below).

Responsibilities:
- Define base classes (ExtractionResult, BaseExtractor) used by all extractors
- PDF text extraction with native/scanned detection (PDFExtractor)
- TextExtractor facade that delegates to specialized extractors
- Re-export all extractors for backward compatibility
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any

from aitao.core.logger import get_logger
from aitao.core.config import ConfigManager
from aitao.core.plugin_registry import discover_plugins, registry

# ---------------------------------------------------------------------------
# Lazy imports for optional dependencies (kept here for PDF + language detect)
# ---------------------------------------------------------------------------

_pypdf = None
_langdetect = None


def _get_pypdf():
    """Lazy load pypdf."""
    global _pypdf
    if _pypdf is None:
        import pypdf
        _pypdf = pypdf
    return _pypdf


def _get_langdetect():
    """Lazy load langdetect with a fixed seed for deterministic results."""
    global _langdetect
    if _langdetect is None:
        import langdetect
        langdetect.DetectorFactory.seed = 0
        _langdetect = langdetect
    return _langdetect


logger = get_logger(__name__)

# Default max file size in MB (can be overridden via config)
DEFAULT_MAX_FILE_SIZE_MB = 50

# Legacy binary Office formats (pre-2007 OLE Compound File format) have no
# extractor and none is planned unless real customer demand shows up (backlog
# decision 2026-07-20, option C) — map each to its modern, indexable equivalent
# so the failure message tells the user what to actually do about it.
_LEGACY_OFFICE_EQUIVALENTS = {
    ".doc": ".docx",
    ".ppt": ".pptx",
    ".xls": ".xlsx",
}

@dataclass
class ExtractionResult:
    """Result of text extraction from a document."""
    
    text: str
    """Extracted text content."""
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    """Document metadata (pages, word_count, language, etc.)."""
    
    success: bool = True
    """Whether extraction was successful."""
    
    error: Optional[str] = None
    """Error message if extraction failed."""
    
    @property
    def word_count(self) -> int:
        """Return word count from metadata."""
        return self.metadata.get("word_count", 0)
    
    @property
    def language(self) -> Optional[str]:
        """Return detected language from metadata."""
        return self.metadata.get("language")
    
    @property
    def pages(self) -> Optional[int]:
        """Return page count from metadata."""
        return self.metadata.get("pages")


class BaseExtractor(ABC):
    """Abstract base class for text extractors."""
    
    # File extensions this extractor handles
    SUPPORTED_EXTENSIONS: set = set()
    
    @abstractmethod
    def extract(self, file_path: Path) -> ExtractionResult:
        """
        Extract text from a file.
        
        Args:
            file_path: Path to the file to extract text from.
            
        Returns:
            ExtractionResult with text and metadata.
        """
        pass
    
    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """Check if this extractor can handle the given file."""
        return file_path.suffix.lower() in cls.SUPPORTED_EXTENSIONS


# NOTE: concrete extractors live in src/plugins/extractors/ (US-25b) and are
# loaded through discover_plugins(); this module only defines the interface
# (ExtractionResult, BaseExtractor) and the TextExtractor facade.


class TextExtractor:
    """
    Main text extractor that delegates to specialized extractors.
    
    This is the primary interface for text extraction. It automatically
    selects the appropriate extractor based on file extension.
    
    Example:
        extractor = TextExtractor()
        result = extractor.extract("/path/to/document.pdf")
        if result.success:
            print(f"Text: {result.text[:100]}...")
            print(f"Language: {result.language}")
            print(f"Word count: {result.word_count}")
    """
    
    # Built-in extractor priority order — first match wins per extension.
    # ImageExtractor MUST precede EXIFExtractor (images need OCR via Step 1b, not
    # just EXIF metadata); PlainTextExtractor is the last-resort fallback. Classes
    # are looked up from the plugin registry, so a new extractor self-registers
    # without editing the core (US-25).
    #
    # "docx", "pptx", "xlsx", "odf" are NOT built into this repository (US-138-1
    # — they ship in the separately distributed AiTao Premium module): they are
    # named here only to fix their priority slot when that module IS installed
    # and has registered them. _init_extractors() below skips any name in this
    # list that isn't actually registered, so a Core-only install (no Premium
    # module) simply ends up without a ".docx"/".pptx"/".xlsx"/".odt" entry in
    # self._extractors — can_extract()/extract() then report it the same way
    # they report any other unsupported extension, never a crash.
    _ORDER: list[str] = [
        "pdf", "docx", "pptx", "xlsx", "odf",
        "image", "exif",
        "json", "code", "plaintext",
    ]

    def __init__(self, max_file_size_mb: Optional[float] = None):
        """
        Initialize the text extractor.

        Args:
            max_file_size_mb: Maximum file size in MB. If None, uses config or default (50 MB).
        """
        self._extractors: Dict[str, BaseExtractor] = {}
        self._max_file_size_mb = max_file_size_mb or self._get_max_size_from_config()
        self._init_extractors()

    def _init_extractors(self):
        """Initialize extractor instances for each extension (first match wins).

        Names in ``_ORDER`` that are not registered (e.g. "docx" without the
        AiTao Premium module installed, US-138-1) are skipped rather than
        raising ``PluginNotFoundError`` — exactly like ``OCRRouter._build_providers``
        skips an unavailable OCR engine."""
        discover_plugins()  # pick up any drop-in extractor from src/plugins/extractors/
        names = list(self._ORDER) + [
            n for n in registry.available("extractor") if n not in self._ORDER
        ]
        for name in names:
            if not registry.is_registered("extractor", name):
                continue
            extractor_class = registry.get("extractor", name)
            instance = extractor_class()
            for ext in extractor_class.SUPPORTED_EXTENSIONS:
                if ext not in self._extractors:
                    self._extractors[ext] = instance
    
    def _get_max_size_from_config(self) -> float:
        """Get max file size from config or return default."""
        try:
            config = ConfigManager("config/config.toml")
            return float(config.resources.max_file_size_mb)
        except Exception:
            return DEFAULT_MAX_FILE_SIZE_MB
    
    def extract(self, file_path: str | Path) -> ExtractionResult:
        """
        Extract text from a file.
        
        Args:
            file_path: Path to the file to extract text from.
            
        Returns:
            ExtractionResult with text and metadata.
        """
        path = Path(file_path)
        
        # Check file exists
        if not path.exists():
            return ExtractionResult(
                text="",
                success=False,
                error=f"File not found: {path}"
            )
        
        if not path.is_file():
            return ExtractionResult(
                text="",
                success=False,
                error=f"Not a file: {path}"
            )
        
        # Check file size limit
        file_size_mb = path.stat().st_size / (1024 * 1024)
        if file_size_mb > self._max_file_size_mb:
            logger.warning(
                f"File too large: {path.name} ({file_size_mb:.1f} MB > {self._max_file_size_mb} MB limit)"
            )
            return ExtractionResult(
                text="",
                success=False,
                error=f"File too large: {file_size_mb:.1f} MB exceeds {self._max_file_size_mb} MB limit"
            )
        
        # Find appropriate extractor
        ext = path.suffix.lower()
        extractor = self._extractors.get(ext)
        
        if extractor is None:
            modern_equivalent = _LEGACY_OFFICE_EQUIVALENTS.get(ext)
            if modern_equivalent:
                return ExtractionResult(
                    text="",
                    success=False,
                    error=(
                        f"Legacy Office format ({ext}) is not supported — "
                        f"re-save the file as {modern_equivalent} to index it."
                    ),
                )
            return ExtractionResult(
                text="",
                success=False,
                error=f"Unsupported file type: {ext}"
            )
        
        # Extract text
        try:
            result = extractor.extract(path)
            
            # Add common metadata
            result.metadata["file_path"] = str(path)
            result.metadata["file_name"] = path.name
            result.metadata["file_size"] = path.stat().st_size
            
            return result
        except Exception as e:
            logger.exception(f"Extraction failed for {path}")
            return ExtractionResult(
                text="",
                success=False,
                error=f"Extraction failed: {e}"
            )
    
    def get_supported_extensions(self) -> set:
        """Return set of all supported file extensions."""
        return set(self._extractors.keys())
    
    def can_extract(self, file_path: str | Path) -> bool:
        """Check if we can extract text from this file type."""
        path = Path(file_path)
        return path.suffix.lower() in self._extractors


# Convenience function
def extract_text(file_path: str | Path) -> ExtractionResult:
    """
    Extract text from a file (convenience function).
    
    Args:
        file_path: Path to the file.
        
    Returns:
        ExtractionResult with text and metadata.
    """
    extractor = TextExtractor()
    return extractor.extract(file_path)


# Backward compat: re-export Document so test patches resolve here
from aitao.core.models import Document  # noqa: E402,F401


def detect_language(text: str):
    """Backward compat: module-level language detection wrapper."""
    langdetect = _get_langdetect()
    return langdetect.detect(text)


# Public interface of this module. Concrete extractors are plugins now
# (src/plugins/extractors/) — import them from plugins.extractors.* directly.
__all__ = [
    "ExtractionResult",
    "BaseExtractor",
    "TextExtractor",
    "extract_text",
    "_get_langdetect",
    "_get_pypdf",
    "detect_language",
    "Document",
]
