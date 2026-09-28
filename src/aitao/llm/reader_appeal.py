# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Reader appeal — LLM second opinion on would-be-flagged sentences (US-104,
part C, ÉPIC-30 phase 3).

response_reader's rules (part A) are deliberately narrow — a false HABILLAGE
or ECHO_METADATA call would suppress a legitimate grounding check, so both
roles require several conditions to line up. That narrowness means some real
habillage/echo sentences slip through as "affirmation" and get flagged by the
deterministic grounding check (answer_validator) as false positives the rules
alone cannot see.

This module is the appeal: it is called ONLY on the sentences the
deterministic pass is about to flag (never on every answer — zero latency on
clean answers), and asks a small LLM, in ONE grouped call, whether each one is
a factual AFFIRMATION or mere HABILLAGE/ECHO of the document metadata.
HABILLAGE/ECHO unflags the sentence (logged: "llm_appeal"); AFFIRMATION,
unparsable, timeout, or any failure leaves it flagged — this module can only
ever REMOVE a flag the rules already raised, never add one, so a broken model
or a disabled feature loses nothing beyond today's behaviour.

Follows the same two patterns already proven in this codebase:
  - answer_validator_llm.py: grouped numbered-line prompt, permissive verdict
    parsing, ``<think>`` stripping for reasoning models.
  - intent_router.py: deterministic decoding (temperature 0), a hard
    wall-clock budget, fail-open on every non-nominal path.

The LLM call is injected (``llm_call``), built by the caller through the same
``verify_model`` infra as the deep-verification pass
(``api.routes.chat_grounding.make_llm_verify_call``) — this module never
builds its own client, keeping llm/ free of any api/ dependency.
"""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

# messages (chat-style dicts) -> assistant text. Same shape as
# answer_validator_llm.LLMCall / intent_router.LLMCall.
LLMCall = Callable[[List[dict]], str]

AFFIRMATION = "affirmation"
HABILLAGE_ECHO = "habillage_echo"

# Deterministic decoding (mirrors intent_router.PROBE_OPTIONS): a classifier
# must be reproducible, never inherit the answer's own sampling options.
# num_predict is generous compared to the single-token intent probe — this
# call answers one line PER flagged sentence.
PROBE_OPTIONS = {"temperature": 0, "num_predict": 200}

# Wall-clock budget: this call only ever runs on sentences already about to
# be flagged (never on a clean answer), but must still never stall the chat.
DEFAULT_TIMEOUT_S = 3.0

_SYSTEM_PROMPT = (
    "Tu relis des phrases tirées de la réponse d'un assistant documentaire. "
    "Pour chaque phrase numérotée, dis si c'est une AFFIRMATION factuelle "
    "tirée du contenu des documents, ou si c'est de l'HABILLAGE : une phrase "
    "qui ne fait que décrire la réponse elle-même (« c'est le premier "
    "document », « voici la liste ») ou qui répète le titre/nom d'un "
    "document sans ajouter d'information. Réponds STRICTEMENT par une ligne "
    "par phrase, au format « <numéro>: <AFFIRMATION|HABILLAGE> », sans autre "
    "texte."
)


@dataclass(frozen=True)
class AppealVerdict:
    """One flagged sentence and the appeal's verdict."""

    sentence: str
    verdict: str  # AFFIRMATION | HABILLAGE_ECHO


def build_appeal_messages(sentences: Sequence[str]) -> List[dict]:
    """Grouped chat prompt: one verdict per numbered sentence."""
    lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"PHRASES :\n{lines}"},
    ]


# Numbered verdict line — permissive separator class, mirrors
# answer_validator_llm._LINE_RE (small models number claims many ways).
_LINE_RE = re.compile(
    r"(\d+)\s*[>:.\)\-]+\s*(AFFIRMATION|HABILLAGE(?:_ECHO|/ECHO)?|ECHO)",
    re.IGNORECASE,
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    tail = re.split(r"</think>", cleaned, flags=re.IGNORECASE)
    return tail[-1] if len(tail) > 1 else cleaned


def parse_appeal(response: str, sentences: Sequence[str]) -> List[AppealVerdict]:
    """Map the model's '<n>: <verdict>' lines back to sentences.

    A sentence without a parsable verdict defaults to AFFIRMATION — the
    appeal can only REMOVE a flag, so "no clear verdict" must never remove
    one (fail-open, not fail-neutral).
    """
    found: dict = {}
    for m in _LINE_RE.finditer(_strip_think(response)):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(sentences):
            token = m.group(2).upper()
            found[idx] = AFFIRMATION if token == "AFFIRMATION" else HABILLAGE_ECHO
    return [
        AppealVerdict(sentence=s, verdict=found.get(i, AFFIRMATION))
        for i, s in enumerate(sentences)
    ]


def _call_with_timeout(llm_call: LLMCall, messages: List[dict], timeout_s: float) -> str:
    """Bounded off-thread call — mirrors intent_router._call_with_timeout."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(llm_call, messages)
    try:
        return future.result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def run_appeal(
    unsupported: Sequence[str],
    llm_call: Optional[LLMCall],
    enabled: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> List[AppealVerdict]:
    """Run the grouped appeal over the sentences about to be flagged.

    Returns [] (no unflagging) when disabled, no ``llm_call`` injected, no
    sentence to appeal, on timeout, or on any exception — every one of these
    paths leaves the deterministic flags untouched (fail-open, the
    reliability net is never lost). Never raises.
    """
    sentences = list(unsupported)
    if not enabled or llm_call is None or not sentences:
        return []
    try:
        messages = build_appeal_messages(sentences)
        raw = _call_with_timeout(llm_call, messages, timeout_s)
    except Exception:
        return []
    if not raw or not raw.strip():
        return []
    try:
        return parse_appeal(raw, sentences)
    except Exception:
        return []


def apply_appeal(
    unsupported: Sequence[str], verdicts: Sequence[AppealVerdict]
) -> List[str]:
    """Remove sentences the appeal ruled HABILLAGE_ECHO from ``unsupported``.

    ``verdicts`` may be a strict subset (or empty) of ``unsupported`` — any
    sentence without a matching verdict stays flagged (fail-open).
    """
    unflagged = {
        v.sentence for v in verdicts if v.verdict == HABILLAGE_ECHO
    }
    return [s for s in unsupported if s not in unflagged]
