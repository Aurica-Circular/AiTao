# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the mojibake detector (US-086 v5).

It must catch garbled native-PDF text (a broken-font export whose code points are
wrong) while leaving legitimate mono- and multilingual documents untouched. The
detection is relative to the declared language universe: text in an unexpected
script is the signal; declared scripts (and Latin) are always fine.
"""

from aitao.indexation.mojibake import (
    contains_any_script,
    expected_scripts_for,
    looks_like_mojibake,
    mojibake_ratio,
)

# A garbled token like the real-world case (Latin + stray Hangul).
GARBLED = "JENANG최겠합친 "
FRENCH = "Le notaire atteste le contenu du document avec une grande rigueur. "
CHINESE = "粒米女性經理人聯誼會的文件內容與會議紀錄非常重要。"


class TestExpectedScripts:
    def test_latin_always_present(self):
        assert expected_scripts_for([]) == {"LATIN"}
        assert "LATIN" in expected_scripts_for(["zh-Hant"])

    def test_chinese_adds_cjk(self):
        assert expected_scripts_for(["fr", "en", "zh-Hant"]) == {"LATIN", "CJK"}

    def test_korean_adds_hangul(self):
        assert expected_scripts_for(["ko"]) == {"LATIN", "HANGUL"}

    def test_unknown_language_ignored(self):
        assert expected_scripts_for(["klingon"]) == {"LATIN"}


class TestMojibakeRatio:
    def test_pure_latin_is_zero(self):
        ratio, letters = mojibake_ratio(FRENCH * 5, {"LATIN"})
        assert ratio == 0.0
        assert letters > 0

    def test_chinese_is_clean_when_cjk_expected(self):
        ratio, _ = mojibake_ratio(CHINESE * 10, {"LATIN", "CJK"})
        assert ratio == 0.0

    def test_hangul_unexpected_for_fr_zh_corpus(self):
        ratio, _ = mojibake_ratio(GARBLED * 40, {"LATIN", "CJK"})
        # 4 of every 10 letters are stray Hangul -> well above the 0.20 floor.
        assert ratio >= 0.4

    def test_digits_and_punctuation_ignored(self):
        ratio, letters = mojibake_ratio("123 ... !!! 456", {"LATIN"})
        assert letters == 0
        assert ratio == 0.0


class TestLooksLikeMojibake:
    def test_legit_french_not_flagged(self):
        assert looks_like_mojibake(FRENCH * 10, {"LATIN"}) is False

    def test_legit_chinese_not_flagged(self):
        assert looks_like_mojibake(CHINESE * 20, {"LATIN", "CJK"}) is False

    def test_garbled_hangul_flagged(self):
        # Enough letters + mostly-unexpected script -> garbled.
        assert looks_like_mojibake(GARBLED * 60, {"LATIN", "CJK"}) is True

    def test_short_garbled_not_flagged(self):
        # Below the min-letters floor we stay silent (avoid false positives).
        assert looks_like_mojibake(GARBLED, {"LATIN", "CJK"}) is False

    def test_no_expected_scripts_disables_detection(self):
        assert looks_like_mojibake(GARBLED * 60, None) is False
        assert looks_like_mojibake(GARBLED * 60, set()) is False

    def test_empty_text(self):
        assert looks_like_mojibake("", {"LATIN"}) is False


class TestContainsAnyScript:
    def test_cjk_present(self):
        assert contains_any_script("Bonjour " + CHINESE, {"CJK"}) is True

    def test_cjk_absent_in_latin_and_digits(self):
        # The Taiwan scan case: Latin + digits only, no CJK though it was expected.
        assert contains_any_script("Ref 404 tel 04-22583988 nhi gov tw", {"CJK"}) is False

    def test_digits_and_symbols_are_neutral(self):
        assert contains_any_script("404 61 84 — #*•", {"CJK", "HANGUL"}) is False

    def test_empty_or_no_scripts(self):
        assert contains_any_script("", {"CJK"}) is False
        assert contains_any_script(CHINESE, set()) is False
