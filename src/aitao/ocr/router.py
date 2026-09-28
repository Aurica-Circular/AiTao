# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# router.py — OCRRouter: selects the best available OCR provider and runs extraction.
#
# Engine selection strategy (in priority order, "auto" mode):
#   1. macos_vision  — Apple Vision framework (M-series, best quality)
#   2. tesseract     — cross-platform, requires tesseract binary + pytesseract
# qwen_vl (Ollama vision model) is a registered engine but opt-in: not shipped
# with AiTao, so it is excluded from the default chain. Enable it explicitly via
# config: ocr.engine_order = ["macos_vision", "tesseract", "qwen_vl"].
#
# Config keys (see [ocr] in config.toml — "engine" is deliberately NOT "provider"
# to avoid confusion with the [llm] backend):
#   ocr.engine       = "auto" follows engine_order; or a name to force one engine
#   ocr.engine_order = priority list tried in "auto" mode
#
# Implements US-I-a (OCRRouter), US-I-b (priority chain), US-I-d (context metadata).
# US-80: engine/engine_order keys + language fallback from config.

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from aitao.core.plugin_registry import discover_plugins, registry
from aitao.ocr.interfaces import LangHint, OCRProvider, OCRResult

logger = logging.getLogger(__name__)

# Default engine priority order for "auto" mode. qwen_vl is intentionally absent
# (opt-in only — not bundled with AiTao); add it via ocr.engine_order to enable.
_PROVIDER_ORDER = ["macos_vision", "tesseract"]

# Premium feature name used by LicenseManager (matches PREMIUM_FEATURES list)
_PREMIUM_FEATURE = "ocr_advanced"


def _enforce_premium() -> None:
    """Raise PremiumFeatureError if the running edition is not Premium.

    Centralised gate for ALL OCR entry points (PDF rasterisation, raw images,
    MCP tool, indexer Step 1b). Importing LicenseManager lazily keeps the
    OCR module usable in tests and tools that don't depend on the license layer.
    """
    try:
        from aitao.core.license import LicenseManager  # type: ignore
    except Exception:
        # License module unavailable in stripped-down environments → fail open.
        return
    LicenseManager().require_premium(_PREMIUM_FEATURE)


class OCRRouter:
    """Routes OCR requests to the best available provider.

    Usage (async context):
        router = OCRRouter()
        result: OCRResult = await router.extract(file_path, lang="fr-FR")

    Usage (sync context, e.g. inside indexer.py):
        result = OCRRouter().extract_sync(file_path)
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        """Initialise with optional config override dict.

        If config is None, tries to read from aitao's get_config().
        """
        self._config = config or self._load_config()
        # Candidate languages used when the caller passes no explicit lang hint
        # (e.g. scanned files where detection yields nothing). See extract().
        self._languages: list[str] = list(self._config.get("languages") or [])
        self._providers: list[OCRProvider] = self._build_providers()

    def _load_config(self) -> dict:
        try:
            from aitao.core.config import get_config
            cfg = get_config()
            return {
                "engine": cfg.ocr.engine,
                "engine_order": cfg.ocr.engine_order,
                "languages": cfg.ocr.languages,
                "vision_model": cfg.ocr.vision_model,
                "ollama_url": cfg.llm.ollama_url,
            }
        except Exception:
            return {}

    def _resolve_order(self) -> list[str]:
        """Compute the engine try-order from ocr.engine + ocr.engine_order.

        - engine_order (or the built-in default) defines the auto chain.
        - engine != "auto" forces that engine first (added if not in the list),
          so e.g. engine = "qwen_vl" works even when it is not in engine_order.
        Registered engines are NOT auto-appended: an engine appears only when the
        config asks for it (keeps qwen_vl opt-in).
        """
        order: list[str] = list(self._config.get("engine_order") or _PROVIDER_ORDER)
        engine = self._config.get("engine") or "auto"
        if engine != "auto":
            order = [engine] + [n for n in order if n != engine]
        return order

    def _build_providers(self) -> list[OCRProvider]:
        """Instantiate engines in priority order; skip unavailable ones."""
        discover_plugins()
        order: list[str] = self._resolve_order()
        providers: list[OCRProvider] = []
        for name in order:
            if not registry.is_registered("ocr", name):
                logger.warning("Unknown OCR provider: %s — skipping", name)
                continue
            cls = registry.get("ocr", name)
            kwargs: dict = {}
            if name == "qwen_vl":
                if self._config.get("vision_model"):
                    kwargs["model"] = self._config["vision_model"]
                if self._config.get("ollama_url"):
                    kwargs["base_url"] = self._config["ollama_url"].rstrip("/") + "/v1"
            instance = cls(**kwargs)
            if instance.is_available():
                providers.append(instance)
                logger.debug("OCR provider available: %s", name)
            else:
                logger.debug("OCR provider not available: %s — skipping", name)
        if not providers:
            logger.error("No OCR provider available — OCR will return empty results")
        return providers

    async def extract(
        self,
        file_path: str | Path,
        lang: Optional[LangHint] = None,
        pages: Optional[str] = None,
        dpi: int = 300,
        provider: Optional[str] = None,
    ) -> OCRResult:
        """Extract text from an image or PDF.

        Args:
            file_path: Path to the file (image or PDF).
            lang:      Language hint (BCP-47, e.g. 'fr-FR') or None for auto.
            pages:     Page range string: '1', '1-3', or None for all pages.
            dpi:       Rasterisation resolution for PDFs (default 300 dpi).
            provider:  Force a specific provider by name ('macos_vision', 'tesseract',
                       'qwen_vl'). Falls back to priority list if unavailable.

        Returns:
            OCRResult — never raises, EXCEPT PremiumFeatureError when the
            current edition is not Premium (centralised gate, see _enforce_premium).
        """
        _enforce_premium()
        resolved = Path(file_path).expanduser().resolve()
        if not resolved.exists():
            return OCRResult(
                text="",
                provider_used="none",
                metadata={"error": f"File not found: {file_path}"},
            )

        # Language fallback: when the caller has no detected language (scanned
        # files), hand the configured candidate languages to the engine so it
        # can recognise non-latin scripts (e.g. zh-Hant). getattr guards routers
        # built via __new__ in tests (which bypass __init__).
        if lang is None:
            configured = getattr(self, "_languages", None)
            if configured:
                lang = list(configured)

        candidates = self._providers
        if provider:
            forced = [p for p in self._providers if p.name == provider]
            if forced:
                candidates = forced + [p for p in self._providers if p.name != provider]

        if not candidates:
            return OCRResult(
                text="",
                provider_used="none",
                metadata={"error": "No OCR provider available"},
            )

        result = await candidates[0].extract(resolved, lang=lang, pages=pages, dpi=dpi)
        if result.is_empty and len(candidates) > 1:
            logger.warning(
                "Provider %s returned empty text — trying fallback",
                candidates[0].name,
            )
            result = await candidates[1].extract(resolved, lang=lang, pages=pages, dpi=dpi)

        logger.info(
            "OCR complete: provider=%s pages=%d words=%d",
            result.provider_used,
            result.page_count,
            result.word_count,
        )
        return result

    def extract_sync(
        self,
        file_path: str | Path,
        lang: Optional[LangHint] = None,
        pages: Optional[str] = None,
        dpi: int = 300,
        provider: Optional[str] = None,
    ) -> OCRResult:
        """Synchronous wrapper around extract().

        Use from non-async code such as indexer.py.
        Handles the case where an event loop is already running (e.g. inside asyncio tasks).
        """
        _enforce_premium()
        coro = self.extract(file_path, lang=lang, pages=pages, dpi=dpi, provider=provider)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running in this thread → safe to drive one ourselves.
            return asyncio.run(coro)
        # A loop is already running (e.g. FastMCP worker) — run in a side thread
        # so we don't try to nest run_until_complete inside the active loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
