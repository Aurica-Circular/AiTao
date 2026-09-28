# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/__init__.py — AiTao MCP tools registry
#
# Registers all tools (free and premium) on the FastMCP server instance.
# Free tools:    aitao_search, aitao_ingest, aitao_stats
# Premium tools: aitao_ocr, aitao_extract

from aitao.mcp_server.tools.search import register_search
from aitao.mcp_server.tools.ingest import register_ingest
from aitao.mcp_server.tools.stats import register_stats
from aitao.mcp_server.tools.ocr import register_ocr
from aitao.mcp_server.tools.extract import register_extract

__all__ = [
    "register_search",
    "register_ingest",
    "register_stats",
    "register_ocr",
    "register_extract",
]
