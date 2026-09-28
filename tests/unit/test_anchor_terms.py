# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_anchor_terms.py — US-89-4: CJK-aware anchoring units for the adequacy gate.
#
# salient_terms returns a whole CJK run ('事假扣薪'); a document discussing the
# topic rarely contains that compound verbatim (it has 事假 and 扣薪 separately).
# anchor_terms adds character bigrams so the gate can anchor on the parts.
# Pure logic, no heavy deps.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.llm.query_terms import anchor_terms, salient_terms, text_contains_term  # noqa: E402


class TestAnchorTermsCJK:
    def test_cjk_run_decomposed_into_bigrams(self):
        terms = anchor_terms("事假扣薪")
        assert "事假扣薪" in terms          # whole run kept
        assert "事假" in terms and "扣薪" in terms  # parts added
        assert "假扣" in terms

    def test_short_cjk_run_kept_as_is(self):
        assert anchor_terms("迎新") == ["迎新"]  # 2 chars → the run itself

    def test_kangxi_and_spaced_cjk_normalised(self):
        # ⽶ (U+2F76 Kangxi) folds to 米; spaces between ideographs are glued.
        terms = anchor_terms("粒 ⽶ ⼥ 性")
        assert "粒米" in terms and "米女" in terms and "女性" in terms

    def test_latin_query_unchanged(self):
        assert anchor_terms("le contrat de Jean Dupont") == ["contrat", "Jean", "Dupont"]

    def test_mixed_latin_and_cjk(self):
        terms = anchor_terms("le document 事假扣薪")
        assert "document" in terms
        assert "事假" in terms and "扣薪" in terms


class TestAnchoringBehaviour:
    """The reason anchor_terms exists: distinguish on-topic from off-topic docs."""

    def test_doc_with_parts_anchors(self):
        # 薪資管理辦法-like content: discusses 事假 and 扣薪 separately.
        content = "第五條 事假 期間之 工資 按 比例 扣薪 計算"
        assert text_contains_term(content, anchor_terms("事假扣薪")) is True

    def test_offtopic_doc_does_not_anchor(self):
        # 外籍從業人員-like content: foreign-worker management, no 事假/扣薪.
        content = "外籍 從業 人員 之 居留 與 工作 許可 管理"
        assert text_contains_term(content, anchor_terms("事假扣薪")) is False

    def test_event_doc_anchors_on_bigrams(self):
        content = "親愛的家長 歲未感恩暨\n迎新年 活動 邀請"
        assert text_contains_term(content, anchor_terms("歲未感恩暨迎新年")) is True


class TestSalientUnchanged:
    def test_salient_terms_still_returns_whole_cjk_run(self):
        # anchor_terms is additive; salient_terms (used by pinning) is untouched.
        assert salient_terms("事假扣薪") == ["事假扣薪"]
