# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# cjk_glue.py — shared CJK "gap gluing" primitives.
#
# OCR and some extraction paths insert stray whitespace between CJK
# ideographs — a word like 承擔 is stored as "承 擔" — because the source
# layout (a justified PDF, a vertical-text scan, a table cell) inserted a
# visual gap that has no meaning as a word boundary. Meilisearch's CJK
# segmenter (jieba) then reads the space as a real token separator (never
# recovers "承擔" as one word), and the bge-m3 embedding of "承 擔" is a
# DIFFERENT vector from "承擔" — it means something else. Gluing the gap
# back together fixes both problems at the source.
#
# This module is the SINGLE source of the CJK character class and the
# same-line gap regex, shared by:
#   - the QUERY side (llm/query_distiller.py, llm/query_terms.py) — glues a
#     user's typed question before distillation/anchoring (US-89-1/89-4).
#   - the CONTENT side (indexation/item_preparer.py, via glue_cjk_content()
#     below) — glues extracted/OCR'd document text before
#     chunking/embedding/indexing (ÉPIC-31 US-111, absorbs backlog item
#     89-6 "recollage CJK à l'indexation").
#
# Rules (v1, DETERMINISTIC and DELIBERATELY CONSERVATIVE — the backlog
# explicitly calls for caution around newlines, which can be a genuine
# sentence break rather than a word split):
#   (a) A run of plain spaces/tabs between two CJK ideographs on the SAME
#       line is always glued ("承 擔" -> "承擔"). `[ \t]` never matches
#       '\n', so this rule can never bridge two lines even without
#       re.MULTILINE/DOTALL — a real paragraph break is untouched by
#       construction, no line-splitting needed to enforce it.
#   (b) A run of newlines is glued ONLY when it is an unambiguous "vertical
#       text" column: >= 3 CONSECUTIVE lines, each holding exactly ONE CJK
#       ideograph once surrounding whitespace is stripped. Any other
#       sequence of lines (a 2-line run, or a single-ideograph line next to
#       an ordinary sentence) is left untouched — it may well be two
#       separate real sentences, not a column.
#
# Re-indexing documents already stored before this fix ships is out of scope
# here (see US-112, the migration story); this module only touches content
# flowing through the ingestion pipeline from now on.

from __future__ import annotations

import re
from typing import List

# One CJK ideograph: CJK Unified Ideographs (4E00-9FFF) + Extension A
# (3400-4DBF) + Compatibility Ideographs (F900-FAFF). Single source of
# truth for the unicode ranges — previously duplicated (with a narrower,
# Unified-only range) in llm/query_distiller.py and llm/query_terms.py.
CJK_CHAR = "[\u3400-\u9fff\uf900-\ufaff]"

# A run of one or more CJK ideographs (llm/query_terms.py's anchor probes).
CJK_RUN = re.compile(rf"{CJK_CHAR}+")

# Rule (a): plain spaces/tabs between two CJK ideographs on the SAME line.
# Used verbatim by llm/query_distiller.py (query side) and by
# glue_cjk_content() below (content side).
CJK_GAP_SAMELINE = re.compile(rf"(?<={CJK_CHAR})[ \t]+(?={CJK_CHAR})")

# Same idea but matching ANY whitespace (including newlines) between two
# ideographs. Kept distinct from CJK_GAP_SAMELINE — NOT used by the content
# gluer (newlines get the dedicated, more conservative rule (b) below) —
# only to preserve llm/query_terms.py's existing behaviour unchanged (a
# query string is a single short line in practice, so the distinction rarely
# matters there, but this module must not silently change tested behaviour).
CJK_GAP_ANY_WHITESPACE = re.compile(rf"(?<={CJK_CHAR})\s+(?={CJK_CHAR})")

_SINGLE_CJK_LINE = re.compile(CJK_CHAR)
_MIN_VERTICAL_RUN = 3  # >= this many consecutive one-ideograph lines = a column


def _glue_vertical_columns(text: str) -> str:
    """Rule (b): join a run of >= 3 consecutive lines that each hold exactly
    one CJK ideograph (whitespace-trimmed) — the extraction/OCR signature of
    a vertical-text column or a narrow table cell wrapped one glyph per
    line. A shorter run, or any line with more than one character (CJK or
    not), stops the run and is left as a normal line break: it may be a
    genuine sentence boundary, and the backlog asks for caution there.
    """
    lines = text.split("\n")
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        j = i
        run: List[str] = []
        while j < n and _SINGLE_CJK_LINE.fullmatch(lines[j].strip()):
            run.append(lines[j].strip())
            j += 1
        if len(run) >= _MIN_VERTICAL_RUN:
            out.append("".join(run))
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def glue_cjk_content(text: str) -> str:
    """Apply both gluing rules to document CONTENT at ingestion time
    (ÉPIC-31, US-111 — absorbs backlog item 89-6). Deterministic, no LLM,
    idempotent (running it twice is a no-op). See the module docstring for
    the exact rules.

    Safe on non-CJK or mixed text: rule (a) only fires between two CJK
    characters, rule (b) only fires on an unambiguous vertical column — a
    French/English paragraph, or a Chinese paragraph with normal line breaks
    between real sentences, is returned unchanged.
    """
    if not text:
        return text
    text = CJK_GAP_SAMELINE.sub("", text)
    text = _glue_vertical_columns(text)
    return text
