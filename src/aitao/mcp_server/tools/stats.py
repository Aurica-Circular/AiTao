# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/stats.py — MCP tool: aitao_stats (US-055)
#
# Responsibilities:
#   - Expose aitao_stats tool to MCP clients
#   - Return Meilisearch index statistics (document count, indexing status)
#   - Include the Meilisearch excerpt/chunk index's vector stats (ÉPIC-31,
#     US-113 — the semantic backend since v4.0 fusion-only; LanceDB removed)
#   - Summarise AiTao server health for MCP clients

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from aitao.core.logger import get_logger
logger = get_logger("mcp.tools.stats")


def register_stats(mcp: FastMCP) -> None:
    """Register aitao_stats tool on the given FastMCP instance."""

    @mcp.tool()
    async def aitao_stats() -> dict[str, Any]:
        """Return AiTao indexing and system statistics.

        Returns:
            Dictionary with:
              - meilisearch: total_documents, is_indexing, index_name
              - vector_store: vector_count (Meilisearch excerpt/chunk index)
              - status: "ready" | "indexing" | "error"
        """
        stats: dict[str, Any] = {
            "status": "ready",
            "meilisearch": {},
            "vector_store": {},
        }

        # --- Meilisearch stats ---
        try:
            from aitao.search.meilisearch_client import MeilisearchClient  # type: ignore
            from aitao.core.registry import StatsKeys
            client = MeilisearchClient()
            ms_stats = client.get_stats()
            stats["meilisearch"] = {
                "total_documents": ms_stats.get(StatsKeys.TOTAL_DOCUMENTS, 0),
                "is_indexing": ms_stats.get(StatsKeys.IS_INDEXING, False),
                "index_name": ms_stats.get(StatsKeys.INDEX_NAME, ""),
                "last_update": ms_stats.get(StatsKeys.LAST_UPDATE, ""),
            }
            if ms_stats.get(StatsKeys.IS_INDEXING):
                stats["status"] = "indexing"
        except Exception as e:
            logger.warning("Failed to fetch Meilisearch stats", metadata={"error": str(e)})
            stats["meilisearch"] = {"error": str(e)}
            stats["status"] = "error"

        # --- Vector store stats (Meilisearch excerpt/chunk index, ÉPIC-31 US-113) ---
        try:
            import meilisearch
            from aitao.core.config import get_config

            ms = get_config().search.meilisearch
            raw = meilisearch.Client(ms.url, ms.api_key or None)
            chunk_stats = raw.index(ms.chunks_index).get_stats()
            vector_count = getattr(chunk_stats, "number_of_documents", None)
            if vector_count is None and isinstance(chunk_stats, dict):
                vector_count = chunk_stats.get("numberOfDocuments", 0)
            stats["vector_store"] = {
                "vector_count": int(vector_count or 0), "backend": "meilisearch",
            }
        except Exception as e:
            logger.debug("Vector store stats unavailable", metadata={"error": str(e)})
            stats["vector_store"] = {"vector_count": None, "error": str(e)}

        logger.debug("aitao_stats called", metadata={"status": stats["status"]})
        return stats
