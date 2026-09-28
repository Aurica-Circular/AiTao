# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for FailedFilesTracker (US-086 v3 retry policy; also US-099 gap).

The tracker records files that failed to index, counts retries and answers
whether a file has exhausted its retry budget — the signal the worker uses to
stop the scanner's reconciliation from re-enqueuing a hopeless file every scan.
These tests isolate it on a temp storage root so nothing touches prod state.
"""

import pytest

import aitao.core.failed_files_tracker as fft_module
from aitao.core.failed_files_tracker import FailedFilesTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """A tracker whose JSON lives under a temp storage root."""
    monkeypatch.setattr(
        fft_module.path_manager, "get_storage_root", lambda: tmp_path
    )
    return FailedFilesTracker()


class TestFailedFilesTracker:
    def test_add_then_present(self, tracker):
        tracker.add_failed_file("/a.pdf", "boom", reason="parse_error")
        assert "/a.pdf" in tracker.get_failed_files()
        assert tracker.get_stats()["total_failed"] == 1
        assert tracker.get_stats()["by_reason"]["parse_error"] == 1

    def test_retry_count_increments(self, tracker):
        tracker.add_failed_file("/a.pdf", "boom")
        tracker.increment_retry("/a.pdf")
        tracker.increment_retry("/a.pdf")
        assert tracker._failed_files["/a.pdf"]["retry_count"] == 2

    def test_is_exhausted_threshold(self, tracker):
        tracker.add_failed_file("/a.pdf", "boom")
        for _ in range(3):
            tracker.increment_retry("/a.pdf")
        assert tracker.is_exhausted("/a.pdf", max_retries=3) is True
        assert tracker.is_exhausted("/a.pdf", max_retries=4) is False

    def test_unknown_file_is_not_exhausted(self, tracker):
        assert tracker.is_exhausted("/never-seen.pdf") is False

    def test_mark_success_clears(self, tracker):
        tracker.add_failed_file("/a.pdf", "boom")
        tracker.increment_retry("/a.pdf")
        tracker.mark_success("/a.pdf")
        assert tracker.is_exhausted("/a.pdf") is False
        assert tracker.get_stats()["total_failed"] == 0

    def test_get_failed_files_respects_max_retries(self, tracker):
        tracker.add_failed_file("/a.pdf", "boom")
        for _ in range(3):
            tracker.increment_retry("/a.pdf")
        # Exhausted file is excluded from the retryable list.
        assert tracker.get_failed_files(max_retries=3) == []

    def test_persistence_round_trip(self, tracker, tmp_path, monkeypatch):
        tracker.add_failed_file("/a.pdf", "boom")
        tracker.increment_retry("/a.pdf")
        # A fresh tracker on the same storage root reloads the state.
        monkeypatch.setattr(
            fft_module.path_manager, "get_storage_root", lambda: tmp_path
        )
        reloaded = FailedFilesTracker()
        assert reloaded._failed_files["/a.pdf"]["retry_count"] == 1
