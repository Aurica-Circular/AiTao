# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Trash registry for deleted files (US-28a).

When the scanner detects that an indexed file disappeared from disk, the file
enters the trash: still searchable but flagged "deleted from disk" (product
decision 2026-06-12 — recover content of accidentally deleted files), until
the retention period expires and the purge removes it from all stores (28b).

The registry is a JSON file (path → deleted_at ISO timestamp) under the
storage root, like the scanner state — single source of truth, no schema
change in Meilisearch/LanceDB/chunk store.

Core feature — no license gating.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from aitao.core.logger import get_logger
from aitao.core.pathmanager import path_manager

logger = get_logger("indexation.trash")


class TrashRegistry:
    """Path → deleted_at registry persisted as JSON."""

    def __init__(self, state_file: Optional[Path] = None) -> None:
        self.state_file = (
            Path(state_file) if state_file else path_manager.get_trash_state_file()
        )
        self._entries: Dict[str, str] = {}
        self._loaded_mtime: float = -1.0
        self._load()

    def _maybe_reload(self) -> None:
        """Pick up writes from other processes (CLI scan vs API vs worker).

        The registry is shared as a file across processes; a cheap mtime check
        keeps each in-process cache fresh without re-reading on every call.
        """
        try:
            mtime = self.state_file.stat().st_mtime
        except OSError:
            return
        if mtime != self._loaded_mtime:
            self._load()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def mark(self, paths: List[str]) -> int:
        """Put paths in the trash (idempotent). Returns newly marked count."""
        self._maybe_reload()
        now = datetime.now(timezone.utc).isoformat()
        added = 0
        for path in paths:
            if path not in self._entries:
                self._entries[path] = now
                added += 1
        if added:
            self._save()
            logger.info("Files marked as deleted", metadata={"count": added})
        return added

    def unmark(self, path: str) -> bool:
        """Remove a path from the trash (file restored). True if it was there."""
        self._maybe_reload()
        if path in self._entries:
            del self._entries[path]
            self._save()
            logger.info("File restored from trash", metadata={"path": path})
            return True
        return False

    def drop(self, path: str) -> None:
        """Remove an entry after definitive purge (28b) — no 'restored' log."""
        if path in self._entries:
            del self._entries[path]
            self._save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def is_trashed(self, path: str) -> bool:
        self._maybe_reload()
        return path in self._entries

    def deleted_at(self, path: str) -> Optional[str]:
        self._maybe_reload()
        return self._entries.get(path)

    def trashed_paths(self) -> Dict[str, str]:
        """All trashed entries (path → deleted_at ISO timestamp)."""
        self._maybe_reload()
        return dict(self._entries)

    def expired_paths(self, retention_days: int) -> List[str]:
        """Paths in the trash for longer than ``retention_days`` (28b purge)."""
        self._maybe_reload()
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        expired = []
        for path, stamp in self._entries.items():
            try:
                deleted = datetime.fromisoformat(stamp)
            except ValueError:
                expired.append(path)  # unreadable stamp — purge rather than keep forever
                continue
            if deleted <= cutoff:
                expired.append(path)
        return expired

    # ------------------------------------------------------------------
    # Persistence (defensive: trash issues must never break indexing)
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.state_file.exists():
                self._entries = json.loads(self.state_file.read_text("utf-8"))
                self._loaded_mtime = self.state_file.stat().st_mtime
        except Exception as e:
            logger.warning("Could not load trash state", metadata={"error": str(e)})
            self._entries = {}

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False), "utf-8"
            )
            self._loaded_mtime = self.state_file.stat().st_mtime
        except Exception as e:
            logger.warning("Could not save trash state", metadata={"error": str(e)})


_registry: Optional[TrashRegistry] = None


def get_trash_registry() -> TrashRegistry:
    """Shared TrashRegistry instance (worker, search, RAG formatter)."""
    global _registry
    if _registry is None:
        _registry = TrashRegistry()
    return _registry


def purge_expired(retention_days: int, indexer: Optional[object] = None) -> int:
    """Definitively remove trash entries older than ``retention_days`` from
    Meilisearch, LanceDB and the chunk store (US-28b). Returns purged count.

    ``indexer`` is injectable for tests; defaults to a DocumentIndexer.
    """
    registry = get_trash_registry()
    expired = registry.expired_paths(retention_days)
    if not expired:
        return 0

    # Lazy import — indexer.py imports this module for trash restore
    from aitao.indexation.indexer import DocumentIndexer
    from aitao.indexation.indexer_helpers import generate_doc_id

    idx = indexer if indexer is not None else DocumentIndexer()
    purged = 0
    for path in expired:
        doc_id = generate_doc_id(path)
        try:
            if getattr(idx, "meilisearch", None):
                idx.meilisearch.delete(doc_id)
            if getattr(idx, "lancedb", None):
                idx.lancedb.delete(doc_id)
            if getattr(idx, "chunk_store", None):
                idx.chunk_store.delete_by_doc_id(doc_id)
            registry.drop(path)
            purged += 1
        except Exception as e:
            # Keep the entry: the next purge pass will retry
            logger.warning(
                "Trash purge failed for a file",
                metadata={"path": path, "error": str(e)},
            )
    if purged:
        logger.info(
            "Trash purged",
            metadata={"purged": purged, "retention_days": retention_days},
        )
    return purged
