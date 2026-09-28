# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_mcp_server.py — Unit tests for MCP server factory (US-055)
#
# Covers: create_mcp_server(), tool registration, lifespan init/teardown.
# Strategy: mock FastMCP internals and AiTao engines; never start real server.

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from aitao.mcp_server.state import get_state, set_state


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_state():
    set_state(None)
    yield
    set_state(None)


# ============================================================================
# Tests: create_mcp_server factory
# ============================================================================

class TestCreateMcpServer:
    """Tests for mcp_server.server.create_mcp_server()."""

    def test_creates_fastmcp_instance(self):
        from aitao.mcp_server.server import create_mcp_server
        mcp = create_mcp_server()
        assert mcp is not None
        assert mcp.name == "AiTao"

    def test_registers_all_five_tools(self):
        from aitao.mcp_server.server import create_mcp_server
        mcp = create_mcp_server()
        tools = _run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            "aitao_search",
            "aitao_ingest",
            "aitao_stats",
            "aitao_ocr",
            "aitao_extract",
        }
        assert expected == tool_names

    def test_server_version(self):
        from aitao.mcp_server.server import _SERVER_VERSION
        # Version must be a semver-like string
        parts = _SERVER_VERSION.split(".")
        assert len(parts) >= 2
        assert all(p.isdigit() for p in parts)

    def test_server_has_instructions(self):
        from aitao.mcp_server.server import _SERVER_INSTRUCTIONS
        assert "aitao_search" in _SERVER_INSTRUCTIONS


# ============================================================================
# Tests: lifespan
# ============================================================================

class TestLifespan:
    """Tests for the MCP server lifespan (init/teardown)."""

    def test_lifespan_sets_state(self):
        """Lifespan should set MCPServerState accessible via get_state()."""
        from aitao.mcp_server.server import _lifespan

        mock_server = MagicMock()
        mock_config = MagicMock()
        mock_config.get = MagicMock(return_value=0.6)
        mock_engine = MagicMock()
        mock_queue = MagicMock()

        async def _test():
            with patch.dict("sys.modules", {
                "aitao.core.config": MagicMock(get_config=MagicMock(return_value=mock_config)),
                "aitao.search.hybrid_engine": MagicMock(HybridSearchEngine=MagicMock(return_value=mock_engine)),
                "aitao.indexation.queue": MagicMock(TaskQueue=MagicMock(return_value=mock_queue)),
            }):
                async with _lifespan(mock_server):
                    state = get_state()
                    assert state is not None
                    assert state.search_engine is not None
                    assert state.task_queue is not None

        _run(_test())

    def test_lifespan_clears_state_on_exit(self):
        """After lifespan exits, state should be cleared."""
        from aitao.mcp_server.server import _lifespan

        mock_server = MagicMock()
        mock_config = MagicMock()
        mock_config.get = MagicMock(return_value=0.6)

        async def _test():
            with patch.dict("sys.modules", {
                "aitao.core.config": MagicMock(get_config=MagicMock(return_value=mock_config)),
                "aitao.search.hybrid_engine": MagicMock(HybridSearchEngine=MagicMock(return_value=MagicMock())),
                "aitao.indexation.queue": MagicMock(TaskQueue=MagicMock(return_value=MagicMock())),
            }):
                async with _lifespan(mock_server):
                    pass
            # After lifespan, state should be None
            with pytest.raises(RuntimeError):
                get_state()

        _run(_test())

    def test_lifespan_degraded_mode(self):
        """If engines fail to load, lifespan still completes (degraded mode)."""
        from aitao.mcp_server.server import _lifespan

        mock_server = MagicMock()

        async def _test():
            # Remove modules so imports fail inside lifespan
            with patch.dict("sys.modules", {
                "aitao.core.config": None,
                "aitao.search.hybrid_engine": None,
                "aitao.indexation.queue": None,
            }):
                async with _lifespan(mock_server):
                    state = get_state()
                    assert state.search_engine is None
                    assert state.task_queue is None
                    assert state.config is None

        _run(_test())
