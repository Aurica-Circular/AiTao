# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the config-question intent classifier (US-DEMO-9).

Covers:
- classify_config_intent: FR/EN phrasings → IDENTITY_SELF / IDENTITY_USER /
  INDEX_SCOPE / NONE, including accents and punctuation
- build_config_directive: produces config-grounded directives, or None
"""

from types import SimpleNamespace

import pytest

from aitao.llm.intent_classifier import (
    ConfigIntent,
    build_config_directive,
    classify_config_intent,
)


class _FakeConfig:
    """Minimal ConfigManager stand-in with typed attributes (US-22)."""

    def __init__(self, values=None, sections=None):
        self._values = values or {}
        self._sections = sections or {}
        self.identity = SimpleNamespace(
            who_are_you=self._values.get("identity.who_are_you", ""),
            who_is_aitao=self._values.get("identity.who_is_aitao", ""),
        )
        indexing_section = (self._sections.get("indexing") or {})
        self.indexing = SimpleNamespace(
            include_paths=indexing_section.get("include_paths", []),
        )

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_section(self, name):
        return self._sections.get(name)


def _config():
    return _FakeConfig(
        values={"identity.who_are_you": "Phil, un Français vivant à Taiwan."},
        sections={"indexing": {"include_paths": ["/Users/phil/Documents", "/Users/alice/Projects"]}},
    )


class TestClassifyConfigIntent:
    @pytest.mark.parametrize("text", [
        "Bonjour, qui es-tu ?",
        "Présente-toi",
        "Qui est AiTao ?",
        "qu'est-ce qu'AiTao ?",
        "Que peux-tu faire ?",
        "Who are you?",
        "what is aitao",
        "Introduce yourself",
    ])
    def test_identity_self(self, text):
        assert classify_config_intent(text) == ConfigIntent.IDENTITY_SELF

    @pytest.mark.parametrize("text", [
        "et moi, qui suis-je ?",
        "Super, qui suis je ?",
        "Que sais-tu de moi ?",
        "parle-moi de moi",
        "mon profil",
        "Et toi tu connais mon nom ?",
        "comment je m'appelle ?",
        "quel est mon nom ?",
        "who am I?",
        "what do you know about me",
        "what's my name?",
    ])
    def test_identity_user(self, text):
        assert classify_config_intent(text) == ConfigIntent.IDENTITY_USER

    @pytest.mark.parametrize("text", [
        "Quels volumes peux-tu indexer ?",
        "Donne-moi la liste des volumes que AiTao peut indexer",
        "Quels dossiers indexes-tu ?",
        "Quelle est ta configuration ?",
        "which folders can you index?",
        "what can you index",
        "where do you search?",
    ])
    def test_index_scope(self, text):
        assert classify_config_intent(text) == ConfigIntent.INDEX_SCOPE

    @pytest.mark.parametrize("text", [
        "Quel jour sommes-nous ?",
        "Bonjour, quel jour sommes-nous ?",
        "Quel jour de la semaine sommes-nous ?",
        "on est quel jour de la semaine ?",
        "on est quel jour ?",
        "Quelle est la date d'aujourd'hui ?",
        "Quelle est la date ?",
        "quelle heure est-il ?",
        "What day is it?",
        "what is today's date",
        "What time is it?",
        "quelle version d'AiTao utilises-tu ?",
    ])
    def test_temporal(self, text):
        assert classify_config_intent(text) == ConfigIntent.TEMPORAL

    @pytest.mark.parametrize("text", [
        "Quelles sont les échéances du contrat ?",
        "Quelle date figure dans le contrat de bail ?",
        "à quelle date le document a-t-il été signé ?",
        # Regression (demo 2026-06-11): a document-date question must reach RAG
        "Quelle est la date du PRD de liaotao CLI ?",
        "quelle est la date de la facture EDF ?",
        "What is the date of the contract?",
        "Résume ce document",
        "Bonjour",
        "",
    ])
    def test_none(self, text):
        assert classify_config_intent(text) == ConfigIntent.NONE

    def test_user_identity_takes_priority_over_self(self):
        # "qui suis-je" must not be swallowed by a self-identity pattern
        assert classify_config_intent("qui suis-je") == ConfigIntent.IDENTITY_USER


class TestBuildConfigDirective:
    def test_none_for_regular_question(self):
        assert build_config_directive("Résume ce document", _config()) is None

    def test_identity_self_directive(self):
        directive = build_config_directive("qui es-tu ?", _config())
        assert directive is not None
        assert "WHO YOU ARE" in directive
        assert "not the user" in directive.lower()

    def test_identity_user_directive_includes_profile(self):
        directive = build_config_directive("qui suis-je ?", _config())
        assert directive is not None
        assert "Phil" in directive
        assert "do not describe yourself" in directive.lower()

    def test_identity_user_returns_none_without_profile(self):
        empty = _FakeConfig(values={})
        assert build_config_directive("qui suis-je ?", empty) is None

    def test_index_scope_directive_lists_paths(self):
        directive = build_config_directive("quels volumes peux-tu indexer ?", _config())
        assert directive is not None
        assert "/Users/phil/Documents" in directive
        assert "/Users/alice/Projects" in directive
        assert "do not generalize" in directive.lower()

    def test_index_scope_returns_none_without_paths(self):
        empty = _FakeConfig(sections={"indexing": {"include_paths": []}})
        assert build_config_directive("quels dossiers ?", empty) is None

    def test_temporal_directive_has_live_date_and_no_citation_rule(self):
        from datetime import datetime

        directive = build_config_directive("Quel jour sommes-nous ?", _config())
        assert directive is not None
        assert datetime.now().strftime("%Y-%m-%d") in directive
        assert "do not cite any source" in directive.lower()

    def test_temporal_directive_survives_greeting_prefix(self):
        directive = build_config_directive("Bonjour, quel jour sommes-nous ?", _config())
        assert directive is not None
        assert "SYSTEM FACTS" in directive
