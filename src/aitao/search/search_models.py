# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Search data models for AiTao.

Pydantic v2 models used across the search module (US-23: validated domain
objects — a missing/mistyped field raises ValidationError at construction):
- SearchFilter: filtering options for hybrid search
- SearchResult: single result with combined scoring
- HybridSearchResponse: complete response from hybrid search
- ChunkSearchResult: single chunk result for RAG retrieval
- ChunkSearchResponse: response from chunk-based RAG search
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aitao.core.language_tags import normalize_language_query


class _Base(BaseModel):
    """Tolerant of surplus keys, strict on missing required fields."""

    model_config = ConfigDict(extra="ignore")


class SearchFilter(_Base):
    """
    Filtering options for hybrid search.

    Attributes:
        path_contains: Substring that must appear in document path
        category: Exact category match
        language: Language code (e.g., 'en', 'fr', 'zh')
        date_after: Documents modified after this date
        date_before: Documents modified before this date
        file_types: List of allowed file extensions (e.g., ['.pdf', '.docx'])
    """
    path_contains: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    date_after: Optional[datetime] = None
    date_before: Optional[datetime] = None
    file_types: Optional[List[str]] = None

    @field_validator("language")
    @classmethod
    def _normalize_language(cls, v: Optional[str]) -> Optional[str]:
        # US-85c: match the canonical short codes stored at index time, so
        # `--language zh-TW`, `--language FR` etc. filter the same documents
        # `--language zh` / `--language fr` do.
        return normalize_language_query(v)


class SearchResult(_Base):
    """
    Single search result with combined scoring.

    Attributes:
        id: Document ID (SHA256 hash)
        path: Absolute file path
        title: Document title or filename
        content: Text excerpt (first 500 chars)
        score: Combined relevance score (0-1)
        semantic_score: Score from LanceDB search (0-1)
        fulltext_score: Score from Meilisearch search (0-1)
        category: Document category
        language: Detected language
        file_size: Size in bytes
        modified_at: Last modification datetime
        metadata: Additional document metadata
    """
    id: str
    path: str
    title: str
    content: str
    score: float
    semantic_score: float = 0.0
    fulltext_score: float = 0.0
    category: Optional[str] = None
    language: Optional[str] = None
    file_size: Optional[int] = None
    modified_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # US-28a — file no longer exists on disk (in trash, pending purge)
    deleted: bool = False


class HybridSearchResponse(_Base):
    """
    Complete response from hybrid search.

    Attributes:
        query: Original search query
        results: List of SearchResult objects
        total: Total number of results found
        lancedb_count: Number of results from LanceDB
        meilisearch_count: Number of results from Meilisearch
        search_time_ms: Total search time in milliseconds
        lancedb_time_ms: LanceDB search time
        meilisearch_time_ms: Meilisearch search time
        mode: Search mode used (hybrid, semantic, fulltext)
    """
    query: str
    results: List[SearchResult]
    total: int
    lancedb_count: int = 0
    meilisearch_count: int = 0
    search_time_ms: float = 0.0
    lancedb_time_ms: float = 0.0
    meilisearch_time_ms: float = 0.0
    mode: str = "hybrid"


class ChunkSearchResult(_Base):
    """
    Single chunk result for RAG retrieval.

    Represents a fine-grained text segment optimized for LLM context.
    Multiple chunks may come from the same source document.

    Attributes:
        chunk_id: Unique chunk identifier
        doc_id: Parent document ID
        path: Source file path
        title: Document title
        content: Chunk text content (typically 512 tokens)
        chunk_index: Position within source document (0-indexed)
        total_chunks: Total chunks in source document
        score: Semantic similarity score (0-1)
        metadata: Additional chunk metadata
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


class ChunkSearchResponse(_Base):
    """
    Response from chunk-based RAG search.

    Attributes:
        query: Original search query
        chunks: List of ChunkSearchResult objects
        total: Total chunks found
        unique_docs: Number of unique source documents
        search_time_ms: Search execution time
    """
    query: str
    chunks: List[ChunkSearchResult]
    total: int
    unique_docs: int = 0
    search_time_ms: float = 0.0
