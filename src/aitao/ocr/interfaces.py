# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# interfaces.py — OCR pipeline abstract interfaces and result types.
#
# Defines the contract that all OCR providers must implement.
# OCRRouter uses these to swap backends without changing the caller.
# Implements US-I-a acceptance criteria.

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

# A language hint may be a single BCP-47 tag ('fr-FR') or a list of candidate
# tags (['fr', 'en', 'zh-Hant']) when the language is unknown — OCR engines
# accept several candidates and pick the best match per region.
LangHint = Union[str, list[str]]


@dataclass
class OCRResult:
    """Structured result from any OCR provider."""

    text: str
    """Extracted plain text (all pages concatenated, separated by form-feeds)."""

    page_count: int = 1
    """Number of pages processed."""

    lang_detected: Optional[str] = None
    """BCP-47 language code detected or used, e.g. 'fr-FR'."""

    provider_used: str = "unknown"
    """Name of the backend that produced this result."""

    confidence: float = 1.0
    """Overall confidence score 0.0–1.0 (1.0 if not supported by backend)."""

    metadata: dict = field(default_factory=dict)
    """Provider-specific extra data (page bounding boxes, warnings, etc.)."""

    @property
    def word_count(self) -> int:
        """Approximate word count from extracted text."""
        return len(self.text.split()) if self.text else 0

    @property
    def is_empty(self) -> bool:
        """Return True when no text was extracted."""
        return not self.text or not self.text.strip()


class OCRProvider(ABC):
    """Abstract interface for all OCR providers.

    Each concrete provider must implement:
    - ``name``: unique identifier (e.g. 'macos_vision', 'tesseract', 'qwen_vl')
    - ``is_available()``: lightweight check that the backend is usable
    - ``extract()``: perform OCR and return an OCRResult

    Providers must NOT raise on OCR failure — they return an OCRResult
    with empty text and store the error in metadata['error'].
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider can be used on the current system."""

    @abstractmethod
    async def extract(
        self,
        file_path: Path,
        lang: Optional[LangHint] = None,
        pages: Optional[str] = None,
        dpi: int = 300,
    ) -> OCRResult:
        """Extract text from a file.

        Args:
            file_path: Resolved absolute path to the file.
            lang:      Language hint — a single BCP-47 tag ('fr-FR') or a list
                       of candidate tags (['fr', 'en', 'zh-Hant']).
                       Pass None for auto-detection.
            pages:     Page range for PDFs: '1', '1-3', or None (all).
            dpi:       Resolution for PDF rasterisation (default 300 dpi).

        Returns:
            OCRResult — never raises; errors are stored in result.metadata['error'].
        """
