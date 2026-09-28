# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for response_reader (US-104, part A — sentence roles).

Covers the two I-10 false positives (habillage ordinal, echoed CJK title),
the I-05 citation-with-fact shape, and the narrowness guards that keep
habillage/echo_metadata from suppressing legitimate claims.
"""

from types import SimpleNamespace

from aitao.llm.response_reader import (
    AFFIRMATION,
    CITATION,
    ECHO_METADATA,
    HABILLAGE,
    NOTICE,
    classify_answer,
    classify_sentences,
    doc_label,
    role_counts,
    strip_doc_mentions,
)


def _doc(path: str, title: str = "", content: str = "") -> SimpleNamespace:
    return SimpleNamespace(path=path, title=title, content=content)


GLASS = _doc(
    "/x/zh_glass.md", "zh_glass",
    "# 百年淬鍊：範例玻璃股份有限公司\n\n創立年份：1923 年。員工人數：312 人。",
)
RECEIPT = _doc(
    "/x/en_receipt.md", "en_receipt",
    "Receipt RCP-2026-0099 confirms your payment was received in full.",
)
INVOICE = _doc(
    "/x/en_invoice.md", "en_invoice",
    "Reference: INV-2026-0042. Total amount due: 4 200 USD.",
)


class TestNoticeRole:
    def test_warning_prefix_is_notice(self):
        role = classify_sentences(["⚠️ Fiabilité : détail non retrouvé."], [])[0]
        assert role.role == NOTICE

    def test_bullet_prefix_is_notice(self):
        role = classify_sentences(["• « un détail »"], [])[0]
        assert role.role == NOTICE


class TestHabillageRole:
    def test_ordinal_plus_structural_vocab_is_habillage(self):
        # I-10a — "premier" is a French number word (answer_validator
        # _FR_NUMBER_WORDS) but this sentence is dressing, not a claim.
        role = classify_sentences(
            ["C'est le premier document du contexte."], [GLASS]
        )[0]
        assert role.role == HABILLAGE

    def test_cjk_ordinal_plus_structural_vocab_is_habillage(self):
        role = classify_sentences(["第一 document listé ici."], [])[0]
        assert role.role == HABILLAGE

    def test_ordinal_without_structural_vocab_is_not_habillage(self):
        # A real claim: an ordinal describing something IN the world, not
        # the answer's own shape.
        role = classify_sentences(
            ["Il est arrivé premier au marathon de Paris."], []
        )[0]
        assert role.role != HABILLAGE

    def test_structural_vocab_without_ordinal_is_not_habillage(self):
        role = classify_sentences(
            ["Ce document contient trois annexes."], []
        )[0]
        assert role.role != HABILLAGE

    def test_ordinal_and_vocab_but_another_fact_remains_is_not_habillage(self):
        # A digit elsewhere in the sentence is a real claim — habillage must
        # stay narrow (a false call would suppress a legitimate check).
        role = classify_sentences(
            ["Ce premier document date de 1923."], []
        )[0]
        assert role.role != HABILLAGE


class TestEchoMetadataRole:
    def test_echoed_markdown_heading_is_echo_metadata(self):
        # I-10b — 百 (CJK numeral) lives inside the document's own H1
        # heading, echoed back verbatim; the doc's stored "title" is the
        # filename ("zh_glass"), not the heading, so this exercises the
        # content-heading fallback.
        role = classify_sentences(
            ["Le fichier s'intitule 百年淬鍊：範例玻璃股份有限公司."], [GLASS]
        )[0]
        assert role.role == ECHO_METADATA

    def test_echoed_filename_with_no_other_fact_is_echo_metadata(self):
        role = classify_sentences(["Le document s'appelle en_receipt.md."], [RECEIPT])[0]
        assert role.role == ECHO_METADATA


class TestCitationRole:
    def test_named_doc_with_fact_is_citation(self):
        # I-05 shape — a doc is named AND the sentence carries a fact beyond
        # that reference.
        role = classify_sentences(
            ["D'après en_receipt.md, le montant reçu s'élève à 4200 USD."],
            [RECEIPT, INVOICE],
        )[0]
        assert role.role == CITATION
        assert role.cited_docs == (RECEIPT,)

    def test_citation_role_carries_the_cited_doc(self):
        role = classify_sentences(["Voir en_invoice.md pour le détail."], [INVOICE])[0]
        # "pour le détail" carries no fact -> echo_metadata, not citation
        assert role.role == ECHO_METADATA


class TestAffirmationRole:
    def test_plain_fact_sentence_is_affirmation(self):
        role = classify_sentences(["Le loyer est de 850 EUR par mois."], [])[0]
        assert role.role == AFFIRMATION

    def test_no_fact_no_doc_is_affirmation(self):
        role = classify_sentences(["Le logement est décent et lumineux."], [])[0]
        assert role.role == AFFIRMATION


class TestStripDocMentions:
    def test_removes_extension_based_reference(self):
        remainder, cited = strip_doc_mentions(
            "D'après en_receipt.md, tout est réglé.", [RECEIPT]
        )
        assert cited == [RECEIPT]
        assert "en_receipt.md" not in remainder

    def test_no_context_docs_is_a_no_op(self):
        remainder, cited = strip_doc_mentions("Une phrase quelconque.", [])
        assert cited == []
        assert remainder == "Une phrase quelconque."

    def test_short_stems_are_not_matched(self):
        # Length floor (>=4 chars) avoids noisy matches on short stems.
        short = _doc("/x/a.md", "a", "contenu")
        remainder, cited = strip_doc_mentions("Le document a été trouvé.", [short])
        assert cited == []


class TestClassifyAnswerAndCounts:
    def test_classify_answer_splits_and_classifies(self):
        answer = (
            "C'est le premier document du contexte.\n"
            "Le fichier s'intitule 百年淬鍊：範例玻璃股份有限公司."
        )
        roles = classify_answer(answer, [GLASS])
        assert [r.role for r in roles] == [HABILLAGE, ECHO_METADATA]

    def test_role_counts_summarises(self):
        roles = classify_answer(
            "C'est le premier document du contexte.\nLe loyer est de 850 EUR.",
            [],
        )
        counts = role_counts(roles)
        assert counts[HABILLAGE] == 1
        assert counts[AFFIRMATION] == 1
        assert counts[CITATION] == 0

    def test_empty_answer_yields_no_roles(self):
        assert classify_answer("", []) == []


class TestDocLabel:
    def test_uses_filename_when_path_present(self):
        assert doc_label(RECEIPT) == "en_receipt.md"

    def test_falls_back_to_title_without_path(self):
        assert doc_label(SimpleNamespace(path="", title="mon-titre")) == "mon-titre"
