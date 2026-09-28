# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Source attribution — extract<->source liaison (US-104, part B, ÉPIC-30 phase 3;
I-15 stale-citation upgrade).

Incident I-05: a TRUE extract attributed to the WRONG document — « D'après
en_receipt.md, le montant reçu s'élève à 4200 USD. » where 4200 exists in
en_invoice.md but NOT in en_receipt.md. The deterministic grounding check
(answer_validator, US-092) only asks "does this digit appear SOMEWHERE in
the retrieved context" — it has no notion of WHICH document a sentence
names, so a right figure pinned to the wrong source sails through unflagged.

This module closes that gap for every "citation" sentence identified by
response_reader (a sentence naming a context doc AND carrying a verifiable
fact): it checks the fact against the ATTRIBUTED doc specifically, and, if
that fails, whether exactly one OTHER context doc actually supports it.

Two rules, mutually exclusive per sentence (mirrors answer_validator's own
"digits decide for digit-bearing sentences, embedding decides for the rest"):
  - digit rule: verbatim digit-token containment, deterministic.
  - embedding rule: cosine-similarity margin, for fact sentences with no
    digit to check verbatim (spelled-out numbers, CJK numerals).

Incident I-15 (étude, field-observed 2026-07-07): after a subject change,
retrieval is RIGHT (the new document is in context) but the writer's prose
still attributes content to the PREVIOUS document, recycled from its own chat
history — that document is NOT in the current context, so the I-05 liaison
above cannot even fire (it only checks citations of IN-context docs; response_
reader.strip_doc_mentions never matches a name that is not among
context_docs). citation_guard.find_fabricated_citations (G2) already detects
the stale name is absent from the retrieved sources and warns generically;
``check_stale_citations`` below promotes that warning to a corrective banner
naming the probable real source, reusing the same two conservative rules
(digit verbatim first, embedding floor second) — with no attributed-doc score
to compare against, the embedding rule uses an ABSOLUTE floor
(answer_validator.GROUNDING_THRESHOLD) rather than I-05's relative margin: a
missed corrective is fine (G2's generic warning still shows), a wrong one
would compound the original mistake with an invented source.

Always active, Core — independent of ``[rag] verify_answer`` (decision Phil
2026-07-03): a true extract pinned to the wrong source is a fabrication-
adjacent failure, the same family as citation_guard's anti-fabrication guard,
not an opt-in reliability tier. Additive only — never rewrites the answer,
never raises (a check failure yields no flags, not a broken chat).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from aitao.llm.answer_validator import GROUNDING_THRESHOLD, digit_tokens
from aitao.llm.response_reader import CITATION, SentenceRole, doc_label

EmbedFn = Callable[[Sequence[str]], Any]

# Embedding-rule margin (US-104, I-05 in-context liaison): the best-anchoring
# OTHER doc's cosine similarity must beat the attributed doc's by at least
# this much before a wrong attribution is flagged. Conservative on purpose —
# a false corrective banner ("your citation is wrong" when it is actually
# right) costs more trust than a missed one; 0.10 requires a clear, not
# marginal, gap (mirrors the reasoning behind answer_validator.
# GROUNDING_THRESHOLD's own margin).
EMBED_MARGIN = 0.10


@dataclass(frozen=True)
class AttributionFlag:
    """One sentence whose cited source looks wrong, and the probable source."""

    sentence: str
    attributed: List[str]  # display names of the doc(s) the sentence cites
    probable_source: str  # display name of the doc that actually matches
    rule: str  # "digit" | "embedding" | "stale_digit" | "stale_embedding"


def _content(doc: Any) -> str:
    return str(getattr(doc, "content", "") or "")


def _normalize(text: str) -> str:
    """NFKC-normalize + casefold — same convention as response_reader's own
    ``_normalize``, so a stale name matches a sentence regardless of
    full-width/half-width or case rendering differences."""
    return unicodedata.normalize("NFKC", text).casefold()


def _remove_span(text: str, needle: str) -> str:
    """Remove every NFKC-normalized, case-insensitive occurrence of ``needle``
    from ``text`` (mirrors response_reader._remove_span — used here to strip a
    stale citation name out of a sentence before tokenizing its digits or
    embedding it, so the name's own characters never leak into the check)."""
    norm_needle = unicodedata.normalize("NFKC", needle).strip()
    if not norm_needle:
        return text
    pattern = re.compile(re.escape(norm_needle), re.IGNORECASE)
    return pattern.sub(" ", unicodedata.normalize("NFKC", text))


def _digit_check(
    role: SentenceRole, context_docs: Sequence[Any]
) -> Optional[AttributionFlag]:
    """Deterministic rule (I-05): fires only when the attributed doc lacks a
    digit token AND exactly ONE other context doc verbatim-contains every
    digit token of the sentence. An ambiguous match (0 or >=2 candidates) is
    silently skipped — guessing a probable source among several would be its
    own trust problem, worse than staying silent (additive-only invariant).
    """
    tokens = digit_tokens(role.sentence)
    if not tokens:
        return None

    attributed_tokens: List[str] = []
    for doc in role.cited_docs:
        attributed_tokens.extend(digit_tokens(_content(doc)))
    if all(any(t in ctx for ctx in attributed_tokens) for t in tokens):
        return None  # attributed doc genuinely supports every digit

    others = [d for d in context_docs if d not in role.cited_docs]
    candidates = []
    for doc in others:
        doc_tokens = digit_tokens(_content(doc))
        if all(any(t in ctx for ctx in doc_tokens) for t in tokens):
            candidates.append(doc)
    if len(candidates) != 1:
        return None

    return AttributionFlag(
        sentence=role.sentence,
        attributed=[doc_label(d) for d in role.cited_docs],
        probable_source=doc_label(candidates[0]),
        rule="digit",
    )


def _embedding_check(
    role: SentenceRole, context_docs: Sequence[Any], embed_fn: EmbedFn
) -> Optional[AttributionFlag]:
    """Embedding rule for citation sentences with NO digit to check verbatim
    (spelled-out numbers, CJK numerals): flags only when some OTHER doc
    anchors the sentence more closely than the attributed one, by a
    conservative margin (EMBED_MARGIN)."""
    others = [d for d in context_docs if d not in role.cited_docs]
    if not others:
        return None
    import numpy as np

    texts = (
        [role.sentence]
        + [_content(d) for d in role.cited_docs]
        + [_content(d) for d in others]
    )
    vecs = np.asarray(embed_fn(texts), dtype=float)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms

    sent_vec = vecs[0]
    n_attributed = len(role.cited_docs)
    attributed_vecs = vecs[1 : 1 + n_attributed]
    other_vecs = vecs[1 + n_attributed :]

    attributed_score = (
        float((attributed_vecs @ sent_vec).max()) if len(attributed_vecs) else -1.0
    )
    other_scores = other_vecs @ sent_vec
    best_idx = int(np.argmax(other_scores))
    best_other_score = float(other_scores[best_idx])

    if best_other_score - attributed_score >= EMBED_MARGIN:
        return AttributionFlag(
            sentence=role.sentence,
            attributed=[doc_label(d) for d in role.cited_docs],
            probable_source=doc_label(others[best_idx]),
            rule="embedding",
        )
    return None


def check_attribution(
    roles: Sequence[SentenceRole],
    context_docs: Sequence[Any],
    embed_fn: Optional[EmbedFn] = None,
) -> List[AttributionFlag]:
    """Run the attribution check over every "citation" role sentence.

    Digit rule decides for digit-bearing sentences, embedding rule decides
    for the rest (same split as answer_validator.evaluate_grounding) — never
    both, so a sentence the digit rule already cleared cannot be flagged
    on an embedding technicality. Skips silently with < 2 context docs (there
    is no "other" document to attribute to) or no citation sentences. Never
    raises: any failure on a given sentence yields no flag for it, not a
    broken chat (étude §6.4 invariant 3: warn, never block).
    """
    if len(context_docs) < 2:
        return []
    flags: List[AttributionFlag] = []
    for role in roles:
        if role.role != CITATION or not role.cited_docs:
            continue
        try:
            if digit_tokens(role.sentence):
                flag = _digit_check(role, context_docs)
            elif embed_fn is not None:
                flag = _embedding_check(role, context_docs, embed_fn)
            else:
                flag = None
            if flag is not None:
                flags.append(flag)
        except Exception:
            continue
    return flags


def _stale_names_in(sentence: str, stale_names: Sequence[str]) -> List[str]:
    """Stale citation names (I-15) contained in ``sentence`` (NFKC-casefold
    substring match, same convention as response_reader's own doc-mention
    matching)."""
    norm_sentence = _normalize(sentence)
    return [n for n in stale_names if _normalize(n) in norm_sentence]


def _digit_check_stale(
    sentence: str, stripped: str, stale: Sequence[str], context_docs: Sequence[Any]
) -> Optional[AttributionFlag]:
    """I-15 deterministic rule: fires only when the digit tokens of the
    sentence — with the stale name itself stripped out first, so its own
    digits (e.g. a dated filename) never leak in — are ALL verbatim-contained
    in exactly ONE context document. Ambiguous (0 or >=2 candidates) stays
    silent, same conservative posture as ``_digit_check`` (I-05)."""
    tokens = digit_tokens(stripped)
    if not tokens:
        return None
    candidates = []
    for doc in context_docs:
        doc_tokens = digit_tokens(_content(doc))
        if all(any(t in ctx for ctx in doc_tokens) for t in tokens):
            candidates.append(doc)
    if len(candidates) != 1:
        return None
    return AttributionFlag(
        sentence=sentence,
        attributed=list(stale),
        probable_source=doc_label(candidates[0]),
        rule="stale_digit",
    )


def _embedding_check_stale(
    sentence: str,
    stripped: str,
    stale: Sequence[str],
    context_docs: Sequence[Any],
    embed_fn: EmbedFn,
) -> Optional[AttributionFlag]:
    """I-15 embedding rule, for a stale-cited sentence with no digit to check
    verbatim: the best-anchoring context doc is the probable source ONLY IF
    its cosine similarity clears an ABSOLUTE floor
    (answer_validator.GROUNDING_THRESHOLD). Unlike ``_embedding_check``'s
    relative margin (I-05, comparing attributed vs. other), there is no
    attributed-doc score to compare against here — the attributed doc is not
    even in context — so the floor is the only guard against a marginal,
    untrustworthy match; below it we stay silent and let G2's generic
    warning stand rather than risk naming the wrong probable source."""
    if not context_docs or not stripped.strip():
        return None
    import numpy as np

    texts = [stripped] + [_content(d) for d in context_docs]
    vecs = np.asarray(embed_fn(texts), dtype=float)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms

    sent_vec = vecs[0]
    doc_vecs = vecs[1:]
    scores = doc_vecs @ sent_vec
    best_idx = int(np.argmax(scores))
    best_score = float(scores[best_idx])

    if best_score >= GROUNDING_THRESHOLD:
        return AttributionFlag(
            sentence=sentence,
            attributed=list(stale),
            probable_source=doc_label(context_docs[best_idx]),
            rule="stale_embedding",
        )
    return None


def check_stale_citations(
    roles: Sequence[SentenceRole],
    stale_names: Sequence[str],
    context_docs: Sequence[Any],
    embed_fn: Optional[EmbedFn] = None,
) -> List[AttributionFlag]:
    """I-15: run the stale-citation liaison over every answer sentence that
    names one of ``stale_names``.

    ``stale_names`` is computed by the CALLER
    (citation_guard.find_fabricated_citations(answer, context_docs)) and
    passed in — keeps this module pure/free of a circular import, exactly
    like ``check_attribution`` takes ``roles`` rather than re-splitting the
    answer. Checked regardless of sentence role (unlike ``check_attribution``,
    which only looks at "citation" roles): a stale name is by definition
    absent from context_docs, so response_reader.strip_doc_mentions can never
    classify a sentence naming it as "citation" in the first place — that is
    precisely the I-15 gap this function closes.

    Digit rule decides for digit-bearing sentences (after stripping the stale
    name out), embedding rule decides for the rest — same split as
    ``check_attribution``/``evaluate_grounding``. Skips silently when there
    are no stale names or no context to attribute to. Never raises: any
    failure on a given sentence yields no flag for it, G2's own generic
    warning still covers it (additive-only invariant).
    """
    if not stale_names or not context_docs:
        return []
    flags: List[AttributionFlag] = []
    for role in roles:
        stale = _stale_names_in(role.sentence, stale_names)
        if not stale:
            continue
        try:
            stripped = role.sentence
            for name in stale:
                stripped = _remove_span(stripped, name)
            if digit_tokens(stripped):
                flag = _digit_check_stale(role.sentence, stripped, stale, context_docs)
            elif embed_fn is not None:
                flag = _embedding_check_stale(
                    role.sentence, stripped, stale, context_docs, embed_fn
                )
            else:
                flag = None
            if flag is not None:
                flags.append(flag)
        except Exception:
            continue
    return flags


def _stale_suffix(flag: AttributionFlag) -> str:
    """" (introuvable dans les documents de ce tour)" for a stale flag (I-15),
    else "" — placed AFTER the attributed name's closing guillemet (not
    inside it), so the banner reads "« X » (introuvable…) semble provenir…":
    the absence note qualifies the citation as a whole, not the name itself.
    """
    return " (introuvable dans les documents de ce tour)" if flag.rule.startswith("stale_") else ""


def build_attribution_warning(
    flags: Sequence[AttributionFlag], max_listed: int = 3
) -> str:
    """Corrective banner naming both the attributed and the probable source.

    Never rewrites the answer ("notaire, pas oracle") — shares the exact
    "\\n\\n⚠️ Fiabilité :" prefix as answer_validator.build_grounding_warning,
    so history_hygiene.strip_reliability_notices strips it with zero change
    (both markers start with that literal string). I-15: a stale flag gets
    " (introuvable dans les documents de ce tour)" appended right after the
    attributed name's closing guillemet, so the corrective banner explains
    why the cited document cannot be trusted, not just what the real source
    probably is.
    """
    if not flags:
        return ""
    if len(flags) == 1:
        f = flags[0]
        return (
            f"\n\n⚠️ Fiabilité : l'extrait attribué à « {', '.join(f.attributed)} »"
            f"{_stale_suffix(f)} semble provenir de « {f.probable_source} » "
            f"— vérifiez la source."
        )
    listed = flags[:max_listed]
    bullets = "\n".join(
        f"• « {_clip(f.sentence)} » : attribué à « {', '.join(f.attributed)} »"
        f"{_stale_suffix(f)}, semble provenir de « {f.probable_source} »."
        for f in listed
    )
    extra = len(flags) - len(listed)
    more = f"\n…et {extra} autre{'s' if extra > 1 else ''}." if extra > 0 else ""
    return (
        f"\n\n⚠️ Fiabilité : {len(flags)} extraits semblent attribués à la mauvaise "
        f"source — vérifiez ces points :\n{bullets}{more}"
    )


def _clip(text: str, limit: int = 120) -> str:
    t = text.strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
