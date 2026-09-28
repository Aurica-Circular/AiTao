# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Hybrid Search Engine for AiTao.

ÉPIC-31 (US-113): fusion is the only search engine since v4.0 (decision D1).
This module provides the HybridSearchEngine class:
- ONE native Meilisearch hybrid call per stage (documents for /api/search,
  chunks/excerpts for chat/RAG) — see search.hybrid_fusion.HybridFusionMixin.
- Chunk search for RAG (fine-grained retrieval for LLM context).
- Query expansion for better recall on short queries.

The parallel LanceDB+Meilisearch fan-out and the Reciprocal Rank Fusion (RRF)
merge it used to require are gone: a single hybrid call already returns hits
pre-ranked by Meilisearch's own blended relevance (see
search.result_merger.build_fusion_results).

Data models are defined in search.search_models.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from aitao.core.config import ConfigManager
from aitao.core.logger import get_logger
from aitao.search.hybrid_fusion import HybridFusionMixin
from aitao.search.search_models import ChunkSearchResponse, HybridSearchResponse, SearchFilter
from aitao.search import search_executors
from aitao.storage.repository import DocumentRepository, make_meilisearch_repository

logger = get_logger("search.hybrid")


class HybridSearchEngine(HybridFusionMixin):
    """
    Single-call hybrid (fusion) search engine (ÉPIC-31, US-110/US-113).

    Meilisearch runs both the lexical AND semantic legs of a query in ONE
    native hybrid call per stage — no parallel fan-out, no separate merge
    step to reconcile two ranked lists.

    Features:
    - Query expansion for short queries (CV → curriculum vitae, resume, 履歷)
    - Native Meilisearch hybrid ranking (blended relevance, one round-trip)

    Example:
        >>> engine = HybridSearchEngine()
        >>> response = await engine.search("où est mon CV ?")
        >>> for result in response.results:
        ...     print(f"{result.title}: {result.score:.2f}")
    """

    def __init__(
        self,
        max_workers: int = 2,
        enable_query_expansion: bool = True,
        lancedb_repo: Optional[DocumentRepository] = None,
        meilisearch_repo: Optional[DocumentRepository] = None,
        config: Optional[ConfigManager] = None,
    ):
        """
        Initialize the hybrid (fusion) search engine.

        Args:
            max_workers: Max threads for the executor running the blocking
                         Meilisearch client call.
            enable_query_expansion: Whether to expand short queries with synonyms.
            lancedb_repo: Historic name, kept for API/test compatibility — it
                          is NOT a real LanceDB connection (that class was
                          removed in US-113). Only ``_embed_text()``/
                          ``embed_texts()`` are used, to compute the query
                          vector for the hybrid call. Injected for tests;
                          defaults to the shared, LanceDB-free embedder
                          (search.embedding_source).
            meilisearch_repo: Full-text + semantic repository; injected for
                          tests or to swap the backend. Defaults to the
                          Meilisearch backend.
            config: ConfigManager instance (default: global singleton). Read
                    for ``semantic_ratio_documents`` — see
                    search/hybrid_fusion.py.
        """
        self.max_workers = max_workers
        self.enable_query_expansion = enable_query_expansion
        self._config = config

        # Repositories — injected, or lazily built from the default backends.
        self._lancedb_client = lancedb_repo
        self._meilisearch_client = meilisearch_repo
        self._chunk_store = None
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        logger.info(
            "HybridSearchEngine initialized",
            metadata={"max_workers": max_workers, "query_expansion": enable_query_expansion},
        )

    @property
    def lancedb_client(self) -> Optional[DocumentRepository]:
        """The query embedder used to compute the hybrid call's vector.

        Historic name (kept for API/test compatibility — ÉPIC-31 predates
        fusion-only v4.0). The ONLY consumers are ``_embed_text()``/
        ``embed_texts()`` calls (the document-stage fusion query vector in
        ``search_fusion_sync``, and ``RAGEngine.embed_texts`` for the answer
        validator's grounding check) — never ``.search()`` or any table
        operation. Returns the shared, LanceDB-free embedder
        (search.embedding_source) — the ``LanceDBClient`` class this used to
        build no longer exists (US-113). A directly injected repo (tests)
        bypasses this branch entirely.
        """
        if self._lancedb_client is None:
            from aitao.search.embedding_source import get_shared_embedder

            self._lancedb_client = get_shared_embedder(config=self._config)
        return self._lancedb_client

    @property
    def meilisearch_client(self) -> Optional[DocumentRepository]:
        """The full-text + semantic repository (lazily built if not injected)."""
        if self._meilisearch_client is None:
            try:
                self._meilisearch_client = make_meilisearch_repository()
            except Exception as e:
                logger.warning(f"Failed to initialize Meilisearch: {e}")
                self._meilisearch_client = None
        return self._meilisearch_client

    @property
    def chunk_store(self):
        """Lazy-load the excerpt store for RAG retrieval (MeiliChunkStore).

        Tests bypass this property entirely (they assign
        ``engine._chunk_store`` directly — see tests/golden/corpus_fixture.py).
        """
        if self._chunk_store is None:
            try:
                from aitao.indexation.chunk_store_meili import MeiliChunkStore

                self._chunk_store = MeiliChunkStore(config=self._config)
            except Exception as e:
                logger.warning(f"Failed to initialize chunk store: {e}")
                self._chunk_store = None
        return self._chunk_store

    async def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[SearchFilter] = None,
        mode: str = "hybrid",
    ) -> HybridSearchResponse:
        """
        Execute a hybrid (fusion) search: ONE native Meilisearch call
        carrying both the lexical query and the locally computed query
        vector — see ``search.hybrid_fusion.HybridFusionMixin._search_fusion``.

        Args:
            query: Search query string
            limit: Maximum number of results to return
            offset: Offset for pagination
            filters: Optional SearchFilter for filtering results
            mode: 'hybrid', 'semantic', or 'fulltext' — selects semanticRatio
                  1.0/0.0 instead of a different code path, so every mode
                  keeps working.

        Returns:
            HybridSearchResponse with the merged (fusion-ranked) results

        Example:
            >>> engine = HybridSearchEngine()
            >>> filters = SearchFilter(category="factures", language="fr")
            >>> response = await engine.search("voyage", filters=filters)
        """
        start_time = time.time()

        query = query.strip()
        if not query:
            return HybridSearchResponse(query=query, results=[], total=0, mode=mode)

        if filters is None:
            filters = SearchFilter()

        # Query expansion for better recall — agnostic to the engine.
        search_query = query
        if self.enable_query_expansion:
            try:
                from aitao.search.query_expansion import expand_query, should_expand

                if should_expand(query):
                    expanded = expand_query(query)
                    if expanded.expansion_applied:
                        search_query = expanded.expanded
                        logger.info(
                            f"Query expanded: '{query}' -> '{search_query}'",
                            metadata={"terms": expanded.terms},
                        )
            except ImportError:
                logger.debug("Query expansion module not available")

        # Adjust limit for offset handling (get more, then slice)
        fetch_limit = limit + offset

        logger.info(
            f"Hybrid search: '{query}'",
            metadata={
                "limit": limit,
                "offset": offset,
                "mode": mode,
                "expanded_query": search_query if search_query != query else None,
                "filters": {
                    "path": filters.path_contains,
                    "category": filters.category,
                    "language": filters.language,
                },
            },
        )

        return await self._search_fusion(
            query, search_query, fetch_limit, offset, limit, filters, mode, start_time,
        )

    def search_sync(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[SearchFilter] = None,
        mode: str = "hybrid",
    ) -> HybridSearchResponse:
        """
        Synchronous version of search for non-async contexts.

        Args:
            query: Search query string
            limit: Maximum number of results
            offset: Offset for pagination
            filters: Optional SearchFilter
            mode: Search mode

        Returns:
            HybridSearchResponse
        """
        # Try to get running loop, if any
        try:
            asyncio.get_running_loop()
            # We're inside an async context, create new loop in thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run, self.search(query, limit, offset, filters, mode)
                )
                return future.result()
        except RuntimeError:
            # No running loop, we can safely use asyncio.run
            return asyncio.run(self.search(query, limit, offset, filters, mode))

    def search_chunks(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        path_contains: Optional[str] = None,
    ) -> ChunkSearchResponse:
        """Delegate chunk search to search_executors.

        ``path_contains`` (US-125): optional folder filter, forwarded as-is
        — see ``search_executors.search_chunks`` for how it's enforced
        (Python post-filter, Meilisearch has no native path-substring
        filter). Not yet threaded from ``RAGEngine.enrich_prompt`` (no
        current caller passes a folder filter into the chat/RAG chunk path);
        exposed here so it is available end-to-end once one does.
        """
        return search_executors.search_chunks(self.chunk_store, query, limit, min_score, path_contains)

    def close(self):
        """Clean up resources."""
        self._executor.shutdown(wait=False)
