# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Core infrastructure modules for AiTao V2.

This package contains foundational components:
- pathmanager: Centralized path management
- logger: Structured JSON logging
- config: YAML configuration loader (TODO)
- system_monitor: System resource monitoring (TODO)
"""

from .pathmanager import AitaoPathManager, path_manager
from .logger import get_logger

__all__ = ['AitaoPathManager', 'path_manager', 'get_logger']
