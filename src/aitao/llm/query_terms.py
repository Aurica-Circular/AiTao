# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Salient-term extraction for retrieval probes (US-17c).

Full natural-language questions drown full-text search in stopwords ("Quelle
est la date de…" matches every French contract before the document actually
asked about). The keyword-anchoring probe therefore searches only the salient
terms of the question — words that carry meaning (names, nouns, years).

FR + EN stopwords only: other languages pass through untouched, which keeps
the probe usable (if less selective) on multilingual corpora.

Pure logic, no LLM call. Core feature — no license gating.
"""

import re
import unicodedata
from typing import List

from aitao.core.cjk_glue import CJK_GAP_ANY_WHITESPACE, CJK_RUN

# Compact FR + EN stopword list (compared accent-free, lowercase)
_STOPWORDS = frozenset(
    """
    le la les un une des du de d au aux et ou mais donc or ni car que qui quoi
    dont ce cette ces cet se sa son ses mon ma mes ton ta tes notre votre leur
    leurs nos vos je tu il elle ils elles on nous vous y en ne pas plus moins
    tres trop bien peu est sont suis es etes sommes etait etaient sera seront
    etre avoir a ai as ont avez avons avait avaient aura auront fait faire
    peut peux peuvent pouvez pouvons pour par sur sous dans avec sans chez
    vers entre apres avant pendant depuis jusqu jusque comme aussi alors ainsi
    quel quelle quels quelles comment pourquoi quand combien lequel laquelle
    si oui non bonjour bonsoir salut coucou merci svp stp
    the a an and or but nor so yet is are was were am be been being do does
    did done have has had having of to in on at by for with about against
    from into over under this that these those it its my your our their his
    her i you he she we they me him us them what which who whom whose when
    where why how can could may might must shall should will would please
    hello hi hey thanks thank
    """.split()
)


def _unaccent(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def text_contains_term(text: str, terms: List[str]) -> bool:
    """True when ``text`` contains at least one of the salient ``terms``.

    Accent- and case-insensitive substring check — deliberately loose: this
    feeds the adequacy gate, where a false "anchored" only means the model
    answers (grounded by the context rules) instead of asking the user to
    reformulate.
    """
    if not text or not terms:
        return False
    haystack = _unaccent(text.lower())
    return any(_unaccent(term.lower()) in haystack for term in terms)


def salient_terms(query: str) -> List[str]:
    """Extract meaning-carrying terms from a natural-language question.

    Keeps original spelling (the search engine handles accents/typos); only
    the stopword comparison is accent- and case-insensitive. Single letters
    are dropped, numbers (years, amounts) are kept.
    """
    words = re.findall(r"\w[\w-]*", query, re.UNICODE)
    return [
        word
        for word in words
        if len(word) >= 2 and _unaccent(word.lower()) not in _STOPWORDS
    ]


# CJK Unified Ideographs (+ Extension A + Compatibility). Used to split runs
# into character bigrams for anchoring (US-89-4). Shared with the
# content-side gluer (indexation/item_preparer.py) via core/cjk_glue.py
# (DRY, ÉPIC-31 US-111) — this file keeps its own "any whitespace" gap
# behaviour (unlike the content side's stricter, newline-conservative rule).
_CJK_RUN = CJK_RUN
_CJK_GAP = CJK_GAP_ANY_WHITESPACE


def _cjk_bigrams(run: str) -> List[str]:
    """Character bigrams of a CJK run; the run itself when 2 chars or fewer."""
    return [run] if len(run) <= 2 else [run[i:i + 2] for i in range(len(run) - 1)]


def anchor_terms(query: str) -> List[str]:
    """Salient terms PLUS CJK character bigrams — units for the anchoring gate.

    CJK has no word boundaries, so ``salient_terms`` returns a whole run
    ('事假扣薪'), which a document rarely contains verbatim even when it clearly
    discusses the topic (it has 事假 and 扣薪 separately). Decomposing each run
    into character bigrams lets the gate anchor on the parts — a document with
    事假 + 扣薪 anchors, one sharing nothing is still rejected. Latin terms are
    unchanged. Deterministic, no LLM. US-89-4.
    """
    terms = list(salient_terms(query))
    # NFKC folds Kangxi radicals → CJK and we glue spaced ideographs, mirroring
    # the query distiller, so bigrams match the documents' regular CJK content.
    norm = _CJK_GAP.sub("", unicodedata.normalize("NFKC", query))
    for run in _CJK_RUN.findall(norm):
        terms.extend(_cjk_bigrams(run))
    # Dedupe, preserve order.
    seen: set = set()
    out: List[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out
