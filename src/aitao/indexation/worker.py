# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Background Worker for document processing.

Core worker loop: queue polling, system-load gating, and task execution.
Daemon lifecycle (PID files, start/stop) is delegated to worker_daemon.py.
"""

import os
import time
import signal
import psutil  # Re-export for backward compat with test patches
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from aitao.core.config import ConfigManager
from aitao.core.failed_files_tracker import FailedFilesTracker
from aitao.core.logger import get_logger
from aitao.core.pathmanager import path_manager
from aitao.indexation.queue import TaskQueue, Task
from aitao.indexation.indexer import DocumentIndexer
from aitao.indexation.scanner import FilesystemScanner
from aitao.indexation.worker_daemon import WorkerDaemon
from aitao.indexation import worker_batch

# Reconciliation re-enqueues an orphan file every scan; this caps how many times
# a persistently-failing file is retried before it is left for manual inspection
# (surfaced via the failed-files tracker), instead of being hammered forever.
# Fallback only — real runs read ``[indexing] max_reconciliation_retries``
# (US-113); this constant is used when no ConfigManager is available (tests
# building a bare BackgroundWorker with config_path=None and no real file).
MAX_INDEX_RETRIES = 3


def _get_logger():
    """Get logger lazily to respect AITAO_QUIET env var."""
    return get_logger("worker")


@dataclass
class WorkerConfig:
    """Worker configuration settings."""
    poll_interval: int = 5           # Seconds between queue polls (default: 5s)
    cpu_threshold: float = 80.0      # Max CPU % before pausing
    max_consecutive_errors: int = 5  # Errors before pause
    error_pause_time: int = 60       # Seconds to pause after errors
    shutdown_timeout: int = 30       # Seconds to wait for graceful shutdown
    stuck_task_timeout: int = 600    # Seconds before a processing task is considered stuck (10 min)
    # ÉPIC-31 (US-111, absorbs US-093): number of pending tasks grouped into
    # ONE combined Meilisearch write (see indexation.worker_batch). Default 1
    # = today's per-file behaviour; set from [indexing] batch_size in
    # __init__ when a real config is available (see core/config_schema.py).
    batch_size: int = 1


@dataclass
class WorkerStats:
    """Worker runtime statistics."""
    started_at: Optional[str] = None
    tasks_processed: int = 0
    tasks_failed: int = 0
    last_poll: Optional[str] = None
    last_task_id: Optional[str] = None
    consecutive_errors: int = 0
    is_running: bool = False
    is_paused: bool = False
    pause_reason: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "started_at": self.started_at,
            "tasks_processed": self.tasks_processed,
            "tasks_failed": self.tasks_failed,
            "last_poll": self.last_poll,
            "last_task_id": self.last_task_id,
            "consecutive_errors": self.consecutive_errors,
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason
        }


class BackgroundWorker:
    """
    Background worker that processes tasks from the queue.
    
    The worker polls the queue at regular intervals and processes
    tasks one at a time. It monitors system load and pauses if
    CPU usage is too high.
    """
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        queue: Optional[TaskQueue] = None,
        task_handler: Optional[Callable[[Task], bool]] = None,
        failed_tracker: Optional["FailedFilesTracker"] = None,
    ):
        """
        Initialize the background worker.

        Args:
            config_path: Path to config.toml
            queue: Optional TaskQueue instance (creates one if not provided)
            task_handler: Optional callback to process tasks
            failed_tracker: Optional FailedFilesTracker. Injected collaborator for
                the retry policy (US-086 v3). When None, failure recording is a
                no-op until ``run()`` lazily creates the real one — so calling
                ``_process_task`` in isolation (tests) never writes to disk.
        """
        # Load configuration
        if config_path:
            self.config_manager = ConfigManager(config_path)
        else:
            config_file = path_manager.root / "config" / "config.toml"
            self.config_manager = ConfigManager(str(config_file)) if config_file.exists() else None
        
        # Worker configuration
        self.worker_config = WorkerConfig()
        if self.config_manager:
            ws = self.config_manager.get("worker", {})
            if isinstance(ws, dict):
                self.worker_config.poll_interval = ws.get("poll_interval", 5)
                self.worker_config.cpu_threshold = ws.get("cpu_threshold", 80.0)
                self.worker_config.stuck_task_timeout = ws.get("stuck_task_timeout", 600)
            # ÉPIC-31 (US-111, absorbs US-093) — see WorkerConfig.batch_size.
            self.worker_config.batch_size = self.config_manager.indexing.batch_size
        
        # Initialize queue
        self.queue = queue or TaskQueue(config_path=config_path)
        self.task_handler = task_handler or self._default_handler

        # Retry-policy collaborator (US-086 v3). Stays None until injected or
        # until run() builds the real one, so unit tests stay side-effect free.
        self._failed_tracker = failed_tracker

        # Runtime state
        self.stats = WorkerStats()
        self._shutdown_requested = False
        
        # Daemon manager (PID file, start/stop)
        if not self.config_manager:
            raise ValueError("ConfigManager required for worker initialization")
        # Paths come from the single path authority (US-098 A4), not ConfigManager.
        self.pid_file = path_manager.get_storage_root() / "worker.pid"
        self._daemon = WorkerDaemon(self.pid_file, path_manager.root)
        
        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
        _get_logger().info(
            "BackgroundWorker initialized",
            metadata={
                "poll_interval": self.worker_config.poll_interval,
                "cpu_threshold": self.worker_config.cpu_threshold,
                "pid_file": str(self.pid_file)
            }
        )
    
    def _select_reconciled(self, reconciled_files):
        """Split reconciled orphans into (to re-enqueue, given-up count).

        A reconciled file is dropped when the failed-files tracker says it has hit
        the retry ceiling — bounding the reconciliation loop (US-086 v3). With no
        tracker, everything is kept (reconciliation unbounded, pre-v3 behaviour).
        Ceiling comes from ``[indexing] max_reconciliation_retries`` (US-113,
        promoted from the previously hard-coded ``MAX_INDEX_RETRIES``
        constant) so it can be tuned without a code change.
        """
        if self._failed_tracker is None:
            return list(reconciled_files), 0
        max_retries = (
            self.config_manager.indexing.max_reconciliation_retries
            if self.config_manager else MAX_INDEX_RETRIES
        )
        kept = [
            fi for fi in reconciled_files
            if not self._failed_tracker.is_exhausted(str(fi.path), max_retries)
        ]
        return kept, len(reconciled_files) - len(kept)

    def _auto_prune_out_of_scope(self) -> None:
        """Opt-in: remove indexed documents outside every configured root (US-126-C).

        Gated by ``[indexing] auto_prune_out_of_scope`` (default False — a no-op
        when unset, so existing deployments see zero behaviour change). Reuses
        the same pure detection logic and safety rules as the manual
        ``aitao index prune`` command (US-126-A): scope is decided ONLY from
        the configured ``include_paths`` (``existing_only=False``), never from
        disk presence, so a demounted-but-still-configured volume is never
        touched. Runs on EVERY periodic scan cycle when enabled — regardless
        of whether the filesystem scan itself found changes, since going
        out-of-scope is a config change, not a filesystem change. Never
        raises: any failure here must not interrupt the worker's scan loop.
        """
        if not self.config_manager or not self.config_manager.indexing.auto_prune_out_of_scope:
            return
        try:
            from aitao.indexation.prune_runner import run_out_of_scope_prune
            from aitao.storage.repository import make_meilisearch_client

            meili = make_meilisearch_client()
            # SAFETY: existing_only=False on purpose — see docstring above.
            configured_roots = path_manager.get_include_paths(existing_only=False)
            deleted, failed = run_out_of_scope_prune(
                meili, configured_roots, logger=_get_logger()
            )
            if deleted or failed:
                _get_logger().info(
                    "Auto-prune complete",
                    metadata={"deleted": deleted, "failed": failed},
                )
        except Exception as exc:
            _get_logger().warning(f"Auto-prune out-of-scope failed: {exc}")

    def _default_handler(self, task: Task) -> bool:
        """Default task handler — indexes documents via DocumentIndexer.

        ``task.metadata["force"]`` (ÉPIC-31, US-112): the migration's CJK-glue
        requeue (search.migrate_v4.MigrationV4Runner._requeue) sets this so the
        file is ALWAYS re-extracted/re-chunked/re-embedded even though its
        mtime on disk never changed — the dedup check
        (DocumentIndexer._is_already_indexed) would otherwise skip it forever,
        since nothing about the FILE changed, only how its already-stored
        content should have been glued.
        """
        _get_logger().info(
            "Processing task",
            metadata={"task_id": task.id, "file_path": task.file_path, "task_type": task.task_type}
        )
        try:
            indexer = DocumentIndexer()
            force = bool(task.metadata.get("force"))
            result = indexer.index_file(task.file_path, force=force)
            if result.success:
                _get_logger().info(
                    "Document indexed successfully",
                    metadata={"task_id": task.id, "doc_id": result.doc_id, "word_count": result.word_count}
                )
                return True
            _get_logger().error(
                "Document indexing failed", metadata={"task_id": task.id, "error": result.error}
            )
            return False
        except Exception as e:
            _get_logger().error(
                "Exception during indexing", metadata={"task_id": task.id, "error": str(e)}
            )
            return False
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal."""
        _get_logger().info(f"Shutdown signal received ({signum})")
        self._shutdown_requested = True
    
    def _process_task(self, task: Task) -> bool:
        """Process a single task. Returns True if successful."""
        self.stats.last_task_id = task.id
        self.queue.mark_processing(task.id)
        _get_logger().info(
            "Starting task",
            metadata={"task_id": task.id, "file": Path(task.file_path).name, "type": task.task_type}
        )
        try:
            success = self.task_handler(task)
            if success:
                self.queue.mark_completed(task.id)
                self.stats.tasks_processed += 1
                self.stats.consecutive_errors = 0
                # Clear any past failure: the file is healthy again (US-086 v3).
                if self._failed_tracker:
                    self._failed_tracker.mark_success(task.file_path)
                _get_logger().info("Task completed", metadata={"task_id": task.id})
                return True
            raise Exception("Task handler returned False")
        except Exception as e:
            self.queue.mark_failed(task.id, str(e))
            self.stats.tasks_failed += 1
            self.stats.consecutive_errors += 1
            # Record the failure + bump the retry counter so the scanner's
            # reconciliation stops re-enqueuing it once the ceiling is hit.
            if self._failed_tracker:
                self._failed_tracker.add_failed_file(
                    task.file_path, str(e), reason="index_failed"
                )
                self._failed_tracker.increment_retry(task.file_path)
            _get_logger().error("Task failed", metadata={"task_id": task.id, "error": str(e)})
            return False
    
    def _reset_stuck_tasks(self) -> int:
        """Reset tasks stuck in 'processing' status (crash recovery)."""
        try:
            reset_count = self.queue.reset_stuck_tasks(
                timeout_seconds=self.worker_config.stuck_task_timeout
            )
            if reset_count > 0:
                _get_logger().info(f"Reset {reset_count} stuck tasks")
            return reset_count
        except Exception as e:
            _get_logger().error(f"Error resetting stuck tasks: {e}")
            return 0
    
    def _check_system_load(self) -> tuple:
        """Backward compat: check system load using module-level psutil."""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent >= self.worker_config.cpu_threshold:
                return False, f"CPU usage too high: {cpu_percent:.1f}%"
            return True, ""
        except Exception:
            return True, ""

    def _poll_and_process(self) -> bool:
        """Poll queue and process one task. Returns True if a task was processed."""
        self.stats.last_poll = datetime.now().isoformat()
        
        # Check system load
        can_process, reason = self._check_system_load()
        if not can_process:
            self.stats.is_paused = True
            self.stats.pause_reason = reason
            _get_logger().warning(f"Pausing: {reason}")
            return False
        
        self.stats.is_paused = False
        self.stats.pause_reason = None
        
        # Check for too many consecutive errors
        if self.stats.consecutive_errors >= self.worker_config.max_consecutive_errors:
            _get_logger().warning(
                f"Too many consecutive errors ({self.stats.consecutive_errors}), "
                f"pausing for {self.worker_config.error_pause_time}s"
            )
            time.sleep(self.worker_config.error_pause_time)
            self.stats.consecutive_errors = 0
        
        # ÉPIC-31 (US-111, absorbs US-093): batch_size<=1 is bit-identical to
        # pre-US-111 behaviour — no batching machinery even touched. A batch
        # that only found 1 pending task also falls back to the plain
        # single-task path (nothing to gain from grouping a single file).
        if self.worker_config.batch_size <= 1:
            task = self.queue.get_next_task()
            if task is None:
                _get_logger().debug("No pending tasks")
                return False
            return self._process_task(task)

        tasks = worker_batch.collect_batch(self, self.worker_config.batch_size)
        if not tasks:
            _get_logger().debug("No pending tasks")
            return False
        if len(tasks) == 1:
            return self._process_task(tasks[0])
        return worker_batch.process_batch(self, tasks)
    
    def run(self) -> None:
        """Run the worker in blocking mode until shutdown is requested."""
        _get_logger().info("Worker starting...")
        
        self._daemon.write_pid_file()
        self.stats.is_running = True
        self.stats.started_at = datetime.now().isoformat()
        self._reset_stuck_tasks()
        
        polls_since_stuck_check = 0
        stuck_check_interval = 10  # Check every 10 polls

        # Retry policy (US-086 v3): the real failed-files tracker is built here so
        # the daemon records failures and bounds re-enqueues; unit tests that call
        # _process_task directly keep a None tracker and stay side-effect free.
        if self._failed_tracker is None:
            self._failed_tracker = FailedFilesTracker()

        # Orphan reconciliation (US-086): give the scanner an oracle of the doc
        # ids actually present in the stores, so a file it has already 'seen' but
        # that never landed (failed index, unmounted volume) is re-enqueued
        # instead of skipped forever. A reused lightweight indexer answers it.
        inventory_indexer = DocumentIndexer(skip_chunking=True)

        def _index_inventory() -> set:
            # healthy_doc_ids (not just indexed_doc_ids): a doc stored with empty
            # content — a scanned file whose OCR yielded nothing — must be treated
            # as NOT done so reconciliation re-OCRs it (US-086 v1b).
            try:
                return inventory_indexer.healthy_doc_ids()
            except Exception as exc:
                _get_logger().warning(f"Index inventory failed: {exc}")
                return set()

        scanner = FilesystemScanner(index_inventory=_index_inventory)
        scan_interval_seconds = (
            self.config_manager.indexing.interval_minutes if self.config_manager else 10
        ) * 60
        last_scan_time = 0
        
        try:
            while not self._shutdown_requested:
                try:
                    current_time = time.time()
                    if current_time - last_scan_time >= scan_interval_seconds:
                        _get_logger().info("Starting periodic filesystem scan...")
                        scan_result = scanner.scan()
                        
                        if scan_result.has_changes:
                            # A genuine change is a fresh attempt: clear any past
                            # failure so the file gets a full retry budget again.
                            for fi in scan_result.modified_files:
                                self._failed_tracker.mark_success(str(fi.path))

                            # New + modified always enqueue. Reconciled (orphans)
                            # enqueue UNLESS they have exhausted their retries —
                            # a persistently-failing file is left for inspection
                            # rather than re-enqueued every scan.
                            reconciled_kept, given_up = self._select_reconciled(
                                scan_result.reconciled_files
                            )

                            to_enqueue = (
                                scan_result.new_files
                                + scan_result.modified_files
                                + reconciled_kept
                            )
                            for fi in to_enqueue:
                                self.queue.add_task(str(fi.path), task_type="index")
                            # US-28a note: trash marking happens inside
                            # scanner.scan() so CLI/API scans record it too
                            _get_logger().info(
                                "Scan complete",
                                metadata={
                                    "new": len(scan_result.new_files),
                                    "modified": len(scan_result.modified_files),
                                    "reconciled": len(reconciled_kept),
                                    "given_up": given_up,
                                    "skipped": scan_result.total_skipped,
                                    "deleted": len(scan_result.deleted_paths)
                                }
                            )
                        else:
                            _get_logger().info(
                                "Scan complete: no changes detected",
                                metadata={"skipped": scan_result.total_skipped}
                            )
                        # US-28b — purge runs on every periodic scan, changes
                        # or not: expired trash must not outlive a quiet system
                        try:
                            from aitao.indexation.trash import purge_expired
                            retention = (
                                self.config_manager.indexing.trash_retention_days
                                if self.config_manager else 30
                            )
                            purge_expired(retention)
                        except Exception as exc:
                            _get_logger().warning(f"Trash purge failed: {exc}")
                        # US-126-C — opt-in, runs on every periodic scan regardless
                        # of scan_result.has_changes: out-of-scope is a CONFIG
                        # change (include_paths edited), not something the
                        # filesystem scan detects. No-op unless explicitly
                        # enabled (default False, see _auto_prune_out_of_scope).
                        self._auto_prune_out_of_scope()
                        last_scan_time = current_time
                    
                    self._poll_and_process()
                    
                    # Periodically check for stuck tasks
                    polls_since_stuck_check += 1
                    if polls_since_stuck_check >= stuck_check_interval:
                        self._reset_stuck_tasks()
                        polls_since_stuck_check = 0
                        
                except Exception as e:
                    _get_logger().error(f"Error in poll loop: {e}")
                
                # Sleep in small increments for quick shutdown
                for _ in range(self.worker_config.poll_interval):
                    if self._shutdown_requested:
                        break
                    time.sleep(1)
            
            _get_logger().info("Worker shutting down gracefully...")
            
        finally:
            self.stats.is_running = False
            self._daemon.remove_pid_file()
            _get_logger().info(
                "Worker stopped",
                metadata={
                    "tasks_processed": self.stats.tasks_processed,
                    "tasks_failed": self.stats.tasks_failed
                }
            )
    
    def run_once(self) -> bool:
        """Process one task and return. Useful for testing."""
        return self._poll_and_process()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics."""
        return self.stats.to_dict()
    
    # -------------------------------------------------------------------------
    # Daemon management (delegated to WorkerDaemon)
    # -------------------------------------------------------------------------
    
    def is_running(self) -> bool:
        """Check if worker daemon is running."""
        if not self.pid_file.exists():
            return False
        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            return False
    
    def get_pid(self) -> Optional[int]:
        """Get worker PID if running."""
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except ValueError:
            return None
    
    def start_daemon(self) -> bool:
        """Start the worker as a background daemon."""
        return self._daemon.start_daemon()
    
    def stop_daemon(self, timeout: int = 10) -> bool:
        """Stop the running daemon."""
        return self._daemon.stop_daemon(timeout)

    # Backward compat: PID helpers using self.pid_file directly
    def _write_pid_file(self):
        """Write current process PID to self.pid_file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()))

    def _remove_pid_file(self):
        """Remove PID file on shutdown."""
        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
        except Exception:
            pass

