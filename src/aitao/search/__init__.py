# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Search engine for AiTao v4.

ÉPIC-31 (US-113): fusion-only since v4.0 (decision D1) — the LanceDB wrapper
(lancedb_client/lancedb_admin) is removed from the live path.

Modules:
- meilisearch_client: Meilisearch wrapper (full-text + semantic fusion search)
- hybrid_engine: Single-call hybrid (fusion) search + chunk search for RAG
"""

from aitao.search.meilisearch_client import (
    MeilisearchClient,
    MeilisearchError,
    MeilisearchConnectionError,
)
from aitao.search.hybrid_engine import HybridSearchEngine
from aitao.search.search_models import (
    ChunkSearchResponse,
    ChunkSearchResult,
    HybridSearchResponse,
    SearchFilter,
    SearchResult,
)

__all__ = [
    # Meilisearch
    "MeilisearchClient",
    "MeilisearchError",
    "MeilisearchConnectionError",
    # Hybrid Engine
    "HybridSearchEngine",
    "HybridSearchResponse",
    "SearchResult",
    "SearchFilter",
    # Chunk Search (RAG)
    "ChunkSearchResult",
    "ChunkSearchResponse",
]
