# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# query_distiller.py — Distill a raw user question into a salient search query.
#
# Problem (US-89): embedding the whole question (function words + key terms,
# often multilingual) pollutes retrieval — common words dominate the embedding
# and the real terms sink, so the wrong documents come back.
#
# Approach (deterministic, language- and model-agnostic — no stopword lists, no
# LLM): keep only the SALIENT tokens.
#   - Structured tokens (emails, codes/IDs, non-latin runs, quoted phrases) are
#     ALWAYS kept — they are inherently distinctive.
#   - Plain latin words are kept only when RARE in the index (low document
#     frequency): "salient = rare". Function words appear in most documents and
#     are dropped; a real term appears in few and is kept.
#
# The frequency oracle is injected (Callable) so the logic is pure and testable.
# ``structured_tokens`` reuses the same distinctive-token patterns for
# exact-token pinning (US-89-2). US-89-1.

from __future__ import annotations

import re
import unicodedata
from typing import Callable, List, Optional, Set

from aitao.core.cjk_glue import CJK_GAP_SAMELINE as _CJK_GAP

# Glue CJK characters separated by spaces back into one run (OCR and some copies
# space ideographs: "粒 米 女" → "粒米女"). Shared with the content-side gluer
# (indexation/item_preparer.py) via core/cjk_glue.py (DRY, ÉPIC-31 US-111).

# --- Structured-token patterns (always kept) -------------------------------
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
# Quoted spans: « … », " … ", ' … ' (keep the inside, >= 2 chars).
_QUOTED = re.compile(r"[«\"']([^«»\"']{2,}?)[»\"']")
# A run of non-latin "wordy" characters: CJK, Japanese kana, Hangul, Arabic,
# Cyrillic, Thai, Hebrew. These never look like latin function words.
_NONLATIN_RUN = re.compile(
    r"[　-鿿぀-ヿ가-힯"
    r"؀-ۿЀ-ӿ฀-๿֐-׿]+"
)
# Code / ID: alphanumeric with at least one digit and one letter, or a long
# digit run (reference numbers, dates).
_CODE = re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])[A-Za-z0-9._-]{3,}\b|\b\d{4,}\b")
# Plain latin word (incl. accents), >= 2 chars — candidates for rarity filtering.
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


def _extract_structured(text: str) -> tuple[List[str], str]:
    """Pull the structured (always-salient) tokens out of ``text``.

    Returns the tokens in reading order plus the residual text with each matched
    span blanked, so a structured token is never re-shredded into latin pieces
    (an email into ``acmemotor`` / ``com`` / ``tw``…). Shared by
    ``distill_query`` (US-89-1) and ``structured_tokens`` (US-89-2). Expects an
    already-normalised ``text`` (NFKC + glued CJK runs).
    """
    kept: List[str] = []
    residual = text

    def _take(pattern: re.Pattern, group: int = 0) -> List[str]:
        nonlocal residual
        found = [(m.group(group) or "").strip() for m in pattern.finditer(residual)]
        residual = pattern.sub(" ", residual)
        return [f for f in found if f]

    kept += _take(_EMAIL)
    kept += _take(_QUOTED, group=1)
    kept += _take(_NONLATIN_RUN)
    kept += _take(_CODE)
    return kept, residual


def structured_tokens(text: str, *, min_nonlatin_len: int = 1) -> List[str]:
    """Distinctive verbatim tokens of ``text`` (emails, IDs, quoted, non-latin).

    These are inherently distinctive — the same tokens ``distill_query`` keeps
    unconditionally. Exact-token pinning (US-89-2) uses them to find every
    document that contains such a token verbatim. ``min_nonlatin_len`` drops
    non-latin runs shorter than N characters: a single common ideograph is not
    distinctive enough to pin on (it appears in most documents of a CJK corpus).
    Order-preserving and deduplicated; returns [] for an ordinary question.
    """
    if not text or not text.strip():
        return []
    norm = _CJK_GAP.sub("", unicodedata.normalize("NFKC", text))
    tokens, _ = _extract_structured(norm)
    if min_nonlatin_len > 1:
        tokens = [
            t
            for t in tokens
            if not _NONLATIN_RUN.fullmatch(t) or len(t) >= min_nonlatin_len
        ]
    return list(dict.fromkeys(t for t in tokens if t))


def distill_query(
    raw: str,
    doc_freq: Optional[Callable[[str], int]] = None,
    total_docs: int = 0,
    *,
    max_doc_ratio: float = 0.15,
) -> str:
    """Return a search query keeping only the salient tokens of ``raw``.

    Args:
        raw:          the user's raw question.
        doc_freq:     oracle returning how many indexed documents contain a term
                      (lower-cased). When None, latin words are kept as-is.
        total_docs:   total number of indexed documents (for the rarity ratio).
        max_doc_ratio: a latin word is "frequent" (dropped) when it appears in
                      more than this fraction of the corpus.

    Falls back to ``raw`` when distillation would be empty (never search blank).
    """
    if not raw or not raw.strip():
        return raw

    # Normalise first: NFKC folds Kangxi radicals (⽶ U+2F76) and other
    # compatibility forms onto regular CJK (米), then glue spaced ideographs into
    # contiguous runs so they are kept as one salient token.
    raw = _CJK_GAP.sub("", unicodedata.normalize("NFKC", raw))

    # Structured tokens (emails, quoted, non-latin runs, codes) are always kept;
    # the residual (those spans blanked) feeds the latin rarity filter below.
    kept, residual = _extract_structured(raw)

    # Latin words: keep only the rare ones (needs a frequency oracle + corpus size).
    if doc_freq is not None and total_docs > 0:
        threshold = max(1, int(max_doc_ratio * total_docs))
        seen: Set[str] = set()
        for word in _LATIN_WORD.findall(residual):
            low = word.lower()
            if low in seen:
                continue
            seen.add(low)
            try:
                freq = doc_freq(low)
            except Exception:
                freq = -1  # oracle failed → treat as salient (keep)
            if freq < 0 or freq < threshold:
                kept.append(word)

    # Dedupe preserving order; never return an empty query.
    distilled = " ".join(dict.fromkeys(t for t in kept if t)).strip()
    return distilled or raw
