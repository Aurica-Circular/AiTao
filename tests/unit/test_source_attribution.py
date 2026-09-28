# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for source_attribution (US-104, part B — extract<->source liaison;
I-15 — stale-citation upgrade).

A deterministic fake embed_fn is injected for the embedding-rule tests (no
bge-m3, no global stubbing — sandboxed per test).
"""

from types import SimpleNamespace
from typing import List, Sequence

from aitao.llm.response_reader import classify_sentences
from aitao.llm.source_attribution import (
    build_attribution_warning,
    check_attribution,
    check_stale_citations,
)


def _doc(path: str, title: str = "", content: str = "") -> SimpleNamespace:
    return SimpleNamespace(path=path, title=title, content=content)


RECEIPT = _doc(
    "/x/en_receipt.md", "en_receipt",
    "Receipt RCP-2026-0099 confirms your payment was received in full.",
)
INVOICE = _doc(
    "/x/en_invoice.md", "en_invoice",
    "Reference: INV-2026-0042. Total amount due: 4 200 USD.",
)


class TestDigitRule:
    def test_wrong_attribution_is_flagged(self):
        # I-05 — the digit exists in the context but not in the ATTRIBUTED doc.
        answer = "D'après en_receipt.md, le montant reçu s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE])
        flags = check_attribution(roles, [RECEIPT, INVOICE])
        assert len(flags) == 1
        assert flags[0].attributed == ["en_receipt.md"]
        assert flags[0].probable_source == "en_invoice.md"
        assert flags[0].rule == "digit"

    def test_correct_attribution_is_not_flagged(self):
        answer = "D'après en_invoice.md, le montant dû s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE])
        assert check_attribution(roles, [RECEIPT, INVOICE]) == []

    def test_ambiguous_match_is_not_flagged(self):
        # Two OTHER docs both carry the digit -> guessing would be its own
        # trust problem, so the flag is withheld (additive-only invariant).
        other = _doc("/x/other.md", "other", "Montant : 4200 USD également.")
        answer = "D'après en_receipt.md, le montant reçu s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE, other])
        assert check_attribution(roles, [RECEIPT, INVOICE, other]) == []

    def test_single_context_doc_is_never_flagged(self):
        # Nothing to attribute TO with only one source.
        answer = "D'après en_receipt.md, le montant reçu s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT])
        assert check_attribution(roles, [RECEIPT]) == []


def _fake_embed_close_to(target_word: str):
    """A 2-D fake embedder: [1, 0] for texts containing target_word, [0, 1]
    otherwise — a maximal, unambiguous margin either way."""

    def embed(texts: Sequence[str]) -> List[List[float]]:
        return [[1.0, 0.0] if target_word in t else [0.0, 1.0] for t in texts]

    return embed


class TestEmbeddingRule:
    def test_wrong_attribution_flagged_by_margin(self):
        attributed = _doc("/x/a.md", "a_doc", "contenu sans rapport")
        other = _doc("/x/b.md", "b_doc", "trois mois de préavis exactement")
        sentence = "D'après a_doc.md, le préavis est de trois mois."
        roles = classify_sentences([sentence], [attributed, other])
        assert roles[0].role == "citation"
        flags = check_attribution(
            roles, [attributed, other], _fake_embed_close_to("préavis")
        )
        assert len(flags) == 1
        assert flags[0].rule == "embedding"
        assert flags[0].probable_source == "b.md"

    def test_no_embed_fn_skips_silently(self):
        attributed = _doc("/x/a.md", "a_doc", "contenu sans rapport")
        other = _doc("/x/b.md", "b_doc", "trois mois de préavis exactement")
        sentence = "D'après a_doc.md, le préavis est de trois mois."
        roles = classify_sentences([sentence], [attributed, other])
        assert check_attribution(roles, [attributed, other], None) == []

    def test_digit_sentences_never_use_the_embedding_rule(self):
        # A digit-bearing sentence the digit rule already cleared must NOT
        # be re-flagged on an embedding technicality (mirrors G7's own
        # "digits decide for digit-bearing sentences" split).
        def embed_favoring_other(texts):
            # Every text anchors best to a constant "other" direction —
            # would flag if the embedding rule ran, must not fire here.
            return [[0.0, 1.0] for _ in texts]

        answer = "D'après en_invoice.md, le montant dû s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE])
        assert check_attribution(roles, [RECEIPT, INVOICE], embed_favoring_other) == []


class TestCheckAttributionRobustness:
    def test_no_citation_sentences_yields_no_flags(self):
        roles = classify_sentences(["Le logement est décent."], [RECEIPT, INVOICE])
        assert check_attribution(roles, [RECEIPT, INVOICE]) == []

    def test_empty_roles_yields_no_flags(self):
        assert check_attribution([], [RECEIPT, INVOICE]) == []

    def test_never_raises_on_broken_embed_fn(self):
        def boom(texts):
            raise RuntimeError("embedding model down")

        attributed = _doc("/x/a.md", "a_doc", "contenu sans rapport")
        other = _doc("/x/b.md", "b_doc", "trois mois de préavis exactement")
        sentence = "D'après a_doc.md, le préavis est de trois mois."
        roles = classify_sentences([sentence], [attributed, other])
        assert check_attribution(roles, [attributed, other], boom) == []


class TestBuildAttributionWarning:
    def test_empty_flags_yields_empty_string(self):
        assert build_attribution_warning([]) == ""

    def test_singular_wording_names_both_sources(self):
        answer = "D'après en_receipt.md, le montant reçu s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE])
        flags = check_attribution(roles, [RECEIPT, INVOICE])
        warning = build_attribution_warning(flags)
        assert warning.startswith("\n\n⚠️ Fiabilité :")
        assert "en_receipt.md" in warning
        assert "en_invoice.md" in warning

    def test_never_rewrites_the_answer(self):
        # Additive only ("notaire, pas oracle") — the warning is a suffix
        # block, never a replacement of the original text.
        answer = "D'après en_receipt.md, le montant reçu s'élève à 4200 USD."
        roles = classify_sentences([answer], [RECEIPT, INVOICE])
        flags = check_attribution(roles, [RECEIPT, INVOICE])
        warning = build_attribution_warning(flags)
        assert answer not in warning


# --- I-15 — stale-citation liaison --------------------------------------
#
# The cited doc (e.g. a doc from a previous turn) is ABSENT from context —
# citation_guard.find_fabricated_citations (G2) already flags it; these
# tests exercise check_stale_citations, which promotes that generic warning
# to a corrective banner naming the probable IN-context source.

FR_BAIL = _doc(
    "/x/fr_bail.md", "fr_bail",
    "Le présent bail fixe le loyer mensuel à 1250 EUR. Locataire : Jean Dupont.",
)


class TestStaleDigitRule:
    def test_exactly_one_candidate_fires(self):
        # I-15 field shape: "zh_glass.md" is absent from context (subject
        # changed), but the digit it's attached to ("1250") is verbatim in
        # the one doc that IS in context (fr_bail.md).
        sentence = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        roles = classify_sentences([sentence], [FR_BAIL])
        flags = check_stale_citations(roles, ["zh_glass.md"], [FR_BAIL])
        assert len(flags) == 1
        assert flags[0].attributed == ["zh_glass.md"]
        assert flags[0].probable_source == "fr_bail.md"
        assert flags[0].rule == "stale_digit"

    def test_zero_candidates_stay_silent(self):
        # No context doc carries the digit -> nothing to name, stay silent
        # (G2's generic warning still shows).
        sentence = "D'après zh_glass.md, le loyer mensuel s'élève à 9999 EUR."
        roles = classify_sentences([sentence], [FR_BAIL])
        assert check_stale_citations(roles, ["zh_glass.md"], [FR_BAIL]) == []

    def test_ambiguous_candidates_stay_silent(self):
        # Two context docs both carry the digit -> guessing would be its own
        # trust problem (mirrors check_attribution's own ambiguity guard).
        other = _doc("/x/other.md", "other", "Un autre document à 1250 EUR aussi.")
        sentence = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        roles = classify_sentences([sentence], [FR_BAIL, other])
        assert check_stale_citations(roles, ["zh_glass.md"], [FR_BAIL, other]) == []

    def test_name_digits_are_stripped_before_tokenizing(self):
        # The stale name itself carries a digit ("report2024.pdf") — it must
        # NOT leak into the digit tokens being checked, or "2024" (found
        # nowhere in context) would make the match ambiguous/empty and the
        # otherwise-clean "1250" match would be lost.
        target = _doc("/x/target.md", "target", "Le montant est de 1250 EUR précisément.")
        sentence = "D'après report2024.pdf, le montant est de 1250 EUR."
        roles = classify_sentences([sentence], [target])
        flags = check_stale_citations(roles, ["report2024.pdf"], [target])
        assert len(flags) == 1
        assert flags[0].rule == "stale_digit"
        assert flags[0].probable_source == "target.md"


def _fake_embed_score(score: float):
    """A 2-D fake embedder: every doc anchors the (stripped) sentence at
    exactly ``score`` cosine similarity — lets the floor test hit precise
    values above/below GROUNDING_THRESHOLD (0.58) without depending on real
    embeddings."""
    import math

    def embed(texts: Sequence[str]) -> List[List[float]]:
        vecs = [[1.0, 0.0]]
        for _ in texts[1:]:
            vecs.append([score, math.sqrt(max(0.0, 1.0 - score ** 2))])
        return vecs

    return embed


class TestStaleEmbeddingRule:
    def test_above_floor_fires(self):
        target = _doc("/x/target.md", "target", "contenu sans chiffre")
        sentence = "D'après zh_glass.md, le document décrit un événement notable."
        roles = classify_sentences([sentence], [target])
        flags = check_stale_citations(
            roles, ["zh_glass.md"], [target], _fake_embed_score(0.6)
        )
        assert len(flags) == 1
        assert flags[0].rule == "stale_embedding"
        assert flags[0].probable_source == "target.md"

    def test_below_floor_stays_silent(self):
        # 0.58 is an ABSOLUTE floor here (no attributed-doc score to compare
        # against, unlike I-05's relative EMBED_MARGIN) — a wrong corrective
        # is worse than none, so a marginal match stays silent.
        target = _doc("/x/target.md", "target", "contenu sans chiffre")
        sentence = "D'après zh_glass.md, le document décrit un événement notable."
        roles = classify_sentences([sentence], [target])
        flags = check_stale_citations(
            roles, ["zh_glass.md"], [target], _fake_embed_score(0.5)
        )
        assert flags == []

    def test_no_embed_fn_skips_silently(self):
        target = _doc("/x/target.md", "target", "contenu sans chiffre")
        sentence = "D'après zh_glass.md, le document décrit un événement notable."
        roles = classify_sentences([sentence], [target])
        assert check_stale_citations(roles, ["zh_glass.md"], [target], None) == []


class TestCheckStaleCitationsRobustness:
    def test_no_stale_names_yields_no_flags(self):
        sentence = "D'après fr_bail.md, le loyer est de 1250 EUR."
        roles = classify_sentences([sentence], [FR_BAIL])
        assert check_stale_citations(roles, [], [FR_BAIL]) == []

    def test_no_context_docs_yields_no_flags(self):
        sentence = "D'après zh_glass.md, le loyer est de 1250 EUR."
        roles = classify_sentences([sentence], [])
        assert check_stale_citations(roles, ["zh_glass.md"], []) == []

    def test_sentence_without_stale_name_is_untouched(self):
        sentence = "Le bail prévoit un loyer de 1250 EUR."
        roles = classify_sentences([sentence], [FR_BAIL])
        assert check_stale_citations(roles, ["zh_glass.md"], [FR_BAIL]) == []

    def test_never_raises_on_broken_embed_fn(self):
        def boom(texts):
            raise RuntimeError("embedding model down")

        target = _doc("/x/target.md", "target", "contenu sans chiffre")
        sentence = "D'après zh_glass.md, le document décrit un événement notable."
        roles = classify_sentences([sentence], [target])
        assert check_stale_citations(roles, ["zh_glass.md"], [target], boom) == []


class TestStaleBannerWording:
    def test_singular_wording_marks_attributed_doc_as_absent(self):
        sentence = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        roles = classify_sentences([sentence], [FR_BAIL])
        flags = check_stale_citations(roles, ["zh_glass.md"], [FR_BAIL])
        warning = build_attribution_warning(flags)
        assert warning.startswith("\n\n⚠️ Fiabilité :")
        assert "introuvable" in warning
        assert "zh_glass.md" in warning
        assert "fr_bail.md" in warning

    def test_multi_flag_bullets_mark_each_stale_attributed_doc(self):
        other = _doc("/x/other.md", "other", "Un séjour de 30 jours est prévu.")
        s1 = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        s2 = "D'après old_doc.md, le séjour dure 30 jours."
        roles = classify_sentences([s1, s2], [FR_BAIL, other])
        flags = check_stale_citations(
            roles, ["zh_glass.md", "old_doc.md"], [FR_BAIL, other]
        )
        assert len(flags) == 2
        warning = build_attribution_warning(flags)
        assert warning.count("introuvable dans les documents de ce tour") == 2
