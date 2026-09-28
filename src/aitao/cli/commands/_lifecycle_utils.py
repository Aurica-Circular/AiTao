# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Lifecycle utility helpers: PID file management, port/host config, health checks.

Low-level helpers shared by the lifecycle services and CLI commands.
Contains no Typer dependency — purely infrastructure utilities.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional

from rich.live import Live
from rich.spinner import Spinner

from aitao.cli.utils import console, status_line
from aitao.core.config import get_config


# ── PID file locations ──────────────────────────────────────────────
# PID files MUST live in a stable, config-derived directory — never $TMPDIR.
# macOS gives each shell context a different per-user $TMPDIR, so a PID written
# by `start` in one context could not be found by `stop` in another, leaving
# orphaned servers behind. We mirror the worker daemon, which already writes
# `<storage_root>/worker.pid`.

def _resolve_pid_dir() -> Path:
    """Return the stable directory holding AiTao PID files.

    Uses the storage root from the single path authority (US-098 A4, same base the
    worker daemon uses); falls back to ``~/.aitao`` so it is never tied to $TMPDIR.
    """
    try:
        from aitao.core.pathmanager import path_manager
        pid_dir = path_manager.get_storage_root()
        pid_dir.mkdir(parents=True, exist_ok=True)
        return pid_dir
    except Exception:
        pass
    fallback = Path.home() / ".aitao"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


_PID_DIR = _resolve_pid_dir()
API_PID_FILE = _PID_DIR / "api.pid"
# Align with the worker daemon's own PID file (worker.py → storage_root/worker.pid)
WORKER_PID_FILE = _PID_DIR / "worker.pid"


# ── Configuration helpers ───────────────────────────────────────────

def _get_api_port() -> int:
    """Get API port from config (default 8200).

    Note: does NOT change the process working directory — get_config() is a
    singleton and resolves config independently of cwd. The previous os.chdir()
    here was a hidden side effect that could break relative path resolution
    elsewhere in the CLI.
    """
    try:
        port = get_config().get("api.port", 8200)
        return port if isinstance(port, int) else int(port)
    except Exception:
        return 8200


def _get_api_host() -> str:
    """Get API host from config (supports dual-stack '::' for IPv4+IPv6)."""
    try:
        return str(get_config().get("api.host", "0.0.0.0"))
    except Exception:
        return "0.0.0.0"


def get_api_port() -> int:
    """Public helper to retrieve the API port."""
    return _get_api_port()


# ── Health checks ───────────────────────────────────────────────────

def _is_meilisearch_responding(timeout: int = 3) -> bool:
    """Check if Meilisearch is already responding on its configured URL."""
    import urllib.request
    try:
        config = get_config()
        ms_url = config.search.meilisearch.url
        req = urllib.request.urlopen(f"{ms_url}/health", timeout=timeout)
        return req.status == 200
    except Exception:
        return False


def _check_meilisearch_running() -> bool:
    """Check if Meilisearch is running via brew services."""
    try:
        result = subprocess.run(
            ["brew", "services", "info", "meilisearch", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "started" in result.stdout.lower()
    except Exception:
        return False


# ── Process / port authority ────────────────────────────────────────
# The TCP port is the source of truth for "is the API up", more reliable than
# a PID file that can go stale. These helpers let stop/start/status reason
# about the real listening process instead of a possibly-orphaned PID.

def _pid_alive(pid: Optional[int]) -> bool:
    """Return True if a process with this PID currently exists."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not signalable by us
    except (ValueError, TypeError):
        return False


def _read_pid_file(path: Path) -> Optional[int]:
    """Read a PID from a file; return None if absent or malformed."""
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _pids_on_port(port: int) -> List[int]:
    """Return PIDs of processes LISTENING on the given local TCP port.

    Tries psutil first (cross-platform), then falls back to lsof. Returns an
    empty list if nothing is listening or the lookup is not permitted.
    """
    # psutil is authoritative when it succeeds — including a legitimate empty
    # result. Only fall back to lsof if psutil is unavailable or not permitted
    # (e.g. raises AccessDenied), so a real "nothing listening" is trusted.
    try:
        import psutil

        pids: set = set()
        for conn in psutil.net_connections(kind="inet"):
            laddr = conn.laddr
            if (
                laddr
                and laddr.port == port
                and conn.status == psutil.CONN_LISTEN
                and conn.pid
            ):
                pids.add(conn.pid)
        return list(pids)
    except Exception:
        pass

    # Fallback: lsof (macOS / Linux)
    pids = set()
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for token in result.stdout.split():
            try:
                pids.add(int(token))
            except ValueError:
                pass
    except Exception:
        pass
    return list(pids)


def _is_api_responding(port: int, timeout: float = 1.5) -> bool:
    """Return True if the API answers HTTP 200 on /api/health."""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _running_api_version(port: int, timeout: float = 1.5):
    """Version reported by the RUNNING API server, or None if unreachable.

    Read from /api/health, i.e. the code the server actually loaded at startup —
    which can differ from the version on disk after a git pull. Showing both on
    restart proves the upgrade really happened.
    """
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/health", timeout=timeout
        ) as resp:
            return json.loads(resp.read().decode()).get("version")
    except Exception:
        return None


# ── Generic command runner ──────────────────────────────────────────

def _run_command(name: str, command: list, timeout: int = 30) -> bool:
    """
    Run a command and return success status.

    Args:
        name: Display name for the service
        command: Command and args to run
        timeout: Timeout in seconds

    Returns:
        True if successful, False otherwise
    """
    try:
        with Live(
            Spinner("dots", text=f"[cyan]{name}[/cyan]..."),
            console=console,
            transient=True,
        ):
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        if result.returncode == 0:
            status_line(name, "OK", ok=True)
            return True
        else:
            status_line(name, f"Failed: {result.stderr[:50]}", ok=False)
            return False
    except subprocess.TimeoutExpired:
        status_line(name, f"Timeout ({timeout}s)", ok=False)
        return False
    except Exception as e:
        status_line(name, f"Error: {str(e)[:50]}", ok=False)
        return False
