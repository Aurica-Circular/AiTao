# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Upstream context gate — "does this context answer THE question?" (US-103).

ÉPIC-30 brique 2 (ETUDE-FIABILITE.md §6 brique 2, §6.5 point 1). Sits between
retrieval and generation, downstream of the conversation dossier (US-102),
and extends the adequacy gate (context_adequacy.evaluate_refusal) from two
outcomes to three:

  1. PROCEED       — the retrieved context can ground an answer: generate.
  2. REFUSAL       — proof that it cannot: targeted notary refusal, no LLM
                     call. Proof is always VERBATIM (a held referent's
                     document missing from the winning context, or NONE of
                     the question's salient tokens present in ANY retrieved
                     document). Never a fuzzy score: RRF scores are ranks,
                     not relevance (I-12) — a wrongful refusal kills trust
                     as much as a false banner (I-09).
  3. CLARIFICATION — proof of ambiguity: ONE short question naming the
                     candidate documents (étude §6.5, "l'empathie du
                     notaire"). Guardrails: never two turns in a row
                     (derived STATELESSLY from history — see
                     CLARIFICATION_PREFIX below), only on deterministic
                     proof, candidates always named.

Deliberately NOT refused: a follow-up whose held referent IS in the context
but does not contain the follow-up's incidental words ("quel est son
titre ?" — "titre" appears nowhere in a CJK document, yet the question is
perfectly answerable from the title metadata). The grounded model handles
that honestly; a verbatim-token refusal there would be the wrongful refusal
I-12 warns about. Token-absence proof therefore applies to NEW-topic turns
only, where the tokens ARE the subject.

Pure logic, no LLM call, no I/O: the dossier state (filled by
conversation_dossier_hook during retrieval) and the retrieved context are
inputs. Every verdict is logged with its reason (étude §6.4, 5th invariant)
at INFO when ``[rag] reliability_debug`` is true, DEBUG otherwise.
Core feature — no license gating.
"""

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from aitao.llm.context_adequacy import evaluate_refusal, is_conversational
from aitao.llm.conversation_dossier import (
    ACTION_HOLD,
    ACTION_NONE,
    ACTION_RECALL,
    FOLLOWUP,
    NEW,
    ORDINAL_FIRST,
    ORDINAL_LAST,
)
from aitao.llm.intent_classifier import ConfigIntent, classify_config_intent
from aitao.llm.query_terms import anchor_terms, salient_terms, text_contains_term

# Gate outcomes
PROCEED = "proceed"
REFUSAL = "refusal"
CLARIFICATION = "clarification"

# Stable marker starting every clarification question. The "never two turns
# in a row" guardrail is derived statelessly from history: if the LAST
# assistant turn starts with this prefix, the gate never asks again this
# turn. Chosen robust by construction: it opens the message (no scanning of
# free prose), survives history_hygiene (which only strips trailing "⚠️ …"
# blocks), and is never produced by the model itself (assistant answers are
# generated from the model or from AiTao's other fixed messages, none of
# which start with it).
CLARIFICATION_PREFIX = "❓ "

_FOLLOWUP_KINDS = (FOLLOWUP, ORDINAL_FIRST, ORDINAL_LAST)

# How many missing salient tokens a targeted refusal names (readability cap).
_MAX_NAMED_TOKENS = 4
# How many rival candidates a clarification question names besides the held one.
_MAX_NAMED_RIVALS = 2


@dataclass(frozen=True)
class GateVerdict:
    """The gate's decision for one turn, with its reason (traceability)."""

    outcome: str  # PROCEED | REFUSAL | CLARIFICATION
    message: Optional[str]
    reason: str

    @property
    def proceed(self) -> bool:
        return self.outcome == PROCEED


def previous_turn_was_clarification(messages: Optional[List[Dict[str, str]]]) -> bool:
    """True when the LAST assistant turn in history was a clarification.

    Stateless guardrail ("never two turns in a row", étude §6.5): only the
    most recent assistant message counts — a clarification two turns ago,
    already followed by a normal answer, does not suppress a new one.
    """
    for m in reversed(messages or []):
        if m.get("role") == "assistant":
            return str(m.get("content") or "").startswith(CLARIFICATION_PREFIX)
    return False


def _display_name(entry: Optional[Dict[str, Any]]) -> str:
    """User-facing name of a dossier entry: the file name, else the title."""
    if not entry:
        return ""
    path = str(entry.get("path") or "")
    if path:
        return PurePath(path).name
    return str(entry.get("title") or "")


def _nothing_anchored(context_docs: List[Any]) -> bool:
    """True when no retrieved document carries an anchoring signal.

    Scores here are the gate-side anchoring booleans (1.0 grounded / 0.0
    noise) computed by RAGEngine._score_context — not raw RRF ranks. Used
    only to gate a CLARIFICATION (a question, additive), never a refusal.
    """
    return all(not float(getattr(d, "score", 0) or 0) for d in context_docs)


def _provably_missing_terms(
    question: str, context_docs: List[Any]
) -> Optional[List[str]]:
    """Salient tokens of ``question`` verbatim-absent from EVERY context doc.

    Returns the display tokens (for the targeted refusal message) only on
    full proof: the question carries salient terms, at least one document was
    retrieved, and not a single term (nor CJK bigram — US-89-4) appears in
    any document's path, title or content. Any partial presence -> None (no
    proof, never refuse). Accent- and case-insensitive, mirroring the
    anchoring probe.
    """
    if not context_docs:
        return None
    terms = anchor_terms(question)
    if not terms:
        return None
    for doc in context_docs:
        haystack = " ".join(
            str(getattr(doc, attr, "") or "") for attr in ("path", "title", "content")
        )
        if text_contains_term(haystack, terms):
            return None
    display = salient_terms(question) or terms
    return display[:_MAX_NAMED_TOKENS]


def _referent_absent_message(name: str) -> str:
    return (
        f"Je n'ai pas « {name} » dans les documents récupérés : impossible de "
        "répondre à cette question de suivi sans ce document. Reposez la "
        "question en nommant le document, ou vérifiez qu'il est toujours indexé."
    )


def _tokens_absent_message(missing: List[str]) -> str:
    listed = ", ".join(f"« {t} »" for t in missing)
    return (
        f"Je n'ai pas {listed} dans les documents récupérés : aucun des "
        "documents retrouvés pour cette question ne contient ces termes. "
        "Reformulez, ou nommez le document qui devrait les contenir."
    )


def _conflict_question(top_name: str, rival_names: List[str]) -> str:
    rivals = "".join(f", ou de « {n} »" for n in rival_names[:_MAX_NAMED_RIVALS])
    return f"{CLARIFICATION_PREFIX}Tu parles toujours de « {top_name} »{rivals} ?"


_NO_REFERENT_QUESTION = (
    f"{CLARIFICATION_PREFIX}De quel document parles-tu ? Je n'ai encore aucun "
    "document de référence dans cette conversation — nomme-le (titre ou "
    "mots-clés) et je réponds."
)


def evaluate_gate(
    question: str,
    context_docs: List[Any],
    dossier_state: Optional[Dict[str, Any]] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    has_session: bool = False,
    logger=None,
    debug: bool = True,
) -> GateVerdict:
    """Confront the retrieved context with the question: one of 3 outcomes.

    ``dossier_state`` is the dict filled by conversation_dossier_hook (see
    its key table); an empty/missing dict degrades gracefully to the
    pre-US-103 behaviour (evaluate_refusal semantics). ``messages`` is the
    conversation history exactly as sent to retrieval (used ONLY for the
    stateless "never two clarifications in a row" guardrail).
    """
    state = dossier_state or {}
    action = state.get("action") or ACTION_NONE
    referent = state.get("referent")
    stack = state.get("stack")
    turn_kind = str(state.get("turn_kind") or "")

    # Config questions and small talk are never gated (mirrors evaluate_refusal).
    if classify_config_intent(question) != ConfigIntent.NONE or is_conversational(
        question
    ):
        return _verdict(
            logger, debug, PROCEED, None,
            "config question / small talk — gate not applicable",
        )

    # Outcome 2a — a held/recalled referent MUST be in the winning context
    # (I-11 defense in depth: the dossier normally pins it; if it could not
    # be fetched, answering from whatever else was retrieved would be the
    # exact I-11 failure again).
    if action in (ACTION_HOLD, ACTION_RECALL) and referent:
        context_paths = {str(getattr(d, "path", "") or "") for d in context_docs}
        if str(referent.get("path") or "") not in context_paths:
            name = _display_name(referent)
            return _verdict(
                logger, debug, REFUSAL, _referent_absent_message(name),
                f"held/recalled referent {name!r} absent from the winning context "
                "(verbatim path check)",
            )

    asked_last_turn = previous_turn_was_clarification(messages)

    # Outcome 3a — proven conflict: the dossier holds a referent, at least
    # one OTHER candidate exists, and the question's OWN words anchor a
    # non-held candidate while anchoring nothing of the held one. The
    # anchoring probe result comes from the hook (None = probe unusable ->
    # never guess, no question).
    if action == ACTION_HOLD and stack and len(stack) >= 2:
        anchored = state.get("anchored_paths")
        if anchored:
            top = stack[0]
            rivals = [
                c for c in stack[1:] if str(c.get("path") or "") in anchored
            ]
            if rivals and str(top.get("path") or "") not in anchored:
                rival_names = [_display_name(c) for c in rivals]
                if asked_last_turn:
                    _log(
                        logger, debug,
                        "context gate: clarification suppressed (one was asked "
                        "last turn — never two in a row)",
                        candidates=[_display_name(top)] + rival_names,
                    )
                else:
                    message = _conflict_question(_display_name(top), rival_names)
                    _log(
                        logger, debug, "clarification question asked",
                        alias=question[:100],
                        candidates=[_display_name(top)] + rival_names,
                    )
                    return _verdict(
                        logger, debug, CLARIFICATION, message,
                        "ambiguity proven: follow-up holds "
                        f"{_display_name(top)!r} but the question's own words "
                        f"anchor {rival_names!r}",
                    )

    # Outcome 3b — a follow-up with NOTHING to hold: anaphor/ordinal turn,
    # empty dossier stack, no session attachment, and no retrieved document
    # anchored by the question's own words. There is no referent to answer
    # about — asking which document is meant beats a generic refusal.
    if (
        turn_kind in _FOLLOWUP_KINDS
        and stack is not None
        and not stack
        and not has_session
        and _nothing_anchored(context_docs)
    ):
        if asked_last_turn:
            _log(
                logger, debug,
                "context gate: clarification suppressed (one was asked last "
                "turn — never two in a row)",
                candidates=[],
            )
        else:
            _log(
                logger, debug, "clarification question asked",
                alias=question[:100], candidates=[],
            )
            return _verdict(
                logger, debug, CLARIFICATION, _NO_REFERENT_QUESTION,
                "ambiguity proven: follow-up turn with an empty dossier stack "
                "and no anchored document",
            )

    # Outcomes 2b/2c — the existing adequacy refusal, upgraded to a targeted
    # message when the absence is verbatim-provable.
    missing = _provably_missing_terms(question, context_docs)

    refusal = evaluate_refusal(question, context_docs, has_session)
    if refusal is not None:
        if missing:
            return _verdict(
                logger, debug, REFUSAL, _tokens_absent_message(missing),
                f"salient tokens {missing!r} verbatim-absent from every "
                "retrieved document",
            )
        return _verdict(
            logger, debug, REFUSAL, refusal,
            "adequacy refusal (no usable context — pre-US-103 behaviour)",
        )

    # Outcome 2c — "validated garbage" guard: retrieval reported usable
    # context (e.g. the widened multi-turn pass anchored on a PAST turn's
    # topic) yet NONE of this question's own tokens appears verbatim
    # anywhere in it. Verbatim proof only; applies ONLY to a turn the
    # dossier hook PROVED to be a new topic (turn_kind == NEW) — never to
    # follow-up/held turns (incidental words must not refuse, see module
    # docstring) and never when the dossier state is absent/unknown (a
    # wrongful refusal is the I-12 trust killer: without the dossier's
    # classification, degrade to the pre-US-103 behaviour instead).
    if missing and action == ACTION_NONE and turn_kind == NEW:
        return _verdict(
            logger, debug, REFUSAL, _tokens_absent_message(missing),
            f"salient tokens {missing!r} verbatim-absent from every retrieved "
            "document (context anchored by history, not by this question)",
        )

    return _verdict(logger, debug, PROCEED, None, "context can ground an answer")


def _verdict(
    logger, debug: bool, outcome: str, message: Optional[str], reason: str
) -> GateVerdict:
    """Build a verdict and log it (5th invariant: every verdict is traced)."""
    _log(logger, debug, "context gate verdict", outcome=outcome, reason=reason)
    return GateVerdict(outcome=outcome, message=message, reason=reason)


def _log(logger, debug: bool, message: str, **metadata) -> None:
    """INFO when reliability_debug is on (rollout default), DEBUG otherwise."""
    if logger is None:
        return
    if debug:
        logger.info(message, metadata=metadata)
    else:
        logger.debug(message, metadata=metadata)
