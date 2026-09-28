# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_query_distiller.py — US-89-1: query distillation keeps only salient tokens.
#
# Pure logic: the document-frequency oracle is injected, so no Meilisearch / no
# heavy deps. "Salient = rare in the index" + structured tokens always kept.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.llm.query_distiller import distill_query, structured_tokens  # noqa: E402

# A small corpus model: function words are frequent, content words are rare.
_FREQ = {
    "recherche": 80, "le": 99, "document": 90, "qui": 95, "parle": 70,
    "de": 99, "où": 85, "est": 95, "il": 95, "dans": 92, "quel": 88,
    "apparait": 60, "email": 45, "harcèlement": 3, "sexuel": 5, "version": 50,
}
_TOTAL = 100


def _df(term: str) -> int:
    return _FREQ.get(term, 0)


def _distill(q: str) -> str:
    return distill_query(q, _df, _TOTAL)


class TestStructuredTokensAlwaysKept:
    def test_cjk_run_kept_and_function_words_dropped(self):
        out = _distill("Recherche le document qui parle de 事假扣薪. Où est-il ?")
        assert out == "事假扣薪"

    def test_email_kept_whole_not_shredded(self):
        out = _distill("Dans quel document apparait l'email k3x7@acmemotor.com.tw ?")
        assert out == "k3x7@acmemotor.com.tw"
        # never split into acmemotor / com / tw
        assert "acmemotor" not in out.replace("k3x7@acmemotor.com.tw", "")

    def test_cjk_plus_rare_latin_kept(self):
        out = _distill("harcèlement sexuel 性騷擾防治管理辦法")
        assert "性騷擾防治管理辦法" in out
        assert "harcèlement" in out and "sexuel" in out

    def test_quoted_phrase_kept(self):
        out = distill_query('cherche la phrase "rupture conventionnelle" ici', _df, _TOTAL)
        assert "rupture conventionnelle" in out

    def test_numeric_code_kept(self):
        assert "20160910" in _distill("version 20160910")

    def test_structured_kept_without_oracle(self):
        # No oracle → latin words are not filtered, but structured tokens still kept.
        out = distill_query("email k3x7@acmemotor.com.tw 事假扣薪", None, 0)
        assert "k3x7@acmemotor.com.tw" in out and "事假扣薪" in out


class TestRarityFilter:
    def test_frequent_latin_dropped_rare_kept(self):
        out = _distill("le document parle de harcèlement")
        assert "harcèlement" in out
        for stop in ("le", "document", "parle", "de"):
            assert stop not in out.split()

    def test_unknown_term_treated_as_salient(self):
        # A term absent from the index (freq 0) is rare → kept.
        assert "zzzrareword" in _distill("le zzzrareword")


class TestStructuredTokens:
    """US-89-2: distinctive tokens exposed for exact-token pinning."""

    def test_email_extracted_whole(self):
        assert structured_tokens("où est k3x7@acmemotor.com.tw ?") == [
            "k3x7@acmemotor.com.tw"
        ]

    def test_cjk_run_extracted(self):
        assert "事假扣薪" in structured_tokens("le doc 事假扣薪 ?")

    def test_code_and_quoted_extracted(self):
        toks = structured_tokens('le code ABC123 et "clause de non-concurrence"')
        assert "ABC123" in toks
        assert "clause de non-concurrence" in toks

    def test_plain_question_has_no_tokens(self):
        assert structured_tokens("de quoi parle ce document ?") == []

    def test_single_ideograph_dropped_when_min_len_2(self):
        # One common ideograph is not distinctive enough to pin on.
        assert structured_tokens("的", min_nonlatin_len=2) == []
        assert structured_tokens("的") == ["的"]  # kept at default min length

    def test_short_cjk_run_kept_at_min_len_2(self):
        assert structured_tokens("事假", min_nonlatin_len=2) == ["事假"]

    def test_empty_input(self):
        assert structured_tokens("") == []
        assert structured_tokens("   ") == []


class TestFallback:
    def test_empty_input_returned_as_is(self):
        assert distill_query("", _df, _TOTAL) == ""
        assert distill_query("   ", _df, _TOTAL) == "   "

    def test_all_function_words_falls_back_to_raw(self):
        # If everything is frequent and nothing structured, never search blank.
        raw = "le document de qui est il"
        assert _distill(raw) == raw

    def test_no_oracle_returns_raw_for_plain_latin(self):
        assert distill_query("le document important", None, 0) == "le document important"
