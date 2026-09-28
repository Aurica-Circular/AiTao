# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_cjk_glue.py — ÉPIC-31 US-111 (absorbs backlog 89-6): content-side CJK
# gap gluing at ingestion. Mirrors the cases enumerated in the US brief:
# a spaced ideograph pair, an intact FR/EN sentence, a real newline between
# two CJK sentences (preserved), a genuine vertical-text column (glued), and
# mixed latin/CJK content.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.core.cjk_glue import glue_cjk_content  # noqa: E402


class TestSameLineGap:
    def test_single_space_glued(self):
        assert glue_cjk_content("承 擔") == "承擔"

    def test_tab_glued(self):
        assert glue_cjk_content("承\t擔") == "承擔"

    def test_multiple_spaces_glued(self):
        assert glue_cjk_content("部   門") == "部門"

    def test_run_of_spaced_ideographs_all_glued(self):
        assert glue_cjk_content("粒 米 女 性") == "粒米女性"


class TestLatinUntouched:
    def test_french_sentence_intact(self):
        text = "Le rapport doit être remis avant vendredi."
        assert glue_cjk_content(text) == text

    def test_english_sentence_intact(self):
        text = "The quick brown fox jumps over the lazy dog."
        assert glue_cjk_content(text) == text


class TestNewlineBetweenSentencesPreserved:
    def test_two_full_cjk_sentences_keep_their_break(self):
        # Each line holds a whole clause (> 1 ideograph) — a real sentence
        # break, not a vertical-text column. Rule (b) must not touch it.
        text = "你好嗎\n我很好"
        assert glue_cjk_content(text) == text

    def test_short_run_below_threshold_not_glued(self):
        # Only 2 consecutive single-ideograph lines: below the >=3
        # threshold, so this is left as a normal (possibly coincidental)
        # line break rather than assumed to be a vertical column.
        text = "部\n門"
        assert glue_cjk_content(text) == text


class TestVerticalColumnGlued:
    def test_three_consecutive_single_ideograph_lines_glued(self):
        text = "部\n門\n管"
        assert glue_cjk_content(text) == "部門管"

    def test_four_consecutive_single_ideograph_lines_with_context(self):
        text = "標題說明\n部\n門\n管\n理\n結尾備註"
        assert glue_cjk_content(text) == "標題說明\n部門管理\n結尾備註"

    def test_whitespace_padded_column_lines_still_glued(self):
        # OCR sometimes pads each vertical glyph with leading/trailing
        # spaces — stripped before the single-ideograph check.
        text = " 部 \n 門 \n 管 "
        assert glue_cjk_content(text) == "部門管"


class TestMixedContent:
    def test_latin_and_cjk_gap_in_same_paragraph(self):
        text = "Le rapport indique que 承 擔 est requis avant lundi."
        assert glue_cjk_content(text) == (
            "Le rapport indique que 承擔 est requis avant lundi."
        )

    def test_mixed_paragraph_with_real_sentence_break_preserved(self):
        # Rule (a) glues EVERY same-line space between two CJK ideographs
        # (normal Chinese prose has none), regardless of which pair looks
        # like "the word" — only the newline (a genuine sentence/language
        # boundary here) is left untouched.
        text = "報告顯示 承 擔 的責任。\nLe rapport est clair."
        assert glue_cjk_content(text) == (
            "報告顯示承擔的責任。\nLe rapport est clair."
        )


class TestEdgeCases:
    def test_empty_string(self):
        assert glue_cjk_content("") == ""

    def test_none_passthrough(self):
        assert glue_cjk_content(None) is None

    def test_idempotent(self):
        text = "標題\n部\n門\n管\n理\n結尾"
        once = glue_cjk_content(text)
        assert glue_cjk_content(once) == once
