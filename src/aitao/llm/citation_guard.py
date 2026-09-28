# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Anti-fabrication citation guard (US-17c).

Post-processes an LLM answer: any cited file that is NOT among the sources
actually retrieved for this question is treated as fabricated and removed,
replaced by an explicit marker. Origin: demo of June 5th — the model cited
`MCP-Generated-Prompt-for-Humane-Inventions.txt`, a file absent from the index.

Streaming responses cannot be rewritten after the fact, so the guard instead
appends a warning naming the unverified sources.

Pure logic, no LLM call. Core feature — no license gating.
"""

import re
from pathlib import PurePath
from typing import Any, Callable, List, Optional, Set, Tuple

REMOVED_MARKER = "[source non vérifiée retirée]"

# File-like references in the answer: a path or bare filename ending in a
# known document extension. No spaces inside the match — models cite exact
# names, and allowing spaces would swallow surrounding prose.
#
# Public (US-104): response_reader / source_attribution reuse this exact
# extraction + matching logic to detect which context doc a sentence names,
# rather than re-implementing citation matching a second time.
CITATION_RE = re.compile(
    r"[^\s\"'`«»()\[\]{},;:]+"
    r"\.(?:pdf|docx?|xlsx?|pptx?|md|markdown|txt|csv|tsv|html?|epub|odt|rtf"
    r"|png|jpe?g|tiff?|json|ya?ml|toml)\b",
    re.IGNORECASE,
)


def allowed_names(context_docs: List[Any]) -> Set[str]:
    """Lowercased identifiers under which a retrieved source may be cited."""
    allowed: Set[str] = set()
    for doc in context_docs:
        path = str(getattr(doc, "path", "") or "")
        if path:
            allowed.add(path.lower())
            allowed.add(PurePath(path).name.lower())
            allowed.add(PurePath(path).stem.lower())
        title = str(getattr(doc, "title", "") or "")
        if title:
            allowed.add(title.lower())
    allowed.discard("")
    return allowed


def is_known_citation(candidate: str, allowed: Set[str]) -> bool:
    """True when ``candidate`` matches a retrieved source.

    Besides exact match on the path / filename / stem, a candidate counts as
    known when it is a **word-boundary suffix** of an allowed name. The
    citation regex stops at whitespace, so a filename with spaces
    ("Ergonomie des interfaces - Dunod.pdf") is captured only as its tail
    ("Dunod.pdf"); without the suffix check that tail was wrongly flagged as
    fabricated (US-20 follow-up). The boundary guard keeps it precise:
    "report.pdf" does NOT match "finalreport.pdf".
    """
    cand = candidate.lower()
    name = PurePath(candidate).name.lower()
    stem = PurePath(candidate).stem.lower()
    if cand in allowed or name in allowed or stem in allowed:
        return True
    for known in allowed:
        if known.endswith(cand):
            prefix_len = len(known) - len(cand)
            if prefix_len == 0 or not known[prefix_len - 1].isalnum():
                return True
        # A cited TITLE is a prefix of an indexed name up to a version/date
        # segment boundary — Chinese RH docs are named <title>-第X版-date.pdf and
        # the model cites just <title>. The candidate must be the START of a name
        # actually in the context (>= 4 chars, boundary follows), so it references
        # a real retrieved doc, never an invention. US-17c.
        if len(stem) >= 4 and known.startswith(stem) and known[len(stem):][:1] in (
            "-", "_", " ", "　", "."
        ):
            return True
    return False


def find_fabricated_citations(
    answer: str, context_docs: List[Any]
) -> List[str]:
    """Return file references in ``answer`` absent from the retrieved sources."""
    allowed = allowed_names(context_docs)
    fabricated: List[str] = []
    for match in CITATION_RE.finditer(answer):
        candidate = match.group(0)
        if not is_known_citation(candidate, allowed) and candidate not in fabricated:
            fabricated.append(candidate)
    return fabricated


def sanitize_citations(
    answer: str, context_docs: List[Any]
) -> Tuple[str, List[str]]:
    """Remove fabricated citations from ``answer``.

    Returns:
        (clean_answer, removed): each fabricated file reference is replaced by
        ``REMOVED_MARKER``; ``removed`` lists what was taken out (empty when
        the answer only cites retrieved sources).
    """
    removed = find_fabricated_citations(answer, context_docs)
    if not removed:
        return answer, []
    clean = answer
    for fabricated in removed:
        clean = clean.replace(fabricated, REMOVED_MARKER)
    # Collapse marker repetitions left by multi-mention citations
    clean = re.sub(
        rf"(?:{re.escape(REMOVED_MARKER)}[\s,;]*){{2,}}",
        f"{REMOVED_MARKER} ",
        clean,
    )
    return clean, removed


def build_stream_citation_warning(removed: List[str]) -> str:
    """Trailing warning for streaming responses (text already sent)."""
    listed = ", ".join(f"« {name} »" for name in removed)
    plural = "s" if len(removed) > 1 else ""
    return (
        f"\n\n⚠️ Avertissement : la réponse ci-dessus cite une source{plural} "
        f"introuvable{plural} dans vos documents indexés : {listed}. "
        f"Ne vous fiez pas à cette référence."
    )


def find_deleted_citations(answer: str, context_docs: List[Any]) -> List[str]:
    """Return retrieved sources cited in ``answer`` that are in the trash.

    A source counts as cited when its filename, stem or full path appears in
    the answer. Trash status is deterministic (the registry), so this does not
    depend on the model heeding the in-context "deleted file" notice (US-28a) —
    granite4 was observed ignoring it.
    """
    try:
        from aitao.indexation.trash import get_trash_registry

        registry = get_trash_registry()
    except Exception:
        return []

    answer_lower = answer.lower()
    deleted: List[str] = []
    for doc in context_docs:
        path = str(getattr(doc, "path", "") or "")
        if not path or not registry.is_trashed(path):
            continue
        name = PurePath(path).name
        stem = PurePath(path).stem
        if (
            path.lower() in answer_lower
            or name.lower() in answer_lower
            or stem.lower() in answer_lower
        ):
            if name not in deleted:
                deleted.append(name)
    return deleted


def build_deleted_files_notice(deleted: List[str]) -> str:
    """Deterministic notice appended when the answer cites trashed sources."""
    listed = ", ".join(f"« {name} »" for name in deleted)
    plural = "s" if len(deleted) > 1 else ""
    return (
        f"\n\nℹ️ Note : le{plural} fichier{plural} {listed} "
        f"n'existe{'nt' if plural else ''} plus sur le disque "
        f"(supprimé{plural}, en attente de purge de l'index)."
    )


def find_multi_source_token(
    question: str, context_docs: List[Any]
) -> Tuple[Any, List[str]]:
    """Distinctive token of the question carried by >= 2 retrieved documents.

    Returns ``(token, [paths])`` for the token with the most bearer documents
    (>= 2), else ``(None, [])``. A bearer is a context document whose text
    contains the token verbatim (NFKC-folded) — guaranteed for exact-token pinned
    docs by the token-centered excerpt (US-89-2 / US-90). Full paths so the user
    can locate each source unambiguously. US-90-4.
    """
    import unicodedata

    from aitao.llm.query_distiller import structured_tokens

    best_token: Any = None
    best_sources: List[str] = []
    for token in structured_tokens(question, min_nonlatin_len=2):
        needle = unicodedata.normalize("NFKC", token).lower()
        sources: List[str] = []
        seen: Set[str] = set()
        for doc in context_docs:
            content = str(getattr(doc, "content", "") or "")
            if needle not in unicodedata.normalize("NFKC", content).lower():
                continue
            label = str(getattr(doc, "path", "") or "") or str(
                getattr(doc, "title", "") or ""
            )
            if label and label not in seen:
                seen.add(label)
                sources.append(label)
        if len(sources) > len(best_sources):
            best_token, best_sources = token, sources
    return (best_token, best_sources) if len(best_sources) >= 2 else (None, [])


def build_multi_source_notice(
    question: str,
    context_docs: List[Any],
    bearer_counter: Optional[Callable[[Any], int]] = None,
) -> str:
    """US-90-4 — deterministic notice listing EVERY retrieved document that holds
    a distinctive token of the question (email, ID, reference, CJK run…).

    Local models routinely cite a single source even when several documents
    contain the answer (granite4 observed citing 1 of 3). This notary-style note
    guarantees the user sees them all, independent of the model's compliance.
    Appended to the answer like ``build_deleted_files_notice``; returns "" when no
    token of the question is carried by >= 2 documents.

    ``bearer_counter`` (optional) returns the true number of indexed documents
    holding the token. When it exceeds the listed sources — pinning capped the
    context (``pin_max_docs``) — the note states "au moins N" rather than
    under-counting (US-90-5). Without it, only the listed sources are counted.
    """
    token, sources = find_multi_source_token(question, context_docs)
    if not sources:
        return ""
    total = len(sources)
    if bearer_counter is not None and token is not None:
        try:
            total = max(total, int(bearer_counter(token)))
        except Exception:
            pass
    listed = "\n".join(f"• {src}" for src in sources)
    if total > len(sources):
        head = (
            f"Cette information apparaît dans au moins {total} documents "
            f"(les {len(sources)} plus pertinents) :"
        )
    else:
        head = f"Cette information apparaît dans {total} documents :"
    return f"\n\n📎 {head}\n{listed}"
