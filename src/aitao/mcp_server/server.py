# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/server.py — AiTao MCP Server (US-055)
#
# Responsibilities:
#   - Create and configure the FastMCP server instance
#   - Initialize shared AiTao engines (search, indexer) via lifespan
#   - Route transport selection (stdio / SSE / Streamable HTTP)
#   - Expose run_stdio(), run_sse(), run_http() entry points for CLI
#
# Transports:
#   stdio           → Claude Desktop, VS Code Copilot, Windsurf, Cursor
#   SSE  (:8201)    → HTTP clients (OnlyOffice, askimo, browsers)
#   HTTP (:8201)    → MCP 1.0 Streamable HTTP standard
#
# stdio note: in stdio mode stdout is the MCP wire. ALL diagnostics MUST go to
# stderr. Never use print() or Rich console without file=sys.stderr here.

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastmcp import FastMCP

from aitao.mcp_server.state import MCPServerState, set_state
from aitao.mcp_server.tools import (
    register_extract,
    register_ingest,
    register_ocr,
    register_search,
    register_stats,
)

# ---------------------------------------------------------------------------
# Server name and version (shown to MCP clients in discovery)
# ---------------------------------------------------------------------------
_SERVER_NAME = "AiTao"
_SERVER_VERSION = "2.9.4"
_SERVER_INSTRUCTIONS = (
    "AiTao — Local document search & indexing engine. "
    "Use aitao_search to query your indexed documents, "
    "aitao_ingest to index new files, and aitao_stats to "
    "see indexing statistics."
)


# ---------------------------------------------------------------------------
# Lifespan: initialize / teardown shared engines
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncGenerator[None, None]:
    """Initialize AiTao engines once at server startup.

    Engine initialisation is best-effort: if engines fail to load (e.g.
    AiTao services not running), the MCP server still starts and tools return
    a clear error message instead of crashing the whole server.
    """
    print("MCP Server: starting lifespan…", file=sys.stderr)

    search_engine = None
    task_queue = None
    config = None

    try:
        # Import core config — may fail if config.yaml is missing
        from aitao.core.config import get_config
        config = get_config()
    except Exception as exc:
        print(f"MCP Server: config load failed — {exc}", file=sys.stderr)

    try:
        from aitao.search.hybrid_engine import HybridSearchEngine
        qexp = config.search.hybrid.enable_query_expansion if config else True
        search_engine = HybridSearchEngine(enable_query_expansion=qexp)
    except Exception as exc:
        print(f"MCP Server: HybridSearchEngine load failed — {exc}", file=sys.stderr)

    try:
        from aitao.indexation.queue import TaskQueue
        task_queue = TaskQueue()
    except Exception as exc:
        print(f"MCP Server: TaskQueue load failed — {exc}", file=sys.stderr)

    state = MCPServerState(
        search_engine=search_engine,
        task_queue=task_queue,
        config=config,
    )
    set_state(state)

    engines_ok = search_engine is not None and task_queue is not None
    print(
        f"MCP Server: ready (engines {'OK' if engines_ok else 'DEGRADED — some tools may be unavailable'})",
        file=sys.stderr,
    )

    try:
        yield
    finally:
        print("MCP Server: shutdown", file=sys.stderr)
        set_state(None)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_mcp_server() -> FastMCP:
    """Create and configure the AiTao FastMCP server with all tools."""
    mcp = FastMCP(
        name=_SERVER_NAME,
        version=_SERVER_VERSION,
        instructions=_SERVER_INSTRUCTIONS,
        lifespan=_lifespan,
    )

    # Register all tools on the server instance
    register_search(mcp)
    register_ingest(mcp)
    register_stats(mcp)
    register_ocr(mcp)
    register_extract(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Transport runners (called from CLI)
# ---------------------------------------------------------------------------
def run_stdio() -> None:
    """Start MCP server in stdio mode (Claude Desktop, Copilot, Windsurf…)."""
    os.environ["AITAO_MCP_TRANSPORT"] = "stdio"
    mcp = create_mcp_server()
    mcp.run(transport="stdio")


def run_sse(host: str = "127.0.0.1", port: int = 8201) -> None:
    """Start MCP server in SSE mode (HTTP clients).

    Args:
        host: Bind address. Use '0.0.0.0' to allow remote access.
        port: Port to listen on (default 8201).
    """
    os.environ["AITAO_MCP_TRANSPORT"] = "sse"
    mcp = create_mcp_server()
    mcp.run(transport="sse", host=host, port=port)


def run_http(host: str = "127.0.0.1", port: int = 8201) -> None:
    """Start MCP server in Streamable HTTP mode (MCP 1.0 standard).

    Args:
        host: Bind address.
        port: Port to listen on (default 8201).
    """
    os.environ["AITAO_MCP_TRANSPORT"] = "streamable-http"
    mcp = create_mcp_server()
    mcp.run(transport="streamable-http", host=host, port=port)
