# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
RAG data models for AiTao.

Pydantic v2 models used across the RAG pipeline (US-23: validated domain
objects — a missing/mistyped field raises ValidationError at construction):
- ContextDocument: a retrieved document used as context
- ContextChunk: a fine-grained text segment used as context
- RAGResult: the full result of a RAG enrichment operation
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Tolerant of surplus keys, strict on missing required fields."""

    model_config = ConfigDict(extra="ignore")


class ContextDocument(_Base):
    """
    A document retrieved as context for RAG.

    Attributes:
        id: Document ID (SHA256 hash)
        path: Absolute file path
        title: Document title or filename
        content: Relevant text excerpt
        score: Relevance score (0-1)
        category: Document category
        language: Detected language
    """
    id: str
    path: str
    title: str
    content: str
    score: float
    category: Optional[str] = None
    language: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextChunk(_Base):
    """
    A chunk retrieved as context for RAG.

    Chunks are fine-grained text segments (typically 512 tokens)
    that provide more precise context than full documents.

    Attributes:
        chunk_id: Unique chunk identifier
        doc_id: Parent document ID
        path: Source file path
        title: Document title
        content: Chunk text content
        chunk_index: Position within document (0-indexed)
        total_chunks: Total chunks in source document
        score: Relevance score (0-1)
        metadata: Additional metadata
    """
    chunk_id: str
    doc_id: str
    path: str
    title: str
    content: str
    chunk_index: int
    total_chunks: int
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RAGResult(_Base):
    """
    Result from RAG context enrichment.

    Attributes:
        original_prompt: The user's original prompt
        enriched_prompt: Prompt with context prepended
        context_docs: List of documents used as context (fallback mode)
        context_chunks: List of chunks used as context (preferred mode)
        total_context_tokens: Estimated token count of context
        search_time_ms: Time spent searching for context
        mode: Retrieval mode used ('chunks' or 'documents')
    """
    original_prompt: str
    enriched_prompt: str
    context_docs: List[ContextDocument]
    context_chunks: List[ContextChunk] = Field(default_factory=list)
    total_context_tokens: int = 0
    search_time_ms: float = 0.0
    mode: str = "documents"
