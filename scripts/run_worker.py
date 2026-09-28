#!/usr/bin/env python3
# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Worker daemon launcher script.

This script is called by BackgroundWorker.start_daemon() to start the worker
in a separate subprocess. This avoids os.fork() which is incompatible with
Metal/MPS GPU libraries on macOS.

Usage:
    python scripts/run_worker.py
"""

import sys
from pathlib import Path

# Add project src to path so imports work even outside editable install
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from aitao.indexation.worker import BackgroundWorker  # noqa: E402


def main():
    """Start the background worker."""
    # Worker will find config automatically via project root detection
    # run() calls write_pid_file() internally via WorkerDaemon
    worker = BackgroundWorker()
    worker.run()


if __name__ == "__main__":
    main()
