# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Queue admin and maintenance operations.

Provides a mixin class with admin/maintenance methods for the TaskQueue:
- Queue statistics (get_stats)
- Task listing with filters (list_tasks)
- Retry failed tasks (retry_failed)
- Cleanup operations (clear_completed, clear_all, reset_stuck_tasks)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aitao.core.logger import get_logger
from aitao.indexation.queue_models import Task, TaskStatus


def _get_logger():
    """Get logger lazily to respect AITAO_QUIET env var."""
    return get_logger("queue")


class QueueAdminMixin:
    """
    Mixin providing admin and maintenance methods for TaskQueue.

    Expects the host class to provide:
    _load_tasks(), _save_tasks(tasks), MAX_RETRIES, update_status().
    """

    def retry_failed(self) -> int:
        """
        Retry failed tasks that haven't exceeded max retries.

        Returns:
            Number of tasks reset to pending
        """
        tasks = self._load_tasks()
        retry_count = 0

        for task in tasks:
            if (task.status == TaskStatus.FAILED.value and
                task.retry_count < self.MAX_RETRIES):
                task.status = TaskStatus.PENDING.value
                task.error_message = None
                retry_count += 1

        if retry_count > 0:
            self._save_tasks(tasks)
            _get_logger().info(f"Reset {retry_count} failed tasks to pending")

        return retry_count

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        tasks = self._load_tasks()

        stats = {
            "total": len(tasks),
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
            "by_type": {},
            "by_priority": {"high": 0, "normal": 0, "low": 0}
        }

        for task in tasks:
            # Count by status
            if task.status == TaskStatus.PENDING.value:
                stats["pending"] += 1
            elif task.status == TaskStatus.PROCESSING.value:
                stats["processing"] += 1
            elif task.status == TaskStatus.COMPLETED.value:
                stats["completed"] += 1
            elif task.status == TaskStatus.FAILED.value:
                stats["failed"] += 1

            # Count by type
            stats["by_type"][task.task_type] = stats["by_type"].get(task.task_type, 0) + 1

            # Count by priority (only pending)
            if task.status == TaskStatus.PENDING.value:
                stats["by_priority"][task.priority] = stats["by_priority"].get(task.priority, 0) + 1

        return stats

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Task]:
        """
        List tasks with optional filtering.

        Args:
            status: Filter by status (or None for all)
            limit: Maximum number of tasks to return

        Returns:
            List of Task objects
        """
        tasks = self._load_tasks()

        if status:
            tasks = [t for t in tasks if t.status == status]

        # Sort by added_at descending (newest first)
        tasks.sort(key=lambda t: t.added_at, reverse=True)

        return tasks[:limit]

    def clear_completed(self) -> int:
        """
        Remove completed tasks from the queue.

        Returns:
            Number of tasks removed
        """
        tasks = self._load_tasks()
        original_count = len(tasks)

        tasks = [t for t in tasks if t.status != TaskStatus.COMPLETED.value]

        removed = original_count - len(tasks)
        if removed > 0:
            self._save_tasks(tasks)
            _get_logger().info(f"Cleared {removed} completed tasks")

        return removed

    def reset_stuck_tasks(self, timeout_seconds: int = 600) -> int:
        """
        Reset tasks stuck in 'processing' status back to 'pending'.

        Tasks are considered stuck if they have been in 'processing' status
        for longer than timeout_seconds. This handles cases where the worker
        crashed without properly completing or failing the task.

        Args:
            timeout_seconds: Time in seconds after which a processing task
                           is considered stuck (default: 600 = 10 minutes)

        Returns:
            Number of tasks reset
        """
        tasks = self._load_tasks()
        now = datetime.now(timezone.utc)
        reset_count = 0

        for task in tasks:
            if task.status != TaskStatus.PROCESSING.value:
                continue

            if not task.started_at:
                # No start time, definitely stuck
                task.status = TaskStatus.PENDING.value
                task.started_at = None
                reset_count += 1
                continue

            # Parse started_at and check age
            try:
                # Handle various ISO formats
                started_str = task.started_at.replace('Z', '+00:00')
                if '+' not in started_str and '-' not in started_str[-6:]:
                    # No timezone, assume UTC
                    started = datetime.fromisoformat(started_str).replace(tzinfo=timezone.utc)
                else:
                    started = datetime.fromisoformat(started_str)

                age_seconds = (now - started).total_seconds()

                if age_seconds > timeout_seconds:
                    _get_logger().warning(
                        "Resetting stuck task",
                        metadata={
                            "task_id": task.id,
                            "file": task.file_path,
                            "stuck_for_seconds": int(age_seconds)
                        }
                    )
                    task.status = TaskStatus.PENDING.value
                    task.started_at = None
                    reset_count += 1

            except (ValueError, TypeError) as e:
                # Can't parse date, reset to be safe
                _get_logger().warning(f"Cannot parse started_at for task {task.id}: {e}")
                task.status = TaskStatus.PENDING.value
                task.started_at = None
                reset_count += 1

        if reset_count > 0:
            self._save_tasks(tasks)
            _get_logger().info(f"Reset {reset_count} stuck tasks to pending")

        return reset_count

    def clear_all(self) -> int:
        """
        Remove all tasks from the queue.

        Returns:
            Number of tasks removed
        """
        tasks = self._load_tasks()
        count = len(tasks)

        self._save_tasks([])
        _get_logger().info(f"Cleared all {count} tasks from queue")

        return count

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task."""
        return self.update_status(task_id, TaskStatus.CANCELLED.value)
