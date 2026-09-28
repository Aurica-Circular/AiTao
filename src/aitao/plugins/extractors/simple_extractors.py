# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Simple text extractors for plain text, source code, and JSON files.

This module provides lightweight extractors that handle common text-based
file formats without requiring heavy external dependencies.

Responsibilities:
- PlainTextExtractor: TXT, MD, LOG, RST, TEX files with encoding detection
- CodeExtractor: Source code files (Python, JS, TS, HTML, CSS, etc.)
- JSONExtractor: JSON files with pretty formatting and key counting
"""

import json
from pathlib import Path
from typing import Optional, Any

from aitao.core.plugin_registry import register
from aitao.indexation.text_extractor import (
    ExtractionResult,
    BaseExtractor,
    _get_langdetect,
)


@register("extractor", "plaintext")
class PlainTextExtractor(BaseExtractor):
    """Extractor for plain text files (TXT, MD, LOG, etc.)."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".tex", ".log"}

    # Common encodings to try
    ENCODINGS = ["utf-8", "utf-16", "latin-1", "cp1252", "iso-8859-1"]

    def extract(self, file_path: Path) -> ExtractionResult:
        """Extract text from plain text file."""
        text = None
        encoding_used = None

        # Try different encodings
        for encoding in self.ENCODINGS:
            try:
                text = file_path.read_text(encoding=encoding)
                encoding_used = encoding
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if text is None:
            return ExtractionResult(
                text="",
                success=False,
                error=f"Could not decode file with any encoding: {self.ENCODINGS}",
            )

        # Calculate metadata
        word_count = len(text.split())
        line_count = text.count("\n") + 1

        # Detect language
        language = self._detect_language(text)

        return ExtractionResult(
            text=text,
            metadata={
                "word_count": word_count,
                "line_count": line_count,
                "language": language,
                "encoding": encoding_used,
                "file_type": file_path.suffix.lower().lstrip("."),
            },
        )

    def _detect_language(self, text: str) -> Optional[str]:
        """Detect language of text."""
        if not text or len(text.strip()) < 20:
            return None

        try:
            langdetect = _get_langdetect()
            # Use first 5000 chars for detection
            sample = text[:5000]
            return langdetect.detect(sample)
        except Exception:
            return None


@register("extractor", "code")
class CodeExtractor(BaseExtractor):
    """Extractor for source code and data files."""

    SUPPORTED_EXTENSIONS = {
        # Python
        ".py", ".pyi", ".pyx",
        # JavaScript/TypeScript
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        # Web
        ".html", ".htm", ".css", ".scss", ".sass", ".less",
        # Data formats
        ".json", ".yaml", ".yml", ".toml", ".xml", ".csv",
        # Shell
        ".sh", ".bash", ".zsh", ".fish",
        # Other
        ".sql", ".r", ".rb", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
        ".java", ".kt", ".swift", ".m", ".mm",
    }

    def extract(self, file_path: Path) -> ExtractionResult:
        """Extract text from source code file."""
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file_path.read_text(encoding="latin-1")
            except Exception as e:
                return ExtractionResult(
                    text="",
                    success=False,
                    error=f"Failed to read file: {e}",
                )

        # Calculate metadata
        word_count = len(text.split())
        line_count = text.count("\n") + 1

        # Detect language (programming vs natural)
        lang = self._detect_language(text)

        return ExtractionResult(
            text=text,
            metadata={
                "word_count": word_count,
                "line_count": line_count,
                "language": lang,
                "programming_language": file_path.suffix.lower().lstrip("."),
                "file_type": "code",
            },
        )

    def _detect_language(self, text: str) -> Optional[str]:
        """Detect natural language in comments/strings."""
        if not text or len(text.strip()) < 50:
            return None

        try:
            langdetect = _get_langdetect()
            # Extract comments and strings for language detection
            sample = text[:5000]
            return langdetect.detect(sample)
        except Exception:
            return None


@register("extractor", "json")
class JSONExtractor(BaseExtractor):
    """Extractor for JSON files with pretty formatting."""

    SUPPORTED_EXTENSIONS = {".json"}

    def extract(self, file_path: Path) -> ExtractionResult:
        """Extract and format JSON content."""
        try:
            text = file_path.read_text(encoding="utf-8")
            # Parse and re-format for consistent output
            data = json.loads(text)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)

            word_count = len(formatted.split())

            return ExtractionResult(
                text=formatted,
                metadata={
                    "word_count": word_count,
                    "file_type": "json",
                    "keys_count": self._count_keys(data),
                },
            )
        except json.JSONDecodeError as e:
            return ExtractionResult(
                text=text if "text" in dir() else "",
                success=False,
                error=f"Invalid JSON: {e}",
            )
        except Exception as e:
            return ExtractionResult(
                text="",
                success=False,
                error=f"Failed to read JSON: {e}",
            )

    def _count_keys(self, data: Any, count: int = 0) -> int:
        """Recursively count keys in JSON."""
        if isinstance(data, dict):
            count += len(data)
            for v in data.values():
                count = self._count_keys(v, count)
        elif isinstance(data, list):
            for item in data:
                count = self._count_keys(item, count)
        return count
