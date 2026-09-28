# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Chat API route for AiTao — Ollama-compatible endpoint.

Provides POST /api/chat with optional RAG context enrichment.
The OpenAI-compatible endpoint lives in chat_openai.py.

RAG behaviour is controlled per-request:
  - Ollama endpoint: `rag_enabled` field in the request body (default: True)
  - OpenAI endpoint: `aitao.rag` extension field (default: False)

No virtual model naming convention. /api/tags returns real Ollama models only.
"""

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from aitao.core.config import get_config
from aitao.core.logger import get_logger
from aitao.llm.protocols import (
    OllamaChatMessage,
    OllamaConnectionError,
    OllamaModelNotFound,
)
from aitao.llm.perf_metrics import PerfTracker
from aitao.llm.rag_engine import RAGEngine, ContextDocument
from aitao.llm.provider import make_llm_client

from .chat_models import (
    _extract_text,
    ChatRequest,
    ChatResponseMessage,
    ChatResponse,
)
from .chat_rag_helpers import (
    inject_base_context,
    context_docs_to_dict,
    _extract_conversation_attachments,
    _last_user_text,
)
from aitao.llm.context_adequacy import (
    context_source,
    needs_document_retrieval,
)
from aitao.llm.context_gate import evaluate_gate
from aitao.llm.history_hygiene import clean_history_messages
from aitao.llm.intent_router import (
    GENERAL,
    GENERAL_ANSWER_BANNER,
    PROBE_OPTIONS,
    classify_intent,
)
from aitao.llm.named_doc_resolver import named_reference_advisory
from aitao.llm.reader_appeal import PROBE_OPTIONS as READER_APPEAL_OPTIONS
from .chat_openai import openai_router  # noqa: F401  # re-export for api/main.py

logger = get_logger("api.chat")


# ============================================================================
# Shared client instances — kept here for backward compat with test mocking
# (tests patch "api.routes.chat.get_ollama_client" / get_rag_engine)
# ============================================================================

# The configured LLM backend (OllamaClient or OpenAICompatClient — drop-ins)
_ollama_client: Optional[Any] = None
_rag_engine: Optional[RAGEngine] = None


def get_ollama_client():
    """Get or create the configured LLM client (singleton).

    Name kept for backward compatibility (tests patch this symbol). The backend
    is config-driven: Ollama by default, or an OpenAI-compatible server
    (llama.cpp, LM Studio, …) when ``[llm] backend = "openai"``.
    """
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = make_llm_client(get_config(), logger)
    return _ollama_client


def get_rag_engine() -> RAGEngine:
    """Get or create RAG engine (singleton)."""
    global _rag_engine
    if _rag_engine is None:
        config = get_config()
        _rag_engine = RAGEngine(config, logger)
    return _rag_engine


def _token_bearer_count(token: str) -> int:
    """True number of indexed docs holding ``token`` — for the US-90-5 notice."""
    try:
        return get_rag_engine().count_token_bearers(token)
    except Exception:
        return 0


# US-87 p2: shown when a streaming model goes silent (idle timeout fires).
_STALL_MESSAGE = (
    "\n\n⚠️ Le modèle n'a pas répondu (silence prolongé). "
    "Réessayez, ou changez de modèle."
)


def _is_idle_timeout(exc: Exception) -> bool:
    """True when a streaming exception is a read/idle timeout (US-87 p2)."""
    return "timeout" in type(exc).__name__.lower()


# Router
router = APIRouter(prefix="/api", tags=["Chat"])


# ============================================================================
# Ollama-Compatible Endpoint
# ============================================================================

@router.post("/chat", response_model=ChatResponse)
async def chat_completion(request: ChatRequest):
    """
    Ollama-compatible chat completion.

    Enriches the conversation with RAG context before forwarding to Ollama
    when `rag_enabled` is True (default). Set `rag_enabled: false` for a
    transparent proxy.
    """
    perf = PerfTracker(model=request.model, endpoint="/api/chat")

    logger.info(
        "Chat request received",
        metadata={
            "model": request.model,
            "message_count": len(request.messages),
            "stream": request.stream,
            "rag_enabled": request.rag_enabled,
        },
    )

    try:
        ollama = get_ollama_client()

        messages = [
            OllamaChatMessage(role=m.role, content=_extract_text(m.content))
            for m in request.messages
        ]

        # v3.1 context pipeline. Tier 1 (identity + config) and Tier 3 (session
        # attachments) are always injected; Tier 2 (document RAG) degrades
        # gracefully. rag_enabled=false turns this into a transparent proxy.
        context_docs: List[ContextDocument] = []
        rag_ran = False
        resolved_source: Optional[str] = None
        last_user = ""
        general_route = False  # US-105 — intent router routed to "general"
        if request.rag_enabled and messages:
            msg_dicts = [
                {"role": m.role, "content": _extract_text(m.content)}
                for m in request.messages
            ]
            # The ⚠️ notices in past assistant turns are for the user, not the
            # model — fed back, they make it double down on old refusals.
            msg_dicts = clean_history_messages(msg_dicts)
            # Tier 1 + Tier 3 — Core, no Premium dependency
            msg_dicts = inject_base_context(request.messages, msg_dicts)

            last_user = _last_user_text(request.messages)
            has_session = bool(_extract_conversation_attachments(request.messages))

            # Tier 2 — document RAG (Premium-gated engine, optional). Skipped
            # for config questions and small talk (answered from config / no
            # docs needed) to avoid a slow search and a padded prompt.
            # ``gate_state`` (US-103): filled by the conversation-dossier hook
            # during enrichment, consumed by the context gate below.
            gate_state: dict = {}
            if needs_document_retrieval(last_user):
                # US-105 (I-16) — before searching, ask a small LLM whether this
                # question is about the user's documents at all ("documentary")
                # or a self-contained calculation/reasoning/general-knowledge
                # question ("general"). Fail-open to documentary: disabled,
                # timeout, error or an unparsable verdict all leave this False,
                # so Tier 2 runs exactly as before US-105.
                if bool(getattr(get_config().rag, "intent_router", True)):
                    from .chat_grounding import make_llm_verify_call

                    # [:-1] — the current question is msg_dicts' last entry
                    # and must not reappear in the probe's history block
                    # (US-105.2); PROBE_OPTIONS, not request.options — the
                    # verdict must be deterministic, sampling options belong
                    # to the answer.
                    recent_turns = [
                        m for m in msg_dicts if m.get("role") != "system"
                    ][:-1]
                    intent_verdict = classify_intent(
                        last_user,
                        recent_turns=recent_turns,
                        llm_call=make_llm_verify_call(
                            ollama, request.model, PROBE_OPTIONS
                        ),
                        logger=logger,
                        debug=get_rag_engine().reliability_debug,
                    )
                    general_route = intent_verdict.route == GENERAL

                if not general_route:
                    try:
                        rag = get_rag_engine()
                        enriched_msgs, context_docs, _ = rag.enrich_messages(
                            msg_dicts,
                            max_context_docs=request.rag_max_docs,
                            dossier_state=gate_state,
                        )
                        msg_dicts = enriched_msgs
                        rag_ran = True
                        logger.debug(
                            "RAG enrichment complete",
                            metadata={"context_docs": len(context_docs)},
                        )
                    except Exception as e:
                        logger.warning(
                            f"Tier 2 RAG unavailable, Tier 1 context preserved: {e}"
                        )

            messages = [
                OllamaChatMessage(role=m["role"], content=m["content"])
                for m in msg_dicts
            ]

            # US-103 — context gate (ÉPIC-30 brique 2): confront the retrieved
            # context with the question. Three outcomes — proceed (generate),
            # targeted notary refusal, or ONE clarification question — the
            # last two answered without any LLM call, exactly like the
            # historical adequacy refusal this gate extends.
            if rag_ran:
                # US-RAG-name — the user named a file that wasn't retrieved:
                # state it deterministically (indexed? watched folder?) instead
                # of letting the model confabulate "I couldn't find it".
                advisory = named_reference_advisory(
                    last_user, context_docs, get_config().indexing.include_paths
                )
                if advisory:
                    logger.info("Named file not indexed — advising without LLM call")
                    perf.emit(logger, llm_called=False,
                              context_chunk_count=len(context_docs))
                    return _ollama_refusal(request, advisory)
                verdict = evaluate_gate(
                    last_user, context_docs, gate_state, messages=msg_dicts,
                    has_session=has_session, logger=logger,
                    debug=get_rag_engine().reliability_debug,
                )
                if not verdict.proceed:
                    logger.info(
                        f"Context gate: {verdict.outcome} — answering without LLM call"
                    )
                    perf.emit(logger, llm_called=False,
                              context_chunk_count=len(context_docs))
                    return _ollama_refusal(request, verdict.message)
            resolved_source = context_source(last_user, context_docs, has_session)

        if request.stream:
            return StreamingResponse(
                _stream_chat_response(
                    ollama, messages, request, context_docs, guard=rag_ran,
                    question=last_user, perf=perf,
                    general_banner=GENERAL_ANSWER_BANNER if general_route else None,
                ),
                media_type="application/x-ndjson",
            )

        response = ollama.chat(
            messages=messages,
            model=request.model,
            stream=False,
            options=request.options,
        )

        perf.capture_response(response)

        content = response.get("message", {}).get("content", "")
        # US-105 — "general" route: prefix the honest banner PROGRAMMATICALLY,
        # never asked of the model.
        if general_route:
            content = GENERAL_ANSWER_BANNER + content
        grounding = None
        # US-17c — strip citations of sources absent from retrieved context
        if rag_ran:
            from aitao.llm.citation_guard import (
                build_deleted_files_notice,
                build_multi_source_notice,
                find_deleted_citations,
                sanitize_citations,
            )

            content, removed = sanitize_citations(content, context_docs)
            if removed:
                logger.warning(
                    "Fabricated citations removed from answer",
                    metadata={"removed": removed},
                )
            # US-28a — deterministically flag cited sources that are in trash
            deleted = find_deleted_citations(content, context_docs)
            if deleted:
                content += build_deleted_files_notice(deleted)
            # US-90-4 — list every retrieved document holding the question's token
            content += build_multi_source_notice(
                last_user, context_docs, bearer_counter=_token_bearer_count
            )
            # US-076/US-104 — reliability trailer (deterministic grounding,
            # role-aware, + reader appeal + attribution check + opt-in deep
            # LLM pass)
            from .chat_grounding import grounding_trailer, make_llm_verify_call

            _trailer = grounding_trailer(
                content,
                context_docs,
                get_rag_engine().embed_texts,
                make_llm_verify_call(ollama, request.model, request.options),
                reader_llm_call=make_llm_verify_call(
                    ollama, request.model, READER_APPEAL_OPTIONS
                ),
            )
            if _trailer:
                _text, grounding = _trailer
                content += _text

        perf.emit(logger, client=ollama, context_chunk_count=len(context_docs))

        return ChatResponse(
            model=request.model,
            message=ChatResponseMessage(
                role="assistant",
                content=content,
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
            rag_context=context_docs_to_dict(context_docs) if context_docs else None,
            context_source=resolved_source,
            grounding_score=grounding.grounding_score if grounding else None,
            grounding_unsupported=grounding.unsupported if grounding else None,
            reliability_roles=grounding.role_counts if grounding else None,
            reliability_attribution=grounding.attribution_notes if grounding else None,
        )

    except OllamaConnectionError as e:
        logger.error(f"Ollama connection error: {e}")
        perf.emit_error(logger, e)
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except OllamaModelNotFound as e:
        logger.error(f"Model not found: {e}")
        perf.emit_error(logger, e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Chat error: {e}")
        perf.emit_error(logger, e)
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")


def _ollama_refusal(request: ChatRequest, text: str):
    """Build an Ollama-format refusal (streaming or not) without an LLM call."""
    if request.stream:
        return StreamingResponse(
            _stream_ollama_static(request.model, text),
            media_type="application/x-ndjson",
        )
    return ChatResponse(
        model=request.model,
        message=ChatResponseMessage(role="assistant", content=text),
        created_at=datetime.now(timezone.utc).isoformat(),
        context_source="none",
    )


async def _stream_ollama_static(model: str, text: str) -> AsyncGenerator[str, None]:
    """Stream a fixed message in Ollama NDJSON format (used for refusals)."""
    yield json.dumps({
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": {"role": "assistant", "content": text},
        "done": False,
    }) + "\n"
    yield json.dumps({
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": {"role": "assistant", "content": ""},
        "done": True,
    }) + "\n"


async def _stream_chat_response(
    ollama: Any,  # the configured LLM backend (Ollama-shaped interface)
    messages: List[OllamaChatMessage],
    request: ChatRequest,
    context_docs: List[ContextDocument],
    guard: bool = False,
    question: str = "",
    perf: Optional[PerfTracker] = None,
    general_banner: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Generate streaming chat response in Ollama NDJSON format.

    With ``guard=True`` (RAG ran), the streamed text is accumulated and checked
    for citations of sources absent from the retrieved context. Streamed text
    cannot be unsent, so a warning chunk is emitted before the final ``done``
    instead of rewriting the answer (US-17c).

    ``general_banner`` (US-105): when the intent router routed this turn to
    "general", the honest banner is sent as the FIRST content chunk — it is a
    prefix, not a suffix, so (unlike the citation/grounding trailers) it must
    go out before the model's own streamed tokens, not after.
    """
    terminated = False
    try:
        if general_banner:
            yield json.dumps({
                "model": request.model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "message": {"role": "assistant", "content": general_banner},
                "done": False,
            }) + "\n"

        if context_docs:
            rag_info = {
                "rag_context": context_docs_to_dict(context_docs),
                "done": False,
            }
            yield json.dumps(rag_info) + "\n"

        streamed_parts: List[str] = []
        from .chat_grounding import make_llm_verify_call

        _verify_llm = make_llm_verify_call(ollama, request.model, request.options)
        _reader_llm = make_llm_verify_call(ollama, request.model, READER_APPEAL_OPTIONS)
        for chunk in ollama.chat(
            messages=messages,
            model=request.model,
            stream=True,
            options=request.options,
        ):
            if perf:
                perf.on_raw_chunk(chunk)
            if guard:
                done, warning = _citation_warning_for_chunk(
                    chunk, streamed_parts, context_docs, question,
                    _verify_llm, _reader_llm,
                )
                if done and warning:
                    yield json.dumps({
                        "model": request.model,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "message": {"role": "assistant", "content": warning},
                        "done": False,
                    }) + "\n"
            yield chunk + "\n"
            if '"done": true' in chunk or '"done":true' in chunk:
                terminated = True

    except Exception as e:
        # Stalled/dropped mid-stream: log and emit a terminating chunk so the
        # client doesn't hang waiting for done=true (US-87).
        logger.warning(f"Ollama streaming interrupted: {type(e).__name__}: {e}")
        if perf:
            perf.record_error(e)
        # US-87 p2: an idle timeout means the model went silent — say so plainly.
        if _is_idle_timeout(e):
            yield json.dumps({
                "model": request.model,
                "message": {"role": "assistant", "content": _STALL_MESSAGE},
                "done": False,
            }) + "\n"
        yield json.dumps({"error": str(e), "done": True}) + "\n"
        terminated = True
    finally:
        # Guarantee a terminator when the backend closed the stream without a
        # final done=true chunk (US-87).
        if not terminated:
            yield json.dumps({"done": True}) + "\n"
        if perf:
            perf.emit(logger, client=ollama,
                      context_chunk_count=len(context_docs))


def _citation_warning_for_chunk(
    chunk: str,
    streamed_parts: List[str],
    context_docs: List[ContextDocument],
    question: str = "",
    llm_call: Optional[Any] = None,
    reader_llm_call: Optional[Any] = None,
) -> tuple[bool, Optional[str]]:
    """Accumulate streamed content; on the final chunk, audit the citations.

    Returns (is_final_chunk, warning_or_None). Malformed chunks are passed
    through untouched — the guard must never break the stream.
    """
    try:
        data = json.loads(chunk)
    except (ValueError, TypeError):
        return False, None
    if not data.get("done"):
        streamed_parts.append(data.get("message", {}).get("content", "") or "")
        return False, None

    from aitao.llm.citation_guard import (
        build_deleted_files_notice,
        build_multi_source_notice,
        build_stream_citation_warning,
        find_deleted_citations,
        find_fabricated_citations,
    )

    answer = "".join(streamed_parts)
    parts: List[str] = []
    removed = find_fabricated_citations(answer, context_docs)
    if removed:
        logger.warning(
            "Fabricated citations detected in streamed answer",
            metadata={"removed": removed},
        )
        parts.append(build_stream_citation_warning(removed))
    # US-28a — flag cited sources that are in trash (deterministic)
    deleted = find_deleted_citations(answer, context_docs)
    if deleted:
        parts.append(build_deleted_files_notice(deleted))
    # US-90-4 — list every retrieved document holding the question's token
    notice = build_multi_source_notice(
        question, context_docs, bearer_counter=_token_bearer_count
    )
    if notice:
        parts.append(notice)
    # US-076/US-104 — reliability trailer (deterministic + reader appeal +
    # attribution check + deep LLM pass) appended after the streamed answer,
    # which can't be rewritten.
    from .chat_grounding import grounding_trailer

    _trailer = grounding_trailer(
        answer, context_docs, get_rag_engine().embed_texts, llm_call,
        reader_llm_call=reader_llm_call,
    )
    if _trailer and _trailer[0]:
        parts.append(_trailer[0])
    return True, ("".join(parts) if parts else None)
