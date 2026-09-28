# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for salient-term extraction and term anchoring (US-17c).

Covers:
- salient_terms: stopword removal FR/EN, numbers kept, accents handled
- text_contains_term: accent/case-insensitive anchoring check
"""

from aitao.llm.query_terms import salient_terms, text_contains_term


class TestSalientTerms:
    def test_french_question(self):
        terms = salient_terms("Quelle est la date du PRD de liaotao CLI ?")
        assert terms == ["date", "PRD", "liaotao", "CLI"]

    def test_english_question(self):
        terms = salient_terms("What is the budget of the Hyperloop project?")
        assert terms == ["budget", "Hyperloop", "project"]

    def test_years_and_numbers_kept(self):
        terms = salient_terms("Quelles sont les charges de copropriété 2031 ?")
        assert "2031" in terms
        assert "charges" in terms
        assert "copropriété" in terms

    def test_greeting_only_yields_nothing(self):
        assert salient_terms("Bonjour, merci !") == []

    def test_accented_stopwords_removed(self):
        # "très" must be recognised as a stopword despite the accent
        assert "très" not in salient_terms("C'est très important")

    def test_empty_query(self):
        assert salient_terms("") == []


class TestTextContainsTerm:
    def test_simple_match(self):
        assert text_contains_term("Contrat de location CHIANG", ["contrat"])

    def test_accent_insensitive(self):
        assert text_contains_term("Les echeances du credit", ["échéances"])
        assert text_contains_term("Les échéances du crédit", ["echeances"])

    def test_no_match(self):
        assert not text_contains_term(
            "Programmer pour les Nuls", ["hyperloop", "mars"]
        )

    def test_empty_inputs(self):
        assert not text_contains_term("", ["x"])
        assert not text_contains_term("texte", [])
