# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Entry point for running the CLI as a module.

Usage:
    python -m cli
    python -m cli status
    python -m cli ms status
    etc.
"""

import sys

from aitao.cli.main import app, normalize_help_argv


if __name__ == "__main__":
    # Treat a trailing bare "help"/"-h" as "--help" everywhere (US-29)
    sys.argv = sys.argv[:1] + normalize_help_argv(sys.argv[1:])
    app()
