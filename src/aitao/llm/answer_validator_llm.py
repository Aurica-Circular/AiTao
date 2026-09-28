# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
LLM verification pass — answer_validator_llm (US-076 phase 2-C, "deep" / Core).

The deterministic check (answer_validator) catches "no support in the sources"
but is blind to "right document, wrong figure": a sentence that paraphrases a
chunk while flipping a number still scores high. This module adds the optional
second pass: for the claims that passed the deterministic check AND carry a
verifiable fact (a number, date or amount), it asks the LLM — in ONE grouped
call — whether each is SUPPORTED, CONTRADICTED or ABSENT given the context, and
surfaces the contradicted ones.

The LLM call is injected (``llm_call``), so this module is pure and testable, and
never raises — a verification failure must not break the chat. Opt-in only
(the "deep" level) is enforced at the call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Sequence

from aitao.core.logger import get_logger
from aitao.llm.answer_validator import has_verifiable_fact

logger = get_logger("llm.answer_validator_llm")

# messages (chat-style) -> assistant text. The real adapter wraps make_llm_client.
LLMCall = Callable[[List[dict]], str]

_VERDICTS = ("supported", "contradicted", "absent")


@dataclass
class ClaimVerdict:
    """One claim and the LLM's verdict against the context."""

    claim: str
    verdict: str  # one of _VERDICTS


def select_claims(sentences: Sequence[str]) -> List[str]:
    """Keep only the sentences worth an LLM verification (fact-bearing).

    ``has_verifiable_fact`` lives in answer_validator since US-092 (the fast
    pass grounds only fact-bearing sentences too); kept re-exported here.
    """
    return [s for s in sentences if has_verifiable_fact(s)]


def build_verification_messages(
    claims: Sequence[str], contexts: Sequence[str]
) -> List[dict]:
    """Build the grouped chat prompt: one verdict per numbered claim."""
    context_block = "\n\n".join(f"[Doc {i + 1}] {c}" for i, c in enumerate(contexts))
    claims_block = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    system = (
        "Tu es un vérificateur factuel rigoureux. On te donne un CONTEXTE "
        "(extraits de documents) et des AFFIRMATIONS numérotées. Pour chaque "
        "affirmation, détermine si le CONTEXTE la confirme (SUPPORTED), la "
        "contredit (CONTRADICTED), ou n'en parle pas (ABSENT). Fie-toi "
        "UNIQUEMENT au contexte, jamais à tes connaissances. Réponds STRICTEMENT "
        "par une ligne par affirmation, au format "
        "« <numéro>: <SUPPORTED|CONTRADICTED|ABSENT> », sans autre texte."
    )
    user = f"CONTEXTE :\n{context_block}\n\nAFFIRMATIONS :\n{claims_block}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# Numbered verdict line. The separator class stays permissive on purpose: small
# models number claims in many ways — "1:", "1)", "1 -", and (observed on
# granite4) angle-bracketed "<1>:". Missing it would silently default the claim
# to 'supported' and hide a real contradiction.
_LINE_RE = re.compile(
    r"(\d+)\s*[>:.\)\-]+\s*(SUPPORTED|CONTRADICTED|ABSENT)", re.IGNORECASE
)

# Reasoning models (qwen3, etc.) prepend a <think>…</think> block that pollutes
# the strict verdict format. Strip closed blocks; if a stray </think> remains
# (unbalanced open), keep only what follows the last one — that is the answer.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove reasoning-model <think> scaffolding before verdict parsing."""
    cleaned = _THINK_RE.sub("", text or "")
    tail = re.split(r"</think>", cleaned, flags=re.IGNORECASE)
    return tail[-1] if len(tail) > 1 else cleaned


def parse_verdicts(response: str, claims: Sequence[str]) -> List[ClaimVerdict]:
    """Map the model's '<n>: <verdict>' lines back to claims.

    A claim without a parsable verdict defaults to 'supported' (no news = no
    flag): the LLM pass only ever *adds* a contradiction warning, it must never
    invent flags from a malformed answer. Unparsed claims are logged (not
    silently swallowed) so a format-breaking model is observable — a reasoning
    model that ignores the format would otherwise hide every contradiction.
    """
    found: dict = {}
    for m in _LINE_RE.finditer(_strip_think(response)):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(claims):
            found[idx] = m.group(2).lower()
    missing = [i + 1 for i in range(len(claims)) if i not in found]
    if missing:
        logger.warning(
            "Deep verification: unparsed verdicts defaulted to 'supported'",
            metadata={"missing_claims": missing, "total": len(claims)},
        )
    return [
        ClaimVerdict(claim=c, verdict=found.get(i, "supported"))
        for i, c in enumerate(claims)
    ]


def verify_claims(
    claims: Sequence[str], contexts: Sequence[str], llm_call: LLMCall
) -> List[ClaimVerdict]:
    """Run the grouped LLM verification (never raises; [] on empty or failure)."""
    claims = list(claims)
    contexts = [c for c in contexts if c and c.strip()]
    if not claims or not contexts:
        return []
    try:
        response = llm_call(build_verification_messages(claims, contexts))
        return parse_verdicts(response, claims)
    except Exception:
        return []


def build_llm_warning(verdicts: Sequence[ClaimVerdict], max_listed: int = 3) -> str:
    """Notary-style warning for claims the LLM found CONTRADICTED by the sources.

    Focuses on contradictions — the dangerous case the deterministic check can't
    see. 'absent' claims are already handled by the deterministic pass, so they
    are not repeated here.
    """
    contradicted = [v.claim for v in verdicts if v.verdict == "contradicted"]
    if not contradicted:
        return ""
    listed = contradicted[:max_listed]
    bullets = "\n".join(f"• « {_clip(c)} »" for c in listed)
    extra = len(contradicted) - len(listed)
    more = f"\n…et {extra} autre{'s' if extra > 1 else ''}." if extra > 0 else ""
    plural = "s" if len(contradicted) > 1 else ""
    return (
        f"\n\n⛔ Vérification approfondie : {len(contradicted)} affirmation{plural} "
        f"ci-dessus semble{'nt' if plural else ''} contredite{plural} par vos "
        f"documents — à corriger en priorité :\n{bullets}{more}"
    )


def _clip(text: str, limit: int = 120) -> str:
    """Trim a claim for display in the warning."""
    t = text.strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
