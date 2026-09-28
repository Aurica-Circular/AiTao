# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Stats endpoint handler.

This module provides index statistics:
- Meilisearch document count and size (documents + excerpts/chunks)
- Queue statistics
- Storage usage
- ``lancedb``: always null since v4.0 (ÉPIC-31, US-113, fusion-only) — kept
  Optional in the response contract for backward compatibility.
"""

from datetime import datetime, timezone
from typing import Optional

from aitao.api.schemas import StatsResponse, IndexStats
from aitao.core.logger import get_logger

logger = get_logger("api.stats")


async def get_lancedb_stats() -> Optional[IndexStats]:
    """LanceDB index statistics — always None (ÉPIC-31, US-113).

    LanceDB is no longer the retrieval backend since v4.0 (fusion-only,
    decision D1); the live write path was removed entirely. The field stays
    in the public contract (Optional[IndexStats]) — moindre casse — but this
    now always returns None, with no attempt to connect to anything.
    """
    return None


async def get_meilisearch_chunks_stats() -> Optional[IndexStats]:
    """Get the excerpt/chunk index statistics (ÉPIC-31, US-113).

    The dedicated Meilisearch chunk index is the chat/RAG retrieval backend
    (fusion is the only engine since v4.0, replacing LanceDB's `chunks`
    table). Reads through a RAW meilisearch.Client, NOT MeiliChunkStore,
    whose constructor loads the bge-m3 embedding model — far too heavy for a
    stats read.
    """
    try:
        import meilisearch
        from aitao.core.config import get_config

        ms = get_config().search.meilisearch
        client = meilisearch.Client(ms.url, ms.api_key or None)
        stats = client.index(ms.chunks_index).get_stats()
        count = getattr(stats, "number_of_documents", None)
        if count is None and isinstance(stats, dict):
            count = stats.get("numberOfDocuments", 0)

        return IndexStats(
            name="meilisearch_chunks",
            document_count=int(count or 0),
            size_bytes=None,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning(f"Failed to get Meilisearch chunks stats: {e}")
        return None


async def get_meilisearch_stats() -> Optional[IndexStats]:
    """Get Meilisearch index statistics."""
    try:
        from aitao.storage.repository import make_meilisearch_client
        client = make_meilisearch_client()
        
        if not client.is_connected():
            return None
        
        stats = client.get_stats()
        
        return IndexStats(
            name="meilisearch",
            document_count=stats.get("numberOfDocuments", 0),
            size_bytes=stats.get("indexSize"),
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning(f"Failed to get Meilisearch stats: {e}")
        return None


async def get_queue_stats() -> Optional[dict]:
    """Get task queue statistics."""
    try:
        from aitao.indexation.queue import TaskQueue
        queue = TaskQueue()
        stats = queue.get_stats()
        
        return {
            "pending": stats.get("pending", 0),
            "processing": stats.get("processing", 0),
            "completed": stats.get("completed", 0),
            "failed": stats.get("failed", 0),
            "total": stats.get("total", 0),
        }
    except Exception as e:
        logger.warning(f"Failed to get queue stats: {e}")
        return None


async def get_storage_stats() -> Optional[dict]:
    """Get storage usage statistics."""
    try:
        from aitao.core.pathmanager import path_manager
        import shutil
        
        storage_root = path_manager.get_storage_root()
        
        # Get disk usage
        total, used, free = shutil.disk_usage(storage_root)
        
        return {
            "storage_root": str(storage_root),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": round((used / total) * 100, 2),
        }
    except Exception as e:
        logger.warning(f"Failed to get storage stats: {e}")
        return None


async def get_index_stats() -> StatsResponse:
    """
    Get comprehensive index statistics.
    
    Returns stats for LanceDB, Meilisearch, queue, and storage.
    """
    lancedb_stats = await get_lancedb_stats()
    meilisearch_stats = await get_meilisearch_stats()
    meilisearch_chunks_stats = await get_meilisearch_chunks_stats()
    queue_stats = await get_queue_stats()
    storage_stats = await get_storage_stats()

    # Calculate total documents
    total = 0
    if lancedb_stats:
        total = max(total, lancedb_stats.document_count)
    if meilisearch_stats:
        total = max(total, meilisearch_stats.document_count)

    return StatsResponse(
        total_documents=total,
        lancedb=lancedb_stats,
        meilisearch=meilisearch_stats,
        meilisearch_chunks=meilisearch_chunks_stats,
        queue=queue_stats,
        storage=storage_stats,
    )
