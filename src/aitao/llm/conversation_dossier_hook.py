# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""Conversation dossier — RAGEngine integration hook (US-102, US-103).

``conversation_dossier.py`` is pure logic (rule table, session replay,
decision) with zero I/O, so it stays trivially unit-testable with fakes. This
module is the thin glue that wires it to a real ``RAGEngine``: it supplies
the resolver callbacks (reusing the engine's existing pinning/retrieval/
anchoring methods — no parallel infrastructure, per the US-102 brief) and
applies the resulting decision to a turn's context. Kept as its own module
(rather than methods on RAGEngine) so rag_engine.py — already well past the
project's file-size guideline before US-102 — does not grow further; see the
US-102 report for that trade-off.

US-103 addition: ``pin_turn_referent`` optionally fills a caller-supplied
``state`` dict with the dossier's full view of the turn (action, referent,
candidate stack, and — on a held follow-up with >= 2 candidates — the paths
the question's OWN words anchor in full-text search). The upstream context
gate (llm/context_gate.py) consumes that dict to prove a refusal or an
ambiguity; keeping the I/O here preserves the gate's purity.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aitao.llm.conversation_dossier import (
    ACTION_HOLD,
    ACTION_NONE,
    NEW,
    DossierEntry,
    classify_turn,
    resolve_dossier_state,
)
from aitao.llm.rag_models import ContextDocument

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, type checking only
    from aitao.llm.rag_engine import RAGEngine

# ``state`` dict keys filled for the context gate (US-103). Documented here,
# read in llm/context_gate.py — plain data, so neither module imports the other:
#   action:         conversation_dossier ACTION_* of the live turn
#   turn_kind:      NEW / FOLLOWUP / ORDINAL_* / "named" (a name resolved
#                   this turn — the referent was released)
#   referent:       {"path", "title"} of the held/recalled referent, or None
#   stack:          [{"path", "title"}, ...] most recent first, or None when
#                   the replay was skipped (new-topic / named short-circuits)
#   anchored_paths: set of STACK-candidate paths whose raw document contains
#                   one of the CURRENT question's own salient terms verbatim
#                   — only computed on a held follow-up with >= 2 candidates
#                   (the ambiguity-proof probe); None otherwise or when the
#                   probe is unusable (never guess).
STATE_TURN_NAMED = "named"


def _fill_state(
    state: Optional[Dict[str, Any]],
    *,
    action: str,
    turn_kind: str,
    referent: Optional[DossierEntry] = None,
    stack: Optional[List[DossierEntry]] = None,
    anchored_paths: Optional[set] = None,
) -> None:
    if state is None:
        return
    state["action"] = action
    state["turn_kind"] = turn_kind
    state["referent"] = (
        {"path": referent.path, "title": referent.title} if referent else None
    )
    state["stack"] = (
        [{"path": e.path, "title": e.title} for e in stack]
        if stack is not None
        else None
    )
    state["anchored_paths"] = anchored_paths


def pin_turn_referent(
    engine: "RAGEngine",
    messages: List[Dict[str, str]],
    last_user_idx: int,
    current_text: str,
    has_new_name_this_turn: bool,
    state: Optional[Dict[str, Any]] = None,
) -> "tuple[Optional[ContextDocument], bool]":
    """US-102 hook: ``(referent_to_pin, zero_every_other_candidate)``.

    Recomputed from scratch on every call — 1st invariant (stateless &
    deterministic, étude §6.4): replays every PAST user turn with
    ``conversation_dossier.resolve_dossier_state`` to know the session's
    referent stack, then decides whether THIS turn should hold or recall one.
    Cheap classification of the CURRENT turn runs first so the (more
    expensive, retrieval-based) full-session replay is skipped entirely on an
    ordinary/new-topic turn — the common case.

    ``state`` (US-103): when provided, filled with the dossier view the
    upstream context gate consumes (see the key table above).
    """
    if has_new_name_this_turn:
        _fill_state(state, action=ACTION_NONE, turn_kind=STATE_TURN_NAMED)
        _log(engine, "referent released: a new document name was resolved this turn")
        return None, False

    turn_kind = classify_turn(current_text)
    if turn_kind == NEW:
        _fill_state(state, action=ACTION_NONE, turn_kind=NEW)
        _log(
            engine,
            "new topic: no anaphor/ordinal signal and no name resolved — "
            "normal retrieval applies",
        )
        return None, False

    past_user_turns = [
        m.get("content", "")
        for m in messages[:last_user_idx]
        if m.get("role") == "user" and m.get("content")
    ]

    def _resolve_named(text: str) -> Optional[DossierEntry]:
        docs = engine._keyword_pinned_docs(text)
        return DossierEntry(path=docs[0].path, title=docs[0].title) if docs else None

    def _resolve_fallback(text: str) -> Optional[DossierEntry]:
        doc = _retrieval_fallback(engine, text)
        return DossierEntry(path=doc.path, title=doc.title) if doc else None

    decision, stack = resolve_dossier_state(
        past_user_turns,
        current_text,
        has_new_name_this_turn,
        _resolve_named,
        _resolve_fallback,
        logger=engine.logger,
        debug=engine.reliability_debug,
    )

    # Ambiguity-proof probe (US-103, gate outcome 3a): on a held follow-up
    # with several candidate referents, check which CANDIDATES' documents
    # contain the question's own salient terms verbatim. A non-held candidate
    # carrying the question's words while the held one does not = deterministic
    # proof of a conflict. Restricted to the stack by construction (only the
    # session's own referents are checked) and verbatim by construction — the
    # search-ranking probe (_keyword_anchored_paths) is deliberately NOT used
    # here: its all-words full-text semantics returns zero hits on a CJK
    # anaphoric question ("它的員工人數…") whose leading run matches nothing,
    # even when the terms plainly appear in a candidate. Only runs in this
    # (rare) held-with-2+-candidates configuration.
    anchored_paths: Optional[set] = None
    if decision.action == ACTION_HOLD and len(stack) >= 2:
        anchored_paths = _stack_anchor_probe(engine, current_text, stack)
    _fill_state(
        state,
        action=decision.action,
        turn_kind=turn_kind,
        referent=decision.referent,
        stack=stack,
        anchored_paths=anchored_paths,
    )

    if decision.referent is None:
        return None, False
    doc = engine._fetch_raw_doc(decision.referent.path)
    return doc, doc is not None


def _stack_anchor_probe(
    engine: "RAGEngine", text: str, stack: List[DossierEntry]
) -> Optional[set]:
    """Stack candidates whose raw document carries the question's own terms.

    Verbatim, accent-/case-insensitive check (query_terms.text_contains_term,
    the same helper the adequacy scoring uses) of the question's salient terms
    + CJK bigrams (anchor_terms, US-89-4) against each candidate's raw stored
    content — the gate's ambiguity proof (outcome 3a). Returns None when the
    probe is unusable (no terms, fetch failure): the gate then never asks
    (never guess).
    """
    try:
        from aitao.llm.query_terms import anchor_terms, text_contains_term

        terms = anchor_terms(text)
        if not terms:
            return None
        anchored: set = set()
        for entry in stack:
            doc = engine._fetch_raw_doc(entry.path)
            if doc is None:
                continue
            haystack = f"{doc.path} {doc.title} {doc.content}"
            if text_contains_term(haystack, terms):
                anchored.add(entry.path)
        return anchored
    except Exception:
        return None


def _retrieval_fallback(engine: "RAGEngine", text: str) -> Optional[ContextDocument]:
    """Referent of ONE past turn when no name resolves it (I-11 case).

    Reuses the SAME retrieval + keyword-anchoring the live gate uses
    (``search_chunks_context`` + ``_keyword_anchored_paths``) so a document
    referenced by its CONTENT ("the document about 範例玻璃"), not its title,
    still becomes a referent. Returns None rather than guess when the
    anchoring probe is unusable or finds nothing — a never-guess resolver
    keeps the dossier itself trustworthy.
    """
    try:
        chunks = engine.search_chunks_context(text)
    except Exception:
        return None
    if not chunks:
        return None
    anchored = engine._keyword_anchored_paths(text)
    if not anchored:
        return None
    for chunk in chunks:
        if chunk.path in anchored:
            return engine._fetch_raw_doc(chunk.path)
    return None


def dossier_solo_anchor(
    context: List[ContextDocument], keep_path: str
) -> List[ContextDocument]:
    """Zero every candidate's anchoring score except ``keep_path`` (US-102).

    Additive per étude §6.4: no document is removed from ``context`` (the
    model still sees all of them), only the anchoring SIGNAL is corrected so
    a document that merely shares a literal word with the follow-up (I-11:
    "pages") is never again mistaken for grounding evidence.
    """
    return [
        d if d.path == keep_path else d.model_copy(update={"score": 0.0})
        for d in context
    ]


def _log(engine: "RAGEngine", message: str) -> None:
    """US-102 5th invariant: dossier decisions are always logged.

    INFO when ``[rag] reliability_debug`` is true (the rollout default, so
    real gains can be measured), DEBUG otherwise — never silenced.
    """
    if engine.reliability_debug:
        engine.logger.info(message)
    else:
        engine.logger.debug(message)
