# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Chat API Pydantic models for AiTao.

Request/response schemas for both Ollama-compatible and OpenAI-compatible
chat endpoints.  Shared by chat.py, chat_openai.py, and any module that
needs to validate or serialize chat payloads.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def _extract_text(content: Any) -> str:
    """
    Normalize OpenAI message content to a plain string.
    Handles: None, plain str, and multimodal list [{"type":"text","text":"..."}].
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal format: extract all text parts
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content)


class ChatMessage(BaseModel):
    """Single chat message."""
    model_config = ConfigDict(extra="ignore")

    role: str = Field(..., description="Role: 'system', 'user', 'assistant'")
    content: Optional[Any] = Field(None, description="Message content (str or multimodal list)")


class ChatRequest(BaseModel):
    """Ollama-compatible chat request."""
    model: str = Field(..., description="Model name (e.g., 'qwen2.5-coder:7b')")
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    stream: bool = Field(default=True, description="Stream response")

    # RAG options
    rag_enabled: bool = Field(default=True, description="Enable RAG context enrichment")
    rag_max_docs: Optional[int] = Field(None, description="Max RAG context documents")

    # Model options (passed to Ollama)
    options: Optional[Dict[str, Any]] = Field(None, description="Model options")


class ChatResponseMessage(BaseModel):
    """Chat response message."""
    role: str = Field(default="assistant")
    content: str


class ChatResponse(BaseModel):
    """Non-streaming chat response."""
    model: str
    message: ChatResponseMessage
    created_at: str
    done: bool = True

    # RAG metadata
    rag_context: Optional[List[Dict[str, Any]]] = Field(
        None, description="RAG context documents used"
    )
    context_source: Optional[str] = Field(
        None, description="Where the answer was grounded: config | docs | session | none"
    )
    # US-076 — grounding check (present only when [rag] verify_answer is on)
    grounding_score: Optional[float] = Field(
        None, description="Mean support of the answer's sentences by the sources (0-1)"
    )
    grounding_unsupported: Optional[List[str]] = Field(
        None, description="Answer sentences not clearly supported by the sources"
    )
    # US-104 (part D6) — response-reader structured metadata, additive to the
    # text banners: how many answer sentences fell into each role, and a
    # short line per wrong-source attribution flagged (I-05).
    reliability_roles: Optional[Dict[str, int]] = Field(
        None, description="Answer sentence count per response_reader role"
    )
    reliability_attribution: Optional[List[str]] = Field(
        None, description="Wrong-source attribution flags (attributed -> probable)"
    )


class OpenAIChatRequest(BaseModel):
    """OpenAI-compatible chat completion request."""
    model_config = ConfigDict(extra="ignore")  # Accept any extra OpenAI field silently

    model: str = Field(..., description="Model name")
    messages: List[ChatMessage] = Field(..., description="Messages")
    stream: bool = Field(default=False, description="Stream response")
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None)

    # AiTao extension: {"rag": true, "folder": "my-folder", "save_history": true}
    # Passed alongside the standard OpenAI payload; ignored by other clients.
    aitao: Optional[Dict[str, Any]] = Field(
        None,
        description="AiTao-specific options (rag, folder, save_history)",
    )


class OpenAIChoice(BaseModel):
    """OpenAI response choice."""
    index: int = 0
    message: ChatResponseMessage
    finish_reason: str = "stop"


class OpenAIChatResponse(BaseModel):
    """OpenAI-compatible chat response."""
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChoice]

    # RAG extension
    rag_context: Optional[List[Dict[str, Any]]] = None
    context_source: Optional[str] = None
    # US-076 — grounding check (present only when [rag] verify_answer is on)
    grounding_score: Optional[float] = None
    grounding_unsupported: Optional[List[str]] = None
    # US-104 (part D6) — see ChatResponse for field semantics
    reliability_roles: Optional[Dict[str, int]] = None
    reliability_attribution: Optional[List[str]] = None
