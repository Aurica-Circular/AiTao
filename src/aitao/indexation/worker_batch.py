# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# worker_batch.py — grouped-task processing for BackgroundWorker (ÉPIC-31,
# US-111, absorbs US-093). Extracted from worker.py to keep it under the
# project's line-count convention.
#
# collect_batch() pulls up to batch_size PENDING tasks from the queue,
# marking each 'processing' immediately so a later poll never double-picks
# it. process_batch() indexes them via DocumentIndexer.index_files_batched()
# (ONE combined Meilisearch write per store instead of N blocking calls),
# then maps the per-file results back onto the ORIGINAL tasks POSITIONALLY —
# index_files_batched() guarantees one IndexResult per requested path, in
# request order, so no path-string matching is needed (paths get
# NFC-normalized internally and could legitimately differ, e.g. macOS NFD
# vs NFC — see indexation.indexer_helpers.normalize_path).
#
# "1 file = 1 state": every task in the group gets an explicit
# completed/failed status — a batch-wide failure (crash, Meilisearch error,
# result-count mismatch) fails every task in the group individually rather
# than leaving any of them silently unresolved; the existing retry policy
# (FailedFilesTracker, scanner reconciliation) then requeues them exactly
# like any other per-file failure.

from __future__ import annotations

from typing import TYPE_CHECKING, List

from aitao.indexation.indexer import DocumentIndexer
from aitao.indexation.queue_models import Task

if TYPE_CHECKING:
    from aitao.indexation.worker import BackgroundWorker


def _get_logger():
    from aitao.core.logger import get_logger
    return get_logger("worker")


def collect_batch(worker: "BackgroundWorker", batch_size: int) -> List[Task]:
    """Pull up to ``batch_size`` pending tasks, marking each 'processing'
    immediately so a subsequent ``get_next_task()`` call never re-picks it
    (each call reloads the queue from disk and filters on status)."""
    tasks: List[Task] = []
    for _ in range(batch_size):
        task = worker.queue.get_next_task()
        if task is None:
            break
        worker.queue.mark_processing(task.id)
        tasks.append(task)
    return tasks


def process_batch(worker: "BackgroundWorker", tasks: List[Task]) -> bool:
    """Index a group of tasks with ONE combined write per store, then apply
    each per-file outcome to its own queue task. Returns True if at least one
    task succeeded (mirrors ``BackgroundWorker._process_task``'s return
    contract, used by ``run_once()``'s callers)."""
    file_paths = [t.file_path for t in tasks]
    try:
        indexer = DocumentIndexer()
        batch_result = indexer.index_files_batched(file_paths)
    except Exception as e:
        _get_logger().error(f"Batch indexing crashed: {e}")
        for task in tasks:
            _mark_failed(worker, task, str(e))
        return False

    if len(batch_result.results) != len(tasks):
        # Defensive: index_files_batched()'s contract is one result per
        # requested path, in order. A mismatch is a bug, not a per-file
        # failure — fail the whole group loudly rather than guess a pairing.
        _get_logger().error(
            "Batch result count mismatch",
            metadata={"results": len(batch_result.results), "tasks": len(tasks)},
        )
        for task in tasks:
            _mark_failed(worker, task, "Batch result count mismatch")
        return False

    any_success = False
    for task, result in zip(tasks, batch_result.results):
        if result.success:
            _mark_completed(worker, task)
            any_success = True
        else:
            _mark_failed(worker, task, result.error or "Unknown batch failure")
    return any_success


def _mark_completed(worker: "BackgroundWorker", task: Task) -> None:
    worker.queue.mark_completed(task.id)
    worker.stats.tasks_processed += 1
    worker.stats.consecutive_errors = 0
    # Clear any past failure: the file is healthy again (US-086 v3).
    if worker._failed_tracker:
        worker._failed_tracker.mark_success(task.file_path)
    _get_logger().info("Task completed (batch)", metadata={"task_id": task.id})


def _mark_failed(worker: "BackgroundWorker", task: Task, error: str) -> None:
    worker.queue.mark_failed(task.id, error)
    worker.stats.tasks_failed += 1
    worker.stats.consecutive_errors += 1
    if worker._failed_tracker:
        worker._failed_tracker.add_failed_file(task.file_path, error, reason="index_failed")
        worker._failed_tracker.increment_retry(task.file_path)
    _get_logger().error("Task failed (batch)", metadata={"task_id": task.id, "error": error})
