# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Worker retry-policy tests (US-086 v3).

The worker records index failures in an injected FailedFilesTracker and uses it
to BOUND the scanner's orphan reconciliation: once a file exhausts its retries it
is no longer re-enqueued. These tests inject a tracker (so nothing touches prod)
and exercise the failure/success recording plus the reconciliation gate.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from aitao.indexation.queue import TaskQueue
from aitao.indexation.worker import BackgroundWorker, MAX_INDEX_RETRIES


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def queue(temp_dir):
    return TaskQueue(queue_file=str(temp_dir / "queue" / "tasks.json"))


def _worker(queue, tracker, handler):
    w = BackgroundWorker(queue=queue, task_handler=handler, failed_tracker=tracker)
    return w


class TestFailureRecording:
    def test_failure_records_and_increments(self, queue):
        tracker = MagicMock()
        task = queue.add_task("/doc.pdf", task_type="index")
        worker = _worker(queue, tracker, handler=lambda _t: False)

        ok = worker._process_task(task)

        assert ok is False
        tracker.add_failed_file.assert_called_once()
        assert tracker.add_failed_file.call_args.args[0] == "/doc.pdf"
        tracker.increment_retry.assert_called_once_with("/doc.pdf")
        tracker.mark_success.assert_not_called()

    def test_success_clears_failure(self, queue):
        tracker = MagicMock()
        task = queue.add_task("/doc.pdf", task_type="index")
        worker = _worker(queue, tracker, handler=lambda _t: True)

        ok = worker._process_task(task)

        assert ok is True
        tracker.mark_success.assert_called_once_with("/doc.pdf")
        tracker.add_failed_file.assert_not_called()

    def test_no_tracker_is_safe(self, queue):
        # Default worker (no tracker) must not raise when recording outcomes.
        task = queue.add_task("/doc.pdf", task_type="index")
        worker = BackgroundWorker(queue=queue, task_handler=lambda _t: False)
        assert worker._process_task(task) is False


class TestReconciliationBound:
    def _fi(self, path):
        return SimpleNamespace(path=path)

    def test_exhausted_orphan_is_dropped(self, queue):
        tracker = MagicMock()
        tracker.is_exhausted.side_effect = lambda p, n: p == "/bad.pdf"
        worker = _worker(queue, tracker, handler=lambda _t: True)

        recon = [self._fi("/good.pdf"), self._fi("/bad.pdf")]
        kept, given_up = worker._select_reconciled(recon)

        assert [fi.path for fi in kept] == ["/good.pdf"]
        assert given_up == 1

    def test_bound_uses_configured_ceiling(self, queue):
        tracker = MagicMock()
        tracker.is_exhausted.return_value = False
        worker = _worker(queue, tracker, handler=lambda _t: True)

        worker._select_reconciled([self._fi("/a.pdf")])

        tracker.is_exhausted.assert_called_with("/a.pdf", MAX_INDEX_RETRIES)

    def test_no_tracker_keeps_all(self, queue):
        worker = BackgroundWorker(queue=queue, task_handler=lambda _t: True)
        recon = [self._fi("/a.pdf"), self._fi("/b.pdf")]
        kept, given_up = worker._select_reconciled(recon)
        assert len(kept) == 2
        assert given_up == 0
