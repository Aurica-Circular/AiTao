# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_language_tags.py — US-85c: canonical language-code normalization.
#
# Guards the contract the `--language` filter now depends on: native (langdetect),
# Apple Vision (BCP-47) and Tesseract (ISO 639-2/T) notations must all collapse
# to the SAME canonical short code so a single filter value matches every source.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.core.language_tags import (  # noqa: E402
    normalize_language,
    normalize_language_query,
)


class TestNormalizeLanguage:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # French across the three notations
            ("fr", "fr"), ("fr-FR", "fr"), ("fr-CA", "fr"), ("fra", "fr"),
            # English
            ("en", "en"), ("en-US", "en"), ("en-GB", "en"), ("eng", "en"),
            # Chinese: langdetect (zh-tw/zh-cn), Apple (zh-Hant/zh-Hans),
            # Tesseract (chi_tra/chi_sim), Cantonese — all fold to "zh"
            ("zh-tw", "zh"), ("zh-cn", "zh"), ("zh-Hant", "zh"),
            ("zh-Hans", "zh"), ("chi_tra", "zh"), ("chi_sim", "zh"),
            ("yue-Hant", "zh"),
            # Others
            ("ja", "ja"), ("jpn", "ja"), ("ko", "ko"), ("kor", "ko"),
            ("de-DE", "de"), ("deu", "de"), ("ar", "ar"), ("ara", "ar"),
        ],
    )
    def test_canonical_short_code(self, raw, expected):
        assert normalize_language(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "zzz", "unknown"])
    def test_empty_or_unknown_multichar_is_unknown(self, raw):
        assert normalize_language(raw) == "unknown"

    @pytest.mark.parametrize("raw,expected", [("sv", "sv"), ("nl", "nl"), ("tr", "tr")])
    def test_unmapped_two_letter_codes_pass_through(self, raw, expected):
        # langdetect emits many ISO 639-1 codes we don't explicitly map; keeping
        # them (rather than folding to "unknown") preserves the filter for those
        # languages. Only 3+ letter unknowns become "unknown".
        assert normalize_language(raw) == expected

    def test_multi_candidate_tesseract_pass_is_unknown(self):
        # A '+'-joined Tesseract pass has no single detected language.
        assert normalize_language("fra+eng+chi_tra") == "unknown"
        assert normalize_language("fra+eng") == "unknown"

    def test_multi_candidate_collapsing_to_one_language_keeps_it(self):
        # Same language expressed twice (e.g. two Chinese packs) is still "zh".
        assert normalize_language("chi_tra+chi_sim") == "zh"


class TestNormalizeLanguageQuery:
    def test_empty_means_no_filter(self):
        assert normalize_language_query(None) is None
        assert normalize_language_query("") is None
        assert normalize_language_query("  ") is None

    @pytest.mark.parametrize(
        "raw,expected",
        [("zh-TW", "zh"), ("FR", "fr"), ("en-US", "en"), ("chi_tra", "zh")],
    )
    def test_precise_input_is_normalized(self, raw, expected):
        assert normalize_language_query(raw) == expected

    def test_literal_unknown_is_preserved(self):
        # Users may deliberately filter documents of undetermined language.
        assert normalize_language_query("unknown") == "unknown"

    def test_unrecognised_input_passes_through_lowercased(self):
        # A typo must match no document (empty result), not silently return
        # every "unknown"-tagged document.
        assert normalize_language_query("Klingon") == "klingon"


class TestSearchFilterNormalizesLanguage:
    """The SearchFilter model (shared by CLI and API) normalizes on construction."""

    def test_filter_normalizes_precise_tag(self):
        from aitao.search.search_models import SearchFilter

        assert SearchFilter(language="zh-Hant").language == "zh"
        assert SearchFilter(language="fr-FR").language == "fr"
        assert SearchFilter(language=None).language is None
