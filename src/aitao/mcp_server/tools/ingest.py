# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/ingest.py — MCP tool: aitao_ingest (US-055)
#
# Responsibilities:
#   - Expose aitao_ingest tool to MCP clients
#   - Accept a local file path or directory and queue it for indexing
#   - Validate path existence before queuing
#   - Return task ID and status for async tracking

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from aitao.core.logger import get_logger
from aitao.mcp_server.state import get_state

logger = get_logger("mcp.tools.ingest")

# Allowed file extensions for indexing
_ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".xlsx", ".xls", ".csv", ".pptx", ".ppt",
    ".html", ".htm", ".odt", ".rtf",
}


def register_ingest(mcp: FastMCP) -> None:
    """Register aitao_ingest tool on the given FastMCP instance."""

    @mcp.tool()
    async def aitao_ingest(
        path: str,
        priority: str = "normal",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """Index a file or directory into AiTao's knowledge base.

        Args:
            path:      Absolute path to a file or directory to index.
            priority:  Task priority — "high", "normal" (default), or "low".
            recursive: If path is a directory, index sub-directories too.

        Returns:
            Dictionary with 'task_ids' list and 'queued' count.
            Each task has: task_id, file_path, status.
        """
        resolved = Path(path).expanduser().resolve()

        if not resolved.exists():
            return {
                "success": False,
                "error": f"Path not found: {path}",
                "task_ids": [],
                "queued": 0,
            }

        valid_priorities = {"high", "normal", "low"}
        if priority not in valid_priorities:
            priority = "normal"

        state = get_state()
        queue = state.task_queue

        if queue is None:
            return {
                "success": False,
                "error": "Task queue is not available. Make sure AiTao services are running (./aitao.sh start).",
                "task_ids": [],
                "queued": 0,
            }

        files: list[Path] = []
        if resolved.is_file():
            files = [resolved]
        elif resolved.is_dir():
            pattern = "**/*" if recursive else "*"
            files = [
                f for f in resolved.glob(pattern)
                if f.is_file() and f.suffix.lower() in _ALLOWED_EXTENSIONS
            ]

        if not files:
            return {
                "success": False,
                "error": f"No indexable files found at: {path}",
                "task_ids": [],
                "queued": 0,
            }

        task_ids: list[dict[str, str]] = []
        for file_path in files:
            try:
                task_id = await _queue_file(queue, file_path, priority)
                task_ids.append({"task_id": task_id, "file_path": str(file_path), "status": "queued"})
            except Exception as e:
                logger.warning("Failed to queue file", metadata={"path": str(file_path), "error": str(e)})
                task_ids.append({"task_id": "", "file_path": str(file_path), "status": f"error: {e}"})

        queued = sum(1 for t in task_ids if t["status"] == "queued")
        logger.info("aitao_ingest called", metadata={"path": path, "queued": queued})

        return {"success": queued > 0, "task_ids": task_ids, "queued": queued}


async def _queue_file(queue: Any, file_path: Path, priority: str) -> str:
    """Queue a single file — handles sync/async queue APIs."""
    try:
        result = await queue.add_task(
            file_path=str(file_path),
            task_type="index",
            priority=priority,
        )
    except TypeError:
        # Fallback: sync API
        result = queue.add_task(
            file_path=str(file_path),
            task_type="index",
            priority=priority,
        )
    if isinstance(result, dict):
        return str(result.get("task_id", result.get("id", "")))
    return str(result)
