# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Search execution helpers for AiTao hybrid engine.

Provides standalone functions for executing the fusion (native Meilisearch
hybrid) search and chunk-based RAG retrieval.

ÉPIC-31 (US-113): the parallel LanceDB+Meilisearch fan-out helpers
(search_lancedb_sync, search_meilisearch_sync, search_parallel) are removed —
v4.0 is fusion-only (decision D1), so a single hybrid call replaces them.

These functions are used by HybridSearchEngine but kept separate to
maintain the main engine class under 400 lines.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from aitao.core.logger import get_logger
from aitao.search.path_filter import filter_hits_by_path, overfetch_limit
from aitao.search.search_models import ChunkSearchResponse, ChunkSearchResult, SearchFilter

logger = get_logger("search.executors")


def search_fusion_sync(
    meilisearch_client,
    lancedb_client,
    query: str,
    limit: int,
    filters: SearchFilter,
    semantic_ratio: float,
) -> Tuple[List[Dict[str, Any]], float]:
    """Execute the ÉPIC-31 (US-110) fusion document search.

    ONE native Meilisearch hybrid call (lexical + semantic in a single
    round-trip) replaces the parallel LanceDB+Meilisearch fan-out + RRF merge.
    The query vector is computed locally with the SAME embedding path the
    "rrf" engine already uses for its LanceDB leg (``lancedb_client._embed_text``)
    — no new embedding stack (US-110 point 4). Returns (hits, elapsed_ms), like
    the other search_*_sync helpers, for a uniform call site in HybridSearchEngine.

    US-125: ``filters.path_contains`` is NOT translatable into a Meilisearch
    filter (substring match, and `path` isn't a filterable attribute), so it
    is enforced as a Python post-filter (``filter_hits_by_path``) applied to
    the hits Meilisearch returns — identically for every mode (semantic /
    hybrid / fulltext), since they all go through this same call with just a
    different ``semantic_ratio``. The Meilisearch page itself is over-fetched
    (``overfetch_limit``) first, to compensate for hits the post-filter will
    discard. A no-op (same limit, no filtering) when ``path_contains`` is
    empty/None.
    """
    start = time.time()
    hits: List[Dict[str, Any]] = []

    if meilisearch_client is None or lancedb_client is None:
        return hits, 0.0

    try:
        vector = lancedb_client._embed_text(query, allow_empty=True)
        fetch_limit = overfetch_limit(limit, filters.path_contains)
        hits = meilisearch_client.search_hybrid(
            query=query,
            vector=vector,
            limit=fetch_limit,
            semantic_ratio=semantic_ratio,
            filter_category=filters.category,
            filter_language=filters.language,
        )
        if filters.path_contains:
            hits = filter_hits_by_path(hits, filters.path_contains)[:limit]
    except Exception as e:
        logger.warning(f"Fusion document search error: {e}")
        hits = []

    elapsed_ms = (time.time() - start) * 1000
    return hits, elapsed_ms


def search_chunks(
    chunk_store,
    query: str,
    limit: int = 5,
    min_score: float = 0.3,
    path_contains: Optional[str] = None,
) -> ChunkSearchResponse:
    """
    Search chunks for RAG context retrieval.

    Performs semantic search on the chunks table to find the most
    relevant text segments for LLM context.

    Args:
        chunk_store: ChunkStore instance (or None)
        query: Search query string
        limit: Maximum number of chunks to return
        min_score: Minimum similarity score threshold (0-1)
        path_contains: US-125 — optional folder filter (substring match on
            the chunk's document path). Same Python post-filter approach as
            ``search_fusion_sync``: over-fetch, then discard out-of-path
            hits, since Meilisearch cannot filter chunk paths natively
            either. A no-op when empty/None.

    Returns:
        ChunkSearchResponse with relevant chunks sorted by score
    """
    start_time = time.time()

    query = query.strip()
    if not query:
        return ChunkSearchResponse(query=query, chunks=[], total=0, unique_docs=0)

    if chunk_store is None:
        logger.warning("ChunkStore not available for chunk search")
        return ChunkSearchResponse(query=query, chunks=[], total=0, unique_docs=0)

    try:
        fetch_limit = overfetch_limit(limit * 2, path_contains)
        raw_results = chunk_store.search(query=query, limit=fetch_limit, min_score=min_score)
        if path_contains:
            raw_results = filter_hits_by_path(
                raw_results, path_contains, path_getter=lambda pair: pair[0].path,
            )

        chunks: List[ChunkSearchResult] = []
        seen_docs: set = set()

        for chunk, score in raw_results:
            seen_docs.add(chunk.doc_id)
            chunks.append(ChunkSearchResult(
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                path=chunk.path,
                title=chunk.title,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                total_chunks=chunk.total_chunks,
                score=round(score, 4),
                metadata=chunk.metadata,
            ))
            if len(chunks) >= limit:
                break

        search_time_ms = (time.time() - start_time) * 1000

        logger.info(
            f"Chunk search completed: {len(chunks)} chunks from {len(seen_docs)} docs",
            metadata={
                "query": query,
                "chunks_found": len(chunks),
                "unique_docs": len(seen_docs),
                "search_time_ms": round(search_time_ms, 2),
            }
        )

        return ChunkSearchResponse(
            query=query, chunks=chunks, total=len(chunks),
            unique_docs=len(seen_docs), search_time_ms=round(search_time_ms, 2),
        )

    except Exception as e:
        logger.error(f"Chunk search failed: {e}")
        return ChunkSearchResponse(query=query, chunks=[], total=0, unique_docs=0)
