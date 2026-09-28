# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Post-generation reliability trailer (US-076, US-104) — chat route helper.

Wires the deterministic answer_validator (+ US-104's response_reader,
reader_appeal, source_attribution) into the chat endpoints. When
``[rag] verify_answer`` is on, it re-reads the model's answer, scores how well
each sentence is supported by the retrieved context, logs the latency, and
returns the report. The callers append a notary-style warning
(``build_grounding_warning``) and expose ``grounding_score`` in the API.

Composition order (US-104 part D), every step guarded so a failure degrades
to today's behaviour rather than breaking the chat:
  1. Classify sentence roles once (response_reader, rules-only, cheap) — reused
     by every later step instead of re-splitting/re-classifying the answer.
  2. Grounding check (existing, verify_answer fast/deep) — now role-aware:
     "notice"/"echo_metadata"/"habillage" sentences are skipped (I-10).
  3. Reader appeal (US-104 part C, ``[rag] reader_llm``): on the sentences the
     grounding check is about to flag ONLY, a small LLM is asked whether each
     is a real affirmation or mere habillage/echo the rules missed. Can only
     REMOVE a flag, never add one.
  4. Attribution check (US-104 part B, I-15): ALWAYS active, independent of
     verify_answer — a true extract pinned to the wrong document (I-05) is a
     fabrication-adjacent failure, the same family as the citation guard.
     Extended (I-15) with a stale-citation liaison: when the answer cites a
     document ABSENT from this turn's context (citation_guard's G2 already
     flags it as fabricated), the probable real in-context source is named
     under the same conservative rules, upgrading G2's generic "ne vous fiez
     pas" warning into a corrective banner.
  5. Deep verification (existing, Core, opt-in "deep" level) — unchanged.

The deterministic checks (2-4) are Core: free, no license gate. The LLM
"high-reliability" pass (5) is the Premium tier. Fully guarded — a disabled
toggle, an empty context, or any failure returns None, so the reliability
trailer never breaks a chat response.

Debug mode (étude §6.4, 5th invariant): every sentence's role (with its
reason) and every reader-appeal verdict is logged, at INFO when
``[rag] reliability_debug`` is on (rollout default), DEBUG otherwise — never
silent. Same convention as intent_router.py / context_gate.py, via the local
``_log`` helper below.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Callable, List, Optional, Sequence, Tuple

from aitao.core.config import get_config
from aitao.core.logger import get_logger
from aitao.llm.answer_validator import (
    GROUNDING_THRESHOLD,
    GroundingReport,
    build_grounding_warning,
    evaluate_grounding,
)
from aitao.llm.answer_validator_llm import (
    ClaimVerdict,
    LLMCall,
    build_llm_warning,
    select_claims,
    verify_claims,
)
from aitao.llm.citation_guard import find_fabricated_citations
from aitao.llm.reader_appeal import apply_appeal, run_appeal
from aitao.llm.response_reader import SentenceRole, classify_answer, role_counts as _role_counts
from aitao.llm.source_attribution import (
    AttributionFlag,
    build_attribution_warning,
    check_attribution,
    check_stale_citations,
)

logger = get_logger("api.grounding")

EmbedFn = Callable[[Sequence[str]], object]

# Sentinel values for [rag] verify_model meaning "reuse the chat model" — the
# template ships "defaut" because the user's model is unknown at install time.
_VERIFY_MODEL_DEFAULTS = frozenset({"", "defaut", "default", "auto"})

# How much of a sentence the debug log shows — enough to recognize it, short
# enough that a long answer's trace stays readable.
_LOG_SENTENCE_CHARS = 80


def _log(message: str, **metadata) -> None:
    """Per-sentence trace point (étude §6.4, 5th invariant: every verdict is
    logged with its reason, débrayable via config). INFO when
    ``[rag] reliability_debug`` is on (rollout default), DEBUG otherwise —
    never silent. Same convention as intent_router.py / context_gate.py,
    adapted to this module's existing style: a module-level logger and
    ``get_config()`` read inline (this module already does that elsewhere,
    e.g. ``run_grounding_check``'s ``verify_answer`` level) rather than
    ``logger``/``debug`` injected as parameters.
    """
    debug = bool(getattr(get_config().rag, "reliability_debug", True))
    if debug:
        logger.info(message, metadata=metadata)
    else:
        logger.debug(message, metadata=metadata)


def run_grounding_check(
    answer: str,
    context_docs: Sequence,
    embed_fn: EmbedFn,
    roles: Optional[Sequence[SentenceRole]] = None,
) -> Optional[GroundingReport]:
    """Score the answer against the context when ``verify_answer`` is enabled.

    Returns the GroundingReport, or None when the check is disabled, has no
    context to ground against, or fails. Never raises. Core feature — no licence
    gate (the deterministic check is free; the LLM "high-reliability" pass is the
    Premium tier). ``roles`` (US-104), when given, is passed through to
    ``evaluate_grounding`` so the answer is classified only once per trailer.
    """
    level = str(getattr(get_config().rag, "verify_answer", "off")).lower()
    if level not in ("fast", "deep"):
        return None
    if not context_docs:
        return None
    try:
        report = evaluate_grounding(answer, context_docs, embed_fn, roles=roles)
        logger.info(
            "Grounding check completed",
            metadata={
                "grounding_score": round(report.grounding_score, 3),
                "weakest_score": round(report.weakest_score, 3),
                "checked": report.checked,
                "unsupported": len(report.unsupported),
                "elapsed_ms": round(report.elapsed_ms, 1),
            },
        )
        return report
    except Exception as e:  # never break the chat on a grounding failure
        logger.warning("Grounding check failed", metadata={"error": str(e)})
        return None


def run_reader_appeal(
    report: GroundingReport, llm_call: Optional[LLMCall]
) -> GroundingReport:
    """US-104 part C: appeal the sentences ``report`` is about to flag.

    Runs only when ``[rag] reader_llm`` is on and there is something to
    appeal; returns ``report`` unchanged on every non-nominal path (disabled,
    no callable, nothing flagged, unparsable/timeout/error) — this step can
    only shrink ``report.unsupported``, never grow it, so losing the appeal
    loses none of the deterministic net.
    """
    if not report.unsupported:
        return report
    if not bool(getattr(get_config().rag, "reader_llm", True)):
        return report
    if llm_call is None:
        return report
    try:
        candidates = list(report.unsupported)
        verdicts = run_appeal(candidates, llm_call)
        for v in verdicts:
            _log(
                "reader appeal verdict",
                sentence=v.sentence[:_LOG_SENTENCE_CHARS],
                verdict=v.verdict,
            )
        if not verdicts:
            return report
        kept = apply_appeal(candidates, verdicts)
        if len(kept) == len(candidates):
            return report
        logger.info(
            "Reader appeal unflagged sentence(s)",
            metadata={
                "unflagged": len(candidates) - len(kept),
                "appealed": len(candidates),
            },
        )
        return dataclasses.replace(report, unsupported=kept)
    except Exception as e:  # never break the chat on an appeal failure
        logger.warning("Reader appeal failed", metadata={"error": str(e)})
        return report


def run_attribution_check(
    roles: Optional[Sequence[SentenceRole]],
    context_docs: Sequence,
    embed_fn: Optional[EmbedFn],
) -> List[AttributionFlag]:
    """US-104 part B: extract<->source liaison, ALWAYS active (Core, no
    verify_answer gate — a true extract pinned to the wrong document is a
    fabrication-adjacent failure). Never raises; [] on any failure or when
    there is nothing to check (no roles, < 2 context docs)."""
    if not roles or not context_docs:
        return []
    try:
        flags = check_attribution(roles, context_docs, embed_fn)
        if flags:
            logger.info(
                "Attribution check flagged wrong-source citation(s)",
                metadata={"count": len(flags), "rules": [f.rule for f in flags]},
            )
        return flags
    except Exception as e:  # never break the chat on an attribution failure
        logger.warning("Attribution check failed", metadata={"error": str(e)})
        return []


def run_stale_citation_check(
    answer: str,
    roles: Optional[Sequence[SentenceRole]],
    context_docs: Sequence,
    embed_fn: Optional[EmbedFn],
) -> List[AttributionFlag]:
    """I-15: a sibling to ``run_attribution_check`` — extends the extract<->
    source liaison to a document ABSENT from this turn's context (a stale
    reference recycled from earlier in the chat, subject-change failure mode).

    citation_guard.find_fabricated_citations (G2) already detects that the
    cited name is not among the retrieved sources; this step runs ONLY when
    G2 found something, and asks source_attribution.check_stale_citations
    whether the fact the sentence states actually anchors in a document that
    IS in context — under the same conservative rules as the in-context
    liaison (part B). ALWAYS active (Core, no verify_answer gate — same
    fabrication-adjacent family). Never raises; [] on any failure or when
    there is nothing to check (no roles, no context, no fabricated name).
    """
    if not roles or not context_docs:
        return []
    try:
        stale_names = find_fabricated_citations(answer, context_docs)
        if not stale_names:
            return []
        flags = check_stale_citations(roles, stale_names, context_docs, embed_fn)
        for f in flags:
            _log(
                "stale citation flag",
                rule=f.rule,
                attributed=", ".join(f.attributed),
                probable_source=f.probable_source,
            )
        if flags:
            logger.info(
                "Stale-citation check flagged corrective attribution(s)",
                metadata={"count": len(flags), "rules": [f.rule for f in flags]},
            )
        return flags
    except Exception as e:  # never break the chat on a stale-citation failure
        logger.warning("Stale-citation check failed", metadata={"error": str(e)})
        return []


def run_deep_verification(
    report: GroundingReport,
    context_docs: Sequence,
    llm_call: Optional[LLMCall],
) -> List[ClaimVerdict]:
    """LLM pass over the fact-bearing claims that passed the deterministic check.

    Only runs at the "deep" level (Core, opt-in) with an LLM available. Targets the
    claims that scored >= GROUNDING_THRESHOLD (not already flagged) AND carry a
    number/date/amount — the place "right document, wrong figure" hides. Never
    raises; returns [] when disabled or on failure.
    """
    if llm_call is None:
        return []
    if str(getattr(get_config().rag, "verify_answer", "off")).lower() != "deep":
        return []

    passed = [g.sentence for g in report.sentences if g.score >= GROUNDING_THRESHOLD]
    claims = select_claims(passed)
    if not claims:
        return []
    contexts = [str(getattr(d, "content", "") or "") for d in context_docs]

    start = time.perf_counter()
    verdicts = verify_claims(claims, contexts, llm_call)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Deep verification completed",
        metadata={
            "claims": len(claims),
            "contradicted": sum(1 for v in verdicts if v.verdict == "contradicted"),
            "elapsed_ms": round(elapsed_ms, 1),
        },
    )
    return verdicts


def _attribution_note(flag: AttributionFlag) -> str:
    return f"{', '.join(flag.attributed)} → {flag.probable_source} ({flag.rule})"


def grounding_trailer(
    answer: str,
    context_docs: Sequence,
    embed_fn: EmbedFn,
    llm_call: Optional[LLMCall] = None,
    reader_llm_call: Optional[LLMCall] = None,
) -> Optional[Tuple[str, GroundingReport]]:
    """Full reliability trailer for a chat answer (US-076 + US-104).

    Returns ``(text_to_append, report)`` or None when there is nothing to
    report (verify_answer off/no context/failure AND no attribution flag).
    The text combines: the deterministic warning (levels fast & deep, after
    the reader appeal removes any false-positive habillage/echo), the
    attribution corrective banner (always, Core), and, at "deep" (Core, opt-in),
    the LLM contradiction warning. ``report`` also carries role_counts and
    attribution_notes (US-104 part D6) for the API's structured metadata.
    """
    if not context_docs:
        return None
    try:
        roles = classify_answer(answer, context_docs)
        for r in roles:
            _log(
                "sentence role classified",
                role=r.role,
                reason=r.reason,
                sentence=r.sentence[:_LOG_SENTENCE_CHARS],
            )
        _log("sentence roles summary", counts=_role_counts(roles))
    except Exception as e:
        logger.warning("Sentence role classification failed", metadata={"error": str(e)})
        roles = None

    report = run_grounding_check(answer, context_docs, embed_fn, roles=roles)
    parts: List[str] = []
    verdicts: List[ClaimVerdict] = []
    if report is not None:
        report = run_reader_appeal(report, reader_llm_call)
        parts.append(build_grounding_warning(report))
        verdicts = run_deep_verification(report, context_docs, llm_call)
        if verdicts:
            parts.append(build_llm_warning(verdicts))

    attribution_flags = run_attribution_check(roles, context_docs, embed_fn)
    attribution_flags = attribution_flags + run_stale_citation_check(
        answer, roles, context_docs, embed_fn
    )
    if attribution_flags:
        parts.append(build_attribution_warning(attribution_flags))

    text = "".join(p for p in parts if p)
    if report is None and not attribution_flags:
        return None

    if report is None:
        report = GroundingReport(grounding_score=1.0, weakest_score=1.0)
    report = dataclasses.replace(
        report,
        role_counts=_role_counts(roles) if roles else {},
        attribution_notes=[_attribution_note(f) for f in attribution_flags],
    )
    return text, report


def _resolve_verify_model(client, chat_model: str) -> str:
    """Pick the model for the deep pass: the dedicated ``verify_model`` if set and
    available, else the chat model (US-076 B).

    A configured-but-missing verify_model (not pulled) falls back silently to the
    chat model and logs — the reliability net is never disabled by a bad config.
    Same fallback when the model list is unreachable. Called lazily (only when the
    deep pass actually fires) so non-deep chats pay no model-listing call.
    """
    verify_model = str(getattr(get_config().rag, "verify_model", "") or "").strip()
    if verify_model.lower() in _VERIFY_MODEL_DEFAULTS or verify_model == chat_model:
        return chat_model
    try:
        available = {m.name for m in client.list_models()}
    except Exception as e:
        logger.warning(
            "Could not list models — verification uses the chat model",
            metadata={"verify_model": verify_model, "error": str(e)},
        )
        return chat_model
    if verify_model in available:
        logger.info("Deep verification uses dedicated model", metadata={"verify_model": verify_model})
        return verify_model
    logger.warning(
        "Configured verify_model not available — falling back to the chat model",
        metadata={"verify_model": verify_model, "chat_model": chat_model},
    )
    return chat_model


def make_llm_verify_call(client, model, options=None) -> LLMCall:
    """Adapt an Ollama-shaped chat client into the LLMCall the verifier needs.

    The client is injected (no import of the route module — avoids a cycle).
    Converts the verifier's message dicts to OllamaChatMessage, runs a single
    non-streaming completion, and returns the assistant text ("" on any error).
    The verification model (chat model or dedicated ``verify_model``, US-076 B) is
    resolved lazily and memoised on first call, so a non-deep chat never pays the
    model-listing lookup.
    """
    resolved: dict = {}

    def _call(messages: List[dict]) -> str:
        try:
            from aitao.llm.protocols import OllamaChatMessage

            if "model" not in resolved:
                resolved["model"] = _resolve_verify_model(client, model)
            msgs = [
                OllamaChatMessage(role=m["role"], content=m["content"])
                for m in messages
            ]
            resp = client.chat(
                messages=msgs, model=resolved["model"], stream=False, options=options
            )
            if isinstance(resp, dict):
                return resp.get("message", {}).get("content", "") or ""
            return ""
        except Exception:
            return ""

    return _call
