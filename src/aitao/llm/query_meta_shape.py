# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# query_meta_shape.py — I-17 meta-question shape reduction (étude US-106).
#
# Problem (I-17, étude US-106 volet 2bis, "méta-questions réparées par AUCUN
# montage"): "Quels sont les documents qui parlent d'enseignants ?" carries
# exactly ONE salient search term — "enseignants" — but the raw question also
# contains "documents" / "parlent" / "quels", none of them rare enough in a
# real index for query_distiller's US-89-1 rarity filter to drop on its own.
# The scaffolding words dominate retrieval at BOTH the document and the
# chunk stage, drowning the target document no matter which search engine
# receives the polluted query (measured on 4 architectures — étude volet 2).
# The fix has to happen upstream of retrieval, on the query text itself.
#
# Approach: a narrow, deterministic (no LLM, no document-frequency oracle)
# regex layer that recognises the META-QUESTION SHAPE in French, English and
# Chinese and reduces the query to whatever text plays the role of the
# subject — kept 100% VERBATIM, never reordered or rewritten. Anything that
# does not match one of the listed shapes EXACTLY falls through unchanged
# (fail-open, ÉPIC-30 invariant): an unrecognised phrasing gets today's
# behaviour, nothing worse.
#
# Independent of query_distiller.distill_query's rarity filter: this rule
# needs no document-frequency oracle, so it can run unconditionally, even
# when US-89-1 distillation is configured off (e.g. the golden bench,
# deliberately disabled on a tiny corpus — see test_golden_conversations.py).

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- FR vocabulary -----------------------------------------------------------
_DOC_FR = r"documents?|fichiers?|sources?"
# Verbs that take a preposition ("parler DE X", "évoquer" here used with "de"
# as in common usage, "traiter DE X").
_VERB_PREP_FR = r"parle(?:nt)?|évoque(?:nt)?|traite(?:nt)?"
# Verbs that take a direct object ("mentionner X", "concerner X", "contenir X").
_VERB_DIRECT_FR = r"mentionnent|mentionne|concernent|concerne|contiennent|contient"
# "de/du/des/d'/sur" — longest alternatives tried first is not required here:
# each is \b-bounded so "de" can never eat the first two letters of "des".
_PREP_FR = r"(?:des\b|du\b|de\b|d['’]|sur\b)\s*"

# --- EN vocabulary -------------------------------------------------------------
_DOC_EN = r"documents?|files?|sources?"
# Phrases already bake in their own preposition where the verb needs one
# ("talk about", "deal with", "are/is about"); bare verbs take a direct
# object ("discuss X", "mention X", "concern X", "contain X", "cover X").
_VERB_EN = (
    r"talk about|discuss(?:es)?|mention(?:s)?|concern(?:s)?|"
    r"contain(?:s)?|cover(?:s)?|deal with|are about|is about"
)

# --- ZH vocabulary (traditional + simplified) ---------------------------------
_DOC_ZH = r"文件|文档|檔案|档案|资料|資料"
_VERB_ZH = r"提到|提及|談到|谈到|討論|讨论|涉及|包含"

# Trailing punctuation/whitespace stripped off a captured subject (never part
# of the search term): ASCII/full-width question mark, bang, stop, comma,
# semicolon, colon, Chinese enumeration comma.
_TRAILING_RE = re.compile(r"[\s?？!！.。,，;；:：·]+$")


@dataclass(frozen=True)
class MetaReduction:
    """Outcome of trying to recognise a meta-question shape.

    ``matched`` False means fail-open: the caller MUST keep using the
    original query, unchanged. ``near_miss`` (only meaningful when
    ``matched`` is False) is a cheap diagnostic signal — a recognisable
    meta-question opener was seen but no shape matched exactly (or matched
    with an empty subject) — kept apart from ``matched`` so a logging hint
    can never be mistaken for a real reduction.
    """

    matched: bool
    subject: str = ""
    shape: str = ""
    near_miss: bool = False
    near_miss_reason: str = ""


def _clean_subject(raw: str) -> str:
    """Strip surrounding whitespace and trailing punctuation, verbatim otherwise."""
    return _TRAILING_RE.sub("", raw.strip()).strip()


def _pattern(expr: str) -> re.Pattern:
    return re.compile(expr, re.IGNORECASE)


# Each entry: (shape name, compiled pattern with a "subject" group). Matched
# with ``fullmatch`` against the whole (stripped, NFKC-normalised) question —
# a meta-question shape embedded mid-sentence does not fire (narrow by design).
_PATTERNS = [
    (
        "fr_quels_documents_parlent_de",
        _pattern(
            rf"(?:quels?|quelles?)\s+(?:sont\s+les\s+|les\s+)?(?:{_DOC_FR})\s+"
            rf"(?:qui\s+)?(?:{_VERB_PREP_FR})\s+{_PREP_FR}(?P<subject>.+)"
        ),
    ),
    (
        "fr_quels_documents_mentionnent",
        _pattern(
            rf"(?:quels?|quelles?)\s+(?:sont\s+les\s+|les\s+)?(?:{_DOC_FR})\s+"
            rf"(?:qui\s+)?(?:{_VERB_DIRECT_FR})\s+(?P<subject>.+)"
        ),
    ),
    (
        "fr_y_a_t_il_des_documents_sur",
        _pattern(
            rf"y\s+a[\s-]?t[\s-]?il\s+(?:un\s+|une\s+|des\s+)?(?:{_DOC_FR})\s+"
            rf"(?:qui\s+(?:{_VERB_PREP_FR})\s+)?{_PREP_FR}(?P<subject>.+)"
        ),
    ),
    (
        "fr_dans_quels_documents_trouve_t_on",
        _pattern(
            rf"dans\s+quels?\s+(?:{_DOC_FR})\s+trouve[\s-]?t[\s-]?on\s+(?P<subject>.+)"
        ),
    ),
    (
        "en_which_documents_talk_about",
        _pattern(rf"(?:which|what)\s+(?:{_DOC_EN})\s+(?:{_VERB_EN})\s+(?P<subject>.+)"),
    ),
    (
        "en_are_there_documents_about",
        _pattern(
            rf"(?:are|is)\s+there\s+(?:any\s+)?(?:{_DOC_EN})\s+"
            rf"(?:about|on|regarding|concerning)\s+(?P<subject>.+)"
        ),
    ),
    (
        "zh_which_documents_mention",
        _pattern(rf"哪些(?:{_DOC_ZH})(?:{_VERB_ZH})(?P<subject>.+)"),
    ),
    (
        "zh_are_there_documents_about",
        _pattern(
            rf"有(?:沒有|没有)(?:關於|关于)\s*(?P<subject>.+?)\s*的(?:{_DOC_ZH})"
            rf"[\s?？!！.。]*"
        ),
    ),
]

# Cheap prefixes for the "near miss" diagnostic: a question that OPENS like a
# meta-question but did not match any shape above (wrong doc-word, missing
# verb, empty subject...). Checked with str.startswith on the casefolded,
# stripped text — no regex needed, so it is safe to run on every question.
_OPENER_PREFIXES = (
    "quel ", "quels ", "quelle ", "quelles ",
    "y a-t-il", "y a t-il", "y a t il",
    "dans quel",
    "which ", "what ", "are there", "is there",
    "哪些", "有沒有", "有没有",
)


def _looks_meta_like(text: str) -> bool:
    return text.strip().casefold().startswith(_OPENER_PREFIXES)


def reduce_meta_question(raw: str) -> MetaReduction:
    """Recognise a meta-question shape in ``raw`` and reduce it to its subject.

    Pure function, deterministic, no I/O. Returns ``MetaReduction(matched=False)``
    for anything that isn't an exact shape match (including a shape match whose
    subject turns out empty) — the caller must fall through to the original
    text in that case.
    """
    if not raw or not raw.strip():
        return MetaReduction(matched=False)

    text = unicodedata.normalize("NFKC", raw).strip()
    empty_shape = ""

    for shape, pattern in _PATTERNS:
        m = pattern.fullmatch(text)
        if not m:
            continue
        subject = _clean_subject(m.group("subject"))
        if subject:
            return MetaReduction(matched=True, subject=subject, shape=shape)
        empty_shape = shape  # shape matched, but nothing left to search on

    if empty_shape:
        return MetaReduction(
            matched=False, near_miss=True,
            near_miss_reason=f"{empty_shape}: empty subject",
        )
    if _looks_meta_like(text):
        return MetaReduction(
            matched=False, near_miss=True,
            near_miss_reason="meta-question opener seen, no shape matched",
        )
    return MetaReduction(matched=False)
