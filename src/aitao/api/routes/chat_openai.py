# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
OpenAI-compatible chat endpoint for AiTao.

Provides POST /v1/chat/completions mirroring the OpenAI API format.
RAG behaviour is controlled per-request via the `aitao` extension field:

    POST /v1/chat/completions
    {
      "model": "llama3.1:8b",
      "messages": [...],
      "aitao": {"rag": true, "folder": "my-docs"}
    }

When `aitao.rag` is false (default), requests are proxied transparently
to Ollama. No virtual model naming convention — /v1/models returns real
Ollama models only.
"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from aitao.core.logger import get_logger
from aitao.llm.protocols import (
    OllamaChatMessage,
    OllamaConnectionError,
    OllamaModelNotFound,
)
from aitao.core.config import get_config
from aitao.llm.rag_engine import ContextDocument
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
from aitao.llm.perf_metrics import PerfTracker
from aitao.llm.reader_appeal import PROBE_OPTIONS as READER_APPEAL_OPTIONS

from .chat_models import (
    _extract_text,
    ChatResponseMessage,
    OpenAIChatRequest,
    OpenAIChoice,
    OpenAIChatResponse,
)
from .chat_rag_helpers import (
    inject_base_context,
    context_docs_to_dict,
    _extract_conversation_attachments,
    _last_user_text,
)

logger = get_logger("api.chat")

openai_router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])


def _token_bearer_count(token: str) -> int:
    """True number of indexed docs holding ``token`` — for the US-90-5 notice."""
    try:
        from .chat import get_rag_engine

        return get_rag_engine().count_token_bearers(token)
    except Exception:
        return 0


@openai_router.post("/chat/completions", response_model=OpenAIChatResponse)
async def openai_chat_completion(request: OpenAIChatRequest):
    """
    OpenAI-compatible chat completion endpoint.

    Proxies to Ollama. Pass `"aitao": {"rag": true}` to enable RAG enrichment.
    """
    # Lazy import to avoid circular dependency and preserve test mock paths
    from .chat import get_ollama_client, get_rag_engine

    perf = PerfTracker(model=request.model, endpoint="/v1/chat/completions")

    # Read AiTao-specific options from the optional `aitao` extension field.
    # v3.1: context is the default. Only an explicit `aitao.rag = false` opts
    # out — clients that send no `aitao` field (e.g. OnlyOffice) get full
    # context, so AiTao's identity and indexed paths are always available.
    aitao_opts: Dict[str, Any] = request.aitao or {}
    effective_context_enabled: bool = bool(aitao_opts.get("rag", True))
    effective_filter: str | None = aitao_opts.get("folder") or None

    logger.info(
        "OpenAI-format chat request",
        metadata={
            "model": request.model,
            "message_count": len(request.messages),
            "stream": request.stream,
            "context_enabled": effective_context_enabled,
        },
    )

    try:
        ollama = get_ollama_client()

        # Convert messages (content can be None or multimodal list)
        messages = [
            OllamaChatMessage(role=m.role, content=_extract_text(m.content))
            for m in request.messages
        ]

        # v3.1 context pipeline. Tier 1 (identity + config) and Tier 3 (session
        # attachments) are always injected; Tier 2 (document RAG) degrades
        # gracefully so identity survives even if retrieval is unavailable.
        context_docs: List[ContextDocument] = []
        rag_ran = False
        resolved_source: str | None = None
        last_user = ""
        general_route = False  # US-105 — intent router routed to "general"
        if effective_context_enabled and messages:
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
            # for config questions and small talk: answered from config / no
            # docs needed, so we avoid the slower search and a padded prompt.
            # ``gate_state`` (US-103): filled by the conversation-dossier hook
            # during enrichment, consumed by the context gate below.
            gate_state: dict = {}
            if needs_document_retrieval(last_user):
                # US-105 (I-16) — see chat.py for the full rationale: a small
                # LLM call decides "documentary" vs "general" before Tier 2
                # runs. Fail-open to documentary on any non-nominal path.
                if bool(getattr(get_config().rag, "intent_router", True)):
                    from .chat_grounding import make_llm_verify_call

                    # [:-1] + PROBE_OPTIONS — same rationale as chat.py
                    # (US-105.2): never show the probe its own question as
                    # history, and keep the verdict deterministic.
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
                        filters = {"category": effective_filter} if effective_filter else None
                        enriched_msgs, context_docs, _ = rag.enrich_messages(
                            msg_dicts,
                            filters=filters,
                            dossier_state=gate_state,
                        )
                        msg_dicts = enriched_msgs
                        rag_ran = True
                    except Exception as e:
                        logger.warning(
                            f"Tier 2 RAG unavailable, Tier 1 context preserved: {e}"
                        )

            messages = [
                OllamaChatMessage(role=m["role"], content=m["content"])
                for m in msg_dicts
            ]

            # US-103 — context gate (ÉPIC-30 brique 2). Only when retrieval
            # actually ran: confront the retrieved context with the question.
            # Proceed, targeted notary refusal, or ONE clarification question —
            # the last two answered without any LLM call.
            if rag_ran:
                # US-RAG-name — the user named a file that wasn't retrieved:
                # state it deterministically instead of confabulating.
                advisory = named_reference_advisory(
                    last_user, context_docs, get_config().indexing.include_paths
                )
                if advisory:
                    logger.info("Named file not indexed — advising without LLM call")
                    perf.emit(logger, llm_called=False,
                              context_chunk_count=len(context_docs))
                    return _openai_refusal(request, advisory)
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
                    return _openai_refusal(request, verdict.message)
            resolved_source = context_source(last_user, context_docs, has_session)

        # Build Ollama options from OpenAI params
        options: Dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens

        # Streaming response
        if request.stream:
            return StreamingResponse(
                _stream_openai_response(
                    ollama, messages, request, options, context_docs,
                    guard=rag_ran, question=last_user, perf=perf,
                    general_banner=GENERAL_ANSWER_BANNER if general_route else None,
                ),
                media_type="text/event-stream",
            )

        # Non-streaming response
        response = ollama.chat(
            messages=messages,
            model=request.model,
            stream=False,
            options=options if options else None,
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
            from .chat import get_rag_engine
            from .chat_grounding import grounding_trailer, make_llm_verify_call

            _trailer = grounding_trailer(
                content,
                context_docs,
                get_rag_engine().embed_texts,
                make_llm_verify_call(
                    ollama, request.model, options if options else None
                ),
                reader_llm_call=make_llm_verify_call(
                    ollama, request.model, READER_APPEAL_OPTIONS
                ),
            )
            if _trailer:
                _text, grounding = _trailer
                content += _text

        perf.emit(logger, message="OpenAI chat completed", client=ollama,
                  context_chunk_count=len(context_docs))

        return OpenAIChatResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=request.model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message=ChatResponseMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            rag_context=context_docs_to_dict(context_docs) if context_docs else None,
            context_source=resolved_source,
            grounding_score=grounding.grounding_score if grounding else None,
            grounding_unsupported=grounding.unsupported if grounding else None,
            reliability_roles=grounding.role_counts if grounding else None,
            reliability_attribution=grounding.attribution_notes if grounding else None,
        )

    except OllamaConnectionError as e:
        perf.emit_error(logger, e)
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except OllamaModelNotFound as e:
        perf.emit_error(logger, e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"OpenAI chat error: {e}")
        perf.emit_error(logger, e)
        raise HTTPException(status_code=500, detail=str(e))


def _openai_refusal(request: OpenAIChatRequest, text: str):
    """Build a refusal response (streaming or not) without calling the LLM."""
    if request.stream:
        return StreamingResponse(
            _stream_openai_static(request.model, text),
            media_type="text/event-stream",
        )
    return OpenAIChatResponse(
        id=f"chatcmpl-{int(time.time())}",
        created=int(time.time()),
        model=request.model,
        choices=[
            OpenAIChoice(
                index=0,
                message=ChatResponseMessage(role="assistant", content=text),
                finish_reason="stop",
            )
        ],
        context_source="none",
    )


async def _stream_openai_static(model: str, text: str) -> AsyncGenerator[str, None]:
    """Stream a fixed message in OpenAI SSE format (used for refusals).

    Chunk sequence mirrors the real OpenAI API: a role-only delta first, then
    the content, then the stop chunk. Clients initialise the assistant message
    on the role delta — content sent before/without it gets dropped (US-17c
    bug: instant refusals displayed as an empty answer).
    """
    base = {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    role_chunk = {
        **base,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": ""},
            "finish_reason": None,
        }],
    }
    yield f"data: {json.dumps(role_chunk)}\n\n"
    content_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(content_chunk)}\n\n"
    last = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(last)}\n\n"
    yield "data: [DONE]\n\n"


async def _stream_openai_response(
    ollama: Any,  # the configured LLM backend (Ollama-shaped interface)
    messages: List[OllamaChatMessage],
    request: OpenAIChatRequest,
    options: Dict[str, Any],
    context_docs: List[ContextDocument],
    guard: bool = False,
    question: str = "",
    perf: Optional[PerfTracker] = None,
    general_banner: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Generate streaming response in OpenAI SSE format.

    With ``guard=True`` (RAG ran), streamed text is accumulated and audited
    for citations of sources absent from the retrieved context; a warning
    delta is emitted before the final chunk when fabrications are found
    (streamed text cannot be unsent) — US-17c. A deterministic multi-source
    notice is also appended when the question's token spans several documents
    (US-90-4), so the user sees every source even if the model named one.

    ``general_banner`` (US-105): when the intent router routed this turn to
    "general", the honest banner is sent as the FIRST content delta — a
    prefix, unlike the trailers above which are suffixes appended after the
    model's own streamed tokens.
    """
    done_sent = False
    try:
        if general_banner:
            banner_chunk = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": general_banner},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(banner_chunk)}\n\n"

        streamed_parts: List[str] = []
        for chunk in ollama.chat(
            messages=messages,
            model=request.model,
            stream=True,
            options=options if options else None,
        ):
            try:
                data = json.loads(chunk)
                if perf:
                    perf.on_chunk(data)
                content = data.get("message", {}).get("content", "")
                done = data.get("done", False)

                if guard and content:
                    streamed_parts.append(content)
                if guard and done:
                    from aitao.llm.citation_guard import build_multi_source_notice

                    warning = _audit_streamed_citations(
                        streamed_parts, context_docs
                    )
                    # US-90-4 — deterministic list of every document holding the
                    # token, so the user sees them all even if the model named one.
                    trailer = (warning or "") + build_multi_source_notice(
                        question, context_docs, bearer_counter=_token_bearer_count
                    )
                    # US-076/US-104 — reliability trailer (deterministic +
                    # reader appeal + attribution check + deep LLM pass)
                    from .chat import get_rag_engine
                    from .chat_grounding import (
                        grounding_trailer,
                        make_llm_verify_call,
                    )

                    _gt = grounding_trailer(
                        "".join(streamed_parts),
                        context_docs,
                        get_rag_engine().embed_texts,
                        make_llm_verify_call(ollama, request.model, options),
                        reader_llm_call=make_llm_verify_call(
                            ollama, request.model, READER_APPEAL_OPTIONS
                        ),
                    )
                    if _gt and _gt[0]:
                        trailer += _gt[0]
                    if trailer:
                        trailer_chunk = {
                            "id": f"chatcmpl-{int(time.time())}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": request.model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": trailer},
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(trailer_chunk)}\n\n"

                openai_chunk = {
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": content} if content else {},
                            "finish_reason": "stop" if done else None,
                        }
                    ],
                }

                yield f"data: {json.dumps(openai_chunk)}\n\n"

                if done:
                    yield "data: [DONE]\n\n"
                    done_sent = True
                    break

            except json.JSONDecodeError:
                continue

    except Exception as e:
        # Backend stalled (request_timeout fired), dropped the connection, or the
        # model errored mid-stream. Log it server-side and close the message
        # cleanly with a finish chunk; the finally below guarantees the SSE
        # terminator so strict clients (OnlyOffice) never hang waiting. US-87.
        logger.warning(f"OpenAI streaming interrupted: {type(e).__name__}: {e}")
        if perf:
            perf.record_error(e)
        # US-87 p2: a read timeout means the model went silent (stalled). Tell the
        # user plainly instead of ending on an empty/cryptic message.
        from .chat import _STALL_MESSAGE, _is_idle_timeout

        if _is_idle_timeout(e):
            stall_chunk = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": _STALL_MESSAGE},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(stall_chunk)}\n\n"
        close_chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(close_chunk)}\n\n"
    finally:
        # Always terminate the SSE stream. On success [DONE] was already sent
        # (done_sent); on exception, or a stream that ends without done=True,
        # it would otherwise be missing — leaving the client hung. US-87.
        if not done_sent:
            yield "data: [DONE]\n\n"
        if perf:
            perf.emit(logger, message="OpenAI chat completed", client=ollama,
                      context_chunk_count=len(context_docs))


def _audit_streamed_citations(
    streamed_parts: List[str], context_docs: List[ContextDocument]
) -> Optional[str]:
    """Return a warning when the streamed answer cites unknown or deleted
    sources (US-17c fabrication guard + US-28a trash flagging)."""
    from aitao.llm.citation_guard import (
        build_deleted_files_notice,
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
    deleted = find_deleted_citations(answer, context_docs)
    if deleted:
        parts.append(build_deleted_files_notice(deleted))
    return "".join(parts) if parts else None
