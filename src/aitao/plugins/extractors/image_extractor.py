# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
ImageExtractor — extractor for raw image files (PNG, JPG, TIFF, etc.).

Responsibilities:
- Detect image files via extension and flag them for OCR processing.
- Enrich the result with EXIF metadata (camera, GPS, dimensions) by
  delegating to EXIFExtractor — but keep the extracted ``text`` empty so
  that DocumentIndexer's "Step 1b" runs OCRRouter.extract_sync() and
  populates the text content.
- After OCR runs in the indexer, the document is indexed in BOTH
  Meilisearch (full-text) and LanceDB (vector) like any other text
  document, while EXIF fields remain available in metadata.

Premium gating happens centrally inside OCRRouter._enforce_premium();
this extractor only marks files as needing OCR.
"""

from __future__ import annotations

from pathlib import Path

from aitao.core.plugin_registry import register
from aitao.indexation.text_extractor import BaseExtractor, ExtractionResult
from aitao.plugins.extractors.exif_extractor import EXIFExtractor


@register("extractor", "image")
class ImageExtractor(BaseExtractor):
    """Extractor for image files — defers text extraction to the OCR pipeline.

    Returns an ExtractionResult with:
        text=""              → triggers OCR Step 1b in DocumentIndexer
        needs_ocr=True       → flag consumed by indexer
        extraction_method="pending_ocr"
        + EXIF-derived metadata fields (camera, gps_lat, gps_lon, etc.)
    """

    SUPPORTED_EXTENSIONS = {
        ".png", ".jpg", ".jpeg",
        ".tiff", ".tif",
        ".bmp", ".gif",
        ".webp", ".heic", ".heif",
    }

    def __init__(self) -> None:
        # Reuse EXIFExtractor for metadata enrichment (camera, GPS, dimensions).
        # If Pillow is missing, EXIF extraction returns success=False and we
        # still mark the file for OCR — the OCR pipeline does not need EXIF.
        self._exif = EXIFExtractor()

    def extract(self, file_path: Path) -> ExtractionResult:
        """Mark the image for OCR; pull EXIF into metadata when available."""
        metadata: dict = {
            "needs_ocr": True,
            "extraction_method": "pending_ocr",
            "file_type": "image",
        }

        # Best-effort EXIF enrichment: failures here must not break indexing.
        try:
            exif_result = self._exif.extract(file_path)
            if exif_result.success and exif_result.metadata:
                # Merge EXIF metadata fields (width, height, camera, gps_lat, ...)
                # but do NOT carry over the EXIF text summary — text must remain
                # empty so the indexer triggers OCR.
                for key, value in exif_result.metadata.items():
                    if key not in metadata:
                        metadata[key] = value
        except Exception:
            # EXIF is optional; never let it break the OCR pipeline.
            pass

        return ExtractionResult(
            text="",
            metadata=metadata,
            success=True,
        )
