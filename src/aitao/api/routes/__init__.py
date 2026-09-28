# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
API routes package.

This package contains the route handlers for all API endpoints:
- health: System health checks
- stats: Index statistics
- search: Document search
- ingest: File ingestion
"""

from aitao.api.routes.health import check_health
from aitao.api.routes.stats import get_index_stats
from aitao.api.routes.search import perform_search
from aitao.api.routes.ingest import queue_file, queue_batch

__all__ = [
    "check_health",
    "get_index_stats", 
    "perform_search",
    "queue_file",
    "queue_batch",
]
