# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# extraction_timeout.py — per-file extraction timeout (ÉPIC-31, US-113 volet 2).
#
# Finding (b): a pathological file (a vector-drawing PDF with no text layer,
# on a Box CloudStorage mount) froze the whole worker for 1h22 with ZERO CPU
# usage — a blocking I/O wait (native OCR call or a cloud placeholder file
# blocking on read() while being materialized), not a slow computation. One
# such file could freeze a fresh installation's very first scan forever.
#
# Approach chosen: run extraction in a daemon THREAD and join() it with a
# timeout, rather than a separate PROCESS. Justification:
#   - The observed failure is a blocking I/O wait, not a CPU-bound loop —
#     thread.join(timeout) already returns control to the caller the moment
#     the timeout elapses, regardless of what the target thread is blocked
#     on. The user-visible symptom (worker stuck) is fully fixed by this.
#   - A subprocess-per-file would guarantee a HARD kill (OS-level
#     termination works even on a blocked syscall, which a thread cannot
#     do), but at the cost of re-importing the whole extraction/OCR stack
#     (PDF parser, python-docx, sentence-transformers, macOS Vision/
#     Tesseract bindings...) for EVERY file — a real throughput regression
#     for the overwhelmingly common case (default batch_size=1, one file at
#     a time), for a benefit that only matters on the rare pathological file.
#   - Known, accepted trade-off: a thread stuck forever on a blocking call is
#     NOT forcibly killed (Python has no safe cross-platform API to cancel a
#     blocked thread) — it leaks as a "zombie" daemon thread for the rest of
#     the worker process's life. Being a daemon thread, it never blocks
#     process shutdown, and the worker itself keeps making progress on the
#     next files immediately after the timeout — which is the actual
#     incident this fixes. A worker that hits this repeatedly (many
#     pathological files) accumulates threads; the daemon is expected to be
#     restarted periodically in normal operation (see WorkerDaemon), which
#     clears them.

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aitao.indexation.text_extractor import ExtractionResult, TextExtractor


class ExtractionTimeoutError(Exception):
    """Raised when a file's extraction exceeds the configured timeout."""


def extract_with_timeout(
    extractor: "TextExtractor", path: Path, timeout_s: float
) -> "ExtractionResult":
    """Run ``extractor.extract(path)`` bounded by ``timeout_s`` seconds.

    Raises ``ExtractionTimeoutError`` if the extraction is still running when
    the timeout elapses — the caller (``item_preparer.prepare_item``) turns
    that into a failed ``IndexResult`` with an explicit "extraction timeout"
    error, exactly like any other extraction failure, so the worker moves on
    to the next file instead of staying stuck.

    ``TextExtractor.extract()`` never raises (it catches its own errors and
    returns a failed ``ExtractionResult`` — see text_extractor.py); this
    wrapper still guards the call defensively in case a future extractor
    plugin does not honour that contract.
    """
    outcome: dict = {}

    def _run() -> None:
        try:
            outcome["result"] = extractor.extract(path)
        except Exception as exc:  # pragma: no cover - defensive, see docstring
            outcome["error"] = exc

    thread = threading.Thread(
        target=_run, name=f"extract-timeout-{path.name}", daemon=True,
    )
    thread.start()
    thread.join(timeout_s)

    if thread.is_alive():
        raise ExtractionTimeoutError(
            f"Extraction exceeded {timeout_s}s (still running in background): {path}"
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]
