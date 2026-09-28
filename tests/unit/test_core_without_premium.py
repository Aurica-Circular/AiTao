# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_core_without_premium.py — the free edition must survive
# the absence of the separately distributed aitao-premium package (US-138-1:
# licence verification moved out of the core into that package).
#
# Covers three things:
#   1. aitao.core.plugin_registry.discover_plugins() also loads plugins
#      advertised as "aitao.plugins" entry points — the mechanism that lets a
#      separately installed Premium package register itself without the core
#      importing or naming it. A broken third-party entry point must be
#      logged and skipped, never crash startup.
#   2. aitao.core.license (the facade) always ships with the core now — it is
#      never "absent". "Core alone" therefore means: no provider registered
#      under ("license", "premium") in the plugin registry (aitao-premium not
#      installed, or installed but not yet discovered). item_preparer.py must
#      behave exactly like an unlicensed user: advanced document formats are
#      refused at ingestion. This is simulated by swapping the global plugin
#      registry for an empty one for the duration of each test — never by
#      making aitao.core.license itself unimportable, since it no longer can
#      be (see aitao.core.license.LicenseManager._provider()).
#   3. US-138-1 (OCR move): the OCR PROVIDERS (MacOSVisionProvider,
#      TesseractProvider, QwenVLProvider) also moved to aitao-premium, so
#      "aitao-premium not installed" now also means zero ("ocr", *) plugins
#      registered — not just no licence provider. A scanned-PDF/image path
#      must be refused the exact same way an unlicensed Core user is refused
#      today (PremiumFeatureError, raised before any provider is even looked
#      up), never a crash.
#
# Every stub here is undone automatically by monkeypatch — never a global
# sys.modules mutation left behind for other test files to trip over.

from __future__ import annotations

import asyncio
import importlib.metadata
from types import SimpleNamespace

import pytest

from aitao.core.license import PremiumFeatureError


# ============================================================================
# 1. Entry-point plugin discovery
# ============================================================================

class TestEntryPointDiscovery:
    def test_entry_point_plugin_is_registered(self, monkeypatch):
        """A well-behaved third-party entry point self-registers on load,
        exactly like a built-in plugin module dropped into aitao.plugins."""
        from aitao.core import plugin_registry as pr

        registered = {}

        class _GoodEntryPoint:
            name = "dummy_premium_plugin"

            def load(self):
                @pr.register("demo_kind_entrypoint", "entry_point_demo")
                class DummyPlugin:
                    pass

                registered["done"] = True
                return DummyPlugin

        def fake_entry_points(*, group=None):
            assert group == "aitao.plugins"
            return [_GoodEntryPoint()]

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        # Force re-discovery: entry points are scanned once per process.
        monkeypatch.setattr(pr, "_entry_points_discovered", False)

        pr.discover_plugins()

        assert registered.get("done") is True
        assert pr.registry.is_registered("demo_kind_entrypoint", "entry_point_demo")

    def test_broken_entry_point_is_logged_and_skipped(self, monkeypatch):
        """An entry point that raises on load must not crash discover_plugins
        (a broken third-party package must not take down the free edition)."""
        from aitao.core import plugin_registry as pr

        class _BrokenEntryPoint:
            name = "broken_premium_plugin"

            def load(self):
                raise RuntimeError("boom — third-party package is broken")

        def fake_entry_points(*, group=None):
            return [_BrokenEntryPoint()]

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        monkeypatch.setattr(pr, "_entry_points_discovered", False)

        pr.discover_plugins()  # must not raise

    def test_discovery_is_idempotent(self, monkeypatch):
        """Calling discover_plugins() twice must not reload entry points the
        second time (idempotent, as required for a core that may call it from
        more than one startup path)."""
        from aitao.core import plugin_registry as pr

        calls = {"count": 0}

        class _CountingEntryPoint:
            name = "counting_plugin"

            def load(self):
                calls["count"] += 1

        def fake_entry_points(*, group=None):
            return [_CountingEntryPoint()]

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        monkeypatch.setattr(pr, "_entry_points_discovered", False)

        pr.discover_plugins()
        pr.discover_plugins()

        assert calls["count"] == 1


# ============================================================================
# 2. Core alone — no licence provider registered
# ============================================================================

def _core_only_registry():
    """A registry holding every plugin the CORE ships, and nothing from
    aitao-premium — i.e. exactly what a free-edition install sees.

    Real discovery runs first (so built-in core plugins such as the pdf/txt
    extractors are loaded even when this test runs alone), then every entry
    whose object comes from the aitao_premium package is left out. An empty
    registry would be wrong: it also removes core extractors, and a test using
    it only passed when an earlier test had already populated them."""
    from aitao.core import plugin_registry as pr

    pr.discover_plugins()
    fresh = pr.PluginRegistry()
    for kind, entries in pr.registry._by_kind.items():
        for name, entry in entries.items():
            if getattr(entry.obj, "__module__", "").startswith("aitao_premium"):
                continue
            fresh._by_kind.setdefault(kind, {})[name] = entry
    return fresh


def _no_license_provider(monkeypatch):
    """Simulate aitao-premium not being installed (or not yet discovered):
    swap the global plugin registry for a fresh, empty one so a lookup of
    ("license", "premium") is guaranteed to miss, regardless of whether the
    real package happens to be installed in this environment. Restored
    automatically by monkeypatch at the end of the test.

    Discovery is neutralised too: without that, a test run in isolation
    (before anything triggered discovery) makes the licence facade call
    discover_plugins(), which loads the installed aitao-premium entry point
    and re-registers its provider into this fresh registry — the test then
    passed only when an earlier test had already run discovery."""
    from aitao.core import plugin_registry as pr

    monkeypatch.setattr(pr, "registry", _core_only_registry())
    monkeypatch.setattr(pr, "discover_plugins", lambda *a, **kw: None)


def _make_indexer(**kwargs):
    from unittest.mock import MagicMock

    from aitao.indexation.indexer import DocumentIndexer
    from aitao.indexation.text_extractor import ExtractionResult

    extractor = MagicMock()
    extractor.extract.return_value = ExtractionResult(
        text="Some plain text content.",
        metadata={"word_count": 4, "language": "en", "file_type": "txt"},
        success=True,
    )
    meilisearch = MagicMock()
    meilisearch.index_document.return_value = "test_doc_id"
    meilisearch.get_document.return_value = None

    return DocumentIndexer(
        meilisearch_client=meilisearch,
        text_extractor=extractor,
        skip_chunking=True,
        allow_temp_paths=True,
        **kwargs,
    )


class TestItemPreparerWithoutPremiumProvider:
    """No provider is registered under ("license", "premium") — the state of
    a Core-only install with aitao-premium not installed. item_preparer.py
    must behave exactly like an unlicensed user: text formats index
    normally, advanced formats are refused before extraction."""

    def test_txt_file_indexes_normally(self, tmp_path, monkeypatch):
        _no_license_provider(monkeypatch)

        indexer = _make_indexer()
        f = tmp_path / "note.txt"
        f.write_text("Hello world.")

        result = indexer.index_file(str(f))

        assert result.success
        indexer.text_extractor.extract.assert_called_once()

    def test_docx_file_is_refused_like_an_unlicensed_user(self, tmp_path, monkeypatch):
        _no_license_provider(monkeypatch)

        indexer = _make_indexer()
        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04 dummy docx bytes")

        result = indexer.index_file(str(f))

        # Same user-facing outcome as the licensed path (see
        # TestPremiumFormatGate.test_docx_skipped_in_core_edition in
        # test_indexer.py): refused before extraction is ever attempted.
        assert not result.success
        assert "Premium format" in (result.error or "")
        indexer.text_extractor.extract.assert_not_called()


class TestChatGroundingWithoutPremiumProvider:
    """Deep verification does not depend on the licence provider at all (the
    fast Core grounding check never touches licensing either) — locked here
    so that never changes by accident."""

    def test_deep_verification_runs_without_premium_provider(self, monkeypatch):
        import aitao.api.routes.chat_grounding as grounding_mod
        from aitao.llm.answer_validator import GroundingReport, SentenceGrounding

        _no_license_provider(monkeypatch)
        monkeypatch.setattr(
            grounding_mod,
            "get_config",
            lambda: SimpleNamespace(rag=SimpleNamespace(verify_answer="deep")),
        )

        report = GroundingReport(
            grounding_score=0.8,
            weakest_score=0.8,
            sentences=[SentenceGrounding("Le préavis est de trois mois.", 0.8)],
            checked=1,
        )

        class _Doc:
            content = "Le préavis est d'un mois."

        verdicts = grounding_mod.run_deep_verification(
            report, [_Doc()], lambda messages: "1: CONTRADICTED"
        )  # must not raise

        assert len(verdicts) == 1
        assert verdicts[0].verdict == "contradicted"


# ============================================================================
# 3. OCR — no provider registered (US-138-1: providers moved to aitao-premium)
# ============================================================================

def _no_ocr_or_license_provider(monkeypatch):
    """Simulate aitao-premium not installed for OCR as well as licensing.

    Like _no_license_provider, but also covers aitao.ocr.router: that module
    does ``from aitao.core.plugin_registry import discover_plugins, registry``
    at IMPORT TIME, so it holds its own name bound to the pre-existing
    (possibly fully populated) registry/function objects — reassigning
    ``aitao.core.plugin_registry.registry`` alone (as _no_license_provider
    does) would not affect it. Both modules' bindings are pointed at the same
    fresh, empty PluginRegistry here, and discovery is neutralised on both so
    the real, installed aitao-premium package (this dev environment has it —
    see the OCR live-check step of US-138-1) cannot repopulate it mid-test,
    regardless of whether some earlier test already triggered discovery.
    """
    from aitao.core import plugin_registry as pr
    from aitao.ocr import router as router_mod

    fresh = _core_only_registry()
    monkeypatch.setattr(pr, "registry", fresh)
    monkeypatch.setattr(pr, "discover_plugins", lambda *a, **kw: None)
    monkeypatch.setattr(router_mod, "registry", fresh)
    monkeypatch.setattr(router_mod, "discover_plugins", lambda *a, **kw: None)


class TestOCRWithoutPremiumProvider:
    """No ("ocr", *) provider and no ("license", "premium") provider — the
    state of a Core-only install with aitao-premium not installed. A scanned
    file must be refused exactly like an unlicensed user, never a crash."""

    def test_router_has_zero_providers(self, monkeypatch):
        """Building the router itself never crashes with nothing registered."""
        from aitao.ocr.router import OCRRouter

        _no_ocr_or_license_provider(monkeypatch)

        router = OCRRouter(config={})
        assert router._providers == []

    def test_scanned_file_refused_like_an_unlicensed_user(self, tmp_path, monkeypatch):
        """extract() on a real, existing image must raise PremiumFeatureError
        — the same clean refusal an unlicensed Core user gets today — not a
        crash. The gate fires before any provider lookup (there is nothing to
        look up), so "no key" and "no Premium module installed" look
        identical to the caller."""
        from aitao.ocr.router import OCRRouter

        _no_ocr_or_license_provider(monkeypatch)

        png = tmp_path / "scan.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")  # content is irrelevant — never reached

        router = OCRRouter(config={})
        with pytest.raises(PremiumFeatureError):
            asyncio.run(router.extract(png))

    def test_extract_sync_also_refuses_cleanly(self, tmp_path, monkeypatch):
        """The sync wrapper used by indexer.py must enforce the same gate."""
        from aitao.ocr.router import OCRRouter

        _no_ocr_or_license_provider(monkeypatch)

        png = tmp_path / "scan.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")

        router = OCRRouter(config={})
        with pytest.raises(PremiumFeatureError):
            router.extract_sync(png)

    def test_indexer_ocr_step_degrades_without_crashing(self, tmp_path, monkeypatch):
        """The indexer's own OCR step (Step 1b) must swallow the refusal and
        keep indexing the document without OCR text — using the REAL
        router/license code path (not a mocked router, see
        test_ocr_pipeline.py::TestIndexerOCRPremiumGate for that), so a
        future regression that makes the router itself misbehave (raise a
        different exception, or crash) would not slip through unnoticed."""
        from unittest.mock import patch

        from aitao.indexation.indexer import DocumentIndexer
        from aitao.indexation.text_extractor import ExtractionResult

        _no_ocr_or_license_provider(monkeypatch)

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

        with patch("aitao.indexation.text_extractor.TextExtractor.extract", return_value=scanned):
            indexer = DocumentIndexer(skip_meilisearch=True, allow_temp_paths=True)
            result = indexer.index_file(str(fake_pdf))

        # No crash — the document is still indexed, just without OCR text.
        assert result.success is True


# ============================================================================
# 4. Office-format extractors — no provider registered (US-138-1: DOCX/PPTX/
#    XLSX/ODF extractors moved to aitao-premium)
# ============================================================================

def _no_extractor_or_license_provider(monkeypatch):
    """Simulate aitao-premium not installed for Office extraction as well as
    licensing.

    Like _no_ocr_or_license_provider, but for aitao.indexation.text_extractor:
    that module does
    ``from aitao.core.plugin_registry import discover_plugins, registry`` at
    IMPORT TIME, so it holds its own name bound to the pre-existing (possibly
    fully populated) registry/function objects — reassigning
    ``aitao.core.plugin_registry.registry`` alone would not affect it. Both
    modules' bindings are pointed at the same fresh, core-only PluginRegistry
    here, and discovery is neutralised on both so the real, installed
    aitao-premium package (this dev environment has it — see the Office
    live-check step of US-138-1) cannot repopulate it mid-test, regardless of
    whether some earlier test already triggered discovery.
    """
    from aitao.core import plugin_registry as pr
    from aitao.indexation import text_extractor as text_extractor_mod

    fresh = _core_only_registry()
    monkeypatch.setattr(pr, "registry", fresh)
    monkeypatch.setattr(pr, "discover_plugins", lambda *a, **kw: None)
    monkeypatch.setattr(text_extractor_mod, "registry", fresh)
    monkeypatch.setattr(text_extractor_mod, "discover_plugins", lambda *a, **kw: None)


class TestTextExtractorWithoutPremiumProvider:
    """No ("extractor", "docx"/"pptx"/"xlsx"/"odf") plugin registered — the
    state of a Core-only install with aitao-premium not installed. A .docx
    path must be refused cleanly (unsupported extension), never a crash —
    TextExtractor()'s constructor must not blow up trying to look up a
    plugin name from its priority order that simply isn't registered."""

    def test_text_extractor_construction_does_not_crash(self, monkeypatch):
        """_init_extractors() must skip unregistered names in _ORDER
        (docx/pptx/xlsx/odf) instead of raising PluginNotFoundError."""
        from aitao.indexation.text_extractor import TextExtractor

        _no_extractor_or_license_provider(monkeypatch)

        extractor = TextExtractor()
        extensions = extractor.get_supported_extensions()

        assert ".docx" not in extensions
        assert ".pptx" not in extensions
        assert ".xlsx" not in extensions
        assert ".odt" not in extensions
        # Core-only extractors are still there.
        assert ".txt" in extensions
        assert ".pdf" in extensions

    def test_docx_path_refused_cleanly_not_crashed(self, tmp_path, monkeypatch):
        from aitao.indexation.text_extractor import TextExtractor

        _no_extractor_or_license_provider(monkeypatch)

        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04 dummy docx bytes")

        extractor = TextExtractor()
        assert not extractor.can_extract(f)

        result = extractor.extract(f)
        assert result.success is False
        assert "Unsupported file type" in (result.error or "")

    def test_indexer_docx_file_is_refused_like_an_unlicensed_user(self, tmp_path, monkeypatch):
        """End-to-end through item_preparer.prepare_item(): the user-facing
        gate (LicenseManager.is_premium_extension) still fires on the file
        extension alone, before any extractor lookup — so this is refused the
        same way whether or not the "docx" plugin happens to be registered."""
        _no_extractor_or_license_provider(monkeypatch)

        indexer = _make_indexer()
        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04 dummy docx bytes")

        result = indexer.index_file(str(f))

        assert not result.success
        assert "Premium format" in (result.error or "")
        indexer.text_extractor.extract.assert_not_called()

    def test_cli_extract_file_gives_premium_message_not_a_crash(self, tmp_path, monkeypatch):
        """``aitao extract file some.docx`` must print the clear "requires
        AiTao Premium" message and exit cleanly (typer.Exit), never raise an
        uncaught exception — this is the CLI call site that used to import
        DOCXExtractor directly (US-138-1)."""
        from typer.testing import CliRunner

        from aitao.cli.commands import extract as extract_cmd

        _no_extractor_or_license_provider(monkeypatch)

        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04 dummy docx bytes")

        runner = CliRunner()
        result = runner.invoke(extract_cmd.app, ["file", str(f)])

        assert result.exit_code == 1
        assert "AiTao Premium" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
