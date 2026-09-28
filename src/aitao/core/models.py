# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Core domain models for AiTao.

Canonical, validated representations of the entities that flow across layers.
These are Pydantic v2 models: a missing or mistyped field raises a
``ValidationError`` at construction time instead of surfacing as a silent
``KeyError`` deep in the pipeline (US-23).

Scope of this module — the *shared vocabulary* used by more than one layer:
- ``Document``  : an indexed document (the unit ingested, stored, retrieved).
- ``ChatMessage`` / ``ChatRole`` : a single turn in a conversation.

Layer-specific result/response models build on these but live next to the code
that owns them (search results in ``search/search_models.py``, RAG context in
``llm/rag_models.py``, indexing outcomes in ``indexation/indexer_helpers.py``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Shared model config.

    ``extra="ignore"`` keeps construction tolerant of surplus keys (e.g. extra
    columns returned by a storage backend) while still rejecting *missing*
    required fields — the validation guarantee US-23 is after.
    """

    model_config = ConfigDict(extra="ignore")


class ChatRole(str, Enum):
    """Author role of a chat message (OpenAI / Ollama wire values)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(_Base):
    """A single message in a conversation.

    ``role`` is kept as a plain ``str`` (not the enum) to stay byte-for-byte
    compatible with the Ollama / OpenAI wire format; ``ChatRole`` documents the
    accepted values.
    """

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> Dict[str, str]:
        """Return the wire format expected by Ollama / OpenAI clients."""
        return {"role": self.role, "content": self.content}


class Document(_Base):
    """An indexed document — the unit ingested, stored and retrieved.

    Used by both the LanceDB (semantic) and Meilisearch (full-text) indexers.
    """

    id: str  # SHA256 hash (stable document identity)
    path: str
    title: str
    content: str
    language: str = "en"
    category: Optional[str] = None
    file_type: Optional[str] = None  # extension, e.g. ".pdf"
    file_size: int = 0  # bytes
    date_indexed: datetime = Field(default_factory=datetime.now)
    date_modified: Optional[datetime] = None
    word_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
