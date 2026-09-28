# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Queue data models and enumerations.

Provides the core data structures used by the task queue system:
- TaskPriority, TaskStatus, TaskType enumerations
- Task dataclass with serialization support
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict


class TaskPriority(str, Enum):
    """Task priority levels."""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def sort_order(self) -> int:
        """Return numeric value for sorting (lower = higher priority)."""
        return {"high": 0, "normal": 1, "low": 2}[self.value]


class TaskStatus(str, Enum):
    """Task status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    """Types of tasks the queue can handle."""
    INDEX = "index"           # Full indexation (text extraction + embedding)
    OCR = "ocr"              # OCR for images/scanned PDFs
    TRANSLATE = "translate"   # Translation task
    REINDEX = "reindex"      # Re-index existing document
    DELETE = "delete"        # Remove from index


@dataclass
class Task:
    """Represents a task in the queue."""
    id: str
    file_path: str
    task_type: str
    priority: str = TaskPriority.NORMAL.value
    status: str = TaskStatus.PENDING.value
    added_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Set default values."""
        if not self.added_at:
            self.added_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create from dictionary."""
        return cls(**data)

    @property
    def is_pending(self) -> bool:
        """Check if task is pending."""
        return self.status == TaskStatus.PENDING.value

    @property
    def is_processing(self) -> bool:
        """Check if task is being processed."""
        return self.status == TaskStatus.PROCESSING.value
