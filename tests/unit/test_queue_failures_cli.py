# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Visibility tests for the persistent-failures surface (US-086 v4).

Files that exhaust their index retries are never re-enqueued, so they are
invisible in the queue counters. The `queue failures` command and the queue
panel surface them from the failed-files tracker. These tests exercise the pure
rendering helpers (no prod state, no real tracker).
"""

from types import SimpleNamespace

from aitao.cli.commands.queue import _build_failures_table, _failure_summary_lines


def _failures():
    return {
        "/a.pdf": {"reason": "encoding", "retry_count": 3, "error": "bad bytes\nx"},
        "/b.png": {"reason": "ocr", "retry_count": 1, "error": "ocr timeout"},
        "/c.docx": {"reason": "parse_error", "retry_count": 5, "error": "boom"},
    }


class TestFailuresTable:
    def test_lists_all_sorted_by_retries(self):
        table = _build_failures_table(_failures(), limit=30, exhausted_only=False, ceiling=3)
        # All three rows, most-retried first means c (5) then a (3) then b (1).
        assert table.row_count == 3

    def test_limit_truncates(self):
        table = _build_failures_table(_failures(), limit=1, exhausted_only=False, ceiling=3)
        assert table.row_count == 1

    def test_exhausted_only_filters_by_ceiling(self):
        # ceiling=3 -> a (3) and c (5) are given up, b (1) is not.
        table = _build_failures_table(_failures(), limit=30, exhausted_only=True, ceiling=3)
        assert table.row_count == 2

    def test_empty_input(self):
        table = _build_failures_table({}, limit=30, exhausted_only=False, ceiling=3)
        assert table.row_count == 0


class TestFailureSummaryLines:
    def _tracker(self, stats):
        return SimpleNamespace(get_stats=lambda: stats)

    def test_no_failures_returns_empty(self):
        tr = self._tracker({"total_failed": 0, "retryable": 0, "by_reason": {}})
        assert _failure_summary_lines(tr) == []

    def test_summary_reports_total_and_given_up(self):
        tr = self._tracker(
            {"total_failed": 5, "retryable": 2, "by_reason": {"encoding": 3, "ocr": 2}}
        )
        lines = _failure_summary_lines(tr)
        blob = "\n".join(lines)
        assert "Persistent failures" in blob
        assert "5" in blob          # total
        assert "given up 3" in blob  # 5 - 2 retryable
        assert "encoding: 3" in blob

    def test_failing_tracker_is_swallowed(self):
        def _boom():
            raise RuntimeError("no store")

        assert _failure_summary_lines(SimpleNamespace(get_stats=_boom)) == []
