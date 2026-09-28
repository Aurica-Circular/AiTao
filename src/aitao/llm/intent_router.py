# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Intent router — documentary vs general question (US-105, ÉPIC-30 phase 2bis).

Incident I-16 (ETUDE-FIABILITE.md §9, journal 2026-07-08): a pure calculation
question ("percentage of days between two Mondays") got contorted through an
administrative calendar document anchored on the incidental words "Monday" /
"day" — the v3.1 always-on document context, plus the "ground yourself in the
documents" system prompt, hijacked a question that had nothing to look up.
Neither the conversation dossier (US-102) nor the context gate (US-103) caused
this: the turn was a legitimate NEW topic, correctly retrieved by keyword
overlap. The gap is upstream of both: AiTao never asked "does this question
need my documents at all?"

This module asks exactly that, with a small LLM call (reusing the same
"verify_model" the answer_validator's deep pass already uses — no new model to
configure). It is intentionally the ONLY component in ÉPIC-30 backed by an
LLM call rather than pure rules: telling "documentary" from "general" apart is
a task of MEANING, not pattern-matching (a regex would need to know world
knowledge to separate "what does my lease say" from "what's 15% of 240").

Contract (non-negotiable, per the product decision):
  - The dangerous mistake is a wrongful "general" verdict: it silently drops
    the user's documents from the answer, exactly the "notary becomes oracle"
    failure ÉPIC-30 exists to prevent. The prompt is explicitly biased toward
    DOCUMENTARY on any doubt, and every failure mode below (disabled feature,
    no callable, empty/unparsable response, timeout, exception) fails open to
    DOCUMENTARY — never GENERAL. No code path here can make the pipeline skip
    retrieval by accident.
  - Every verdict is logged with its reason and measured latency (étude §6.4,
    5th invariant), at the same INFO/DEBUG level as context_gate.py and
    conversation_dossier.py (gated by ``[rag] reliability_debug``).
  - Pure w.r.t. I/O: this module never builds its own LLM client. The caller
    (src/api/routes/chat.py) injects an ``llm_call`` built the exact same way
    as the answer_validator's deep-verification pass
    (``api.routes.chat_grounding.make_llm_verify_call`` — same dedicated
    "verify_model", same fallback-to-chat-model behaviour). This keeps
    llm/ free of any dependency on api/ while still sharing the model
    resolution logic, rather than duplicating or relocating it.

Core feature — no license gating (routing is part of the free chat pipeline).
"""

from __future__ import annotations

import concurrent.futures
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

# messages (chat-style dicts) -> assistant text. Same shape as
# answer_validator_llm.LLMCall / chat_grounding.make_llm_verify_call's return.
LLMCall = Callable[[List[dict]], str]

DOCUMENTARY = "documentary"
GENERAL = "general"

# Prefixed PROGRAMMATICALLY to a "general" answer — never asked of the model,
# never part of the prompt, so it can't be confused with model output.
GENERAL_ANSWER_BANNER = "ℹ️ Réponse générale — pas issue de vos documents.\n\n"

# Wall-clock budget for the routing call. Short on purpose: this call sits in
# the critical path of every factual question, and a slow/hung interpreter
# must never stall the chat — it just fails open to the current behaviour.
DEFAULT_TIMEOUT_S = 3.0

# Decoding options for the probe call. A classifier must be deterministic:
# the chat request's own sampling options (temperature etc.) are for the
# ANSWER, never for this verdict — inheriting them made the route vary
# between identical questions (US-105.2 field incident).
PROBE_OPTIONS = {"temperature": 0, "num_predict": 8}

# How many of the most recent turns are shown to the interpreter as context
# (a bare "what is its title?" must never be classified in isolation).
_RECENT_TURNS_SHOWN = 3
_RECENT_TURN_CHARS = 200

# Few-shot examples in French, English and Chinese (AiTao's three working
# languages) — bias toward DOCUMENTARY is stated explicitly, not just implied
# by the examples. Tuned via US-105.1 workshop (V1 variant, 2026-07-08): scores
# documentary 10/10 / general 8/10 vs baseline V0 (7/10 general).
_SYSTEM_PROMPT = (
    "You classify ONE user question as DOCUMENTARY or GENERAL.\n"
    "DOCUMENTARY: answering it requires information stored in the user's own files (invoices, contracts, rules, notes, emails, calendars), or it follows up on a document already discussed in this conversation.\n"
    "GENERAL: anyone could answer it correctly WITHOUT access to any of the user's files — arithmetic, percentages, unit or time conversions, logic puzzles, definitions, world facts. Mentions of time, dates, hours or money do NOT make a question documentary when the computation is self-contained.\n"
    "If unsure, answer DOCUMENTARY.\n"
    "Answer with EXACTLY one word: DOCUMENTARY or GENERAL. No punctuation, no explanation.\n\n"
    "Examples:\n"
    "Q: Quel est le montant de la facture INV-2026-0042 ?\nA: DOCUMENTARY\n"
    "Q: What does my lease say about the notice period?\nA: DOCUMENTARY\n"
    "Q: 這份文件是什麼時候簽的？\nA: DOCUMENTARY\n"
    "Q: Quel est son titre ?\nA: DOCUMENTARY\n"
    "Q: Quels sont les jours fériés selon le calendrier du bureau ?\nA: DOCUMENTARY\n"
    "Q: Combien font 15% de 240 ?\nA: GENERAL\n"
    "Q: What's the capital of Japan?\nA: GENERAL\n"
    "Q: 兩個星期一之間相差百分之幾的天數？\nA: GENERAL\n"
    "Q: J'ai passé 6h sur 48h au total, quel pourcentage ?\nA: GENERAL\n"
    "Q: Convertis 3 semaines en jours.\nA: GENERAL\n"
)

_ROUTE_RE = re.compile(r"\b(DOCUMENTARY|GENERAL)\b", re.IGNORECASE)


@dataclass(frozen=True)
class IntentVerdict:
    """The router's decision for one turn, with its reason (traceability)."""

    route: str  # DOCUMENTARY | GENERAL
    reason: str
    latency_ms: float

    @property
    def is_general(self) -> bool:
        return self.route == GENERAL


def _build_messages(
    question: str, recent_turns: Optional[Sequence[Dict[str, str]]]
) -> List[dict]:
    """Short prompt: few-shot system message + the question, with a thin slice
    of recent history so a bare follow-up ("what is its title?") is never
    judged in isolation."""
    history_block = ""
    if recent_turns:
        turns = list(recent_turns)
        # Belt-and-braces (US-105.2): the question being classified must never
        # appear inside its own "recent turns" block. Callers pass the request
        # messages, whose last entry IS the current question — seeing it twice
        # in a meta structure reliably flips a small model to DOCUMENTARY
        # (reproduced 5/5 on granite4 with the I-16 question).
        if (
            turns
            and turns[-1].get("role") == "user"
            and str(turns[-1].get("content") or "").strip() == question.strip()
        ):
            turns = turns[:-1]
        lines = []
        for turn in turns[-_RECENT_TURNS_SHOWN:]:
            role = str(turn.get("role") or "user")
            content = str(turn.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content[:_RECENT_TURN_CHARS]}")
        if lines:
            history_block = (
                "Recent turns (context only — classify the LAST question "
                "below, not these):\n" + "\n".join(lines) + "\n\n"
            )
    user = f"{history_block}Q: {question.strip()}\nA:"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _parse_route(raw: str) -> Optional[str]:
    """Strict single-token parse. Anything else (empty, prose, both words,
    neither word) is unparsable — the caller fails open to DOCUMENTARY."""
    match = _ROUTE_RE.search(raw or "")
    if not match:
        return None
    return GENERAL if match.group(1).upper() == "GENERAL" else DOCUMENTARY


def _call_with_timeout(llm_call: LLMCall, messages: List[dict], timeout_s: float) -> str:
    """Run ``llm_call`` off-thread with a hard wall-clock budget.

    A blocking Ollama/OpenAI client call has no per-call timeout knob here, so
    a background thread + ``future.result(timeout=...)`` is the only way to
    bound it. On timeout the pool is shut down WITHOUT waiting (the orphaned
    call is left to finish or die on its own) — waiting would defeat the
    purpose of the timeout by blocking this function until the slow call
    eventually returns.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(llm_call, messages)
    try:
        return future.result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def classify_intent(
    question: str,
    recent_turns: Optional[Sequence[Dict[str, str]]] = None,
    llm_call: Optional[LLMCall] = None,
    enabled: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    logger=None,
    debug: bool = True,
) -> IntentVerdict:
    """Classify ``question`` as DOCUMENTARY or GENERAL.

    Fail-open to DOCUMENTARY (never GENERAL) on every non-nominal path:
    feature disabled, no ``llm_call`` injected, empty question, timeout,
    any exception, or a response that doesn't parse to exactly one of the
    two tokens. This mirrors the context gate's "never guess" contract —
    here, the wrong guess would be losing the user's documents, not a
    superfluous refusal.
    """
    start = time.perf_counter()

    if not enabled:
        return _verdict(logger, debug, DOCUMENTARY, "intent router disabled by config", start)
    if llm_call is None:
        return _verdict(logger, debug, DOCUMENTARY, "no LLM call available — fail-open", start)
    if not question or not question.strip():
        return _verdict(logger, debug, DOCUMENTARY, "empty question — fail-open", start)

    messages = _build_messages(question, recent_turns)
    try:
        raw = _call_with_timeout(llm_call, messages, timeout_s)
    except concurrent.futures.TimeoutError:
        return _verdict(
            logger, debug, DOCUMENTARY,
            f"timeout after {timeout_s}s — fail-open", start,
        )
    except Exception as e:  # never break the chat on a routing failure
        return _verdict(
            logger, debug, DOCUMENTARY,
            f"error ({type(e).__name__}: {e}) — fail-open", start,
        )

    route = _parse_route(raw)
    if route is None:
        return _verdict(
            logger, debug, DOCUMENTARY,
            f"unparsable response {raw!r} — fail-open", start,
        )
    return _verdict(logger, debug, route, f"model verdict: {route}", start)


def _verdict(logger, debug: bool, route: str, reason: str, start: float) -> IntentVerdict:
    latency_ms = (time.perf_counter() - start) * 1000
    _log(
        logger, debug, "intent router verdict",
        route=route, reason=reason, latency_ms=round(latency_ms, 1),
    )
    return IntentVerdict(route=route, reason=reason, latency_ms=latency_ms)


def _log(logger, debug: bool, message: str, **metadata) -> None:
    """INFO when reliability_debug is on (rollout default), DEBUG otherwise —
    same convention as context_gate.py / conversation_dossier.py."""
    if logger is None:
        return
    if debug:
        logger.info(message, metadata=metadata)
    else:
        logger.debug(message, metadata=metadata)
