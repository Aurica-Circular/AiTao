# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# hybrid_fusion.py — US-110 (ÉPIC-31) native Meilisearch hybrid search mixin.
#
# Mixin for HybridSearchEngine: a SINGLE native Meilisearch hybrid call for
# the DOCUMENT stage (/api/search, HybridSearchEngine.search()) — the only
# search path since v4.0 (US-113, decision D1; the former parallel
# LanceDB+Meilisearch fan-out + Reciprocal Rank Fusion merge is gone). The
# excerpt/chunk stage (chat) is handled separately by
# indexation.chunk_store_meili.MeiliChunkStore.
#
# Kept in its own module so hybrid_engine.py stays close to the project's
# file-size convention instead of growing the fusion logic inline. Expects
# the host class (HybridSearchEngine) to provide: meilisearch_client,
# lancedb_client, _executor (ThreadPoolExecutor), _config (Optional[ConfigManager]).

from __future__ import annotations

import asyncio
import time
from typing import List

from aitao.core.config import get_config
from aitao.core.events import SEARCH_EXECUTED, event_bus
from aitao.core.logger import get_logger
from aitao.search.result_merger import build_fusion_results
from aitao.search.search_models import HybridSearchResponse, SearchFilter, SearchResult

logger = get_logger("search.hybrid_fusion")


class HybridFusionMixin:
    """Single native hybrid-call search path — the only engine since v4.0."""

    def _semantic_ratio_documents(self) -> float:
        """Hybrid semanticRatio for the document stage (US-106 volet 2bis)."""
        try:
            cfg = self._config or get_config()
            return cfg.search.semantic_ratio_documents
        except Exception:
            return 0.5

    def _search_fusion_sync(self, query, limit, filters, semantic_ratio):
        """Delegate to search_executors (thread-pool friendly, synchronous)."""
        from aitao.search import search_executors
        return search_executors.search_fusion_sync(
            self.meilisearch_client, self.lancedb_client, query, limit, filters, semantic_ratio,
        )

    @staticmethod
    def _flag_trashed(results: List[SearchResult]) -> None:
        """US-28a — flag results whose source file was deleted from disk."""
        try:
            from aitao.indexation.trash import get_trash_registry
            registry = get_trash_registry()
            for result in results:
                if registry.is_trashed(result.path):
                    result.deleted = True
        except Exception as exc:
            logger.debug(f"Trash flagging skipped: {exc}")

    async def _search_fusion(
        self,
        query: str,
        search_query: str,
        fetch_limit: int,
        offset: int,
        limit: int,
        filters: SearchFilter,
        mode: str,
        start_time: float,
    ) -> HybridSearchResponse:
        """US-110 — one native Meilisearch hybrid call, no RRF, no fan-out.

        ``mode`` still selects semantic-only / fulltext-only via semanticRatio
        (1.0 / 0.0) instead of dispatching to a different code path, so every
        mode the "rrf" engine supports keeps working under "fusion" too.
        """
        ratio = {"semantic": 1.0, "fulltext": 0.0}.get(mode, self._semantic_ratio_documents())

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        hits, meili_time = await loop.run_in_executor(
            self._executor, self._search_fusion_sync, search_query, fetch_limit, filters, ratio,
        )

        all_results = build_fusion_results(hits, fetch_limit)
        paginated = all_results[offset:offset + limit]
        self._flag_trashed(paginated)

        total_time_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Fusion search completed: {len(paginated)} results",
            metadata={
                "total_results": len(all_results),
                "semantic_ratio": ratio,
                "total_time_ms": round(total_time_ms, 2),
                "meilisearch_time_ms": round(meili_time, 2),
            },
        )
        event_bus.publish(
            SEARCH_EXECUTED,
            query=query, results=len(paginated), total=len(all_results),
            time_ms=round(total_time_ms, 2),
        )
        return HybridSearchResponse(
            query=query,
            results=paginated,
            total=len(all_results),
            lancedb_count=0,
            meilisearch_count=len(all_results),
            search_time_ms=round(total_time_ms, 2),
            lancedb_time_ms=0.0,
            meilisearch_time_ms=round(meili_time, 2),
            mode=mode,
        )
