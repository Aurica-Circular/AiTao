# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Health check endpoint handler.

This module provides two health check endpoints:
1. /api/health - Fast minimal check (API is responding)
2. /api/health/debug - Detailed diagnostics (all services + stats)

Design principle: Minimal endpoint for monitoring, detailed endpoint for debugging.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from aitao.api.schemas import HealthResponse, ServiceStatus
from aitao.core.logger import get_logger

logger = get_logger("api.health")


async def check_service_lancedb() -> ServiceStatus:
    """Legacy status (ÉPIC-31, US-113): LanceDB is no longer a live service —
    v4.0 ships fusion-only (decision D1). Reports healthy with no I/O at all,
    so it can never skew the aggregate health status; the real semantic/RAG
    backend is now the Meilisearch excerpt index — see
    ``check_service_meilisearch_chunks``. Kept in the response only so an
    existing consumer polling the "lancedb" key does not get a KeyError.
    """
    return ServiceStatus(
        name="lancedb",
        status="healthy",
        message="Not used since v4.0 (fusion engine) — legacy store, see docs/MIGRATION-4.0.md",
        latency_ms=0.0,
    )


async def check_service_meilisearch_chunks() -> ServiceStatus:
    """Check the Meilisearch excerpt/chunk index — the real semantic/RAG
    retrieval backend since v4.0 (fusion-only, ÉPIC-31, US-113). Reads
    through a RAW meilisearch.Client, NOT MeiliChunkStore, whose constructor
    loads the bge-m3 embedding model — far too heavy for a health check.
    """
    start = time.time()
    try:
        import meilisearch
        from aitao.core.config import get_config

        ms = get_config().search.meilisearch
        client = meilisearch.Client(ms.url, ms.api_key or None)
        stats = client.index(ms.chunks_index).get_stats()
        count = getattr(stats, "number_of_documents", None)
        if count is None and isinstance(stats, dict):
            count = stats.get("numberOfDocuments", 0)
        latency = (time.time() - start) * 1000

        return ServiceStatus(
            name="meilisearch_chunks",
            status="healthy",
            message=f"{int(count or 0)} excerpts indexed",
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        logger.warning(f"Meilisearch chunks health check failed: {e}")
        return ServiceStatus(
            name="meilisearch_chunks",
            status="down",
            message=str(e),
            latency_ms=round(latency, 2),
        )


async def check_service_meilisearch() -> ServiceStatus:
    """Check Meilisearch connectivity."""
    start = time.time()
    try:
        from aitao.storage.repository import make_meilisearch_client
        client = make_meilisearch_client()
        
        # Check if connected
        if not client.is_connected():
            latency = (time.time() - start) * 1000
            return ServiceStatus(
                name="meilisearch",
                status="down",
                message="Not connected to Meilisearch server",
                latency_ms=round(latency, 2),
            )
        
        stats = client.get_stats()
        latency = (time.time() - start) * 1000
        
        return ServiceStatus(
            name="meilisearch",
            status="healthy",
            message=f"{stats.get('numberOfDocuments', 0)} documents indexed",
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        logger.warning(f"Meilisearch health check failed: {e}")
        return ServiceStatus(
            name="meilisearch",
            status="down",
            message=str(e),
            latency_ms=round(latency, 2),
        )


async def check_service_worker() -> ServiceStatus:
    """Check background worker status."""
    try:
        from aitao.indexation.worker import BackgroundWorker
        worker = BackgroundWorker()
        
        if worker.is_running():
            return ServiceStatus(
                name="worker",
                status="healthy",
                message="Worker is running",
            )
        else:
            return ServiceStatus(
                name="worker",
                status="degraded",
                message="Worker is not running",
            )
    except Exception as e:
        logger.warning(f"Worker health check failed: {e}")
        return ServiceStatus(
            name="worker",
            status="degraded",
            message=str(e),
        )


async def check_health(start_time: Optional[float], version: str) -> HealthResponse:
    """
    Minimal fast health check - API is responding?
    
    This is the SIMPLE endpoint used for monitoring (e.g., load balancers, CLI status).
    Returns instantly without checking dependent services.
    
    Args:
        start_time: API start timestamp for uptime calculation
        version: API version string
    
    Returns:
        HealthResponse with minimal info (API status + uptime)
    """
    # API is healthy if we reach this point (FastAPI app is responding)
    api_status = ServiceStatus(
        name="api",
        status="healthy",
        message=f"Version {version}",
    )
    
    # Calculate uptime
    uptime = None
    if start_time:
        uptime = time.time() - start_time
    
    # Minimal response - just the API itself
    return HealthResponse(
        status="healthy",
        version=version,
        timestamp=datetime.now(timezone.utc),
        services={"api": api_status},
        uptime_seconds=round(uptime, 2) if uptime else None,
    )


async def check_health_debug(start_time: Optional[float], version: str) -> HealthResponse:
    """
    Detailed health check - all services + diagnostic info.

    This is the DEBUG endpoint used for diagnostics and troubleshooting.
    Checks Meilisearch (documents + excerpts) and the Worker, and includes
    detailed metrics. ``lancedb`` stays in the response (always healthy,
    zero I/O) for backward compatibility — see ``check_service_lancedb``;
    v4.0 ships fusion-only (ÉPIC-31, US-113), so the real semantic/RAG
    backend is the Meilisearch excerpt index (``meilisearch_chunks``).

    WARNING: This endpoint is SLOWER than /api/health because it performs
    full stats reads across services. Use /api/health for monitoring.

    Args:
        start_time: API start timestamp for uptime calculation
        version: API version string

    Returns:
        HealthResponse with detailed service diagnostics
    """
    # Profile each service check
    check_start = time.time()

    lancedb_status = await check_service_lancedb()

    meilisearch_start = time.time()
    meilisearch_status = await check_service_meilisearch()
    meilisearch_time = (time.time() - meilisearch_start) * 1000

    chunks_start = time.time()
    chunks_status = await check_service_meilisearch_chunks()
    chunks_time = (time.time() - chunks_start) * 1000

    worker_start = time.time()
    worker_status = await check_service_worker()
    worker_time = (time.time() - worker_start) * 1000

    total_check_time = (time.time() - check_start) * 1000

    # Log timings for profiling (visible only in DEBUG mode)
    logger.info(
        "Debug health check breakdown",
        metadata={
            "meilisearch_ms": round(meilisearch_time, 2),
            "meilisearch_chunks_ms": round(chunks_time, 2),
            "worker_ms": round(worker_time, 2),
            "total_ms": round(total_check_time, 2),
        }
    )

    # API is always healthy if we reach this point
    api_status = ServiceStatus(
        name="api",
        status="healthy",
        message=f"Version {version}",
    )

    # Build services dict
    services = {
        "api": api_status,
        "lancedb": lancedb_status,
        "meilisearch": meilisearch_status,
        "meilisearch_chunks": chunks_status,
        "worker": worker_status,
    }

    # Determine overall status
    # Critical services (search capability): the two Meilisearch indexes —
    # documents and excerpts/chunks. ``lancedb_status`` is always healthy
    # (legacy, no I/O — see check_service_lancedb) so it never counts here.
    critical_services = [meilisearch_status, chunks_status]
    all_critical_down = all(s.status == "down" for s in critical_services)
    
    statuses = [s.status for s in services.values()]
    if all(s == "healthy" for s in statuses):
        overall_status = "healthy"
    elif all_critical_down:
        # System is down if both search backends are unavailable
        overall_status = "down"
    elif any(s == "down" for s in statuses):
        overall_status = "degraded"
    else:
        overall_status = "degraded"
    
    # Calculate uptime
    uptime = None
    if start_time:
        uptime = time.time() - start_time
    
    return HealthResponse(
        status=overall_status,
        version=version,
        timestamp=datetime.now(timezone.utc),
        services=services,
        uptime_seconds=round(uptime, 2) if uptime else None,
    )
