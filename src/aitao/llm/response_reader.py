# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Response reader — sentence roles for the reliability pipeline (US-104, part A,
ÉPIC-30 phase 3 "le lecteur de reponse").

The deterministic grounding check (answer_validator, US-076/US-092) grades
every fact-bearing sentence the same way: is a digit/date/amount found
verbatim in the retrieved context? Two incidents showed that "fact-bearing"
is not "worth checking":

  - I-10a: "C'est le premier document du contexte." — "premier" is a French
    number word (answer_validator._FR_NUMBER_WORDS), so the sentence reads as
    fact-bearing, but it is dressing (habillage) around the document list, not
    a claim from the user's documents.
  - I-10b: "Le fichier s'intitule 百年淬鍊：範例玻璃股份有限公司." — 百 ("hundred")
    is a CJK numeral, but it lives inside a document title the model is simply
    echoing back from the retrieved metadata, not asserting as a fact.

This module classifies each answer sentence into a ROLE before grounding runs,
so answer_validator.evaluate_grounding can skip roles that were never a claim
to verify in the first place. Each role carries a ``reason`` string (étude
§6.4, 2nd invariant — every verdict is traceable).

Roles (most specific first):
  - notice: AiTao's own appended notice line (shares NOTICE_PREFIXES with
    answer_validator — single source of truth, never re-validated).
  - echo_metadata: the sentence names a context doc (title/path/filename/stem)
    and, once that reference is stripped out, nothing checkable remains — it
    is repeating metadata, not making a claim.
  - citation: the sentence names a context doc AND still carries a verifiable
    fact once the reference is stripped out — this is the "extract attributed
    to a source" shape the source_attribution module (part B) checks.
  - habillage: no document is named, but the sentence's only fact signal is an
    ordinal word (premier, deuxième, 第一, 最後…) next to structural vocabulary
    (document, fichier, source, contexte…) — dressing around the answer's own
    shape, not a claim from the documents. Deliberately narrow: a false
    habillage call would suppress a legitimate grounding check.
  - affirmation: everything else — graded exactly as before this module
    existed.

Pure logic: no LLM call, no import of API modules. Core feature, no license
gate (this is a refinement of the free G7 check, not a new tier).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, List, Sequence, Tuple

from aitao.llm.answer_validator import NOTICE_PREFIXES, has_verifiable_fact, split_sentences
from aitao.llm.citation_guard import CITATION_RE, allowed_names, is_known_citation

NOTICE = "notice"
ECHO_METADATA = "echo_metadata"
CITATION = "citation"
HABILLAGE = "habillage"
AFFIRMATION = "affirmation"

# Ordinal words that, alone, are not a fact worth grounding — they describe
# the answer's OWN structure ("the first document"), not a document's
# content. Deliberately excludes cardinals ("deux", "trois"...) which DO
# state a quantity from the documents. French + the two CJK ordinals observed
# in the field (I-10b's sibling incident, 第一/第X "nth", 最後/最后 "last").
_ORDINAL_WORDS = frozenset({
    "premier", "première", "second", "seconde", "deuxième", "troisième",
    "quatrième", "cinquième", "dernier", "dernière",
})
_CJK_ORDINAL_RE = re.compile(r"第[一二三四五六七八九十百]+|最後|最后")

# Structural vocabulary: words about the ANSWER's own shape (which document,
# which source, which passage) rather than document content. A sentence needs
# BOTH an ordinal word AND one of these to be classified as habillage — an
# ordinal alone ("le troisième employé") is a real claim.
_STRUCTURAL_WORDS = frozenset({
    "document", "documents", "fichier", "fichiers", "source", "sources",
    "contexte", "extrait", "extraits", "passage", "passages", "résultat",
    "résultats", "liste",
})

_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class SentenceRole:
    """One answer sentence, its role, and why (traceability, étude §6.4)."""

    sentence: str
    role: str
    reason: str
    cited_docs: Tuple[Any, ...] = field(default_factory=tuple)


def _normalize(text: str) -> str:
    """NFKC-normalize + casefold, so full-width/half-width and case variants
    of a title match regardless of how the model or the source rendered them."""
    return unicodedata.normalize("NFKC", text).lower()


def _remove_span(text: str, needle: str) -> str:
    """Remove every NFKC-normalized, case-insensitive occurrence of ``needle``
    from ``text`` (used to compute what remains of a sentence once a document
    reference has been stripped out)."""
    norm_needle = unicodedata.normalize("NFKC", needle).strip()
    if not norm_needle:
        return text
    pattern = re.compile(re.escape(norm_needle), re.IGNORECASE)
    return pattern.sub(" ", unicodedata.normalize("NFKC", text))


def strip_doc_mentions(
    sentence: str, context_docs: Sequence[Any]
) -> Tuple[str, List[Any]]:
    """Remove every context-doc reference from ``sentence``; return
    ``(remainder, cited_docs)``.

    Two matching passes, both reusing citation_guard's own logic (single
    source of truth with the anti-fabrication guard):
      1. filename-like tokens (``a.pdf``, ``report.docx``) validated the same
         way citation_guard validates a real citation — exact / suffix /
         Chinese-title-prefix rules included.
      2. bare title/stem/path substrings without an extension (a model can
         say "the file en_receipt" or echo a Markdown H1 verbatim) — guarded
         by a length floor so short stems don't match on noise.
    """
    if not context_docs:
        return sentence, []
    docs = list(context_docs)
    cited: List[Any] = []
    remainder = sentence

    for match in CITATION_RE.finditer(sentence):
        candidate = match.group(0)
        for doc in docs:
            if doc in cited:
                continue
            if is_known_citation(candidate, allowed_names([doc])):
                cited.append(doc)
                remainder = _remove_span(remainder, candidate)

    norm_remainder = _normalize(remainder)
    for doc in docs:
        if doc in cited:
            continue
        for variant in allowed_names([doc]) | _content_headings(doc):
            if len(variant) < 4:
                continue
            if _normalize(variant) in norm_remainder:
                cited.append(doc)
                remainder = _remove_span(remainder, variant)
                norm_remainder = _normalize(remainder)
                break

    return remainder, cited


_MD_HEADING_RE = re.compile(r"^#{1,6}\s*(.+)$", re.MULTILINE)


def _content_headings(doc: Any) -> set:
    """Markdown heading lines ("# Title") found in ``doc``'s retrieved content.

    A retrieved excerpt's own title metadata is the FILENAME (indexer
    convention, indexer_helpers.get_document_title), not any heading inside
    the file — so a model echoing "the file is titled 百年淬鍊…" is quoting a
    Markdown H1 from the document's CONTENT, not its stored title. Matched
    against every heading line (not just the first) so the check still works
    when the retrieved excerpt is a mid-document chunk. Length-capped to stay
    a plausible title, not an accidental match on a whole paragraph.
    """
    content = str(getattr(doc, "content", "") or "")
    headings = set()
    for m in _MD_HEADING_RE.finditer(content):
        heading = m.group(1).strip()
        if 4 <= len(heading) <= 200:
            headings.add(heading)
    return headings


def _has_structural_vocab(sentence: str) -> bool:
    words = set(w.lower() for w in _WORD_RE.findall(sentence))
    return bool(words & _STRUCTURAL_WORDS)


def _strip_ordinals(sentence: str) -> str:
    """Remove ordinal words/expressions, leaving the rest of the sentence for
    the "is there another fact hiding here" check."""
    out = sentence
    for word in _ORDINAL_WORDS:
        out = re.sub(rf"\b{re.escape(word)}\b", " ", out, flags=re.IGNORECASE)
    out = _CJK_ORDINAL_RE.sub(" ", out)
    return out


def _has_ordinal(sentence: str) -> bool:
    words = set(w.lower() for w in _WORD_RE.findall(sentence))
    if words & _ORDINAL_WORDS:
        return True
    return bool(_CJK_ORDINAL_RE.search(sentence))


def _is_habillage(sentence: str) -> bool:
    """Narrow on purpose (US-104): a false call here suppresses a legitimate
    grounding check. Requires ALL three: an ordinal word, structural
    vocabulary, AND no other verifiable fact once the ordinal is stripped."""
    if not _has_ordinal(sentence):
        return False
    if not _has_structural_vocab(sentence):
        return False
    return not has_verifiable_fact(_strip_ordinals(sentence))


def classify_sentence(sentence: str, context_docs: Sequence[Any]) -> SentenceRole:
    """Classify one already-split, trimmed sentence."""
    s = sentence.strip()
    if s.startswith(NOTICE_PREFIXES):
        return SentenceRole(s, NOTICE, "AiTao's own appended notice line")

    remainder, cited = strip_doc_mentions(s, context_docs)
    if cited:
        names = ", ".join(doc_label(d) for d in cited)
        if has_verifiable_fact(remainder):
            return SentenceRole(
                s, CITATION,
                f"names {names} and carries a fact beyond that reference",
                tuple(cited),
            )
        return SentenceRole(
            s, ECHO_METADATA,
            f"echoes {names}'s title/path with no additional fact",
        )

    if _is_habillage(s):
        return SentenceRole(
            s, HABILLAGE,
            "ordinal wording + structural vocabulary, no other verifiable fact",
        )

    return SentenceRole(s, AFFIRMATION, "no document reference, no habillage pattern")


def doc_label(doc: Any) -> str:
    """Display name for a doc in a banner (filename, falling back to title)."""
    path = str(getattr(doc, "path", "") or "")
    if path:
        return PurePath(path).name
    return str(getattr(doc, "title", "") or "document")


def classify_sentences(
    sentences: Sequence[str], context_docs: Sequence[Any]
) -> List[SentenceRole]:
    """Classify a pre-split list of sentences (never raises)."""
    return [classify_sentence(s, context_docs) for s in sentences]


def classify_answer(answer: str, context_docs: Sequence[Any]) -> List[SentenceRole]:
    """Split ``answer`` into sentences (answer_validator.split_sentences) and
    classify each one — the convenience entry point for callers that have not
    already split the answer themselves."""
    return classify_sentences(split_sentences(answer), context_docs)


def role_counts(roles: Sequence[SentenceRole]) -> dict:
    """Summary count per role — cheap structured metadata for the API (US-104
    part D6), independent of the text banners."""
    counts = {NOTICE: 0, ECHO_METADATA: 0, CITATION: 0, HABILLAGE: 0, AFFIRMATION: 0}
    for r in roles:
        counts[r.role] = counts.get(r.role, 0) + 1
    return counts
