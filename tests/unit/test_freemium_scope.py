# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the freemium scope fix (PRD §5).

The Premium boundary is the document *perimeter* — advanced formats / OCR at
ingestion — NOT the RAG/chat engine. These tests lock that invariant so the old
drift (whole RAG gated Premium) cannot silently come back:
- RAG chat is Core (no longer gated by `rag_chat`);
- advanced formats (docx/xlsx/odt/epub…) are Premium at ingestion.
"""

from aitao.core.license import LicenseManager


class TestPremiumFeatureScope:
    def test_rag_chat_is_no_longer_premium(self):
        assert "rag_chat" not in LicenseManager.PREMIUM_FEATURES

    def test_advanced_formats_is_premium(self):
        assert "advanced_formats" in LicenseManager.PREMIUM_FEATURES


class TestPremiumExtensions:
    def test_advanced_formats_flagged(self):
        for ext in (".docx", ".xlsx", ".odt", ".epub", ".pptx", ".DOCX"):
            assert LicenseManager.is_premium_extension(ext), ext

    def test_text_formats_stay_core(self):
        for ext in (".pdf", ".txt", ".md", ".log", ".ini", ".json"):
            assert not LicenseManager.is_premium_extension(ext), ext


class TestRAGEngineIsCore:
    def test_rag_engine_builds_in_core_edition(self, monkeypatch):
        # Force Core edition: RAGEngine must still build — chat is Core now,
        # it used to raise PremiumFeatureError here (the drift we are fixing).
        monkeypatch.setattr(
            "aitao.core.license.LicenseManager.is_premium", lambda self: False
        )
        from aitao.core.config import get_config
        from aitao.core.logger import get_logger
        from aitao.llm.rag_engine import RAGEngine

        engine = RAGEngine(get_config(), get_logger("test.freemium"))
        assert engine is not None
