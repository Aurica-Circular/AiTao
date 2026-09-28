# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
PDFExtractor - Enhanced PDF text extraction with scanned/native detection.

This module provides advanced PDF extraction capabilities for document indexing.
It detects whether a PDF contains native text or is scanned (image-based),
setting a needs_ocr flag for deferred OCR processing in Sprint 5.

Responsibilities:
- Extract text from native (text-based) PDF documents
- Detect scanned PDFs vs native PDFs using multiple heuristics
- Extract PDF metadata (author, title, page_count, creation_date)
- Flag scanned PDFs with needs_ocr=True for OCR pipeline
- Handle multi-page documents efficiently
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any

from aitao.core.logger import get_logger

# Lazy imports for optional dependencies
_pypdf = None
_langdetect = None

logger = get_logger(__name__)


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


@dataclass
class PDFAnalysisResult:
    """
    Result of PDF analysis for text extraction.
    
    Attributes:
        text: Extracted text content (empty if scanned PDF).
        needs_ocr: True if PDF appears to be scanned/image-based.
        page_count: Total number of pages.
        pages_with_text: Number of pages containing extractable text.
        pages_with_images: Number of pages containing images.
        text_coverage: Ratio of pages with text (0.0-1.0).
        metadata: PDF document metadata.
        success: Whether analysis was successful.
        error: Error message if analysis failed.
    """
    
    text: str = ""
    needs_ocr: bool = False
    page_count: int = 0
    pages_with_text: int = 0
    pages_with_images: int = 0
    text_coverage: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class PDFExtractor:
    """
    Enhanced PDF extractor with native/scanned detection.
    
    This extractor analyzes PDFs to determine if they contain extractable
    text or are scanned documents requiring OCR. It uses multiple heuristics:
    
    1. Text extraction attempt on each page
    2. Image presence detection
    3. Text coverage ratio calculation
    4. Character density analysis
    
    Thresholds (configurable):
        - MIN_TEXT_COVERAGE: Minimum ratio of pages with text (default: 0.3)
        - MIN_CHARS_PER_PAGE: Minimum average chars per page (default: 100)
    
    Example:
        extractor = PDFExtractor()
        result = extractor.analyze("/path/to/document.pdf")
        if result.needs_ocr:
            print("This PDF requires OCR processing")
        else:
            print(f"Extracted {len(result.text)} characters")
    """
    
    # File extensions this extractor handles
    SUPPORTED_EXTENSIONS = {".pdf"}
    
    # Detection thresholds
    MIN_TEXT_COVERAGE = 0.3  # At least 30% of pages must have text
    MIN_CHARS_PER_PAGE = 100  # Minimum average characters per page
    MIN_TEXT_LENGTH = 50  # Minimum total text length to consider as native

    # Scan detection (US-086 v6). An image-backed page carrying fewer than this
    # many characters is a scanned page whose (often junk) text layer must not be
    # trusted — a real full A4/Letter text page carries ~1500-3000 chars. When a
    # majority of pages look like this, the PDF is a scan and is routed to OCR.
    SCAN_PAGE_MAX_CHARS = 250
    # Belt for non-Latin corpora: when an expected non-Latin script (e.g. CJK) is
    # entirely absent from an image-backed document below this density, the native
    # layer is a junk transcription of an un-OCR'd scan — route to OCR.
    SCAN_MISSING_SCRIPT_CEILING = 800
    
    def __init__(
        self,
        min_text_coverage: float = MIN_TEXT_COVERAGE,
        min_chars_per_page: int = MIN_CHARS_PER_PAGE,
        expected_scripts: Optional[set] = None,
    ):
        """
        Initialize PDF extractor.

        Args:
            min_text_coverage: Minimum ratio of pages with text (0.0-1.0).
            min_chars_per_page: Minimum average chars per page for native PDF.
            expected_scripts: Unicode scripts considered legitimate for native
                text (from the declared ``ocr.languages``). When set, a text-rich
                PDF whose text is mostly outside these scripts is treated as
                garbled and routed to OCR (US-086 v5). None disables the check.
        """
        self.min_text_coverage = min_text_coverage
        self.min_chars_per_page = min_chars_per_page
        self.expected_scripts = expected_scripts
    
    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """Check if this extractor can handle the given file."""
        return file_path.suffix.lower() in cls.SUPPORTED_EXTENSIONS
    
    def analyze(self, file_path: Path) -> PDFAnalysisResult:
        """
        Analyze PDF and extract text if native, or flag for OCR if scanned.
        
        Args:
            file_path: Path to the PDF file.
            
        Returns:
            PDFAnalysisResult with text, needs_ocr flag, and metadata.
        """
        try:
            pypdf = _get_pypdf()
        except ImportError:
            return PDFAnalysisResult(
                success=False,
                error="pypdf not installed. Run: uv pip install pypdf"
            )
        
        try:
            reader = pypdf.PdfReader(str(file_path))
            page_count = len(reader.pages)
            
            if page_count == 0:
                return PDFAnalysisResult(
                    success=False,
                    error="PDF has no pages"
                )
            
            # Extract text from all pages and analyze
            pages_text = []
            pages_with_text = 0
            pages_with_images = 0
            total_chars = 0
            sparse_scan_pages = 0  # image-backed pages with a thin (junk) text layer

            for i, page in enumerate(reader.pages):
                # Extract text from page
                page_text = page.extract_text() or ""
                page_text = page_text.strip()
                page_has_image = self._page_has_images(page)

                if page_text and len(page_text) > 10:
                    pages_with_text += 1
                    total_chars += len(page_text)
                    pages_text.append(page_text)

                # An image-backed page carrying little text is a scanned page whose
                # text layer (if any) cannot be trusted — count it toward the scan
                # ratio used by _needs_ocr below.
                if page_has_image:
                    pages_with_images += 1
                    if len(page_text) < self.SCAN_PAGE_MAX_CHARS:
                        sparse_scan_pages += 1
            
            # Join all text
            full_text = "\n\n".join(pages_text)
            
            # Calculate metrics
            text_coverage = pages_with_text / page_count if page_count > 0 else 0.0
            avg_chars_per_page = total_chars / page_count if page_count > 0 else 0
            image_ratio = pages_with_images / page_count if page_count > 0 else 0.0
            sparse_scan_ratio = sparse_scan_pages / page_count if page_count > 0 else 0.0

            # Determine if OCR is needed
            needs_ocr = self._needs_ocr(
                text_coverage=text_coverage,
                avg_chars_per_page=avg_chars_per_page,
                total_text_length=len(full_text),
                pages_with_images=pages_with_images,
                page_count=page_count,
                sparse_scan_ratio=sparse_scan_ratio,
            )
            ocr_reason = None
            if needs_ocr:
                ocr_reason = "scanned_sparse" if sparse_scan_ratio >= 0.5 else "low_text"

            # Belt (US-086 v6): an expected non-Latin script (e.g. CJK) entirely
            # absent from an image-backed, low-density PDF means the native layer
            # is a junk transcription of an un-OCR'd scan — the mojibake ratio can
            # miss this (junk is often Latin/digits, which are always "expected").
            if not needs_ocr and full_text and self.expected_scripts:
                from aitao.indexation.mojibake import contains_any_script
                nonlatin = self.expected_scripts - {"LATIN"}
                if (
                    nonlatin
                    and image_ratio >= 0.8
                    and avg_chars_per_page < self.SCAN_MISSING_SCRIPT_CEILING
                    and not contains_any_script(full_text, nonlatin)
                ):
                    needs_ocr = True
                    ocr_reason = "missing_script"
                    logger.warning(
                        f"Expected script(s) {sorted(nonlatin)} absent from an "
                        f"image-backed PDF, routing to OCR: {file_path.name}"
                    )

            # Mojibake guard (US-086 v5): a text-rich PDF can still carry garbled
            # text (broken-font export). It passes the low-text heuristics above,
            # so check the script makeup and route it to OCR instead of indexing
            # garbage. Only runs when expected scripts are declared.
            if not needs_ocr and full_text and self.expected_scripts:
                from aitao.indexation.mojibake import looks_like_mojibake
                if looks_like_mojibake(full_text, self.expected_scripts):
                    needs_ocr = True
                    ocr_reason = "mojibake"
                    logger.warning(
                        f"PDF text looks garbled (mojibake), routing to OCR: "
                        f"{file_path.name}"
                    )

            # Extract metadata
            metadata = self._extract_metadata(reader, file_path)
            
            # Detect language if we have text
            language = self._detect_language(full_text) if full_text else None
            if language:
                metadata["language"] = language
            
            # Add extraction stats to metadata
            metadata.update({
                "word_count": len(full_text.split()) if full_text else 0,
                "pages": page_count,
                "pages_with_text": pages_with_text,
                "pages_with_images": pages_with_images,
                "text_coverage": round(text_coverage, 2),
                "avg_chars_per_page": int(avg_chars_per_page),
                "file_type": "pdf",
                "extraction_method": "native" if not needs_ocr else "pending_ocr",
                "ocr_reason": ocr_reason,
            })
            
            logger.info(
                f"PDF analysis: {file_path.name} - "
                f"pages={page_count}, text_coverage={text_coverage:.1%}, "
                f"needs_ocr={needs_ocr}"
            )
            
            return PDFAnalysisResult(
                text=full_text if not needs_ocr else "",
                needs_ocr=needs_ocr,
                page_count=page_count,
                pages_with_text=pages_with_text,
                pages_with_images=pages_with_images,
                text_coverage=text_coverage,
                metadata=metadata,
                success=True,
            )
            
        except Exception as e:
            logger.exception(f"Failed to analyze PDF: {file_path}")
            return PDFAnalysisResult(
                success=False,
                error=f"Failed to analyze PDF: {e}"
            )
    
    def _page_has_images(self, page) -> bool:
        """
        Check if a PDF page contains images.
        
        Args:
            page: pypdf Page object.
            
        Returns:
            True if page contains images.
        """
        try:
            # Check for XObject images in page resources
            if "/XObject" in page.get("/Resources", {}):
                xobject = page["/Resources"]["/XObject"]
                if xobject:
                    for obj in xobject.values():
                        # Resolve indirect object if needed
                        if hasattr(obj, "get_object"):
                            obj = obj.get_object()
                        if obj.get("/Subtype") == "/Image":
                            return True
            return False
        except Exception:
            # If we can't determine, assume no images
            return False
    
    def _needs_ocr(
        self,
        text_coverage: float,
        avg_chars_per_page: float,
        total_text_length: int,
        pages_with_images: int,
        page_count: int,
        sparse_scan_ratio: float = 0.0,
    ) -> bool:
        """
        Determine if PDF needs OCR based on multiple heuristics.

        Heuristics:
        1. Very low text coverage → likely scanned
        2. Very few characters per page → likely scanned
        3. Total text too short → likely scanned
        4. Many images + low text → likely scanned
        5. Majority of pages are image-backed with a thin text layer → scan with
           a junk/partial text layer (US-086 v6)

        Args:
            text_coverage: Ratio of pages with extractable text.
            avg_chars_per_page: Average characters per page.
            total_text_length: Total extracted text length.
            pages_with_images: Number of pages with images.
            page_count: Total page count.
            sparse_scan_ratio: Ratio of pages that are image-backed yet carry
                fewer than SCAN_PAGE_MAX_CHARS characters.

        Returns:
            True if OCR is needed.
        """
        # Very short document with images - likely scanned
        if total_text_length < self.MIN_TEXT_LENGTH:
            return True

        # Low text coverage
        if text_coverage < self.min_text_coverage:
            return True

        # Very few characters per page on average
        if avg_chars_per_page < self.min_chars_per_page:
            return True

        # Document is mostly images with very little text
        image_ratio = pages_with_images / page_count if page_count > 0 else 0
        if image_ratio > 0.8 and text_coverage < 0.5:
            return True

        # Scan with a thin/junk text layer: most pages are full-page images yet
        # each carries only a sliver of text (100% "coverage" of garbage would
        # otherwise pass the checks above). Trust OCR over the sparse layer.
        if sparse_scan_ratio >= 0.5:
            return True

        return False
    
    def _extract_metadata(self, reader, file_path: Path) -> Dict[str, Any]:
        """
        Extract PDF document metadata.
        
        Args:
            reader: pypdf PdfReader object.
            file_path: Path to the PDF file.
            
        Returns:
            Dictionary of metadata fields.
        """
        metadata = {}
        
        try:
            if reader.metadata:
                # Standard PDF metadata fields
                field_mapping = {
                    "/Title": "title",
                    "/Author": "author",
                    "/Subject": "subject",
                    "/Creator": "creator",
                    "/Producer": "producer",
                    "/CreationDate": "creation_date",
                    "/ModDate": "modification_date",
                    "/Keywords": "keywords",
                }
                
                for pdf_key, meta_key in field_mapping.items():
                    value = reader.metadata.get(pdf_key)
                    if value:
                        # Clean up date strings if needed
                        if "Date" in pdf_key and isinstance(value, str):
                            value = self._parse_pdf_date(value)
                        metadata[meta_key] = value
        except Exception as e:
            logger.warning(f"Failed to extract PDF metadata: {e}")
        
        return metadata
    
    def _parse_pdf_date(self, date_str: str) -> str:
        """
        Parse PDF date format (D:YYYYMMDDHHmmss) to ISO format.
        
        Args:
            date_str: PDF date string.
            
        Returns:
            Cleaned date string or original if parsing fails.
        """
        try:
            # Remove D: prefix if present
            if date_str.startswith("D:"):
                date_str = date_str[2:]
            
            # Extract date components (minimum YYYYMMDD)
            if len(date_str) >= 8:
                year = date_str[0:4]
                month = date_str[4:6]
                day = date_str[6:8]
                
                # Add time if available
                if len(date_str) >= 14:
                    hour = date_str[8:10]
                    minute = date_str[10:12]
                    second = date_str[12:14]
                    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"
                
                return f"{year}-{month}-{day}"
        except Exception:
            pass
        
        return date_str
    
    def _detect_language(self, text: str) -> Optional[str]:
        """
        Detect language of extracted text.
        
        Args:
            text: Text to analyze.
            
        Returns:
            ISO language code or None.
        """
        if not text or len(text.strip()) < 20:
            return None
        
        try:
            langdetect = _get_langdetect()
            # Use first 5000 chars for detection (faster)
            sample = text[:5000]
            return langdetect.detect(sample)
        except Exception:
            return None


# Convenience function for backward compatibility
def extract_pdf(file_path: str | Path) -> PDFAnalysisResult:
    """
    Extract text from a PDF file.
    
    Args:
        file_path: Path to the PDF file.
        
    Returns:
        PDFAnalysisResult with text, needs_ocr flag, and metadata.
    """
    extractor = PDFExtractor()
    return extractor.analyze(Path(file_path))
