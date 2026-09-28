# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for lifecycle commands (start/stop/restart services).

Test coverage for the user-facing CLI surface:
- Start command
- Stop command
- Restart command
- Lifecycle group commands

Process-management internals (port-authoritative stop/start, PID handling)
are covered in tests/unit/test_lifecycle_process.py.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

# Add src directory to path
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from aitao.cli.main import app  # noqa: E402

runner = CliRunner()


# ============================================================================
# Shared helpers: patch all side-effects of the start/stop commands
# ============================================================================

def _start_patches():
    """Return a dict of patch objects needed to isolate the 'start' command.

    Model verification uses make_llm_client (provider-agnostic; ModelManager and
    the direct OllamaClient were removed in v3).
    """
    return {
        "llm": patch("aitao.cli.commands.lifecycle.make_llm_client"),
        "is_meili": patch("aitao.cli.commands.lifecycle._is_meilisearch_responding", return_value=False),
        "run_cmd": patch("aitao.cli.commands.lifecycle._run_command", return_value=True),
        "start_api": patch("aitao.cli.commands.lifecycle._start_api_server", return_value=(True, 1234)),
        "start_worker": patch("aitao.cli.commands.lifecycle._start_worker", return_value=(True, 5678)),
        "initial_scan": patch("aitao.cli.commands.lifecycle._run_initial_scan", return_value=(0, 0)),
    }


def _setup_llm_client(mock_make_client):
    """Configure the mocked make_llm_client so the model-verification step runs."""
    client = MagicMock()
    model = MagicMock()
    model.name = "qwen3.5:latest"
    client.list_models.return_value = [model]
    mock_make_client.return_value = client
    return client


def _stop_patches():
    """Return a dict of patch objects needed to isolate the 'stop' command."""
    return {
        "stop_worker": patch("aitao.cli.commands.lifecycle._stop_worker", return_value=True),
        "stop_api": patch("aitao.cli.commands.lifecycle._stop_api_server", return_value=True),
        "run_cmd": patch("aitao.cli.commands.lifecycle._run_command", return_value=True),
    }


class TestRootLifecycleCommands:
    """Test start/stop/restart at root level (user-friendly interface)."""

    def test_start_command(self):
        """Test root-level 'start' command starts all services."""
        patches = _start_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            _setup_llm_client(mocks["llm"])

            result = runner.invoke(app, ["start"])

            assert result.exit_code == 0
            assert "Starting all AiTao services" in result.stdout
            # Meilisearch start via _run_command
            mocks["run_cmd"].assert_called_once()
        finally:
            for p in patches.values():
                p.stop()

    def test_start_failure_handling(self):
        """Test start command handles service failures (Meilisearch fails)."""
        patches = _start_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            _setup_llm_client(mocks["llm"])
            # Meilisearch start fails
            mocks["run_cmd"].return_value = False
            mocks["is_meili"].return_value = False

            result = runner.invoke(app, ["start"])

            assert result.exit_code == 1
        finally:
            for p in patches.values():
                p.stop()

    def test_start_meilisearch(self):
        """Test that Meilisearch is started via _run_command."""
        patches = _start_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            _setup_llm_client(mocks["llm"])

            runner.invoke(app, ["start"])

            calls = mocks["run_cmd"].call_args_list
            assert len(calls) >= 1
            assert "Meilisearch" in str(calls[0])
        finally:
            for p in patches.values():
                p.stop()

    def test_stop_command(self):
        """Test root-level 'stop' command stops all services."""
        patches = _stop_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            result = runner.invoke(app, ["stop"])

            assert result.exit_code == 0
            assert "Stopping all AiTao services" in result.stdout
            mocks["run_cmd"].assert_called_once()
        finally:
            for p in patches.values():
                p.stop()

    def test_restart_command(self):
        """Test root-level 'restart' command restarts all services."""
        stop_p = _stop_patches()
        start_p = _start_patches()
        all_patches = {**stop_p, **start_p}
        mocks = {k: p.start() for k, p in all_patches.items()}
        try:
            _setup_llm_client(mocks["llm"])

            result = runner.invoke(app, ["restart"])

            assert result.exit_code == 0
            assert "Restarting all AiTao services" in result.stdout
        finally:
            for p in all_patches.values():
                p.stop()


class TestCommandHelp:
    """Test help messages for lifecycle commands."""

    def test_start_help(self):
        """Test start command help."""
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "Start core AiTao services" in result.stdout
        assert "Meilisearch" in result.stdout

    def test_stop_help(self):
        """Test stop command help."""
        result = runner.invoke(app, ["stop", "--help"])
        assert result.exit_code == 0
        assert "Stop core AiTao services" in result.stdout

    def test_restart_help(self):
        """Test restart command help."""
        result = runner.invoke(app, ["restart", "--help"])
        assert result.exit_code == 0
        assert "Restart core AiTao services" in result.stdout

    def test_lifecycle_group_help(self):
        """Test lifecycle group help."""
        result = runner.invoke(app, ["lifecycle", "--help"])
        assert result.exit_code == 0
        assert "start" in result.stdout
        assert "stop" in result.stdout
        assert "restart" in result.stdout
