# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Daemon lifecycle management for the background worker.

This module handles:
- PID file management (write, read, remove)
- Daemon start/stop via subprocess (Metal/MPS safe)
- System load monitoring (CPU threshold)
- Process status checking

Extracted from worker.py to keep module size manageable.
"""

import os
import sys
import time
import signal
import psutil
import subprocess
from pathlib import Path
from typing import Optional

from aitao.core.logger import get_logger


def _get_logger():
    """Get logger lazily to respect AITAO_QUIET env var."""
    return get_logger("worker")


def check_system_load(cpu_threshold: float) -> tuple[bool, str]:
    """
    Check if system load allows task processing.

    Args:
        cpu_threshold: Maximum CPU percentage before pausing

    Returns:
        Tuple of (can_process, reason)
    """
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        if cpu_percent >= cpu_threshold:
            return False, f"CPU usage too high: {cpu_percent:.1f}%"
        return True, ""
    except Exception as e:
        _get_logger().warning(f"Failed to check system load: {e}")
        return True, ""  # Continue if we can't check


class WorkerDaemon:
    """
    Manages daemon lifecycle for the background worker.

    Handles PID file operations, process spawning via subprocess.Popen,
    and graceful shutdown.  Uses subprocess instead of os.fork() for
    macOS Metal/MPS compatibility.
    """

    def __init__(self, pid_file: Path, project_root: Path):
        """
        Initialize daemon manager.

        Args:
            pid_file: Path to the worker PID file
            project_root: Project root directory (for locating run_worker.py)
        """
        self.pid_file = pid_file
        self.project_root = project_root

    # ------------------------------------------------------------------
    # PID file helpers
    # ------------------------------------------------------------------

    def write_pid_file(self) -> None:
        """Write current process PID to file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()))
        _get_logger().debug(f"PID file written: {self.pid_file}")

    def remove_pid_file(self) -> None:
        """Remove PID file on shutdown."""
        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
                _get_logger().debug(f"PID file removed: {self.pid_file}")
        except Exception as e:
            _get_logger().warning(f"Failed to remove PID file: {e}")

    # ------------------------------------------------------------------
    # Process status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if worker daemon is running."""
        if not self.pid_file.exists():
            return False
        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, 0)  # Signal 0 = existence check
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            return False

    def get_pid(self) -> Optional[int]:
        """Get worker PID if running."""
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Daemon start / stop
    # ------------------------------------------------------------------

    def start_daemon(self) -> bool:
        """
        Start the worker as a background daemon using subprocess.

        Uses subprocess.Popen instead of os.fork() for macOS Metal
        compatibility.  The fork() system call is not safe with
        Metal/MPS GPU libraries.

        Returns:
            True if started successfully, False otherwise
        """
        if self.is_running():
            _get_logger().warning("Worker already running")
            return False

        python_exe = sys.executable
        worker_script = self.project_root / "scripts" / "run_worker.py"
        worker_cmd = [python_exe, str(worker_script)]

        try:
            process = subprocess.Popen(
                worker_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # Detach from parent
                cwd=str(self.project_root),
            )

            # Give it a moment to start
            time.sleep(0.5)

            if process.poll() is None:
                _get_logger().info(f"Worker started with PID {process.pid}")
                return True
            else:
                _get_logger().error("Worker failed to start")
                return False

        except Exception as e:
            _get_logger().error(f"Failed to start worker: {e}")
            return False

    def stop_daemon(self, timeout: int = 10) -> bool:
        """
        Stop the running daemon.

        Sends SIGTERM for graceful shutdown, then SIGKILL if the process
        does not terminate within *timeout* seconds.

        Args:
            timeout: Seconds to wait for graceful shutdown

        Returns:
            True if stopped successfully, False otherwise
        """
        pid = self.get_pid()
        if pid is None:
            _get_logger().info("Worker not running")
            return True

        try:
            os.kill(pid, signal.SIGTERM)

            # Wait for process to terminate
            for _ in range(timeout):
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    _get_logger().info("Worker stopped")
                    self.remove_pid_file()
                    return True

            # Force kill if still running
            _get_logger().warning("Worker not responding, forcing kill")
            os.kill(pid, signal.SIGKILL)
            self.remove_pid_file()
            return True

        except ProcessLookupError:
            _get_logger().info("Worker already stopped")
            self.remove_pid_file()
            return True
        except PermissionError:
            _get_logger().error("Permission denied to stop worker")
            return False
