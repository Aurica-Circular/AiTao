# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
RAG context building and prompt helpers for chat endpoints.

Contains shared logic used by both the Ollama-compatible and
OpenAI-compatible chat endpoints:
- RAG system context construction (identity, user profile, indexed paths)
- Conversation attachment extraction for multi-turn context preservation
- System message injection / merging
- Context document serialization

Note (v3.1): Context is on by default. Tier 1 (identity + config) and Tier 3
(session attachments) are injected by ``inject_base_context`` independently of
the Premium RAG engine; Tier 2 document retrieval degrades gracefully. Only an
explicit per-request opt-out (`rag=false`) turns the endpoint into a pure proxy.
"""

from typing import Any, Dict, List, Optional

from aitao.core.logger import get_logger
from aitao.llm.rag_engine import ContextDocument

from .chat_models import _extract_text

logger = get_logger("api.chat")


# ---------------------------------------------------------------------------
# RAG system context
# ---------------------------------------------------------------------------

def _build_rag_system_context() -> str:
    """
    Build the Tier 1 system prompt (identity + behavioural contract + profile +
    indexed paths).

    Delegates to ``llm.system_prompt.SystemPromptBuilder`` (US-DEMO-8), which
    keeps AiTao's editable identity separate from the fixed anti-hallucination
    contract and clearly distinguishes the assistant from the user.

    Returns:
        A formatted system prompt string (always at least the contract), or an
        empty string if construction fails.
    """
    try:
        from aitao.llm.system_prompt import SystemPromptBuilder

        return SystemPromptBuilder().build()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Conversation attachment extraction
# ---------------------------------------------------------------------------

def _extract_conversation_attachments(messages: List) -> str:
    """
    Extract text from multimodal user messages (file attachments) in prior turns.

    Collects file text from earlier turns so it can be re-injected as a
    persistent system context block, ensuring the LLM never "forgets" a file
    shared earlier in the session.

    Only user messages *before* the last user message are examined.

    Args:
        messages: Full list of ChatMessage objects from the current request.

    Returns:
        Concatenated attachment text (separated by ---), or empty string.
    """
    if len(messages) <= 1:
        return ""

    last_user_idx: Optional[int] = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            last_user_idx = i
            break

    if last_user_idx is None or last_user_idx == 0:
        return ""

    parts: List[str] = []
    for i in range(last_user_idx):
        msg = messages[i]
        if msg.role != "user":
            continue
        # List-type content signals a client-side file or image attachment
        if isinstance(msg.content, list):
            text = _extract_text(msg.content)
            if text.strip():
                parts.append(text.strip())

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# System message injection
# ---------------------------------------------------------------------------

def _inject_rag_system_message(
    messages: list,
    system_context: str,
) -> list:
    """
    Inject or prepend the RAG system context into the message list.

    If a 'system' message already exists, the context is prepended to it.
    Otherwise a new 'system' message is inserted at position 0.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        system_context: The context string to inject.

    Returns:
        Updated message list (shallow copy — original is not mutated).
    """
    if not system_context:
        return messages

    messages = list(messages)

    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            existing = msg.get("content", "")
            messages[i] = {
                "role": "system",
                "content": f"{system_context}\n\n{existing}".strip(),
            }
            return messages

    messages.insert(0, {"role": "system", "content": system_context})
    return messages


# ---------------------------------------------------------------------------
# Base context injection (Tier 1 + Tier 3) — Core, no Premium dependency
# ---------------------------------------------------------------------------

def _last_user_text(request_messages: List) -> str:
    """Return the plain text of the most recent user message (or "")."""
    for msg in reversed(request_messages):
        if getattr(msg, "role", None) == "user":
            return _extract_text(msg.content)
    return ""


def inject_base_context(request_messages: List, msg_dicts: list) -> list:
    """Inject Tier 1 (identity + config) and Tier 3 (session attachments).

    This context is built purely from configuration and the current request,
    with no dependency on the Premium RAG engine. AiTao's identity and its
    awareness of the configured ``include_paths`` are therefore guaranteed in
    every edition (Core included) and survive even when document retrieval
    (Tier 2) is unavailable — the v3.1 "always-on context" contract.

    Args:
        request_messages: Original ChatMessage objects (for attachment scan).
        msg_dicts: Mutable list of {role, content} dicts to enrich.

    Returns:
        The updated msg_dicts (system message injected or merged in place).
    """
    # Tier 1 — identity, user profile, indexed directories (from config.toml)
    system_ctx = _build_rag_system_context()
    msg_dicts = _inject_rag_system_message(msg_dicts, system_ctx)

    # Tier 3 — files shared earlier in the same conversation
    conv_files = _extract_conversation_attachments(request_messages)
    if conv_files:
        note = (
            "Content shared by the user earlier in this conversation "
            "(use as primary reference for follow-up questions):\n\n"
            + conv_files
        )
        for _m in msg_dicts:
            if _m.get("role") == "system":
                _m["content"] = _m["content"] + "\n\n" + note
                break
        else:
            msg_dicts.insert(0, {"role": "system", "content": note})

    # US-DEMO-9 — config-priority directive for identity / scope questions.
    # Gives the model the exact config answer for "qui es-tu / qui suis-je /
    # quels volumes" so it stops guessing or answering as the wrong persona.
    try:
        from aitao.llm.intent_classifier import build_config_directive

        directive = build_config_directive(_last_user_text(request_messages))
    except Exception:
        directive = None
    if directive:
        for _m in msg_dicts:
            if _m.get("role") == "system":
                _m["content"] = _m["content"] + "\n\n" + directive
                break
        else:
            msg_dicts.insert(0, {"role": "system", "content": directive})

    return msg_dicts


# ---------------------------------------------------------------------------
# Context document serialization
# ---------------------------------------------------------------------------

def context_docs_to_dict(docs: List[ContextDocument]) -> List[Dict[str, Any]]:
    """Convert ContextDocument list to serialisable dicts for JSON responses."""
    return [
        {
            "id": doc.id,
            "path": doc.path,
            "title": doc.title,
            "score": doc.score,
            "category": doc.category,
        }
        for doc in docs
    ]
