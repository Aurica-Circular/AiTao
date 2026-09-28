# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp_server/state.py — Shared server state for MCP tools (US-055)
#
# Responsibilities:
#   - Hold references to initialized AiTao engines (search, task queue)
#   - Provide a process-level singleton accessible from every tool handler
#   - Support degraded mode: engines may be None if AiTao services are offline

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MCPServerState:
    """Holds live references to AiTao engines for the duration of the server.

    Fields may be None when the corresponding service failed to start.
    Tools should check for None and return a friendly error message.
    """

    search_engine: Any          # HybridSearchEngine | None
    task_queue: Any             # TaskQueue | None
    config: Any                 # Config singleton | None


_state: Optional[MCPServerState] = None


def set_state(state: Optional[MCPServerState]) -> None:
    """Set (or clear) the global server state. Called from server lifespan."""
    global _state
    _state = state


def get_state() -> MCPServerState:
    """Return the active server state. Raises RuntimeError when called outside lifespan."""
    if _state is None:
        raise RuntimeError("MCP server state is not initialised — server may not be running")
    return _state
