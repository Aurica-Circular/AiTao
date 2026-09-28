# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_scan_reindex_enqueue.py — regression test for the live bug
# found 2026-07-16: `aitao scan reindex` requeued 254 vector-less documents
# but NONE of them were actually re-embedded.
#
# Root cause: `scan_reindex()` submitted the requeue through
# `TaskQueue.add_tasks_batch()` with no metadata. The worker's default task
# handler (indexation.worker.BackgroundWorker._default_handler) decides
# whether to bypass the dedup/mtime check ONLY via `task.metadata["force"]`
# (never via `task_type`), so every one of those REINDEX tasks was silently
# short-circuited as "already indexed" — the file's mtime hadn't changed, so
# DocumentIndexer._is_already_indexed() returned True and prepare_item()
# returned an early success result without ever re-extracting, re-chunking,
# or recomputing the doc-level vector. Meilisearch reported the requeue as
# "processed successfully" for every file; nothing had actually changed.
#
# This test exercises the REAL typer command (via CliRunner, not a bare
# function call — scan_reindex()'s parameter defaults are typer.Option()
# sentinels that only resolve correctly through the Click/Typer runtime) and
# asserts the exact metadata contract the worker depends on.

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.cli.commands.scan import app  # noqa: E402

runner = CliRunner()

MISSING = ["/docs/a.pdf", "/docs/b.pdf"]


def _fake_meili():
    meili = MagicMock()
    meili.get_all_document_paths.return_value = MISSING + ["/docs/c.pdf"]
    meili.get_incomplete_document_paths.return_value = MISSING
    return meili


def test_reindex_requeue_forces_bypass_of_dedup():
    """The requeued tasks MUST carry metadata={"force": True, ...} — without
    it the worker's dedup/mtime check skips every file (the actual bug)."""
    fake_queue = MagicMock()
    fake_queue.add_tasks_batch.return_value = [MagicMock(), MagicMock()]

    with patch("aitao.storage.repository.make_meilisearch_client", return_value=_fake_meili()), \
         patch("aitao.indexation.queue.TaskQueue", return_value=fake_queue):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    fake_queue.add_tasks_batch.assert_called_once()
    _, kwargs = fake_queue.add_tasks_batch.call_args
    metadata = kwargs.get("metadata")
    assert metadata is not None, (
        "add_tasks_batch() called without metadata — the worker's dedup "
        "check will silently skip every requeued file (mtime unchanged)"
    )
    assert metadata.get("force") is True


def test_dry_run_never_touches_the_queue():
    with patch("aitao.storage.repository.make_meilisearch_client", return_value=_fake_meili()), \
         patch("aitao.indexation.queue.TaskQueue") as ctor:
        result = runner.invoke(app, ["reindex", "--dry-run"])

    assert result.exit_code == 0, result.output
    ctor.assert_not_called()


def test_nothing_missing_does_not_touch_the_queue():
    meili = _fake_meili()
    meili.get_incomplete_document_paths.return_value = []

    with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
         patch("aitao.indexation.queue.TaskQueue") as ctor:
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    ctor.assert_not_called()
