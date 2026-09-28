# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_worker_batch.py — unit tests for indexation.worker_batch (ÉPIC-31,
# US-111, absorbs US-093): grouped task processing for BackgroundWorker.
#
# Covers: collect_batch() marks tasks 'processing' immediately (no
# double-pick), process_batch() maps DocumentIndexer.index_files_batched()'s
# positional results back onto the ORIGINAL queue tasks, a crash fails every
# task in the group, a result-count mismatch fails every task defensively,
# and BackgroundWorker._poll_and_process() only engages the batch path when
# worker_config.batch_size > 1 AND more than one task is actually pending
# (batch_size<=1, or a lone pending task, stays bit-identical to pre-US-111).

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation import worker_batch  # noqa: E402
from aitao.indexation.worker import BackgroundWorker  # noqa: E402
from aitao.indexation.queue import TaskQueue, TaskStatus  # noqa: E402


@dataclass
class _FakeIndexResult:
    path: str
    success: bool
    error: Optional[str] = None


@dataclass
class _FakeBatchResult:
    results: List[_FakeIndexResult]


@pytest.fixture(autouse=True)
def calm_cpu():
    with patch("aitao.indexation.worker.psutil.cpu_percent", return_value=10.0):
        yield


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_queue(temp_dir):
    return TaskQueue(queue_file=str(temp_dir / "queue" / "tasks.json"))


@pytest.fixture
def worker(temp_dir, temp_queue):
    w = BackgroundWorker(queue=temp_queue)
    w.pid_file = temp_dir / "worker.pid"
    return w


class TestCollectBatch:
    def test_collects_up_to_batch_size_pending_tasks(self, temp_queue):
        for i in range(5):
            temp_queue.add_task(f"/test/doc{i}.txt")

        tasks = worker_batch.collect_batch(
            MagicMock(queue=temp_queue), batch_size=3
        )

        assert len(tasks) == 3
        stats = temp_queue.get_stats()
        assert stats["processing"] == 3
        assert stats["pending"] == 2

    def test_stops_early_when_queue_runs_out(self, temp_queue):
        temp_queue.add_task("/test/doc0.txt")

        tasks = worker_batch.collect_batch(MagicMock(queue=temp_queue), batch_size=5)

        assert len(tasks) == 1

    def test_each_collected_task_is_marked_processing_before_the_next_pick(
        self, temp_queue,
    ):
        """Guards against double-picking the same task twice in one batch."""
        t1 = temp_queue.add_task("/test/doc0.txt")
        t2 = temp_queue.add_task("/test/doc1.txt")

        tasks = worker_batch.collect_batch(MagicMock(queue=temp_queue), batch_size=2)

        assert {t.id for t in tasks} == {t1.id, t2.id}


class TestProcessBatch:
    def test_maps_results_positionally_onto_tasks(self, worker, temp_queue):
        t1 = temp_queue.add_task("/test/a.txt")
        t2 = temp_queue.add_task("/test/b.txt")
        worker.queue.mark_processing(t1.id)
        worker.queue.mark_processing(t2.id)

        fake_result = _FakeBatchResult(results=[
            _FakeIndexResult(path="/test/a.txt", success=True),
            _FakeIndexResult(path="/test/b.txt", success=False, error="boom"),
        ])
        mock_indexer = MagicMock()
        mock_indexer.index_files_batched.return_value = fake_result

        with patch("aitao.indexation.worker_batch.DocumentIndexer", return_value=mock_indexer):
            any_success = worker_batch.process_batch(worker, [t1, t2])

        assert any_success is True
        assert worker.queue.get_task(t1.id).status == TaskStatus.COMPLETED.value
        assert worker.queue.get_task(t2.id).status == TaskStatus.FAILED.value
        assert worker.queue.get_task(t2.id).error_message == "boom"
        assert worker.stats.tasks_processed == 1
        assert worker.stats.tasks_failed == 1

    def test_crash_fails_every_task_in_the_group(self, worker, temp_queue):
        t1 = temp_queue.add_task("/test/a.txt")
        t2 = temp_queue.add_task("/test/b.txt")
        worker.queue.mark_processing(t1.id)
        worker.queue.mark_processing(t2.id)

        with patch(
            "aitao.indexation.worker_batch.DocumentIndexer",
            side_effect=RuntimeError("meilisearch unreachable"),
        ):
            any_success = worker_batch.process_batch(worker, [t1, t2])

        assert any_success is False
        assert worker.queue.get_task(t1.id).status == TaskStatus.FAILED.value
        assert worker.queue.get_task(t2.id).status == TaskStatus.FAILED.value

    def test_result_count_mismatch_fails_every_task(self, worker, temp_queue):
        t1 = temp_queue.add_task("/test/a.txt")
        t2 = temp_queue.add_task("/test/b.txt")
        worker.queue.mark_processing(t1.id)
        worker.queue.mark_processing(t2.id)

        fake_result = _FakeBatchResult(results=[_FakeIndexResult(path="/test/a.txt", success=True)])
        mock_indexer = MagicMock()
        mock_indexer.index_files_batched.return_value = fake_result

        with patch("aitao.indexation.worker_batch.DocumentIndexer", return_value=mock_indexer):
            any_success = worker_batch.process_batch(worker, [t1, t2])

        assert any_success is False
        assert worker.queue.get_task(t1.id).status == TaskStatus.FAILED.value
        assert worker.queue.get_task(t2.id).status == TaskStatus.FAILED.value


class TestWorkerPollBatchWiring:
    def test_batch_size_1_uses_the_legacy_single_task_path(self, worker, temp_queue, temp_dir):
        worker.worker_config.batch_size = 1
        test_file = temp_dir / "doc.txt"
        test_file.write_text("hello")
        temp_queue.add_task(str(test_file))

        with patch("aitao.indexation.worker.DocumentIndexer") as mock_cls:
            mock_indexer = MagicMock()
            mock_indexer.index_file.return_value = MagicMock(success=True, doc_id="x", word_count=1)
            mock_cls.return_value = mock_indexer

            result = worker._poll_and_process()

        assert result is True
        mock_indexer.index_file.assert_called_once()

    def test_batch_size_above_1_with_a_single_pending_task_still_uses_single_path(
        self, worker, temp_queue, temp_dir,
    ):
        """Nothing to gain from grouping ONE file — falls back to
        _process_task exactly like batch_size=1."""
        worker.worker_config.batch_size = 8
        test_file = temp_dir / "doc.txt"
        test_file.write_text("hello")
        temp_queue.add_task(str(test_file))

        with patch("aitao.indexation.worker.DocumentIndexer") as mock_cls:
            mock_indexer = MagicMock()
            mock_indexer.index_file.return_value = MagicMock(success=True, doc_id="x", word_count=1)
            mock_cls.return_value = mock_indexer

            result = worker._poll_and_process()

        assert result is True
        mock_indexer.index_file.assert_called_once()

    def test_batch_size_above_1_with_several_pending_tasks_uses_process_batch(
        self, worker, temp_queue, temp_dir,
    ):
        worker.worker_config.batch_size = 8
        paths = []
        for i in range(3):
            f = temp_dir / f"doc{i}.txt"
            f.write_text("hello")
            paths.append(str(f))
            temp_queue.add_task(str(f))

        fake_result = _FakeBatchResult(results=[
            _FakeIndexResult(path=p, success=True) for p in paths
        ])
        mock_indexer = MagicMock()
        mock_indexer.index_files_batched.return_value = fake_result

        with patch("aitao.indexation.worker_batch.DocumentIndexer", return_value=mock_indexer):
            result = worker._poll_and_process()

        assert result is True
        mock_indexer.index_files_batched.assert_called_once()
        assert worker.stats.tasks_processed == 3
