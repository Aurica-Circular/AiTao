# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao CLI - Command Line Interface.

This module provides a modular CLI for AiTao operations:
- Typer-based command routing
- Rich console output with colors and progress bars
- Modular command structure in commands/ subdirectory

Usage:
    python -m aitao.cli <command> [options]
    
Or via the shell wrapper:
    ./aitao.sh <command> [options]
"""

__all__ = ["app"]
