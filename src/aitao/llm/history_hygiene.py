# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
History hygiene — strip AiTao's own reliability notices from past turns.

The citation guard and the answer validator append warning blocks ("⚠️ …") to
the answers shown to the user. Chat clients send the full transcript back on the
next turn, so those warnings — "do not trust this reference", "N claims are not
grounded" — re-enter the model's context attached to its own previous answers.
A model reading its past answer flagged as unreliable tends to double down on
refusing, and the refusal wording then propagates turn after turn (observed:
two different models produced a near-identical refusal because the second was
copying the first from history). The notices are for the USER, not the model:
strip them from assistant turns before sending history to the LLM.

US-105 adds a PREFIX notice (the intent router's "ℹ️ Réponse générale" banner,
llm.intent_router.GENERAL_ANSWER_BANNER): it is stripped from the front of a
past assistant turn the same way the ⚠️ blocks are stripped from the back —
the model must not see its own "this wasn't grounded in your documents" banner
and imitate the phrasing. This must NOT touch the US-103 clarification marker
("❓ ", llm.context_gate.CLARIFICATION_PREFIX): that one is deliberately kept
in history so the "never two clarifications in a row" guardrail can detect it
— it uses a different emoji and is never matched here.
"""

from typing import Dict, List

# Exact prefixes the guard/validator prepend to their blocks. The blocks are
# always appended at the END of an answer, so everything from the first marker
# on is notice material.
_NOTICE_MARKERS = (
    "\n\n⚠️ Avertissement",
    "\n\n⚠️ Fiabilité",
)

# Exact prefix the intent router (US-105) prepends to a "general" answer. This
# one is prepended at the START of the answer, so it is stripped from the
# front instead of cutting a tail.
_PREFIX_MARKERS = (
    "ℹ️ Réponse générale — pas issue de vos documents.\n\n",
)


def strip_reliability_notices(text: str) -> str:
    """Return assistant text without AiTao's own notice/banner blocks.

    Strips a leading intent-router banner (if present) and any trailing
    guard/validator notice block (if present) — independently, so a turn can
    carry either, both, or neither.
    """
    if not text:
        return text
    for prefix in _PREFIX_MARKERS:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if "⚠️" not in text:
        return text
    cut = len(text)
    for marker in _NOTICE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].rstrip()


def clean_history_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Strip notices from the assistant turns of {role, content} dicts.

    User turns and the messages list itself are left untouched (a new list is
    returned; entries are copied only when modified).
    """
    cleaned: List[Dict[str, str]] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("content"):
            stripped = strip_reliability_notices(m["content"])
            if stripped != m["content"]:
                m = {**m, "content": stripped}
        cleaned.append(m)
    return cleaned
