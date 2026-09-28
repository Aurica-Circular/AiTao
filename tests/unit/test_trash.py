# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the deleted-files trash lifecycle (US-28).

Covers:
- TrashRegistry: mark / unmark / drop, persistence, expiry computation
- purge_expired: definitive removal from all three stores (mocked indexer)
- Scanner guard: a missing root must not mark its files as deleted
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aitao.indexation.trash import TrashRegistry, purge_expired
import aitao.indexation.trash as trash_module


@pytest.fixture
def registry(tmp_path):
    return TrashRegistry(state_file=tmp_path / "trash_state.json")


class TestTrashRegistry:
    def test_mark_and_query(self, registry):
        assert registry.mark(["/vol/a.md", "/vol/b.pdf"]) == 2
        assert registry.is_trashed("/vol/a.md")
        assert registry.deleted_at("/vol/a.md")
        assert not registry.is_trashed("/vol/c.txt")

    def test_mark_is_idempotent(self, registry):
        registry.mark(["/vol/a.md"])
        first_stamp = registry.deleted_at("/vol/a.md")
        assert registry.mark(["/vol/a.md"]) == 0
        assert registry.deleted_at("/vol/a.md") == first_stamp

    def test_unmark_restores(self, registry):
        registry.mark(["/vol/a.md"])
        assert registry.unmark("/vol/a.md") is True
        assert not registry.is_trashed("/vol/a.md")
        assert registry.unmark("/vol/a.md") is False

    def test_persistence_across_instances(self, registry):
        registry.mark(["/vol/a.md"])
        reloaded = TrashRegistry(state_file=registry.state_file)
        assert reloaded.is_trashed("/vol/a.md")

    def test_expired_paths(self, registry):
        registry.mark(["/vol/old.md", "/vol/recent.md"])
        # Age one entry artificially past the retention window
        old_stamp = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        registry._entries["/vol/old.md"] = old_stamp
        expired = registry.expired_paths(retention_days=30)
        assert expired == ["/vol/old.md"]

    def test_corrupt_state_file_recovers(self, tmp_path):
        state = tmp_path / "trash_state.json"
        state.write_text("{not json", "utf-8")
        registry = TrashRegistry(state_file=state)
        assert registry.trashed_paths() == {}


class TestPurgeExpired:
    def _aged_registry(self, tmp_path, days_ago=31):
        registry = TrashRegistry(state_file=tmp_path / "trash.json")
        registry.mark(["/vol/old.md"])
        registry._entries["/vol/old.md"] = (
            datetime.now(timezone.utc) - timedelta(days=days_ago)
        ).isoformat()
        registry._save()
        return registry

    def test_purges_all_three_stores(self, tmp_path, monkeypatch):
        registry = self._aged_registry(tmp_path)
        monkeypatch.setattr(trash_module, "_registry", registry)
        indexer = MagicMock()
        assert purge_expired(30, indexer=indexer) == 1
        indexer.meilisearch.delete.assert_called_once()
        indexer.lancedb.delete.assert_called_once()
        indexer.chunk_store.delete_by_doc_id.assert_called_once()
        assert not registry.is_trashed("/vol/old.md")

    def test_keeps_recent_entries(self, tmp_path, monkeypatch):
        registry = self._aged_registry(tmp_path, days_ago=5)
        monkeypatch.setattr(trash_module, "_registry", registry)
        indexer = MagicMock()
        assert purge_expired(30, indexer=indexer) == 0
        indexer.meilisearch.delete.assert_not_called()
        assert registry.is_trashed("/vol/old.md")

    def test_store_failure_keeps_entry_for_retry(self, tmp_path, monkeypatch):
        registry = self._aged_registry(tmp_path)
        monkeypatch.setattr(trash_module, "_registry", registry)
        indexer = MagicMock()
        indexer.meilisearch.delete.side_effect = RuntimeError("down")
        assert purge_expired(30, indexer=indexer) == 0
        assert registry.is_trashed("/vol/old.md")


class TestScannerMissingRootGuard:
    """US-28a — an unmounted volume must not flag its tree as deleted."""

    def _scanner(self, tmp_path, roots):
        from aitao.indexation.scanner import FilesystemScanner

        scanner = FilesystemScanner.__new__(FilesystemScanner)
        scanner.include_paths = [Path(r) for r in roots]
        scanner.exclude_dirs = set()
        scanner.exclude_files = set()
        scanner.exclude_extensions = set()
        scanner.supported_extensions = {".md", ".txt"}
        scanner.state_file = tmp_path / "scanner_state.json"
        scanner._file_state = {}
        return scanner

    def test_missing_root_files_not_deleted(self, tmp_path):
        live_root = tmp_path / "live"
        live_root.mkdir()
        (live_root / "kept.md").write_text("still here")
        gone_root = tmp_path / "unmounted"  # never created

        scanner = self._scanner(tmp_path, [live_root, gone_root])
        # Previous state: one file per root
        scanner._file_state = {
            str(live_root / "kept.md"): {"mtime": 0, "hash": "x"},
            str(gone_root / "lost.md"): {"mtime": 0, "hash": "y"},
        }

        result = scanner.scan(save_state=False)

        # The unmounted root's file is NOT deleted and keeps its state
        assert str(gone_root / "lost.md") not in result.deleted_paths
        assert str(gone_root / "lost.md") in scanner._file_state

    def test_genuinely_deleted_file_is_detected(self, tmp_path):
        root = tmp_path / "live"
        root.mkdir()
        (root / "kept.md").write_text("still here")

        scanner = self._scanner(tmp_path, [root])
        scanner._file_state = {
            str(root / "kept.md"): {"mtime": 0, "hash": "x"},
            str(root / "removed.md"): {"mtime": 0, "hash": "y"},
        }

        result = scanner.scan(save_state=False)

        assert str(root / "removed.md") in result.deleted_paths
        assert str(root / "removed.md") not in scanner._file_state

    def test_scan_marks_deleted_files_in_trash(self, tmp_path, monkeypatch):
        """Every scan path (worker AND CLI) must record deletions (US-28a):
        the CLI scan consumed the event without marking it — bug 2026-06-12."""
        registry = TrashRegistry(state_file=tmp_path / "trash.json")
        monkeypatch.setattr(trash_module, "_registry", registry)

        root = tmp_path / "live"
        root.mkdir()
        (root / "kept.md").write_text("still here")

        scanner = self._scanner(tmp_path, [root])
        scanner._file_state = {
            str(root / "kept.md"): {"mtime": 0, "hash": "x"},
            str(root / "removed.md"): {"mtime": 0, "hash": "y"},
        }

        scanner.scan(save_state=True)

        assert registry.is_trashed(str(root / "removed.md"))

    def test_dry_scan_does_not_touch_trash(self, tmp_path, monkeypatch):
        registry = TrashRegistry(state_file=tmp_path / "trash.json")
        monkeypatch.setattr(trash_module, "_registry", registry)

        root = tmp_path / "live"
        root.mkdir()
        scanner = self._scanner(tmp_path, [root])
        scanner._file_state = {str(root / "removed.md"): {"mtime": 0, "hash": "y"}}

        scanner.scan(save_state=False)

        assert not registry.is_trashed(str(root / "removed.md"))
