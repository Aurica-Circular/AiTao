# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/search.py — MCP tool: aitao_search (US-055)
#
# Responsibilities:
#   - Expose aitao_search tool to MCP clients
#   - Wrap HybridSearchEngine (semantic + keyword hybrid)
#   - Support mode selection: hybrid, semantic, keyword
#   - Return structured results with score, category, excerpt

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from aitao.core.logger import get_logger
from aitao.mcp_server.state import get_state

logger = get_logger("mcp.tools.search")


def register_search(mcp: FastMCP) -> None:
    """Register aitao_search tool on the given FastMCP instance."""

    @mcp.tool()
    async def aitao_search(
        query: str,
        limit: int = 10,
        category: str | None = None,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        """Search indexed documents using AiTao's hybrid search engine.

        Args:
            query:    Natural language or keyword query.
            limit:    Maximum number of results to return (1–50).
            category: Optional document category filter (e.g. "contract",
                      "invoice"). Pass null to search all categories.
            mode:     Search mode — "hybrid" (default), "semantic",
                      or "keyword".

        Returns:
            Dictionary with 'results' list and 'total' count.
            Each result has: id, title, excerpt, score, category, path.
        """
        limit = max(1, min(50, limit))
        valid_modes = {"hybrid", "semantic", "keyword"}
        if mode not in valid_modes:
            mode = "hybrid"

        state = get_state()
        engine = state.search_engine

        if engine is None:
            return {
                "error": "Search engine is not available. Make sure AiTao services are running (./aitao.sh start).",
                "results": [],
                "total": 0,
            }

        filters: dict[str, Any] = {}
        if category:
            filters["category"] = category

        logger.debug("aitao_search called", metadata={"query": query, "limit": limit, "mode": mode})

        try:
            raw = await engine.search(
                query=query,
                limit=limit,
                offset=0,
                filters=filters if filters else None,
                mode=mode,
            )
        except Exception as e:
            logger.warning("aitao_search failed — falling back to search_sync", metadata={"error": str(e)})
            raw = engine.search_sync(query=query, limit=limit, filters=filters if filters else None)

        results = _normalise_results(raw)
        return {"results": results, "total": len(results), "query": query, "mode": mode}


def _normalise_results(raw: Any) -> list[dict[str, Any]]:
    """Normalise raw engine output into a consistent MCP-friendly list."""
    from aitao.search.search_models import HybridSearchResponse, SearchResult  # noqa: E402

    if isinstance(raw, HybridSearchResponse):
        items: list[Any] = list(raw.results)
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("hits", raw.get("results", []))
    else:
        return []

    normalised = []
    for item in items:
        if isinstance(item, SearchResult):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        normalised.append(
            {
                "id": item.get("id", ""),
                "title": item.get("title", item.get("filename", "")),
                "excerpt": item.get("excerpt", item.get("content", ""))[:500],
                "score": round(float(item.get("_score", item.get("score", 0.0))), 4),
                "category": item.get("category", ""),
                "path": item.get("path", item.get("file_path", "")),
            }
        )
    return normalised
