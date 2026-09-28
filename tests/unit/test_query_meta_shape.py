# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_query_meta_shape.py — I-17: meta-question shape reduction (étude US-106).
#
# Pure logic, no I/O: every shape must reduce to its verbatim subject, every
# near-miss must NOT fire (fail-open), and the exact I-17 phrasing must reduce
# to "enseignants" — the one word that actually finds the target document.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.llm.query_meta_shape import reduce_meta_question  # noqa: E402


def _subject(text: str):
    """Return the reduced subject, or None if the shape did not fire."""
    result = reduce_meta_question(text)
    return result.subject if result.matched else None


class TestIncidentI17:
    def test_exact_i17_phrasing(self):
        assert _subject("Quels sont les documents qui parlent d'enseignants ?") == "enseignants"

    def test_bonus_style_multi_word_subject(self):
        assert _subject("Quels documents parlent de AI transformation glass ?") == (
            "AI transformation glass"
        )


class TestFrenchShapes:
    def test_quels_documents_parlent_de(self):
        assert _subject("Quels documents parlent de Gearboxes ?") == "Gearboxes"

    def test_quelles_sources_parlent_de_apostrophe(self):
        assert _subject("Quelles sources parlent d'assurance maladie ?") == "assurance maladie"

    def test_quels_fichiers_qui_parlent_de(self):
        assert _subject("Quels fichiers qui parlent de sécurité ?") == "sécurité"

    def test_quels_documents_mentionnent(self):
        assert _subject("Quels documents mentionnent enseignants ?") == "enseignants"

    def test_quels_documents_concernent(self):
        assert _subject("Quels documents concernent le télétravail ?") == "le télétravail"

    def test_quels_documents_contiennent(self):
        assert _subject("Quels documents contiennent RGPD ?") == "RGPD"

    def test_y_a_t_il_des_documents_sur(self):
        assert _subject("Y a-t-il des documents sur Calendrier chinois ?") == "Calendrier chinois"

    def test_y_a_t_il_un_document_qui_parle_du(self):
        # Real committed-corpus phrasing (sain_impersonnel.yaml) — must keep firing.
        assert _subject("Y a-t-il un document qui parle du loyer mensuel ?") == "loyer mensuel"

    def test_dans_quels_documents_trouve_t_on(self):
        assert _subject(
            "Dans quels documents trouve-t-on la clause de confidentialité ?"
        ) == "la clause de confidentialité"

    def test_cjk_subject_kept_verbatim(self):
        assert _subject("Quel document parle de 範例玻璃 ?") == "範例玻璃"


class TestEnglishShapes:
    def test_which_documents_talk_about(self):
        assert _subject("Which documents talk about teachers?") == "teachers"

    def test_which_files_mention(self):
        assert _subject("Which files mention GDPR compliance") == "GDPR compliance"

    def test_are_there_documents_about(self):
        assert _subject("Are there documents about pension plans?") == "pension plans"

    def test_are_there_any_files_on(self):
        assert _subject("Are there any files on quarterly earnings?") == "quarterly earnings"


class TestChineseShapes:
    def test_forward_which_documents_mention(self):
        assert _subject("哪些文件提到教師?") == "教師"

    def test_forward_simplified(self):
        assert _subject("哪些文档讨论教师?") == "教师"

    def test_reversed_are_there_documents_about(self):
        assert _subject("有沒有關於教師的文件?") == "教師"

    def test_reversed_simplified(self):
        assert _subject("有没有关于教师的文档?") == "教师"


class TestNearMissesDoNotFire:
    """These LOOK meta but must fall through unchanged (narrow by design)."""

    def test_quels_enseignants_is_not_a_document_word(self):
        assert _subject("Quels enseignants sont mentionnés ?") is None

    def test_le_document_parle_de_is_not_an_opener(self):
        assert _subject("Le document parle de X") is None

    def test_ordinary_factual_question(self):
        assert _subject("Quel est le montant total dû sur la facture INV-2026-0042 ?") is None

    def test_combien_de_documents_is_not_quels(self):
        assert _subject("Combien de documents parlent de 範例玻璃 ?") is None

    def test_empty_subject_falls_through(self):
        assert _subject("Quels documents parlent de ?") is None

    def test_empty_input(self):
        assert _subject("") is None
        assert _subject("   ") is None

    def test_near_miss_flag_set_for_opener_without_shape(self):
        result = reduce_meta_question("Quels enseignants sont mentionnés ?")
        assert result.matched is False
        assert result.near_miss is True

    def test_no_near_miss_for_unrelated_question(self):
        result = reduce_meta_question("Le document parle de X")
        assert result.matched is False
        assert result.near_miss is False


class TestRobustness:
    def test_accents_and_case_insensitive_opener(self):
        assert _subject("QUELS DOCUMENTS PARLENT DE enseignants ?") == "enseignants"
        assert _subject("quels documents parlent de enseignants ?") == "enseignants"

    def test_curly_apostrophe(self):
        assert _subject("Quels documents parlent d’enseignants ?") == "enseignants"

    def test_trailing_punctuation_variants_stripped(self):
        assert _subject("Quels documents parlent de enseignants") == "enseignants"
        assert _subject("Quels documents parlent de enseignants.") == "enseignants"
        assert _subject("Quels documents parlent de enseignants !") == "enseignants"
        assert _subject("Quels documents parlent de enseignants ？") == "enseignants"

    def test_subject_never_rewritten_case_preserved(self):
        assert _subject("Quels documents parlent de Système Prompt ?") == "Système Prompt"

    def test_extra_internal_whitespace_tolerated(self):
        assert _subject("Quels   documents   parlent   de   enseignants  ?") == "enseignants"
