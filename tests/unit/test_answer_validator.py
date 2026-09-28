# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for answer_validator (US-076, phase 1 — deterministic grounding).

A fake bag-of-words embedder is injected, so these tests run fast and never load
bge-m3: sentences sharing vocabulary with a context score high, disjoint ones
score zero. This keeps the suite isolated (no global model stubbing).
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Sequence

import pytest

from aitao.llm.answer_validator import (
    GROUNDING_THRESHOLD,
    GroundingReport,
    build_grounding_warning,
    evaluate_grounding,
    split_sentences,
)

# Fixed vocabulary for the deterministic fake embedder.
_VOCAB = [
    "préavis", "mois", "bail", "loyer", "résiliation",
    "chat", "lune", "pizza", "vélo", "montagne",
]


def _fake_embed(texts: Sequence[str]) -> List[List[float]]:
    """Bag-of-words one-hot over _VOCAB — deterministic, no model needed."""
    out: List[List[float]] = []
    for text in texts:
        low = text.lower()
        out.append([1.0 if word in low else 0.0 for word in _VOCAB])
    return out


@dataclass
class _Doc:
    """Minimal stand-in for a ContextDocument (only .content is read)."""

    content: str


def _ctx(*contents: str) -> List[_Doc]:
    return [_Doc(c) for c in contents]


class TestSplitSentences:
    def test_splits_latin_punctuation(self):
        sents = split_sentences("Le bail court. Le préavis est court ! Vraiment ?")
        assert len(sents) == 3

    def test_splits_on_newlines(self):
        assert len(split_sentences("Ligne une\nLigne deux\n\nLigne trois")) == 3

    def test_splits_cjk_terminators(self):
        assert len(split_sentences("合同有效。租金每月支付！")) == 2

    def test_empty_text_yields_no_sentences(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []


class TestEvaluateGrounding:
    def test_supported_sentence_scores_high(self):
        answer = "Le préavis du bail est de trois mois."
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 1
        assert report.grounding_score > GROUNDING_THRESHOLD
        assert report.unsupported == []

    def test_offtopic_sentence_is_flagged(self):
        answer = "Le chat dort sur la lune en mangeant 3 pizzas."
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 1
        assert report.grounding_score < GROUNDING_THRESHOLD
        assert len(report.unsupported) == 1

    def test_mixed_answer_flags_only_unsupported(self):
        answer = (
            "Le préavis du bail est de trois mois. "
            "Le loyer augmente de 200 euros chaque montagne à vélo."
        )
        report = evaluate_grounding(
            answer,
            _ctx("Le bail fixe le préavis et le loyer mensuel."),
            _fake_embed,
        )
        assert report.checked == 2
        assert len(report.unsupported) == 1
        assert "vélo" in report.unsupported[0]

    def test_empty_answer_is_perfectly_grounded(self):
        report = evaluate_grounding("", _ctx("anything"), _fake_embed)
        assert report.grounding_score == 1.0
        assert report.checked == 0
        assert report.unsupported == []

    def test_no_context_flags_every_sentence(self):
        answer = "Le préavis du bail est de trois mois."
        report = evaluate_grounding(answer, _ctx(), _fake_embed)
        assert report.grounding_score == 0.0
        assert report.unsupported == [answer.strip()]

    def test_short_fragments_are_not_checked(self):
        # "Bonjour." and "Voici :" are below MIN_SENTENCE_CHARS -> skipped.
        report = evaluate_grounding(
            "Bonjour. Voici :", _ctx("Le bail prévoit un préavis."), _fake_embed
        )
        assert report.checked == 0
        assert report.grounding_score == 1.0

    def test_latency_is_measured(self):
        report = evaluate_grounding(
            "Le préavis du bail est de trois mois.",
            _ctx("Le bail prévoit un préavis de trois mois."),
            _fake_embed,
        )
        assert report.elapsed_ms >= 0.0

    def test_appended_notice_lines_are_skipped(self):
        # The validator must not re-flag AiTao's own notice lines.
        answer = (
            "Le préavis du bail est de trois mois.\n"
            "📎 Cette information apparaît dans 2 documents :"
        )
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 1


class TestFactBearingOnly:
    """US-092: free paraphrase is not grounded — only factual details are."""

    def test_paraphrase_without_figures_is_not_checked(self):
        # A good summary reformulates: low overlap with any single chunk used
        # to raise a false alarm. Without a number/date/amount the sentence is
        # now skipped entirely — no banner on a correct summary.
        answer = (
            "Le document décrit la santé générale du chat sur la montagne "
            "et sa préférence pour la pizza au clair de lune."
        )
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 0
        assert report.unsupported == []
        assert report.grounding_score == 1.0

    def test_fact_bearing_offtopic_is_still_flagged(self):
        # The net still catches what actually hurts: an unfounded figure.
        answer = "Le chat a mangé 42 pizzas sur la lune."
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 1
        assert len(report.unsupported) == 1

    def test_spelled_out_french_number_counts_as_fact(self):
        answer = "Le préavis du bail est de trois mois."
        report = evaluate_grounding(
            answer, _ctx("Le bail prévoit un préavis de trois mois."), _fake_embed
        )
        assert report.checked == 1


class TestVerbatimDigitRule:
    """US-092: digit-bearing sentences are judged by verbatim digit presence,
    not embedding similarity — fixes both the multi-chunk condensation false
    alarm and the flipped-figure miss."""

    def test_digits_condensed_from_two_chunks_are_not_flagged(self):
        # A correct summary sentence merging figures from two different chunks
        # has a low best-single-chunk embedding score — it must NOT be flagged
        # as long as every figure exists somewhere in the context.
        answer = "La société fondée en 1988 emploie 2 400 personnes."
        report = evaluate_grounding(
            answer,
            _ctx(
                "Créée en 1988 à Taipei, la société fabrique du verre.",
                "L'effectif total est de 2 400 employés.",
            ),
            _fake_embed,  # shares no vocab -> embedding score is 0
        )
        assert report.checked == 1
        assert report.unsupported == []

    def test_flipped_figure_is_flagged_despite_high_similarity(self):
        # Same vocabulary (high embedding score) but a wrong figure: the old
        # embedding rule let it pass, the verbatim rule catches it.
        answer = "Le loyer du bail est fixé à 999 euros par mois."
        report = evaluate_grounding(
            answer,
            _ctx("Le loyer du bail est fixé à 1 200 euros par mois."),
            _fake_embed,  # shares loyer/bail/mois -> high similarity
        )
        assert report.checked == 1
        assert len(report.unsupported) == 1

    def test_number_formatting_differences_do_not_mismatch(self):
        # "1200" in the answer vs "1 200" in the source: both normalise to the
        # same token, so no false alarm on formatting.
        answer = "Le loyer du bail est fixé à 1200 euros par mois."
        report = evaluate_grounding(
            answer,
            _ctx("Le loyer du bail est fixé à 1 200 euros par mois."),
            _fake_embed,
        )
        assert report.unsupported == []

    def test_short_digit_covered_by_longer_context_figure(self):
        # Conservative containment: "8" (a percentage) is covered by "1988" —
        # short digits never raise a false alarm on their own.
        answer = "La croissance du loyer du bail atteint 8 pour cent par mois."
        report = evaluate_grounding(
            answer,
            _ctx("Le loyer du bail, créé en 1988, augmente chaque mois."),
            _fake_embed,
        )
        assert report.unsupported == []


class TestTitlePathDigitTokens:
    """US-127: a digit that lives only in a cited document's title/path (a
    version number or date baked into the filename) is not an LLM invention —
    the verbatim digit rule must also look at title+path, not just content.

    ``roles=[...]`` forces the "affirmation" role on every sentence so these
    tests exercise the digit-token fix itself, independently of
    response_reader's separate citation/echo_metadata skip (which, for some
    phrasings, could otherwise mask a broken fix).
    """

    def _affirmation(self, sentence: str):
        return [SimpleNamespace(sentence=sentence, role="affirmation")]

    def test_version_number_in_title_is_not_flagged(self):
        # Exact bug scenario: the version "13.4" and date "20250312" live in
        # the cited document's title, never in the retrieved chunk content.
        @dataclass
        class _DocMeta:
            path: str
            title: str
            content: str

        doc = _DocMeta(
            path="/docs/國外出差管理辦法第13.4版-20250312.pdf",
            title="國外出差管理辦法第13.4版-20250312",
            content="出差人員應於出發前填寫申請表並取得主管核准。",
        )
        answer = "根據國外出差管理辦法第13.4版-20250312，員工出差前應取得主管核准。"
        report = evaluate_grounding(
            answer, [doc], _fake_embed, roles=self._affirmation(answer)
        )
        assert report.checked == 1
        assert report.unsupported == []
        assert build_grounding_warning(report) == ""

    def test_amount_absent_from_title_and_content_is_still_flagged(self):
        # Non-regression: a genuinely invented figure — nowhere in content NOR
        # in title/path — must still be caught.
        @dataclass
        class _DocMeta:
            path: str
            title: str
            content: str

        doc = _DocMeta(
            path="/docs/rapport_depenses.pdf",
            title="rapport_depenses",
            content="Les frais de déplacement sont remboursés sur justificatif.",
        )
        answer = "Le plafond de remboursement est fixé à 5 000 euros."
        report = evaluate_grounding(
            answer, [doc], _fake_embed, roles=self._affirmation(answer)
        )
        assert report.checked == 1
        assert report.unsupported == [answer.strip()]
        assert "Fiabilité" in build_grounding_warning(report)

    def test_digit_in_content_is_still_verified_as_before(self):
        # A figure genuinely present in the chunk content keeps working,
        # regardless of what the title/path also contain.
        @dataclass
        class _DocMeta:
            path: str
            title: str
            content: str

        doc = _DocMeta(
            path="/docs/rapport_v2.pdf",
            title="rapport_v2",
            content="Le plafond de remboursement est fixé à 5 000 euros.",
        )
        answer = "Le plafond de remboursement est fixé à 5 000 euros."
        report = evaluate_grounding(
            answer, [doc], _fake_embed, roles=self._affirmation(answer)
        )
        assert report.checked == 1
        assert report.unsupported == []


class TestBuildGroundingWarning:
    def test_empty_when_all_supported(self):
        report = GroundingReport(grounding_score=0.9, weakest_score=0.8)
        assert build_grounding_warning(report) == ""

    def test_lists_unsupported_claims(self):
        report = GroundingReport(
            grounding_score=0.4,
            weakest_score=0.1,
            unsupported=["Le loyer triple chaque année."],
        )
        warning = build_grounding_warning(report)
        assert "Fiabilité" in warning
        assert "Le loyer triple chaque année." in warning
        assert "1 détail chiffré" in warning  # singular
        assert "n'a pas été retrouvé" in warning  # softened wording (US-092)

    def test_caps_listed_and_counts_the_rest(self):
        report = GroundingReport(
            grounding_score=0.2,
            weakest_score=0.0,
            unsupported=[f"Affirmation numéro {i} non soutenue." for i in range(5)],
        )
        warning = build_grounding_warning(report, max_listed=3)
        assert "5 détails chiffrés" in warning
        assert "et 2 autres" in warning


class TestCJKChecking:
    def test_short_cjk_sentence_is_checked(self):
        # US-076 ph.2: a dense Chinese clause (< 20 chars) must still be grounded,
        # the latin MIN_SENTENCE_CHARS floor must not silently skip it.
        report = evaluate_grounding(
            "外籍員工的試用期為三個月。", _ctx("試用期 三個月"), _fake_embed
        )
        assert report.checked == 1


class TestRoleAwareGrounding:
    """US-104 — evaluate_grounding classifies roles lazily (roles=None) and
    skips notice/echo_metadata/habillage sentences (I-10)."""

    def test_ordinal_habillage_produces_no_banner(self):
        # I-10a — "premier" is a French number word (_FR_NUMBER_WORDS) but
        # this sentence is dressing around the answer's own shape.
        report = evaluate_grounding(
            "C'est le premier document du contexte.",
            _ctx("Le bail prévoit un préavis de trois mois."),
            _fake_embed,
        )
        assert report.checked == 0
        assert report.unsupported == []
        assert build_grounding_warning(report) == ""

    def test_echoed_title_produces_no_banner(self):
        # I-10b — 百 (CJK numeral) lives inside a document title echoed back
        # verbatim, not a fact asserted from the document's content.
        @dataclass
        class _DocMeta:
            path: str
            title: str
            content: str

        doc = _DocMeta(
            path="/x/zh_glass.md", title="zh_glass",
            content="# 百年淬鍊：範例玻璃股份有限公司\n\n創立於 1923 年。",
        )
        report = evaluate_grounding(
            "Le fichier s'intitule 百年淬鍊：範例玻璃股份有限公司.",
            [doc],
            _fake_embed,
        )
        assert report.checked == 0
        assert report.unsupported == []

    def test_plain_affirmation_is_still_checked(self):
        # I-09 must stay fixed: role-awareness must not touch real claims.
        report = evaluate_grounding(
            "Le chat pèse 3 kilos.", _ctx("Le chat dort sur le canapé."), _fake_embed,
        )
        assert report.checked == 1
        assert report.unsupported  # "3" is nowhere in the context

    def test_explicit_roles_are_reused_not_reclassified(self):
        from aitao.llm.response_reader import classify_sentences

        sentences = split_sentences("Le chat pèse 3 kilos.")
        roles = classify_sentences(sentences, [])
        assert roles[0].role == "affirmation"
        report = evaluate_grounding(
            "Le chat pèse 3 kilos.", _ctx("Le chat dort."), _fake_embed, roles=roles,
        )
        assert report.checked == 1

    def test_role_classification_failure_fails_open(self, monkeypatch):
        import aitao.llm.response_reader as rr_mod

        def boom(*a, **k):
            raise RuntimeError("reader broken")

        monkeypatch.setattr(rr_mod, "classify_sentences", boom)
        # Pre-US-104 behaviour: every fact-bearing sentence is graded, so the
        # reliability net is never silently lost to a reader bug.
        report = evaluate_grounding(
            "C'est le premier document du contexte.",
            _ctx("Le bail prévoit un préavis de trois mois."),
            _fake_embed,
        )
        assert report.checked == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
