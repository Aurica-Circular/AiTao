# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Post-generation grounding check — answer_validator (US-076, phase 1).

After the LLM has answered a factual question, this module scores how well the
answer is actually supported by the retrieved context — WITHOUT a second LLM
pass (the "deterministic first" decision). Each answer sentence is embedded with
the same bge-m3 model already loaded for retrieval and compared, by cosine
similarity, to the retrieved chunks. A sentence whose best match stays below
``GROUNDING_THRESHOLD`` is flagged as unsupported.

The user-facing action is a notary-style warning listing the unsupported claims
— never a rewrite ("notaire, pas oracle"). A global grounding score (0..1) is
exposed so the API / UI can surface a confidence signal.

Scope (US-092): only sentences carrying a verifiable fact — a number, date or
amount — are checked. Free paraphrase (summaries, translations) condenses and
reformulates by nature, so its embedding overlap with any single chunk is low:
grounding it produced false alarms on correct answers (field-proven on a good
summary flagged 4 times), which destroys the very trust the banner exists to
build. The risky claims are the factual details; those are what we ground.

How a fact-bearing sentence is judged (US-092): digits are checked VERBATIM —
every digit token of the sentence must appear (normalised) somewhere in the
context. Embedding similarity is the wrong tool for figures both ways: a
correct sentence condensing figures from two chunks dilutes its best
single-chunk score (false alarm), while a flipped figure in a well-paraphrased
sentence still scores high (missed error). The verbatim rule fixes both,
measured on bge-m3. Sentences whose only fact is spelled out (« trois mois »,
三個月) keep the embedding check — there is no digit to match.

Known limit: a wrong figure that happens to appear elsewhere in the context
(right digits, wrong relation) still passes; that finer semantic check is the
(opt-in) LLM pass.

Default-on at the "fast" level since US-092 (Core, free — no license gate);
the LLM "high-reliability" pass ("deep") is Core too, opt-in. The embedding
function is injected, so this module loads no model and makes no LLM call —
pure, testable, deterministic.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

# Cosine-similarity floor below which an answer sentence is considered
# unsupported by the retrieved context. bge-m3 scores a sentence against its
# true source chunk well above this, and against unrelated text well below it.
# Calibrated on the synthetic set (scripts/calibrate_grounding_threshold.py,
# US-076 phase 2): supported claims floor at ~0.64, off-topic caps at ~0.54, so
# 0.58 separates them with margin both sides (0 false positives, all off-topic
# flagged). Contradictions score high like supported claims — the deterministic
# blind spot the phase-2 LLM pass must cover.
GROUNDING_THRESHOLD = 0.58

# Sentences shorter than this (trimmed characters) are not worth checking:
# greetings, "Voici :", bullets, closing politeness — no factual claim, so
# flagging them would only add noise.
MIN_SENTENCE_CHARS = 20

# CJK is dense: a run of this many ideographs is already a full claim, so the
# latin character floor above must NOT discard short Chinese/Japanese sentences
# (US-076 phase 2 calibration finding).
MIN_CJK_CHARS = 6
_CJK_CHARS = re.compile(r"[㐀-鿿豈-﫿]")

# Lines that are AiTao's own appended notices (citation guard, multi-source,
# deleted-file, this validator's own warning) must never be re-validated.
# Public (US-104): response_reader's "notice" role reuses this exact tuple —
# single source of truth, no duplicated prefix list to drift out of sync.
NOTICE_PREFIXES = ("📎", "⚠️", "ℹ️", "•")

# A sentence is worth grounding only if it states a checkable fact — essentially
# a quantity: digits, a CJK numeral, or a spelled-out number (US-092). This is
# where hallucinations hurt (figures, dates, amounts) while reformulated prose
# is where the false positives lived. "un"/"une" are excluded — too often mere
# articles. Shared with the LLM pass (answer_validator_llm re-imports it).
_DIGIT = re.compile(r"\d")
_CJK_NUMERAL = re.compile(r"[一二三四五六七八九十百千萬億兩两零半]")
_FR_NUMBER_WORDS = frozenset({
    "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
    "onze", "douze", "treize", "quatorze", "quinze", "seize", "vingt", "trente",
    "quarante", "cinquante", "soixante", "cent", "cents", "mille", "million",
    "milliard", "premier", "première", "second", "seconde", "demi", "demie",
    "moitié", "tiers", "quart", "douzaine", "dizaine", "centaine",
})

# Digit tokens for the verbatim check (US-092): a thousands-grouped number
# ("2 400", "1.200", "12,000"), a decimal ("8,5"), or a plain digit run. Kept
# deliberately narrow so enumerations ("points 1, 2 et 3") stay separate tokens.
_DIGIT_TOKEN = re.compile(
    r"\d{1,3}(?:[\s.,]\d{3})+"  # thousands-grouped ("2 400"; \s covers NBSP)
    r"|\d+[.,]\d+"  # decimal ("8,5")
    r"|\d+"  # plain run ("1988")
)

# A list of texts -> a matrix of vectors (one row per text). bge-m3 ``encode``
# fits this; tests inject a deterministic fake.
EmbedFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]

# Sentence boundary. Three cases:
#   - a CJK terminator (。！？) splits immediately — CJK has no space after it;
#   - a Latin terminator (.!?…) splits only when whitespace follows, so "3.14"
#     and "M." don't over-split;
#   - a newline splits, so list items and paragraphs become separate units.
# Over-splitting (abbreviations) is benign for a grounding score — each fragment
# is still compared to the context.
_SENT_SPLIT = re.compile(r"(?<=[。！？])|(?<=[.!?…])\s+|\n+")


@dataclass
class SentenceGrounding:
    """One answer sentence and its best similarity to the retrieved context."""

    sentence: str
    score: float


@dataclass
class GroundingReport:
    """Outcome of the deterministic grounding check (US-076)."""

    grounding_score: float  # mean best-similarity over checked sentences, 0..1
    weakest_score: float  # min best-similarity (the weakest claim), 0..1
    sentences: List[SentenceGrounding] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)
    checked: int = 0  # number of sentences actually scored
    # US-104 (part D6) — structured reliability metadata, populated by
    # chat_grounding.grounding_trailer, additive to the text banners: how
    # many sentences fell into each response_reader role, and a short
    # display line per wrong-source attribution flagged. Left empty by
    # evaluate_grounding itself (this module has no dependency on
    # response_reader/source_attribution beyond the lazy role classification
    # above) — the API layer fills them in when it has that context.
    role_counts: Dict[str, int] = field(default_factory=dict)
    attribution_notes: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


def split_sentences(text: str) -> List[str]:
    """Split answer text into sentences (Latin + CJK), trimmed and non-empty."""
    if not text:
        return []
    return [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]


def has_verifiable_fact(claim: str) -> bool:
    """True when a claim states a checkable quantity (number / date / amount)."""
    if _DIGIT.search(claim) or _CJK_NUMERAL.search(claim):
        return True
    words = re.findall(r"\w+", claim.lower(), re.UNICODE)
    return any(w in _FR_NUMBER_WORDS for w in words)


def digit_tokens(text: str) -> List[str]:
    """Digit tokens of ``text``, normalised to bare digit strings ("2 400" -> "2400")."""
    return [re.sub(r"\D", "", m) for m in _DIGIT_TOKEN.findall(text)]


def _missing_digits(sentence: str, context_tokens: Sequence[str]) -> List[str]:
    """Digit tokens of the sentence not found verbatim in the context (US-092).

    A token counts as found when it equals or is contained in a context token —
    substring containment keeps short figures conservative ("88" is covered by
    "1988"; a percent's "8" by any year containing an 8), erring on the side of
    NOT flagging: a false alarm costs more trust than a missed short digit.
    Both sides are normalised by the same rule, so formatting never mismatches.
    """
    return [
        t
        for t in digit_tokens(sentence)
        if not any(t in ctx for ctx in context_tokens)
    ]


def _is_checkable(sentence: str) -> bool:
    """True when a sentence carries a factual claim worth grounding.

    Only fact-bearing sentences (number / date / amount) are grounded (US-092):
    reformulated prose scores low against any single chunk and was flagging
    correct summaries. Also filters trivia that would only add noise: too-short
    fragments and AiTao's own appended notice lines (which are not model
    claims). CJK sentences are judged on their ideograph count, not the latin
    character floor (a dense Chinese clause is a full claim well under 20
    characters).
    """
    s = sentence.strip()
    if s.startswith(NOTICE_PREFIXES):
        return False
    if not has_verifiable_fact(s):
        return False
    if len(_CJK_CHARS.findall(s)) >= MIN_CJK_CHARS:
        return True
    return len(s) >= MIN_SENTENCE_CHARS


def evaluate_grounding(
    answer: str,
    context_docs: Sequence,
    embed_fn: EmbedFn,
    threshold: float = GROUNDING_THRESHOLD,
    roles: Optional[Sequence] = None,
) -> GroundingReport:
    """Score each answer sentence against the retrieved context (deterministic).

    Only fact-bearing sentences are checked (US-092). A sentence containing
    digits is judged by the VERBATIM rule — flagged iff one of its digit tokens
    appears nowhere in the context; embedding similarity is ignored for it (a
    correct multi-chunk condensation scores low, a flipped figure scores high —
    both wrong calls). A fact sentence without digits (spelled-out or CJK
    numeral) keeps the embedding threshold.

    US-104 (response reader): only "affirmation" and "citation" sentences are
    claims worth grounding — "notice" (AiTao's own lines), "echo_metadata"
    (a document title/path echoed back with no added fact) and "habillage"
    (ordinal dressing like "the first document") are skipped (I-10). Pass
    ``roles`` (``llm.response_reader.classify_sentences`` output) when the
    caller already classified the answer once (chat_grounding does, to avoid
    classifying twice); otherwise this classifies lazily. A classification
    failure fails OPEN to the pre-US-104 behaviour (grade every fact-bearing
    sentence) — the reliability net is never lost to a bug in the reader.

    ``embed_fn`` maps a list of texts to a matrix of vectors (e.g. bge-m3
    ``encode``). Vectors are L2-normalised here, so cosine similarity is a dot
    product. Never raises on an empty answer or empty context: an empty answer
    yields a perfect score (nothing claimed), an empty context flags every
    sentence (nothing to ground against).
    """
    import numpy as np

    start = time.perf_counter()

    all_sentences = split_sentences(answer)
    if roles is None:
        try:
            from aitao.llm.response_reader import classify_sentences

            roles = classify_sentences(all_sentences, context_docs)
        except Exception:
            roles = None
    role_by_sentence = (
        {r.sentence: r.role for r in roles} if roles else None
    )

    def _gradable(s: str) -> bool:
        if not _is_checkable(s):
            return False
        if role_by_sentence is not None:
            return role_by_sentence.get(s) in ("affirmation", "citation")
        return True

    sentences = [s for s in all_sentences if _gradable(s)]
    contexts = [
        c
        for c in (str(getattr(d, "content", "") or "") for d in context_docs)
        if c.strip()
    ]

    if not sentences:
        return GroundingReport(
            grounding_score=1.0,
            weakest_score=1.0,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    if not contexts:
        graded = [SentenceGrounding(s, 0.0) for s in sentences]
        return GroundingReport(
            grounding_score=0.0,
            weakest_score=0.0,
            sentences=graded,
            unsupported=list(sentences),
            checked=len(sentences),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    def _l2(mat: "np.ndarray") -> "np.ndarray":
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0  # leave a zero vector at zero (similarity 0)
        return mat / norms

    sent_vecs = _l2(np.asarray(embed_fn(sentences), dtype=float))
    ctx_vecs = _l2(np.asarray(embed_fn(contexts), dtype=float))

    # Best context similarity per sentence: (S, C) -> (S,)
    best = (sent_vecs @ ctx_vecs.T).max(axis=1)

    graded = [SentenceGrounding(s, float(b)) for s, b in zip(sentences, best)]

    # Verbatim digit rule (US-092): digits decide for digit-bearing sentences,
    # the embedding threshold decides for the others (spelled-out / CJK facts).
    # US-127: also accept digits found in a retrieved document's title/path —
    # a version number or date that is part of a *cited* document's own name
    # (e.g. "...第13.4版-20250312") is not an LLM invention, it is verbatim in
    # what was actually retrieved, just outside the chunk text. Embedding
    # vectors (contexts/sent_vecs/ctx_vecs) are untouched: a title is not prose
    # to compare by similarity, this only widens the verbatim digit corpus.
    # Same conservative bias as _missing_digits: erring towards NOT flagging.
    title_path_tokens = [
        t
        for d in context_docs
        for t in digit_tokens(
            f"{getattr(d, 'title', '') or ''} {getattr(d, 'path', '') or ''}"
        )
    ]
    ctx_tokens = [t for c in contexts for t in digit_tokens(c)] + title_path_tokens
    unsupported = []
    for g in graded:
        if digit_tokens(g.sentence):
            if _missing_digits(g.sentence, ctx_tokens):
                unsupported.append(g.sentence)
        elif g.score < threshold:
            unsupported.append(g.sentence)

    return GroundingReport(
        grounding_score=float(best.mean()),
        weakest_score=float(best.min()),
        sentences=graded,
        unsupported=unsupported,
        checked=len(sentences),
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


def build_grounding_warning(report: GroundingReport, max_listed: int = 3) -> str:
    """Notary-style warning naming the unsupported claims (empty when all hold).

    Mirrors citation_guard's appended-notice style: AiTao does not rewrite the
    answer, it flags what it could not back with the user's documents. Wording
    (US-092): only factual details are checked now, so the banner names them as
    such ("détail chiffré non retrouvé") instead of the accusatory "affirmation
    qui ne s'appuie pas sur vos documents" that killed trust on good summaries.
    """
    if not report.unsupported:
        return ""
    listed = report.unsupported[:max_listed]
    bullets = "\n".join(f"• « {_clip(s)} »" for s in listed)
    extra = len(report.unsupported) - len(listed)
    more = f"\n…et {extra} autre{'s' if extra > 1 else ''}." if extra > 0 else ""
    n = len(report.unsupported)
    if n == 1:
        head = "1 détail chiffré de cette réponse n'a pas été retrouvé"
    else:
        head = f"{n} détails chiffrés de cette réponse n'ont pas été retrouvés"
    return (
        f"\n\n⚠️ Fiabilité : {head} dans vos documents "
        f"— vérifiez ces points dans les sources :\n{bullets}{more}"
    )


def _clip(sentence: str, limit: int = 120) -> str:
    """Trim a sentence for display in the warning."""
    s = sentence.strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
