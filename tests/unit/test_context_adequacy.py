# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the context adequacy gate (US-DEMO-10).

A factual question with no relevant local document must be refused (so the
model cannot invent an answer), while config questions and small talk must
never be refused.
"""

import pytest

from aitao.llm.context_adequacy import (
    REFUSAL_MESSAGE,
    SOURCE_CONFIG,
    SOURCE_DOCS,
    SOURCE_NONE,
    SOURCE_SESSION,
    context_source,
    evaluate_refusal,
    is_conversational,
    needs_document_retrieval,
)


class TestIsConversational:
    @pytest.mark.parametrize("text", [
        "Bonjour",
        "Salut, ça va ?",
        "Merci beaucoup",
        "ok super",
        "hello",
        "au revoir",
    ])
    def test_true(self, text):
        assert is_conversational(text) is True

    @pytest.mark.parametrize("text", [
        "Bonjour AiTao !",
        "Salut mon ami",
        "hello there",
    ])
    def test_greeting_with_short_tail_is_still_conversational(self, text):
        assert is_conversational(text) is True

    @pytest.mark.parametrize("text", [
        "Quelles sont les échéances du contrat de location signé en mars ?",
        "Résume le rapport annuel de la société",
        "facture EDF",
        "le budget",
    ])
    def test_false(self, text):
        assert is_conversational(text) is False

    # US-17b — a greeting prefix must not mask the real question
    @pytest.mark.parametrize("text", [
        "Bonjour, quelles sont les échéances du contrat ?",
        "Salut, résume le rapport annuel",
        "Bonjour, où est la facture EDF ?",
        "Hello, what are the contract deadlines?",
        "coucou, combien de documents sont indexés ?",
    ])
    def test_greeting_prefix_does_not_mask_question(self, text):
        assert is_conversational(text) is False

    # US-89-4 — a CJK question normalises to "" (ASCII-oriented _normalize) and
    # would be mistaken for small talk, bypassing the refusal gate entirely.
    @pytest.mark.parametrize("text", [
        "粒米女性經理人聯誼會",
        "事假扣薪",
        "外籍從業人員管理辦法",
        "歲末感恩暨迎新年",
    ])
    def test_cjk_question_is_not_conversational(self, text):
        assert is_conversational(text) is False

    @pytest.mark.parametrize("text", ["你好", "謝謝"])
    def test_short_cjk_greeting_stays_conversational(self, text):
        assert is_conversational(text) is True


class TestEvaluateRefusal:
    def test_refuses_factual_without_docs(self):
        assert evaluate_refusal(
            "Quelles sont les charges de copropriété 2025 ?", []
        ) == REFUSAL_MESSAGE

    def test_no_refusal_with_docs(self):
        assert evaluate_refusal("Quelles sont les charges ?", [object()]) is None

    def test_no_refusal_for_config_question(self):
        assert evaluate_refusal("qui es-tu ?", []) is None
        assert evaluate_refusal("quels volumes peux-tu indexer ?", []) is None
        assert evaluate_refusal("qui suis-je ?", []) is None

    def test_no_refusal_for_greeting(self):
        assert evaluate_refusal("Bonjour, ça va ?", []) is None

    def test_no_refusal_when_session_files(self):
        assert evaluate_refusal("Résume le document", [], has_session=True) is None

    def test_refuses_greeting_plus_factual_without_docs(self):
        # US-17b — "Bonjour" must not bypass the adequacy gate
        assert evaluate_refusal(
            "Bonjour, quelles sont les charges de copropriété 2025 ?", []
        ) == REFUSAL_MESSAGE


class TestWeakContextReformulation:
    """US-17c — uniformly weak retrieval asks for a reformulation."""

    @staticmethod
    def _doc(score, title="Un document"):
        from types import SimpleNamespace

        return SimpleNamespace(score=score, title=title, path="/tmp/doc.md")

    def test_all_weak_docs_ask_reformulation(self):
        docs = [self._doc(0.45, "Rapport A"), self._doc(0.40, "Note B")]
        message = evaluate_refusal("Quel est le budget du projet Zeta ?", docs)
        assert message is not None
        assert "reformuler" in message.lower()
        assert "Rapport A" in message
        assert "Note B" in message

    def test_one_strong_doc_passes(self):
        docs = [self._doc(0.96), self._doc(0.40)]
        assert evaluate_refusal("Quel est le budget ?", docs) is None

    def test_score_none_is_conservative(self):
        docs = [self._doc(None)]
        assert evaluate_refusal("Quel est le budget ?", docs) is None

    def test_weak_gate_skipped_for_conversational(self):
        docs = [self._doc(0.1)]
        assert evaluate_refusal("Bonjour, ça va ?", docs) is None


class TestNeedsDocumentRetrieval:
    @pytest.mark.parametrize("text", [
        "Quelles sont les échéances du contrat ?",
        "Résume le rapport annuel",
        "Où est le document sur le voyage en Allemagne ?",
    ])
    def test_factual_needs_retrieval(self, text):
        assert needs_document_retrieval(text) is True

    @pytest.mark.parametrize("text", [
        "qui es-tu ?",
        "qui suis-je ?",
        "quels volumes peux-tu indexer ?",
        "Bonjour, ça va ?",
        "merci",
        "Quel jour sommes-nous ?",
        "Bonjour, quel jour sommes-nous ?",
        "what time is it?",
    ])
    def test_config_or_smalltalk_skips_retrieval(self, text):
        assert needs_document_retrieval(text) is False

    def test_greeting_plus_factual_triggers_retrieval(self):
        # US-17b — the question behind the greeting must reach the search
        assert needs_document_retrieval(
            "Bonjour, quelles sont les échéances du contrat ?"
        ) is True


class TestContextSource:
    def test_config(self):
        assert context_source("qui suis-je ?", []) == SOURCE_CONFIG

    def test_docs(self):
        assert context_source("question factuelle", [object()]) == SOURCE_DOCS

    def test_session(self):
        assert context_source("question", [], has_session=True) == SOURCE_SESSION

    def test_none(self):
        assert context_source("question sans reponse locale", []) == SOURCE_NONE
