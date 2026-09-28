# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Mojibake detector — spots garbled native-PDF text so it can be re-OCR'd.

A PDF exported from a broken-font source (e.g. a PowerPoint) can carry plenty of
*extractable* text that is nonetheless garbage: the glyphs render fine on screen
but the underlying code points are wrong (``JENANG최겠합친…`` where Latin/Han was
expected). Such a PDF passes the "has enough text" checks, so it is never sent to
OCR and the garbage gets indexed — and the model then confabulates (US-086 v5).

The detector is deterministic and aligned with the US-85 philosophy: *you* declare
the language universe (config ``ocr.languages``); the detector merely measures how
much of the text falls OUTSIDE the expected scripts. It never guesses a language.
With no expected scripts declared it stays silent (opt-in), so it cannot wrongly
re-OCR a legitimate document.
"""

import unicodedata
from functools import lru_cache
from typing import Iterable, Optional, Set, Tuple

# Map config language codes -> the Unicode "script" tokens we accept for them.
# Tokens match the first word of unicodedata.name() (see _script_of).
_LANG_SCRIPTS = {
    "fr": {"LATIN"}, "en": {"LATIN"}, "es": {"LATIN"}, "de": {"LATIN"},
    "it": {"LATIN"}, "pt": {"LATIN"}, "nl": {"LATIN"}, "vi": {"LATIN"},
    "zh": {"CJK"}, "zh-hant": {"CJK"}, "zh-hans": {"CJK"}, "zh-tw": {"CJK"},
    "ja": {"CJK", "HIRAGANA", "KATAKANA"},
    "ko": {"HANGUL"},
    "ru": {"CYRILLIC"}, "uk": {"CYRILLIC"},
    "ar": {"ARABIC"}, "el": {"GREEK"}, "he": {"HEBREW"}, "th": {"THAI"},
}

# Default detection parameters. Conservative on purpose: re-OCR is lossy, so we
# only flag clear garbage and only once there is enough text to judge.
DEFAULT_THRESHOLD = 0.20   # >=20% of letters in unexpected scripts
DEFAULT_MIN_LETTERS = 200  # need this many letters before trusting the ratio
DEFAULT_MAX_SAMPLE = 5000  # cap the scan: mojibake is pervasive, a sample suffices


def expected_scripts_for(languages: Optional[Iterable[str]]) -> Set[str]:
    """Translate declared languages into the set of accepted Unicode scripts.

    Latin is always accepted (filenames, brand names, numbers, units appear in
    every corpus). An empty/unknown declaration yields just {"LATIN"}.
    """
    scripts: Set[str] = set()
    for lang in languages or []:
        scripts |= _LANG_SCRIPTS.get(str(lang).lower(), set())
    scripts.add("LATIN")
    return scripts


@lru_cache(maxsize=4096)
def _script_of(ch: str) -> Optional[str]:
    """Return the script token of a character, or None if it has no name.

    Uses the first word of the Unicode name: 'LATIN SMALL LETTER A' -> 'LATIN',
    'CJK UNIFIED IDEOGRAPH-4E2D' -> 'CJK', 'HANGUL SYLLABLE GA' -> 'HANGUL'.
    """
    try:
        return unicodedata.name(ch).split(" ", 1)[0]
    except ValueError:
        return None


def mojibake_ratio(
    text: str,
    expected_scripts: Set[str],
    max_sample: int = DEFAULT_MAX_SAMPLE,
) -> Tuple[float, int]:
    """Return (unexpected-letter ratio, letters inspected).

    Only alphabetic characters count: digits, punctuation, whitespace and symbols
    are script-neutral and ignored. Scanning stops after ``max_sample`` letters.
    """
    letters = 0
    unexpected = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        script = _script_of(ch)
        if script is None or script not in expected_scripts:
            unexpected += 1
        if letters >= max_sample:
            break
    if letters == 0:
        return 0.0, 0
    return unexpected / letters, letters


def contains_any_script(
    text: str,
    scripts: Set[str],
    max_sample: int = DEFAULT_MAX_SAMPLE,
) -> bool:
    """True if any alphabetic char in ``text`` (sampled) belongs to ``scripts``.

    Used to spot an *expected* script that is entirely missing — e.g. a scan that
    declares Chinese but whose junk native text layer carries no CJK at all
    (US-086 v6). Digits, punctuation and symbols are script-neutral and ignored.
    """
    if not scripts or not text:
        return False
    seen = 0
    for ch in text:
        if not ch.isalpha():
            continue
        seen += 1
        if _script_of(ch) in scripts:
            return True
        if seen >= max_sample:
            break
    return False


def looks_like_mojibake(
    text: str,
    expected_scripts: Optional[Set[str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_letters: int = DEFAULT_MIN_LETTERS,
) -> bool:
    """True if ``text`` is likely garbled relative to the expected scripts.

    Stays silent (returns False) when no scripts are declared, or when there are
    too few letters to judge — both guard against false positives.
    """
    if not expected_scripts or not text:
        return False
    ratio, letters = mojibake_ratio(text, expected_scripts)
    return letters >= min_letters and ratio >= threshold
