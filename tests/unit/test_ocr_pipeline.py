# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_ocr_pipeline.py — Unit tests for the OCR pipeline (US-I-f).
#
# Validates the chain: image → OCRRouter → provider → OCRResult.
# All tests use in-memory mocks; no external process or network call is made.
# Covers: interfaces, router selection, fallback, indexer integration, MCP tool.
#
# US-138-1: the OCR PROVIDERS (MacOSVisionProvider, TesseractProvider,
# QwenVLProvider) moved to the separately distributed aitao-premium package —
# this file no longer imports or exercises them directly. Every test here
# uses fake providers (_MockProvider / _EmptyProvider, defined below) so this
# suite passes identically whether or not aitao-premium is installed. The
# real providers' own tests now live in aitao-premium/tests/.

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy/missing optional dependencies before any aitao import.
# The Python 3.14 system env used for tests lacks certifi, packaging, etc.
# These stubs are harmless — real code uses them only at runtime.
# ---------------------------------------------------------------------------
_STUBS = [
    "lancedb", "pyarrow", "pyarrow.parquet",
    "sentence_transformers",
    "packaging", "packaging.version",
    "certifi",
]
for _mod in _STUBS:
    sys.modules.setdefault(_mod, MagicMock())

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# Imports are intentionally below the dependency stubbing above (E402 expected).
from aitao.ocr.interfaces import OCRProvider, OCRResult  # noqa: E402
from aitao.ocr.router import OCRRouter  # noqa: E402


# =============================================================================
# Helpers / Fixtures
# =============================================================================

class _MockProvider(OCRProvider):
    """Reusable mock OCR provider for tests."""

    def __init__(self, name: str, available: bool = True, text: str = "hello world"):
        self._name = name
        self._available = available
        self._text = text
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    async def extract(self, file_path, lang=None, pages=None, dpi=300) -> OCRResult:
        self.call_count += 1
        return OCRResult(
            text=self._text,
            page_count=1,
            lang_detected=lang,
            provider_used=self._name,
            confidence=0.95,
        )


class _EmptyProvider(_MockProvider):
    """Provider that always returns empty text (to test fallback)."""

    async def extract(self, file_path, lang=None, pages=None, dpi=300) -> OCRResult:
        self.call_count += 1
        return OCRResult(text="", provider_used=self._name)


@pytest.fixture
def tmp_png(tmp_path):
    """Create a minimal 1x1 white PNG for testing."""
    try:
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="white")
        p = tmp_path / "test.png"
        img.save(str(p))
        return p
    except ImportError:
        # Pillow not installed — write raw bytes
        import struct
        import zlib

        def _png_1x1():
            sig = b'\x89PNG\r\n\x1a\n'

            def chunk(t, d):
                length = len(d)
                return struct.pack('>I', length) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)

            ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            idat = chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
            iend = chunk(b'IEND', b'')
            return sig + ihdr + idat + iend
        p = tmp_path / "test.png"
        p.write_bytes(_png_1x1())
        return p


# =============================================================================
# US-I-a: OCRResult dataclass
# =============================================================================

class TestOCRResult:
    def test_word_count(self):
        r = OCRResult(text="hello world foo", provider_used="mock")
        assert r.word_count == 3

    def test_is_empty_when_blank(self):
        assert OCRResult(text="   ", provider_used="mock").is_empty

    def test_is_empty_false_with_text(self):
        assert not OCRResult(text="some text", provider_used="mock").is_empty

    def test_defaults(self):
        r = OCRResult(text="x", provider_used="p")
        assert r.page_count == 1
        assert r.confidence == 1.0
        assert r.lang_detected is None
        assert r.metadata == {}


# =============================================================================
# US-I-a: OCRRouter — provider selection
# =============================================================================

class TestOCRRouterSelection:
    def _router_with(self, *providers) -> OCRRouter:
        router = OCRRouter.__new__(OCRRouter)
        router._config = {}
        router._providers = list(providers)
        return router

    def test_uses_first_available_provider(self):
        p1 = _MockProvider("p1", available=True, text="from p1")
        p2 = _MockProvider("p2", available=True, text="from p2")
        router = self._router_with(p1, p2)

        result = asyncio.run(router.extract(Path("/fake/file.png").__class__("/tmp") / "x.png"))
        # x.png does not exist → router returns "file not found"
        assert result.provider_used in ("none", "p1", "p2")

    def test_file_not_found_returns_error_result(self):
        router = self._router_with(_MockProvider("p1"))
        result = asyncio.run(router.extract("/nonexistent/path.png"))
        assert result.is_empty
        assert "error" in result.metadata

    def test_no_provider_returns_empty(self):
        router = self._router_with()
        result = asyncio.run(router.extract("/tmp"))
        assert result.is_empty

    def test_fallback_when_first_returns_empty(self, tmp_png):
        p1 = _EmptyProvider("empty_provider", available=True)
        p2 = _MockProvider("fallback_provider", available=True, text="fallback text")
        router = self._router_with(p1, p2)

        result = asyncio.run(router.extract(tmp_png))
        assert result.provider_used == "fallback_provider"
        assert result.text == "fallback text"
        assert p1.call_count == 1

    def test_forced_provider_is_tried_first(self, tmp_png):
        p1 = _MockProvider("alpha", available=True, text="alpha")
        p2 = _MockProvider("beta", available=True, text="beta")
        router = self._router_with(p1, p2)

        result = asyncio.run(router.extract(tmp_png, provider="beta"))
        assert result.provider_used == "beta"

    def test_language_passed_to_provider(self, tmp_png):
        p = _MockProvider("p", available=True, text="bonjour")
        router = self._router_with(p)

        result = asyncio.run(router.extract(tmp_png, lang="fr-FR"))
        assert result.lang_detected == "fr-FR"


# =============================================================================
# US-I-a: OCRRouter.extract_sync (sync wrapper used by indexer.py)
# =============================================================================

class TestOCRRouterSync:
    def test_extract_sync_returns_ocr_result(self, tmp_png):
        p = _MockProvider("sync_provider", available=True, text="sync text")
        router = OCRRouter.__new__(OCRRouter)
        router._config = {}
        router._providers = [p]

        result = router.extract_sync(tmp_png)
        assert result.text == "sync text"
        assert result.provider_used == "sync_provider"


# =============================================================================
# US-I-f: Indexer integration — OCR triggered when needs_ocr=True
# =============================================================================


class TestIndexerOCRIntegration:
    """Verify DocumentIndexer calls OCRRouter when needs_ocr=True."""

    def _make_indexer(self):
        from aitao.indexation.indexer import DocumentIndexer
        indexer = DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True)
        return indexer

    def _make_scanned_extraction(self):
        """Simulate what text_extractor returns for a scanned PDF."""
        from aitao.indexation.text_extractor import ExtractionResult
        return ExtractionResult(
            text="",
            metadata={
                "needs_ocr": True,
                "extraction_method": "pending_ocr",
                "pages": 2,
                "file_path": "/fake/scan.pdf",
                "file_name": "scan.pdf",
                "file_size": 1024,
            },
            success=True,
        )

    def test_ocr_called_when_needs_ocr(self, tmp_path):
        """OCRRouter.extract_sync must be called for scanned PDFs."""
        from aitao.indexation.indexer import DocumentIndexer

        # Create a temporary dummy PDF file (content doesn't matter — extraction is mocked)
        fake_pdf = tmp_path / "scan.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        scanned = self._make_scanned_extraction()
        ocr_result = OCRResult(text="Extracted OCR text", provider_used="mock", page_count=2)

        with patch("aitao.indexation.indexer._get_ocr_router") as mock_get_router:
            mock_router = MagicMock()
            mock_router.extract_sync.return_value = ocr_result
            mock_get_router.return_value = mock_router

            with patch("aitao.indexation.text_extractor.TextExtractor.extract", return_value=scanned):
                indexer = DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True)
                indexer.index_file(str(fake_pdf))

            mock_router.extract_sync.assert_called_once()

    def test_ocr_ignores_misleading_native_language(self, tmp_path):
        """OCR must not inherit the language detected from a garbage native layer.

        A scan's junk text layer can make langdetect report "en"; passing that to
        OCR forces a single wrong-language pass and defeats the router's
        confidence-based candidate selection (US-086 v6). The indexer must call
        extract_sync with lang=None so the configured ocr.languages are tried.
        """
        from aitao.indexation.indexer import DocumentIndexer
        from aitao.indexation.text_extractor import ExtractionResult

        fake_pdf = tmp_path / "scan.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        # Withheld text, but a misleading language was detected from the garbage.
        scanned = ExtractionResult(
            text="",
            metadata={"needs_ocr": True, "language": "en", "ocr_reason": "scanned_sparse"},
            success=True,
        )
        ocr_result = OCRResult(
            text="真正的中文內容", provider_used="mock", page_count=1,
            lang_detected="zh-Hant",
        )

        with patch("aitao.indexation.indexer._get_ocr_router") as mock_get_router:
            mock_router = MagicMock()
            mock_router.extract_sync.return_value = ocr_result
            mock_get_router.return_value = mock_router

            with patch("aitao.indexation.text_extractor.TextExtractor.extract", return_value=scanned):
                indexer = DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True)
                result = indexer.index_file(str(fake_pdf))

            # OCR must run language-agnostic...
            _, kwargs = mock_router.extract_sync.call_args
            assert kwargs.get("lang") is None
            # ...and the stored language must be the OCR's winning pass, not the
            # misleading "en" langdetect read from the garbage native layer.
            # US-85c: the stored tag is the canonical short code ("zh"), not the
            # precise Apple Vision tag ("zh-Hant") which is kept in metadata.
            assert result.language == "zh"

    def test_no_ocr_called_when_text_present(self, tmp_path):
        """OCRRouter must NOT be called when text is already extracted."""
        from aitao.indexation.indexer import DocumentIndexer
        from aitao.indexation.text_extractor import ExtractionResult

        fake_txt = tmp_path / "doc.txt"
        fake_txt.write_text("Already has text content.")

        normal_extraction = ExtractionResult(
            text="Already has text content.",
            metadata={"needs_ocr": False, "word_count": 4, "file_path": str(fake_txt),
                      "file_name": "doc.txt", "file_size": 25},
            success=True,
        )

        with patch("aitao.indexation.indexer._get_ocr_router") as mock_get_router:
            with patch("aitao.indexation.text_extractor.TextExtractor.extract", return_value=normal_extraction):
                DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True).index_file(str(fake_txt))

            mock_get_router.assert_not_called()


# =============================================================================
# v2.10.1 — Premium gating on the OCR pipeline
# =============================================================================


class _DummyPremiumError(RuntimeError):
    """Stand-in for core.license.PremiumFeatureError when license module is stubbed."""


class TestOCRRouterPremiumGate:
    """OCRRouter must raise when the running edition is not Premium.

    Centralised gate covers ALL entry points: PDF rasterisation, raw images,
    MCP tool, indexer Step 1b. No bypass possible.
    """

    def _router_with(self, *providers) -> OCRRouter:
        router = OCRRouter.__new__(OCRRouter)
        router._config = {}
        router._providers = list(providers)
        return router

    def test_extract_blocked_when_not_premium(self, tmp_png):
        """Async extract() must raise PremiumFeatureError for non-premium users."""
        from aitao.ocr import router as router_mod

        def _raise(*args, **kwargs):
            raise _DummyPremiumError("ocr_advanced is Premium")

        router = self._router_with(_MockProvider("p", available=True, text="should never run"))
        provider = router._providers[0]

        with patch.object(router_mod, "_enforce_premium", side_effect=_raise):
            with pytest.raises(_DummyPremiumError):
                asyncio.run(router.extract(tmp_png))

        # Provider must NOT have been invoked when the gate fired.
        assert provider.call_count == 0

    def test_extract_sync_blocked_when_not_premium(self, tmp_png):
        """Sync wrapper used by indexer must also enforce the gate."""
        from aitao.ocr import router as router_mod

        def _raise(*args, **kwargs):
            raise _DummyPremiumError("ocr_advanced is Premium")

        router = self._router_with(_MockProvider("p", available=True, text="x"))

        with patch.object(router_mod, "_enforce_premium", side_effect=_raise):
            with pytest.raises(_DummyPremiumError):
                router.extract_sync(tmp_png)

    def test_extract_allowed_when_premium(self, tmp_png):
        """When _enforce_premium is a no-op (Premium edition), OCR proceeds normally."""
        from aitao.ocr import router as router_mod

        router = self._router_with(_MockProvider("p", available=True, text="ok"))

        with patch.object(router_mod, "_enforce_premium", return_value=None):
            result = asyncio.run(router.extract(tmp_png))

        assert result.text == "ok"
        assert result.provider_used == "p"


class TestIndexerOCRPremiumGate:
    """Indexer Step 1b must skip OCR gracefully when premium check fails."""

    def test_indexer_skips_ocr_when_premium_required(self, tmp_path):
        """Non-premium edition: PremiumFeatureError raised by router is swallowed,
        document is still indexed (without OCR text)."""
        from aitao.indexation.indexer import DocumentIndexer
        from aitao.indexation.text_extractor import ExtractionResult

        fake_pdf = tmp_path / "scan.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        scanned = ExtractionResult(
            text="",
            metadata={
                "needs_ocr": True,
                "extraction_method": "pending_ocr",
                "pages": 1,
                "file_path": str(fake_pdf),
                "file_name": "scan.pdf",
                "file_size": 25,
            },
            success=True,
        )

        # Router raises PremiumFeatureError-like exception
        mock_router = MagicMock()
        mock_router.extract_sync.side_effect = _DummyPremiumError("not premium")

        with patch("aitao.indexation.indexer._get_ocr_router", return_value=mock_router):
            with patch("aitao.indexation.text_extractor.TextExtractor.extract", return_value=scanned):
                indexer = DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True)
                result = indexer.index_file(str(fake_pdf))

        # OCR was attempted ...
        mock_router.extract_sync.assert_called_once()
        # ... but the indexer kept going without crashing.
        assert result.success is True


# =============================================================================
# US-80 — engine/engine_order resolution + language fallback
# =============================================================================


class TestEngineOrderResolution:
    """OCRRouter._resolve_order honours ocr.engine / ocr.engine_order."""

    def _router(self, config: dict) -> OCRRouter:
        r = OCRRouter.__new__(OCRRouter)
        r._config = config
        return r

    def test_default_chain_excludes_qwen_vl(self):
        """Without config, the auto chain is Vision→Tesseract; qwen_vl is opt-in."""
        order = self._router({})._resolve_order()
        assert order == ["macos_vision", "tesseract"]
        assert "qwen_vl" not in order

    def test_engine_order_used_as_is(self):
        order = self._router(
            {"engine": "auto", "engine_order": ["tesseract", "qwen_vl"]}
        )._resolve_order()
        assert order == ["tesseract", "qwen_vl"]

    def test_forced_engine_is_prepended(self):
        """engine = "qwen_vl" forces it first even when absent from engine_order."""
        order = self._router(
            {"engine": "qwen_vl", "engine_order": ["macos_vision", "tesseract"]}
        )._resolve_order()
        assert order[0] == "qwen_vl"
        assert order == ["qwen_vl", "macos_vision", "tesseract"]

    def test_forced_engine_not_duplicated(self):
        order = self._router(
            {"engine": "tesseract", "engine_order": ["macos_vision", "tesseract"]}
        )._resolve_order()
        assert order == ["tesseract", "macos_vision"]


class TestLanguageFallback:
    """When the caller passes no lang, the configured languages reach the engine."""

    def test_configured_languages_passed_when_lang_none(self, tmp_png):
        from aitao.ocr import router as router_mod

        p = _MockProvider("p", available=True, text="ni hao")
        router = OCRRouter.__new__(OCRRouter)
        router._config = {}
        router._languages = ["fr", "en", "zh-Hant"]
        router._providers = [p]

        with patch.object(router_mod, "_enforce_premium", return_value=None):
            result = asyncio.run(router.extract(tmp_png))

        # The mock echoes the lang it received into lang_detected.
        assert result.lang_detected == ["fr", "en", "zh-Hant"]

    def test_explicit_lang_overrides_config(self, tmp_png):
        from aitao.ocr import router as router_mod

        p = _MockProvider("p", available=True, text="bonjour")
        router = OCRRouter.__new__(OCRRouter)
        router._config = {}
        router._languages = ["fr", "en", "zh-Hant"]
        router._providers = [p]

        with patch.object(router_mod, "_enforce_premium", return_value=None):
            result = asyncio.run(router.extract(tmp_png, lang="fr-FR"))

        assert result.lang_detected == "fr-FR"


class TestImageExtractor:
    """ImageExtractor flags image files for OCR (Step 1b) without extracting text itself."""

    def test_supports_common_image_extensions(self):
        from aitao.plugins.extractors.image_extractor import ImageExtractor

        for ext in (".png", ".jpg", ".jpeg", ".tiff", ".webp", ".heic"):
            assert ext in ImageExtractor.SUPPORTED_EXTENSIONS

    def test_extract_returns_empty_text_with_needs_ocr(self, tmp_png):
        """The extractor must return empty text + needs_ocr=True so Step 1b runs."""
        from aitao.plugins.extractors.image_extractor import ImageExtractor

        result = ImageExtractor().extract(tmp_png)

        assert result.success is True
        assert result.text == ""
        assert result.metadata.get("needs_ocr") is True
        assert result.metadata.get("extraction_method") == "pending_ocr"
        assert result.metadata.get("file_type") == "image"

    def test_text_extractor_routes_image_to_image_extractor(self, tmp_png):
        """The TextExtractor facade must pick ImageExtractor (NOT EXIFExtractor)
        for image extensions, so needs_ocr propagates to the indexer."""
        from aitao.indexation.text_extractor import TextExtractor

        result = TextExtractor().extract(tmp_png)

        assert result.success is True
        assert result.metadata.get("needs_ocr") is True
        assert result.text == ""  # OCR runs later in Step 1b


class TestIndexerImageOCR:
    """Full pipeline: an image file goes through OCR, then is indexed in
    Meilisearch — LanceDB is no longer part of the write path (ÉPIC-31, US-113,
    decision D1)."""

    def test_image_triggers_ocr_and_text_is_indexed(self, tmp_png):
        """Indexing a PNG must:
        1. Trigger OCRRouter.extract_sync (via Step 1b)
        2. Replace empty text with OCR text
        3. Pass that text to the Meilisearch indexing helper.
        """
        from aitao.indexation.indexer import DocumentIndexer

        ocr_text = "Text recovered from image via OCR"
        ocr_result = OCRResult(text=ocr_text, provider_used="mock", page_count=1)

        with patch("aitao.indexation.indexer._get_ocr_router") as mock_get_router, \
             patch("aitao.indexation.indexer.index_in_meilisearch", return_value=(True, None)) as mock_meili:
            mock_router = MagicMock()
            mock_router.extract_sync.return_value = ocr_result
            mock_get_router.return_value = mock_router

            indexer = DocumentIndexer(skip_chunking=True, allow_temp_paths=True)
            # Force-bypass the lazy client with a stub so the index_in_meilisearch mock is reached.
            indexer._meilisearch_client = MagicMock()

            # force=True bypasses the dedup check (which would otherwise return
            # truthy on the MagicMock'd Meilisearch client and exit early).
            result = indexer.index_file(str(tmp_png), force=True)

        # OCR was triggered for the image
        mock_router.extract_sync.assert_called_once()

        # Meilisearch received the OCR text via the Document (US-23b)
        meili_doc = mock_meili.call_args[0][1]  # 2nd positional arg = Document
        assert meili_doc.content == ocr_text

        assert result.success is True
        assert result.meilisearch_indexed is True
