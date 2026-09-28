# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# api/routes/mcp.py — REST endpoint for MCP server status (US-055)
#
# Responsibilities:
#   - Expose GET /api/mcp/status for dashboards and health monitors
#   - Report whether the SSE/HTTP MCP server is running and on which port
#   - No authentication required (informational endpoint, localhost bound)

from __future__ import annotations

import urllib.error
import urllib.request

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp", tags=["MCP"])

_DEFAULT_MCP_PORT = 8201
_DEFAULT_MCP_HOST = "127.0.0.1"


class MCPStatusResponse(BaseModel):
    running: bool
    transport: str
    host: str
    port: int
    url: str | None
    message: str


@router.get("/status", response_model=MCPStatusResponse)
async def mcp_status() -> MCPStatusResponse:
    """Check whether the AiTao MCP server (SSE/HTTP) is reachable.

    Returns:
        MCPStatusResponse with 'running' boolean and connection details.
    """
    host = _DEFAULT_MCP_HOST
    port = _DEFAULT_MCP_PORT
    url = f"http://{host}:{port}/"

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — loopback only
            return MCPStatusResponse(
                running=True,
                transport="sse/http",
                host=host,
                port=port,
                url=url,
                message=f"MCP server is running (HTTP {resp.status})",
            )
    except (urllib.error.URLError, OSError):
        return MCPStatusResponse(
            running=False,
            transport="unknown",
            host=host,
            port=port,
            url=None,
            message="MCP server is not running on SSE/HTTP. Use stdio transport for Claude Desktop.",
        )
