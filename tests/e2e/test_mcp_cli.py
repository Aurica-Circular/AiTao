# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/e2e/test_mcp_cli.py — E2E tests for MCP CLI commands (US-055)
#
# Covers: aitao mcp serve/stop/status/config subcommands via CliRunner.
# These tests invoke the real Typer CLI without starting real servers.

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

# Ensure src is importable
_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.cli.commands.mcp import app  # noqa: E402

runner = CliRunner()


# ============================================================================
# Tests: mcp serve
# ============================================================================

class TestMcpServe:
    """E2E tests for `aitao mcp serve`."""

    def test_serve_unknown_transport(self):
        result = runner.invoke(app, ["serve", "--transport", "grpc"])
        assert result.exit_code != 0
        assert "Unknown transport" in result.output or result.exit_code == 1

    def test_serve_stdio_with_daemon_rejected(self):
        result = runner.invoke(app, ["serve", "--transport", "stdio", "--daemon"])
        assert result.exit_code != 0
        assert "not supported" in result.output.lower() or result.exit_code == 1

    @patch("aitao.cli.commands.mcp._run_foreground")
    def test_serve_stdio_foreground(self, mock_run):
        result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(transport="stdio", host="127.0.0.1", port=8201)

    @patch("aitao.cli.commands.mcp._run_foreground")
    def test_serve_sse_foreground(self, mock_run):
        result = runner.invoke(app, ["serve", "--transport", "sse"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(transport="sse", host="127.0.0.1", port=8201)

    @patch("aitao.cli.commands.mcp._run_foreground")
    def test_serve_http_foreground(self, mock_run):
        result = runner.invoke(app, ["serve", "--transport", "http"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(transport="http", host="127.0.0.1", port=8201)

    @patch("aitao.cli.commands.mcp._run_foreground")
    def test_serve_custom_host_port(self, mock_run):
        result = runner.invoke(app, [
            "serve", "--transport", "sse",
            "--host", "0.0.0.0", "--port", "9999",
        ])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(transport="sse", host="0.0.0.0", port=9999)

    @patch("aitao.cli.commands.mcp._start_daemon")
    def test_serve_daemon_mode(self, mock_daemon):
        result = runner.invoke(app, ["serve", "--transport", "sse", "--daemon"])
        assert result.exit_code == 0
        assert mock_daemon.called
        call_kwargs = mock_daemon.call_args
        assert call_kwargs.kwargs.get("transport") or call_kwargs[1].get("transport") or "sse" in str(call_kwargs)


# ============================================================================
# Tests: mcp stop
# ============================================================================

class TestMcpStop:
    """E2E tests for `aitao mcp stop`."""

    def test_stop_no_pid_file(self, tmp_path):
        with patch("aitao.cli.commands.mcp._MCP_PID_FILE", tmp_path / "no.pid"):
            result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_stop_stale_pid(self, tmp_path):
        pid_file = tmp_path / "mcp.pid"
        pid_file.write_text("99999999")  # Non-existent PID
        with patch("aitao.cli.commands.mcp._MCP_PID_FILE", pid_file):
            result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "stale" in result.output.lower() or "not running" in result.output.lower()

    def test_stop_running_process(self, tmp_path):
        pid_file = tmp_path / "mcp.pid"
        pid_file.write_text(str(os.getpid()))  # Use our own PID (exists)
        with patch("aitao.cli.commands.mcp._MCP_PID_FILE", pid_file):
            with patch("os.kill") as mock_kill:
                result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        mock_kill.assert_called_once()
        assert "stopped" in result.output.lower()


# ============================================================================
# Tests: mcp status
# ============================================================================

class TestMcpStatus:
    """E2E tests for `aitao mcp status`."""

    def test_status_not_running(self, tmp_path):
        with patch("aitao.cli.commands.mcp._MCP_PID_FILE", tmp_path / "no.pid"):
            with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_running_responds(self, tmp_path):
        pid_file = tmp_path / "mcp.pid"
        pid_file.write_text(str(os.getpid()))

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("aitao.cli.commands.mcp._MCP_PID_FILE", pid_file):
            with patch("os.kill"):  # PID probe succeeds
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "UP" in result.output.upper() or "running" in result.output.lower()


# ============================================================================
# Tests: mcp config
# ============================================================================

class TestMcpConfig:
    """E2E tests for `aitao mcp config`."""

    def test_config_stdio(self):
        result = runner.invoke(app, ["config", "--transport", "stdio"])
        assert result.exit_code == 0
        assert "mcpServers" in result.output
        assert "stdio" in result.output

    def test_config_sse(self):
        result = runner.invoke(app, ["config", "--transport", "sse"])
        assert result.exit_code == 0
        assert "mcpServers" in result.output
        assert "/sse" in result.output

    def test_config_custom_port(self):
        result = runner.invoke(app, ["config", "--transport", "sse", "--port", "9999"])
        assert result.exit_code == 0
        assert "9999" in result.output


# ============================================================================
# Tests: mcp help
# ============================================================================

class TestMcpHelp:
    """E2E tests for `aitao mcp help`."""

    def test_help_shows_all_commands(self):
        result = runner.invoke(app, ["help"])
        assert result.exit_code == 0
        assert "serve" in result.output
        assert "stop" in result.output
        assert "status" in result.output
        assert "config" in result.output
