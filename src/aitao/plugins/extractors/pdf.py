# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
PDF extractor plugin — native text extraction with scanned-PDF detection.

Registered as the "pdf" extractor. Thin wrapper around the PDF analysis engine
(`indexation.pdf_extractor`): extracts native text and flags scanned PDFs that
need OCR via the ``needs_ocr`` metadata field (consumed by DocumentIndexer
Step 1b).
"""

from pathlib import Path

from aitao.core.logger import get_logger
from aitao.core.plugin_registry import register
from aitao.indexation.text_extractor import BaseExtractor, ExtractionResult

logger = get_logger("plugins.extractors.pdf")


@register("extractor", "pdf")
class PDFExtractor(BaseExtractor):
    """
    Enhanced PDF extractor with native/scanned detection.

    This wrapper integrates with the enhanced PDFExtractor from
    indexation/pdf_extractor.py, providing native text extraction with automatic
    detection of scanned PDFs that require OCR processing (flagged via the
    needs_ocr metadata field).

    Attributes:
        SUPPORTED_EXTENSIONS: Set of file extensions this extractor handles.
    """

    SUPPORTED_EXTENSIONS = {".pdf"}

    def __init__(self):
        """Initialize PDF extractor with enhanced analyzer."""
        # Lazy import to avoid circular imports
        self._analyzer = None

    def _get_analyzer(self):
        """Lazy load the enhanced PDF analyzer, scripts from declared languages."""
        if self._analyzer is None:
            from aitao.indexation.pdf_extractor import PDFExtractor as EnhancedPDFExtractor
            self._analyzer = EnhancedPDFExtractor(
                expected_scripts=self._expected_scripts()
            )
        return self._analyzer

    def _expected_scripts(self):
        """Derive the legitimate scripts from config ``ocr.languages`` (US-086 v5).

        Returns None (mojibake detection off) if the config or languages are
        unavailable — detection is strictly opt-in so it never wrongly re-OCRs.
        """
        try:
            from aitao.core.config import get_config
            from aitao.indexation.mojibake import expected_scripts_for

            languages = get_config().ocr.languages
            return expected_scripts_for(languages) if languages else None
        except Exception:
            return None

    def extract(self, file_path: Path) -> ExtractionResult:
        """
        Extract text from PDF file with native/scanned detection.

        Uses enhanced PDF analysis to determine if the PDF contains
        extractable text or requires OCR. Sets needs_ocr=True in metadata
        for scanned PDFs.

        Args:
            file_path: Path to the PDF file.

        Returns:
            ExtractionResult with text and metadata including needs_ocr flag.
        """
        try:
            analyzer = self._get_analyzer()
            result = analyzer.analyze(file_path)

            if not result.success:
                return ExtractionResult(
                    text="",
                    success=False,
                    error=result.error
                )

            # Add needs_ocr flag to metadata for DocumentIndexer
            metadata = result.metadata.copy()
            metadata["needs_ocr"] = result.needs_ocr

            # Log if OCR is needed for tracking
            if result.needs_ocr:
                logger.info(
                    f"PDF marked for OCR: {file_path.name} "
                    f"(text_coverage={result.text_coverage:.1%})"
                )

            return ExtractionResult(
                text=result.text,
                metadata=metadata,
                success=True,
            )

        except Exception as e:
            logger.exception(f"PDF extraction failed: {file_path}")
            return ExtractionResult(
                text="",
                success=False,
                error=f"Failed to extract PDF: {e}"
            )
