# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Lifecycle service management: start/stop helpers for API, Worker, and scanner.

Handles the actual process management for each AiTao service.
Used by the CLI commands in lifecycle.py.
"""

import os
import signal
import subprocess
import sys
import time
from typing import Optional, Tuple

from aitao.cli.commands._lifecycle_utils import (
    API_PID_FILE,
    _get_api_host,
    _get_api_port,
    _is_api_responding,
    _pid_alive,
    _pids_on_port,
    _read_pid_file,
)
from aitao.core.pathmanager import path_manager


# ── API Server ──────────────────────────────────────────────────────

def _terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    """SIGTERM a PID, escalate to SIGKILL if needed, wait until it's gone.

    Returns True once the process no longer exists (or never did).
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.25)

    # Graceful window elapsed — force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    time.sleep(0.3)
    return not _pid_alive(pid)


def _start_api_server(skip_pull: bool = False) -> Tuple[bool, Optional[int]]:
    """
    Start the FastAPI server as a background process.

    Success is confirmed by polling /api/health — not by a fixed sleep — so a
    uvicorn that dies on a port-bind failure is reported as a failure rather
    than a false positive. Any stale process squatting on the port is reaped
    first so the new server can bind.

    Args:
        skip_pull: If True, skip automatic model download on startup

    Returns:
        (success, pid) tuple where pid is the real listening process.
    """
    port = _get_api_port()
    host = _get_api_host()

    # Already up and healthy? Record its real PID and report success.
    if _is_api_responding(port):
        listening = _pids_on_port(port)
        pid = listening[0] if listening else _read_pid_file(API_PID_FILE)
        if pid:
            API_PID_FILE.write_text(str(pid))
        return True, pid

    # Port held by a non-responding process → stale orphan on our port. Reap it
    # so the fresh uvicorn can bind (prevents the silent start/shutdown loop).
    for stale in _pids_on_port(port):
        _terminate_pid(stale)

    try:
        python_path = sys.executable
        api_module = "aitao.api.main:app"  # relative to src/, not project root

        # Build environment variables for the subprocess
        env = os.environ.copy()
        if skip_pull:
            env["AITAO_SKIP_MODEL_PULL"] = "1"

        # Open log file for API process console output (append mode).
        # Use the configured logs dir (storage_root/logs), never the project tree
        # (US-098 A5). Distinct name so it does not clobber the structured api.log.
        log_dir = path_manager.get_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        api_log_path = log_dir / "api.console.log"
        api_log_file = open(api_log_path, "a")  # noqa: WPS515

        # Start process detached — cwd=src/ so that 'api.main:app' resolves
        process = subprocess.Popen(
            [
                python_path, "-m", "uvicorn",
                api_module,
                "--host", host,
                "--port", str(port),
                "--log-level", "info",
            ],
            stdout=api_log_file,
            stderr=api_log_file,
            cwd=str(path_manager.get_src_dir()),
            start_new_session=True,
            env=env,
        )
        api_log_file.close()  # Parent process closes its handle; child keeps it

        # Poll for genuine readiness. A bare sleep gives false positives when
        # uvicorn binds late then exits; only an HTTP 200 on /api/health counts.
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if process.poll() is not None:
                return False, None  # died early (e.g. address already in use)
            if _is_api_responding(port):
                listening = _pids_on_port(port)
                pid = listening[0] if listening else process.pid
                API_PID_FILE.write_text(str(pid))
                return True, pid
            time.sleep(0.5)

        return False, None  # never became healthy within the window

    except Exception:
        return False, None


def _stop_api_server() -> bool:
    """Stop the API server reliably.

    Two-pronged so a stale/missing PID file can never leave an orphan behind:
      1. Terminate the PID we recorded (if still alive).
      2. Reap ANY process still LISTENING on the API port (the real fix for
         orphaned servers — the port is the source of truth).
    Finally, confirm the port is actually free.
    """
    ok = True

    # 1. Kill the recorded PID, if any
    pid = _read_pid_file(API_PID_FILE)
    if _pid_alive(pid):
        ok = _terminate_pid(pid) and ok  # type: ignore[arg-type]

    # 2. Reap anything still holding the API port
    port = _get_api_port()
    for orphan in _pids_on_port(port):
        ok = _terminate_pid(orphan) and ok

    API_PID_FILE.unlink(missing_ok=True)

    # 3. Verify the port is free and nothing still answers
    if _pids_on_port(port) or _is_api_responding(port):
        return False
    return ok


def start_api(skip_pull: bool = False) -> Tuple[bool, Optional[int]]:
    """Public helper to start the API server."""
    return _start_api_server(skip_pull=skip_pull)


def stop_api() -> bool:
    """Public helper to stop the API server."""
    return _stop_api_server()


# ── Worker Daemon ───────────────────────────────────────────────────

def _start_worker() -> Tuple[bool, Optional[int]]:
    """
    Start the background worker daemon.

    Returns:
        (success, pid) tuple
    """
    try:
        from aitao.indexation.worker import BackgroundWorker

        worker = BackgroundWorker()

        if worker.is_running():
            pid = worker.get_pid()
            return True, pid  # Already running

        # Start daemon. The daemon writes its PID file asynchronously, so poll
        # briefly to report the real PID instead of None.
        if worker.start_daemon():
            pid = None
            for _ in range(20):  # up to ~5s
                pid = worker.get_pid()
                if pid:
                    break
                time.sleep(0.25)
            return True, pid
        else:
            return False, None

    except Exception:
        return False, None


def _stop_worker() -> bool:
    """Stop the background worker."""
    try:
        from aitao.indexation.worker import BackgroundWorker

        worker = BackgroundWorker()
        return worker.stop_daemon()

    except Exception:
        return False


# ── Initial Filesystem Scan ─────────────────────────────────────────

def _run_initial_scan() -> Tuple[int, int]:
    """
    Run initial filesystem scan and populate queue.

    Returns:
        (new_files_count, modified_files_count) tuple
    """
    try:
        from aitao.indexation.scanner import FilesystemScanner
        from aitao.indexation.queue import TaskQueue

        scanner = FilesystemScanner()
        queue = TaskQueue()

        # Run scan
        result = scanner.scan(save_state=True)

        # Add files to queue
        added = 0
        for file_info in result.new_files + result.modified_files:
            try:
                queue.add_task(
                    file_path=file_info.path,
                    task_type="index",
                    priority="normal",
                )
                added += 1
            except Exception:
                pass  # Skip duplicates

        return len(result.new_files), len(result.modified_files)

    except Exception:
        return 0, 0
