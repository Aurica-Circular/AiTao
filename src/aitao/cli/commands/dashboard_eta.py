# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Dashboard ETA estimation helpers.

This module computes a pragmatic indexing ETA for the dashboard by combining:
- Source inventory (eligible vs unsupported files)
- Already indexed source paths (Meilisearch)
- Recent worker throughput and per-extension durations

Design goals:
- Auto-adapt to future extension support changes from scanner config
- Auto-adapt to source path changes from config
- Keep calculations resilient (best-effort, never raise to caller)
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp safely and return timezone-aware datetime."""
    if not ts:
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _inventory_sources(scanner: Any) -> Dict[str, Any]:
    """Inventory source files using scanner rules and dynamic supported extensions."""
    eligible_paths: set[str] = set()
    unsupported_by_ext: Counter[str] = Counter()

    for root in scanner.include_paths:
        if not root.exists() or not root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            # Apply scanner directory exclusion policy dynamically.
            dirnames[:] = [d for d in dirnames if not scanner._should_skip_dir(d)]

            for name in filenames:
                file_path = Path(dirpath) / name
                ext = file_path.suffix.lower() or "(noext)"

                # Keep explicit excludes out of ETA scope.
                if name.startswith("."):
                    continue
                if name in scanner.exclude_files:
                    continue
                if ext in scanner.exclude_extensions:
                    continue

                # Unsupported format: kept for diagnostics, excluded from ETA workload.
                if ext not in scanner.supported_extensions:
                    unsupported_by_ext[ext] += 1
                    continue

                eligible_paths.add(str(file_path))

    return {
        "eligible_paths": eligible_paths,
        "eligible_count": len(eligible_paths),
        "unsupported_count": sum(unsupported_by_ext.values()),
        "unsupported_by_ext": dict(unsupported_by_ext.most_common(8)),
    }


def _recent_worker_metrics(queue: Any, remaining_by_ext: Dict[str, int], window_minutes: int) -> Dict[str, Any]:
    """Compute recent processing speed and weighted ETA from completed tasks."""
    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    completed_tasks = queue.list_tasks(status="completed", limit=5000)

    recent_completed = 0
    recent_durations: list[float] = []
    durations_by_ext: dict[str, list[float]] = defaultdict(list)

    for task in completed_tasks:
        completed_at = _parse_iso(getattr(task, "completed_at", None))
        started_at = _parse_iso(getattr(task, "started_at", None))

        if not completed_at or completed_at < recent_cutoff:
            continue

        recent_completed += 1

        if started_at and completed_at >= started_at:
            duration_s = (completed_at - started_at).total_seconds()
            if duration_s > 0:
                recent_durations.append(duration_s)
                ext = Path(task.file_path).suffix.lower() or "(noext)"
                durations_by_ext[ext].append(duration_s)

    rate_docs_per_min = recent_completed / max(window_minutes, 1)

    global_avg_s = None
    if recent_durations:
        global_avg_s = sum(recent_durations) / len(recent_durations)

    eta_seconds = None
    if remaining_by_ext:
        weighted_total = 0.0
        for ext, count in remaining_by_ext.items():
            if count <= 0:
                continue
            ext_samples = durations_by_ext.get(ext, [])
            if len(ext_samples) >= 3:
                avg_s = sum(ext_samples) / len(ext_samples)
            elif global_avg_s is not None:
                avg_s = global_avg_s
            else:
                avg_s = None

            if avg_s is None:
                weighted_total = None
                break
            weighted_total += count * avg_s

        if weighted_total is not None:
            eta_seconds = int(weighted_total)

    if eta_seconds is None and rate_docs_per_min > 0:
        total_remaining = sum(remaining_by_ext.values())
        eta_seconds = int((total_remaining / rate_docs_per_min) * 60)

    sample_size = len(recent_durations)
    if sample_size >= 30:
        confidence = "high"
    elif sample_size >= 10:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "window_minutes": window_minutes,
        "recent_completed": recent_completed,
        "rate_docs_per_min": rate_docs_per_min,
        "eta_seconds": eta_seconds,
        "confidence": confidence,
        "sample_size": sample_size,
    }


def _fmt_eta(eta_seconds: Optional[int]) -> str:
    """Format ETA seconds into a compact human-friendly string."""
    if eta_seconds is None:
        return "unknown"
    if eta_seconds < 60:
        return f"{eta_seconds}s"
    minutes = eta_seconds // 60
    if minutes < 60:
        return f"~{minutes} min"
    hours = minutes // 60
    rem_min = minutes % 60
    return f"~{hours}h{rem_min:02d}"


def build_eta_snapshot(ms_client: Any = None) -> Dict[str, Any]:
    """Return an ETA snapshot for dashboard rendering (best-effort)."""
    from aitao.indexation.scanner import FilesystemScanner
    from aitao.indexation.queue import TaskQueue

    scanner = FilesystemScanner()
    source = _inventory_sources(scanner)

    indexed_paths: set[str] = set()
    if ms_client is not None:
        try:
            indexed_paths = set(ms_client.get_all_document_paths())
        except Exception:
            indexed_paths = set()

    eligible_paths = source["eligible_paths"]
    remaining_paths = eligible_paths - indexed_paths

    remaining_by_ext: Counter[str] = Counter(
        (Path(p).suffix.lower() or "(noext)") for p in remaining_paths
    )

    queue = TaskQueue()
    queue_stats = queue.get_stats()

    metrics = _recent_worker_metrics(
        queue=queue,
        remaining_by_ext=dict(remaining_by_ext),
        window_minutes=15,
    )

    return {
        "source_eligible": source["eligible_count"],
        "source_unsupported": source["unsupported_count"],
        "unsupported_by_ext": source["unsupported_by_ext"],
        "indexed_in_source": len(indexed_paths & eligible_paths),
        "remaining_in_source": len(remaining_paths),
        "remaining_by_ext": dict(remaining_by_ext.most_common(5)),
        "pending": queue_stats.get("pending", 0),
        "processing": queue_stats.get("processing", 0),
        "rate_docs_per_min": metrics["rate_docs_per_min"],
        "recent_completed": metrics["recent_completed"],
        "window_minutes": metrics["window_minutes"],
        "eta_seconds": metrics["eta_seconds"],
        "eta_human": _fmt_eta(metrics["eta_seconds"]),
        "confidence": metrics["confidence"],
        "sample_size": metrics["sample_size"],
    }
