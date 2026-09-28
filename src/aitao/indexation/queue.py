# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Task Queue for asynchronous document processing.

Core TaskQueue class with add/get/update/complete operations.
Models and enums are in queue_models.py, admin ops in queue_admin.py.

Usage:
    queue = TaskQueue()
    queue.add_task("/path/to/doc.pdf", task_type="index")
    task = queue.get_next_task()
    queue.update_status(task.id, "completed")
"""

import json
import uuid
import fcntl
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from aitao.core.config import ConfigManager, get_config
from aitao.core.logger import get_logger
from aitao.core.pathmanager import path_manager

# Import models (also re-exported for backward compatibility)
from aitao.indexation.queue_models import Task, TaskPriority, TaskStatus, TaskType
from aitao.indexation.queue_admin import QueueAdminMixin


# Backward-compatibility re-exports
__all__ = [
    "TaskQueue", "Task", "TaskPriority", "TaskStatus", "TaskType",
]


def _get_logger():
    """Get logger lazily to respect AITAO_QUIET env var."""
    return get_logger("queue")


class TaskQueue(QueueAdminMixin):
    """
    File-based task queue with JSON persistence.
    
    Thread-safe through file locking (fcntl).
    Tasks are ordered by priority, then by added_at timestamp.
    Admin operations (stats, purge, retry) provided by QueueAdminMixin.
    """
    
    MAX_RETRIES = 3
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        queue_file: Optional[str] = None
    ):
        """
        Initialize the task queue.
        
        Args:
            config_path: Path to config.toml
            queue_file: Custom path to queue file
        """
        # Load configuration (use global singleton for consistent paths)
        if config_path:
            self.config = ConfigManager(config_path)
        else:
            try:
                self.config = get_config()
            except Exception:
                self.config = None
        
        # Determine queue file location
        if queue_file:
            self.queue_file = Path(queue_file)
        else:
            # Use PathManager for queue file location
            self.queue_file = path_manager.get_queue_file()
        
        # Ensure directory exists
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize empty queue if file doesn't exist
        if not self.queue_file.exists():
            self._save_tasks([])
        
        _get_logger().info(
            "TaskQueue initialized",
            metadata={"queue_file": str(self.queue_file)}
        )
    
    def _load_tasks(self) -> List[Task]:
        """Load tasks from JSON file with file locking."""
        tasks = []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
                try:
                    data = json.load(f)
                    tasks = [Task.from_dict(t) for t in data]
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as e:
            _get_logger().error(f"Corrupted queue file: {e}")
            # Backup corrupted file and start fresh
            backup = self.queue_file.with_suffix(".json.corrupted")
            self.queue_file.rename(backup)
            _get_logger().warning(f"Backed up corrupted queue to {backup}")
        return tasks
    
    def _save_tasks(self, tasks: List[Task]) -> None:
        """Save tasks to JSON file with file locking."""
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
                try:
                    data = [t.to_dict() for t in tasks]
                    json.dump(data, f, indent=2, ensure_ascii=False)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            _get_logger().error(f"Failed to save queue: {e}")
            raise
    
    def _with_lock(self, operation):
        """Execute operation with exclusive file lock."""
        tasks = self._load_tasks()
        result = operation(tasks)
        self._save_tasks(tasks)
        return result
    
    def add_task(
        self,
        file_path: str,
        task_type: str = TaskType.INDEX.value,
        priority: str = TaskPriority.NORMAL.value,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Task:
        """
        Add a new task to the queue.
        
        Args:
            file_path: Path to the file to process
            task_type: Type of task (index, ocr, translate, etc.)
            priority: Priority level (high, normal, low)
            metadata: Additional task metadata
        
        Returns:
            The created Task object
        """
        task = Task(
            id=str(uuid.uuid4())[:8],  # Short UUID
            file_path=file_path,
            task_type=task_type,
            priority=priority,
            metadata=metadata or {}
        )
        
        tasks = self._load_tasks()
        
        # Check for duplicate (same file, same type, pending)
        for existing in tasks:
            if (existing.file_path == file_path and 
                existing.task_type == task_type and
                existing.status == TaskStatus.PENDING.value):
                _get_logger().debug(f"Task already exists for {file_path}")
                return existing
        
        tasks.append(task)
        self._save_tasks(tasks)
        
        _get_logger().info(
            "Task added",
            metadata={
                "task_id": task.id,
                "file": Path(file_path).name,
                "type": task_type,
                "priority": priority
            }
        )
        
        return task
    
    def add_tasks_batch(
        self,
        file_paths: List[str],
        task_type: str = TaskType.INDEX.value,
        priority: str = TaskPriority.NORMAL.value,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Task]:
        """
        Add multiple tasks efficiently.

        Args:
            file_paths: List of file paths
            task_type: Type of task
            priority: Priority level
            metadata: Extra task metadata applied to EVERY created task (e.g.
                ``{"force": True}``). Without this, ``add_task()`` was the
                only way to attach metadata — a caller batching a REINDEX
                requeue through this method had no way to signal "force":
                the worker's default handler reads ``task.metadata["force"]``
                (never ``task_type``) to decide whether to bypass the
                dedup/mtime check, so a REINDEX task queued here without it
                was silently treated as "already indexed" and skipped
                (bug found live: ``aitao scan reindex`` requeued 254
                vector-less documents this way and none of them were
                actually re-embedded).

        Returns:
            List of created Task objects
        """
        tasks = self._load_tasks()
        existing_paths = {
            t.file_path for t in tasks
            if t.task_type == task_type and t.status == TaskStatus.PENDING.value
        }

        new_tasks = []
        for file_path in file_paths:
            if file_path not in existing_paths:
                task = Task(
                    id=str(uuid.uuid4())[:8],
                    file_path=file_path,
                    task_type=task_type,
                    priority=priority,
                    metadata=dict(metadata) if metadata else {},
                )
                tasks.append(task)
                new_tasks.append(task)
        
        if new_tasks:
            self._save_tasks(tasks)
            _get_logger().info(f"Added {len(new_tasks)} tasks to queue")
        
        return new_tasks
    
    def get_next_task(self) -> Optional[Task]:
        """
        Get the next pending task to process.
        
        Returns tasks ordered by:
        1. Priority (high → normal → low)
        2. Added timestamp (oldest first)
        
        Returns:
            Next pending Task or None if queue is empty
        """
        tasks = self._load_tasks()
        
        # Filter pending tasks
        pending = [t for t in tasks if t.status == TaskStatus.PENDING.value]
        
        if not pending:
            return None
        
        # Sort by priority, then by timestamp
        pending.sort(key=lambda t: (
            TaskPriority(t.priority).sort_order,
            t.added_at
        ))
        
        return pending[0]
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a specific task by ID."""
        tasks = self._load_tasks()
        for task in tasks:
            if task.id == task_id:
                return task
        return None
    
    def update_status(
        self,
        task_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update task status.
        
        Args:
            task_id: Task ID
            status: New status
            error_message: Error message (for failed status)
        
        Returns:
            True if task was updated, False if not found
        """
        tasks = self._load_tasks()
        
        for task in tasks:
            if task.id == task_id:
                task.status = status
                
                if status == TaskStatus.PROCESSING.value:
                    task.started_at = datetime.now().isoformat()
                elif status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
                    task.completed_at = datetime.now().isoformat()
                
                if error_message:
                    task.error_message = error_message
                    task.retry_count += 1
                
                self._save_tasks(tasks)
                
                _get_logger().info(
                    "Task status updated",
                    metadata={
                        "task_id": task_id,
                        "status": status,
                        "error": error_message
                    }
                )
                return True
        
        return False
    
    def mark_processing(self, task_id: str) -> bool:
        """Mark task as processing."""
        return self.update_status(task_id, TaskStatus.PROCESSING.value)
    
    def mark_completed(self, task_id: str) -> bool:
        """Mark task as completed."""
        return self.update_status(task_id, TaskStatus.COMPLETED.value)
    
    def mark_failed(self, task_id: str, error: str) -> bool:
        """Mark task as failed with error message."""
        return self.update_status(task_id, TaskStatus.FAILED.value, error)
