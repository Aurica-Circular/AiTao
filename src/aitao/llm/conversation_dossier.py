# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""Conversation dossier — the session's referent document(s) (US-102).

ÉPIC-30 brique 1 (ETUDE-FIABILITE.md §6 brique 1, §6.6 "connexion
rationnelle"). Fixes incident I-11: a follow-up turn ("Combien de pages
contient-il ?") used to restart retrieval from scratch on the follow-up's own
words alone, and could land on a document that only shares a literal word
with the question (e.g. "pages") instead of the document the conversation was
actually about.

This module is PURE LOGIC (no I/O, no LLM — étude §6.4, "rules first"): it
takes plain strings and an injected resolver callback, and returns a
deterministic decision plus its reason (5th invariant: traceable). All I/O
(retrieval, title search) lives in the resolver callbacks the caller
(RAGEngine, see rag_engine.py::_conversation_dossier_pin) supplies — this
keeps the module trivially unit-testable with fakes.

Design (see ETUDE-FIABILITE.md §6.6 and the US-102 brief):
  - The dossier is an ORDERED stack of past referents, most-recent first. It
    is rebuilt from scratch on every call by replaying every past user turn
    in order (1st invariant: stateless & deterministic — no server-side
    session storage; same history -> same dossier).
  - Rule table for classifying a turn (cheapest/most-specific check first):
      1. the turn resolves a document BY NAME this turn (title, exact
         token, or verbatim phrase — reuses RAGEngine's existing pinning) ->
         NEW: that document becomes the referent, unconditionally (a named
         reference always wins over an anaphor/ordinal pattern in the same
         sentence).
      2. else the turn is an ORDINAL reference ("le premier document",
         "reviens au premier", 第一 / "le dernier document", 最後/最后) ->
         recalls a SPECIFIC past referent (first ever / most recent).
      3. else the turn carries an ANAPHOR ("il", "elle", "ce document", "le
         document", "ce fichier", 它, 它的, 这个, 這個) OR is short and
         question-like -> FOLLOW-UP: the current top of the dossier is held.
         Impersonal French "il" ("y a-t-il", "il y a", "s'il", "il faut",
         "il existe", "il s'agit"...) is stripped before the anaphor match —
         an existential question is a NEW topic, not a follow-up.
      4. else -> NEW TOPIC: no override; normal retrieval decides, and (for
         past turns) the resolver's retrieval-based fallback may discover a
         new referent for the replay.
  - On a FOLLOW-UP or ORDINAL turn, the resolved referent must be pinned and
    become the ONLY anchored document (see rag_engine.py's
    ``_dossier_solo_anchor``) — additive per étude §6.4: nothing is removed
    from the retrieved context, only the anchoring SCORE of the
    non-referent candidates is zeroed, so they are never mistaken again for
    grounding evidence (the exact I-11 failure).

What this module intentionally does NOT do (documented per the US-102 brief):
  - It does not parse assistant answers for "cited source" mentions. The
    golden bench's multi-turn scenarios never carry a scripted assistant
    answer at all (deterministic, non-LLM turns), so that signal is unusable
    here; user-named references + the retrieval/anchoring fallback already
    cover the two target scenarios (I-11, "fil de session"). If a future
    scenario needs it, add a dedicated, clearly-scoped parser rather than
    guessing at prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Rule table — cheap, deterministic text patterns (no I/O). Kept intentionally
# small (étude §6.4: "keep the rule table small and documented").
# ---------------------------------------------------------------------------

# Anaphoric references that hold the current referent instead of naming a new
# one. FR + EN pronouns/demonstratives (word-boundary regex) + simplified/
# traditional Chinese (plain substrings — CJK has no word boundaries).
_ANAPHOR_PATTERNS = (
    r"\bil\b",
    r"\belle\b",
    r"\bce document\b",
    r"\ble document\b",
    r"\bce fichier\b",
    r"\bcelui-ci\b",
    r"\bcelui-là\b",
    r"\bit\b",
    r"\bthis document\b",
    r"\bthe document\b",
    r"\bthis file\b",
    "它的",  # "its" (zh)
    "它",  # "it" (zh)
    "这个",  # "this" (zh simplified)
    "這個",  # "this" (zh traditional)
)

# Impersonal/existential French "il" is NOT an anaphor: these constructions are
# neutralised BEFORE the anaphor check, so "Y a-t-il un document sur X ?" in
# mid-session opens a NEW topic instead of holding the previous referent (the
# mirror of I-11: wrong referent HELD instead of wrong referent picked). True
# verb-subject inversion ("contient-il ?" — I-11 itself) keeps matching.
# Deliberately short, auditable list — do not grow beyond clearly impersonal
# forms.
_IMPERSONAL_IL = re.compile(
    r"y a-t-il|il y a|s['’]il|il faut|il existe|il est possible"
    r"|est-il possible|il s['’]agit",
    re.IGNORECASE,
)

# Ordinal references: "the first/last document" recalls a SPECIFIC past
# referent, not just "the current one". FR (masc/fem) + zh.
_ORDINAL_FIRST_PATTERNS = (r"\bpremier\b", r"\bpremière\b", "第一")
_ORDINAL_LAST_PATTERNS = (
    r"\bdernier\b",
    r"\bdernière\b",
    "最後",  # traditional
    "最后",  # simplified
)

# A short question with no anaphor still behaves like a follow-up ("et la
# suite ?", "combien de pages ?") — capped so an ordinary longer question
# (turn 3 of "fil de session": 10 words) is never mistaken for one.
_SHORT_QUESTION_MAX_WORDS = 7
_QUESTION_CUES = re.compile(
    r"\b(combien|quoi|qui|comment|quel|quelle|pourquoi|what|who|how|which)\b",
    re.IGNORECASE,
)

# Turn classifications (private to this module + rag_engine's integration).
NEW = "new"
FOLLOWUP = "followup"
ORDINAL_FIRST = "ordinal_first"
ORDINAL_LAST = "ordinal_last"

# Dossier decision actions.
ACTION_NONE = "none"
ACTION_HOLD = "hold"
ACTION_RECALL = "recall"


def _matches_any(text: str, patterns) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _is_short_question(text: str) -> bool:
    words = text.strip().split()
    if not words or len(words) > _SHORT_QUESTION_MAX_WORDS:
        return False
    return "?" in text or bool(_QUESTION_CUES.search(text))


def classify_turn(text: str) -> str:
    """Cheap, text-only classification of ONE turn (no I/O, no dossier state).

    Ordinal patterns are checked before the anaphor/short-question ones so
    "reviens au premier document" (which also happens to be a question) is
    never mistaken for a plain follow-up.
    """
    if not text:
        return NEW
    if _matches_any(text, _ORDINAL_FIRST_PATTERNS):
        return ORDINAL_FIRST
    if _matches_any(text, _ORDINAL_LAST_PATTERNS):
        return ORDINAL_LAST
    # Impersonal-il forms must not count as anaphors; a stripped text is used
    # for the anaphor match only (the short-question rule still sees the
    # original, documented interplay: a SHORT impersonal question like "Y
    # a-t-il des congés payés ?" remains a contextual follow-up).
    text_for_anaphor = _IMPERSONAL_IL.sub(" ", text)
    if _matches_any(text_for_anaphor, _ANAPHOR_PATTERNS) or _is_short_question(text):
        return FOLLOWUP
    return NEW


@dataclass(frozen=True)
class DossierEntry:
    """One document the conversation has referred to (by path)."""

    path: str
    title: str = ""


@dataclass(frozen=True)
class DossierDecision:
    """What the live turn should do, and WHY (5th invariant: traceable)."""

    action: str  # ACTION_NONE | ACTION_HOLD | ACTION_RECALL
    referent: Optional[DossierEntry]
    reason: str


# A resolver turns one user turn's raw text into a referent, or None if it
# cannot establish one confidently (a resolver must never guess).
Resolver = Callable[[str], Optional[DossierEntry]]


def _recall_ordinal(stack: List[DossierEntry], kind: str) -> Optional[DossierEntry]:
    """Move the first/last entry of ``stack`` to the front; return it."""
    if not stack:
        return None
    target = stack[-1] if kind == ORDINAL_FIRST else stack[0]
    if stack[0].path != target.path:
        stack.remove(target)
        stack.insert(0, target)
    return target


def _log(logger, debug: bool, message: str, **metadata) -> None:
    if logger is None:
        return
    if debug:
        logger.info(message, metadata=metadata)
    else:
        logger.debug(message, metadata=metadata)


def build_dossier(
    past_user_turns: List[str],
    resolve_named: Resolver,
    resolve_fallback: Resolver,
    logger=None,
    debug: bool = True,
) -> List[DossierEntry]:
    """Rebuild the ordered referent stack (index 0 = most recent) by replaying
    every PAST user turn, oldest first — exactly as a live request would have
    seen them. Nothing is persisted between calls (1st invariant: stateless).

    ``resolve_named`` is the CHEAP, high-precision check (title/exact-token/
    verbatim-phrase pinning) — it runs on every turn and, when it fires,
    always wins (a named reference is never mistaken for an anaphor even if
    the same sentence also contains one). ``resolve_fallback`` is the more
    expensive retrieval + keyword-anchoring probe (I-11: a document named by
    its CONTENT, not its title) — it only runs for a turn that resolves to
    neither a name nor a follow-up/ordinal pattern, so an anaphoric turn is
    NEVER re-run through retrieval (that would reproduce I-11 by construction:
    "combien de pages" would itself anchor on any document containing "pages").
    """
    stack: List[DossierEntry] = []
    for idx, text in enumerate(past_user_turns):
        named = resolve_named(text)
        if named is not None:
            if not stack or stack[0].path != named.path:
                stack.insert(0, named)
            _log(logger, debug, "dossier replay: turn names a document", turn=idx, path=named.path)
            continue

        kind = classify_turn(text)
        if kind == FOLLOWUP:
            _log(logger, debug, "dossier replay: turn held (anaphor/short question)", turn=idx)
            continue
        if kind in (ORDINAL_FIRST, ORDINAL_LAST):
            recalled = _recall_ordinal(stack, kind)
            _log(
                logger, debug, "dossier replay: turn recalls an ordinal referent",
                turn=idx, kind=kind, path=recalled.path if recalled else None,
            )
            continue

        entry = resolve_fallback(text)
        if entry is not None and (not stack or stack[0].path != entry.path):
            stack.insert(0, entry)
            _log(
                logger, debug, "dossier replay: turn resolves a new topic via retrieval",
                turn=idx, path=entry.path,
            )
        else:
            _log(logger, debug, "dossier replay: turn resolves nothing usable", turn=idx)
    return stack


def decide(
    current_text: str,
    has_new_name_this_turn: bool,
    stack: List[DossierEntry],
) -> DossierDecision:
    """Decide what the LIVE (current) turn should do with the dossier stack."""
    if has_new_name_this_turn:
        return DossierDecision(
            ACTION_NONE, None,
            "referent released: a new document name was resolved this turn",
        )

    kind = classify_turn(current_text)

    if kind == ORDINAL_FIRST:
        if not stack:
            return DossierDecision(
                ACTION_NONE, None,
                "ordinal reference to the first document, but the session has no referent yet",
            )
        return DossierDecision(
            ACTION_RECALL, stack[-1],
            "ordinal reference ('premier'/'first'/第一) resolved to the session's first referent",
        )

    if kind == ORDINAL_LAST:
        if not stack:
            return DossierDecision(
                ACTION_NONE, None,
                "ordinal reference to the last document, but the session has no referent yet",
            )
        return DossierDecision(
            ACTION_RECALL, stack[0],
            "ordinal reference ('dernier'/'last'/最後) resolved to the session's most recent referent",
        )

    if kind == FOLLOWUP:
        if not stack:
            return DossierDecision(
                ACTION_NONE, None,
                "follow-up detected (anaphor/short question) but the session has no referent to hold",
            )
        return DossierDecision(
            ACTION_HOLD, stack[0],
            "referent held: follow-up turn introduces no new document name",
        )

    return DossierDecision(
        ACTION_NONE, None,
        "new topic: no anaphor/ordinal signal and no name resolved — normal retrieval applies",
    )


def resolve_dossier_state(
    past_user_turns: List[str],
    current_text: str,
    has_new_name_this_turn: bool,
    resolve_named: Resolver,
    resolve_fallback: Resolver,
    logger=None,
    debug: bool = True,
) -> "tuple[DossierDecision, List[DossierEntry]]":
    """Replay the session, decide the live turn's action, return decision + stack.

    The stack (the session's candidate referents, most recent first) is what
    the upstream context gate (US-103, brique 2) consumes to PROVE an
    ambiguity — e.g. two candidates in the dossier while the question's own
    words anchor the non-held one.
    """
    stack = build_dossier(past_user_turns, resolve_named, resolve_fallback, logger, debug)
    decision = decide(current_text, has_new_name_this_turn, stack)
    _log(
        logger, debug, "conversation dossier decision",
        action=decision.action,
        referent=decision.referent.path if decision.referent else None,
        reason=decision.reason,
    )
    return decision, stack


def resolve_dossier(
    past_user_turns: List[str],
    current_text: str,
    has_new_name_this_turn: bool,
    resolve_named: Resolver,
    resolve_fallback: Resolver,
    logger=None,
    debug: bool = True,
) -> DossierDecision:
    """Single entry point: replay the session, then decide the live turn's action."""
    decision, _ = resolve_dossier_state(
        past_user_turns, current_text, has_new_name_this_turn,
        resolve_named, resolve_fallback, logger, debug,
    )
    return decision
