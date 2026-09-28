# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# language_tags.py — canonical language-code normalization (US-85c).
#
# AiTao stores a document's language from three sources that each use a
# different notation:
#   - native text  -> langdetect codes ("fr", "en", "ja", "zh-cn", "zh-tw")
#   - Apple Vision -> BCP-47 region/script tags ("fr-FR", "zh-Hant", "en-US")
#   - Tesseract    -> ISO 639-2/T 3-letter, joined ("fra", "chi_tra", "fra+eng")
#
# The Meilisearch `language` filter is an EXACT string match and the CLI promises
# short codes ("en, fr, zh"), so these mixed notations silently broke filtering:
# `--language zh` matched neither "zh-tw" (native) nor "zh-Hant" (OCR). This
# module collapses every notation to ONE canonical 2-letter code so the filter
# behaves the same across native and scanned documents. The precise tag is kept
# separately in the document metadata (`language_precise`) — nothing is lost.
#
# Deterministic by design ("notary, not oracle"): a pure lookup, no guessing.

from __future__ import annotations

UNKNOWN = "unknown"

# ISO 639-2/T (and a few 639-2/B) 3-letter codes -> canonical 2-letter.
# Restricted to the languages AiTao actually produces (OCR packs + common
# langdetect outputs); anything else falls through to the 2-letter passthrough
# or UNKNOWN. Cantonese (yue) is folded into Chinese for filtering purposes.
_THREE_TO_TWO: dict[str, str] = {
    "fra": "fr", "eng": "en", "deu": "de", "ger": "de",
    "spa": "es", "ita": "it", "por": "pt", "nld": "nl",
    "jpn": "ja", "kor": "ko", "ara": "ar", "rus": "ru",
    "ukr": "uk", "tha": "th", "vie": "vi", "pol": "pl",
    "chi": "zh", "zho": "zh", "yue": "zh",
}


def _normalize_atom(raw: str) -> str:
    """Normalize ONE tag (no '+') to a canonical 2-letter code or UNKNOWN."""
    key = raw.strip().lower().replace("_", "-")
    if not key:
        return UNKNOWN
    if key == UNKNOWN:
        return UNKNOWN
    primary = key.split("-", 1)[0]
    if primary in _THREE_TO_TWO:
        return _THREE_TO_TWO[primary]
    if len(primary) == 2 and primary.isalpha():
        return primary
    return UNKNOWN


def normalize_language(raw: str | None) -> str:
    """Collapse any stored/detected language notation to a canonical short code.

    Examples:
        "fr", "fr-FR", "fra"          -> "fr"
        "zh-tw", "zh-Hant", "chi_tra" -> "zh"
        "en-US", "eng"                -> "en"
        "fra+eng+chi_tra"             -> "unknown"  (multi-candidate, not a
                                                      single detected language)
        None, "", "xx"                -> "unknown"

    A '+'-joined value comes from a Tesseract pass that ran several candidate
    languages at once (no per-language confidence, so no winner) — we refuse to
    attribute a single language to it and return UNKNOWN rather than guess.
    """
    if not raw:
        return UNKNOWN
    if "+" in raw:
        atoms = {_normalize_atom(part) for part in raw.split("+")}
        atoms.discard(UNKNOWN)
        return next(iter(atoms)) if len(atoms) == 1 else UNKNOWN
    return _normalize_atom(raw)


def normalize_language_query(raw: str | None) -> str | None:
    """Normalize a user-supplied `--language` filter value.

    Returns None for an empty value (no filtering). Unlike the index-time
    normaliser, an *unrecognised* non-empty input is passed through lowercased
    rather than folded to "unknown": that way a typo matches no document
    (predictable empty result) instead of silently returning every
    untagged/"unknown" document. The literal "unknown" is preserved so users
    can deliberately filter documents whose language could not be determined.
    """
    if raw is None:
        return None
    stripped = raw.strip().lower()
    if not stripped:
        return None
    norm = normalize_language(stripped)
    if norm == UNKNOWN and stripped != UNKNOWN:
        return stripped
    return norm
