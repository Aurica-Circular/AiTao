# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Named-document resolver (US-RAG-name).

When a user references a document by name ("translate 20260701_Assurance Maladie
taiwan.pdf", "what is the CréationEmployeur file about?"), content-based retrieval
fails: the query's surrounding words — often in a language different from the
document's content — dilute the filename signal and push the target out of the
top-K. The RAG then wrongly reports the file as missing.

This module resolves such references deterministically against the indexed
*titles* (not content): it asks Meilisearch for title matches, then keeps only
candidates whose title is substantially covered by the query words. The RAG pins
the resolved documents into the context, guaranteeing they reach the model.

No LLM, no guessing — pure measurement, in the spirit of the mojibake detector.
"""

import os
import re
import unicodedata
from pathlib import PurePath
from typing import Any, Dict, List, Optional, Protocol

# Extensions we treat as an explicit "this is a file" signal in the query. Used
# only to decide whether to emit the "named but not indexed" message — matching
# itself is title-based and does not require an extension.
_KNOWN_EXTENSIONS = frozenset({
    "pdf", "docx", "doc", "odt", "rtf", "txt", "md", "csv",
    "xlsx", "xls", "ods", "pptx", "ppt", "odp",
    "png", "jpg", "jpeg", "tiff", "tif", "gif", "webp", "heic",
})

# Title tokens that are too generic to anchor a filename reference on their own.
_GENERIC_TOKENS = frozenset({
    "document", "doc", "fichier", "file", "rapport", "report", "note", "scan",
    "copy", "copie", "final", "draft", "version", "v1", "v2", "vf", "page",
})

_DEFAULT_COVERAGE = 0.8   # fraction of a title's tokens that must appear in the query
_DEFAULT_MAX_DOCS = 5     # never pin more than this many named documents

# Framing words dropped from the *search* text (not the coverage check) so they
# don't drag unrelated titles ahead of the named file. The real precision guard
# stays the coverage filter on the full query, so this list only needs to remove
# the worst offenders — short function words and document-request verbs. A bare
# "2" ("the first 2 pages") is the classic case: it matches every "第2版" title.
_STOPWORDS = frozenset({
    # French
    "le", "la", "les", "un", "une", "des", "du", "moi", "ce", "cet", "cette",
    "quoi", "parle", "donne", "donnes", "traduis", "traduction", "traduire",
    "resume", "résume", "résumé", "resumé", "premiere", "premieres", "première",
    "premières", "page", "pages", "français", "francais", "fichier", "document",
    "stp", "svp", "sur", "pour", "avec", "dans", "que", "qui",
    # English
    "the", "give", "please", "this", "that", "file", "document", "translate",
    "translation", "summarize", "summary", "first", "page", "pages", "about",
    "what", "into", "french",
})


def _search_text(query: str) -> str:
    """Denoise the query for title search: drop stopwords and tiny tokens.

    Keeps distinctive filename words (long, numeric like ``20260701``, or CJK)
    and removes framing noise. Coverage is still measured on the full query, so
    dropping a legitimate word here can only affect recall, never precision;
    falls back to the raw tokens if denoising leaves nothing.
    """
    kept = [t for t in _tokens(query) if len(t) >= 3 and t not in _STOPWORDS]
    return " ".join(kept) if kept else " ".join(_tokens(query))


class _TitleSearcher(Protocol):
    def search_titles(self, query: str, limit: int = 10) -> List[Dict[str, Any]]: ...


def _normalize(text: str) -> str:
    """NFC-normalize, lowercase, and split on non-alphanumerics (CJK preserved).

    NFC folds the macOS NFD/NFC filename variants together so a name typed one way
    still matches a title stored the other way. Underscores, dots and dashes in
    filenames become token separators.
    """
    text = unicodedata.normalize("NFC", text).lower()
    # Replace anything that is not a letter/number (any script) with a space.
    return re.sub(r"[^0-9a-zÀ-￿]+", " ", text)


def _tokens(text: str) -> List[str]:
    return [t for t in _normalize(text).split() if t]


def _title_tokens(title: str) -> List[str]:
    """Tokens of a title, minus a trailing extension token if present."""
    toks = _tokens(title)
    if toks and toks[-1] in _KNOWN_EXTENSIONS:
        toks = toks[:-1]
    return toks


def _is_distinctive(tokens: List[str]) -> bool:
    """True if the title carries at least one token specific enough to anchor on.

    Guards against pinning a document whose whole title is generic/common words
    (e.g. "document", "rapport") that happen to appear in an unrelated query.
    """
    for t in tokens:
        if t in _GENERIC_TOKENS:
            continue
        if len(t) >= 4 or t.isdigit() or not t.isascii():  # long, numeric, or CJK
            return True
    return False


_PATH_MENTION_RE = re.compile(
    r"(?:~|/|[A-Za-z]:[\\/])[^\n\t\"?*<>|]+?\.(?:" + "|".join(_KNOWN_EXTENSIONS) + r")\b",
    re.IGNORECASE,
)


def _expand_path(p: str) -> str:
    """Expand ${VARS}/~, NFC-normalize and strip a trailing slash for comparison."""
    expanded = os.path.expanduser(os.path.expandvars(p))
    return unicodedata.normalize("NFC", expanded).rstrip("/")


def _path_under_any(path: str, include_paths: List[str]) -> bool:
    """True if ``path`` lies within one of the configured scan folders."""
    target = _expand_path(path)
    for inc in include_paths or []:
        base = _expand_path(inc)
        if base and (target == base or target.startswith(base + "/")):
            return True
    return False


def query_names_a_file(query: str) -> bool:
    """True if the query contains an explicit filename token (``name.ext``).

    Only used to decide whether to tell the user a named file is not indexed —
    resolution itself does not require an extension.
    """
    for m in re.finditer(r"\.([0-9a-z]{2,5})\b", query.lower()):
        if m.group(1) in _KNOWN_EXTENSIONS:
            return True
    return False


class NamedDocMatch:
    """A document the query referenced by name, resolved against the index."""

    __slots__ = ("path", "title", "coverage")

    def __init__(self, path: str, title: str, coverage: float):
        self.path = path
        self.title = title
        self.coverage = coverage

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"NamedDocMatch(title={self.title!r}, coverage={self.coverage:.2f})"


def resolve_named_documents(
    query: str,
    searcher: Optional[_TitleSearcher],
    *,
    coverage_threshold: float = _DEFAULT_COVERAGE,
    max_docs: int = _DEFAULT_MAX_DOCS,
) -> List[NamedDocMatch]:
    """Resolve documents the query names explicitly, against indexed titles.

    A candidate title matches when a fraction >= ``coverage_threshold`` of its
    (extension-stripped) tokens appear in the query AND the title carries at least
    one distinctive token. All matches at/above the threshold are returned
    (deduplicated by path, most-covered first) so genuine duplicates — e.g. the
    same file indexed under NFD and NFC forms — are both pinned.
    """
    if not query or searcher is None:
        return []

    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []

    candidates = searcher.search_titles(_search_text(query), limit=max(20, max_docs * 2))
    if not isinstance(candidates, list):  # defensive: backend error / bad stub
        return []
    matches: Dict[str, NamedDocMatch] = {}
    for cand in candidates:
        path = cand.get("path")
        title = cand.get("title") or ""
        if not path:
            continue
        t_tokens = _title_tokens(title)
        if not t_tokens or not _is_distinctive(t_tokens):
            continue
        covered = sum(1 for t in t_tokens if t in q_tokens) / len(t_tokens)
        if covered >= coverage_threshold:
            prev = matches.get(path)
            if prev is None or covered > prev.coverage:
                matches[path] = NamedDocMatch(path, title, covered)

    ranked = sorted(matches.values(), key=lambda m: m.coverage, reverse=True)
    return ranked[:max_docs]


def _title_is_covered(title: str, q_tokens: set, threshold: float) -> bool:
    tt = _title_tokens(title)
    if not tt or not _is_distinctive(tt):
        return False
    return sum(1 for t in tt if t in q_tokens) / len(tt) >= threshold


def named_reference_advisory(
    query: str,
    context: Optional[List[Any]],
    include_paths: Optional[List[str]],
    *,
    coverage_threshold: float = _DEFAULT_COVERAGE,
) -> Optional[str]:
    """Deterministic advisory when the query names a file that isn't in context.

    Fires only when the query names a file explicitly (``name.ext``) AND no
    retrieved/pinned document matches that name — so the assistant states the
    fact instead of confabulating "I couldn't find it". When the query gives a
    path, the message distinguishes (US-RAG-name):
      - a path inside a configured scan folder → "not indexed yet, run a scan";
      - a path outside the scan folders → "this folder isn't watched (config)".
    Returns None (proceed normally) for ordinary questions or when the named file
    is present in the context. Messages are in French to match the refusal gate.
    """
    if not query or not query_names_a_file(query):
        return None

    q_tokens = set(_tokens(query))
    for doc in context or []:
        title = str(getattr(doc, "title", "") or "")
        if _title_is_covered(title, q_tokens, coverage_threshold):
            return None  # the named file was retrieved/pinned — answer normally

    m = _PATH_MENTION_RE.search(query)
    if m:
        path = m.group(0)
        name = PurePath(path).name
        if _path_under_any(path, include_paths or []):
            return (
                f"Le fichier « {name} » se trouve dans un dossier surveillé mais "
                "n'est pas encore indexé. Lancez une indexation "
                "(./aitao.sh scan run) puis reposez votre question."
            )
        return (
            f"Le chemin « {path} » n'est pas dans vos dossiers à indexer "
            "(paramètre indexing.include_paths). Ajoutez son dossier à la "
            "configuration, puis lancez un scan."
        )

    return (
        "Le fichier que vous nommez n'est pas présent dans vos documents indexés. "
        "Vérifiez qu'il se trouve dans un dossier surveillé, puis indexez-le."
    )
