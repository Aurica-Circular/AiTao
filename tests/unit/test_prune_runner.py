# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_prune_runner.py — US-126-C: headless auto-prune orchestration.
#
# `run_out_of_scope_prune` is the I/O wrapper the worker's periodic scan calls
# when `[indexing].auto_prune_out_of_scope` is enabled. These tests mock the
# Meilisearch client entirely — no real Meilisearch, no real disk — and pin
# down the same reliability guardrails as US-126-A: empty config -> no-op,
# listing failure -> no-op (never raises), one bad delete doesn't stop others.

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.indexation.prune_runner import run_out_of_scope_prune  # noqa: E402


def _mock_meili(paths, delete_side_effect=None):
    meili = MagicMock()
    meili.get_all_document_paths.return_value = paths
    if delete_side_effect is not None:
        meili.delete_by_path.side_effect = delete_side_effect
    else:
        meili.delete_by_path.return_value = True
    return meili


class TestRunOutOfScopePrune:
    def test_path_under_configured_root_is_not_deleted(self):
        meili = _mock_meili(["/Users/phil/Documents/report.pdf"])

        deleted, failed = run_out_of_scope_prune(meili, ["/Users/phil/Documents"])

        meili.delete_by_path.assert_not_called()
        assert (deleted, failed) == (0, 0)

    def test_path_under_removed_root_is_deleted(self):
        meili = _mock_meili(["/Users/phil/OldProject/notes.txt"])

        deleted, failed = run_out_of_scope_prune(meili, ["/Users/phil/Documents"])

        meili.delete_by_path.assert_called_once_with("/Users/phil/OldProject/notes.txt")
        assert (deleted, failed) == (1, 0)

    def test_empty_configured_roots_deletes_nothing(self):
        meili = _mock_meili(["/Users/phil/OldProject/notes.txt"])

        deleted, failed = run_out_of_scope_prune(meili, [])

        meili.get_all_document_paths.assert_not_called()
        meili.delete_by_path.assert_not_called()
        assert (deleted, failed) == (0, 0)

    def test_listing_failure_returns_zero_and_does_not_raise(self):
        meili = MagicMock()
        meili.get_all_document_paths.side_effect = RuntimeError("Meilisearch down")

        deleted, failed = run_out_of_scope_prune(meili, ["/Users/phil/Documents"])

        meili.delete_by_path.assert_not_called()
        assert (deleted, failed) == (0, 0)

    def test_one_failing_delete_does_not_interrupt_the_rest(self):
        meili = _mock_meili(
            [
                "/Users/phil/OldProject/a.txt",
                "/Users/phil/OldProject/b.txt",
                "/Users/phil/OldProject/c.txt",
            ],
            delete_side_effect=[True, RuntimeError("boom"), True],
        )

        deleted, failed = run_out_of_scope_prune(meili, ["/Users/phil/Documents"])

        assert meili.delete_by_path.call_count == 3
        assert (deleted, failed) == (2, 1)

    def test_logger_is_optional_and_never_required(self):
        meili = _mock_meili(["/Users/phil/OldProject/notes.txt"])

        # No logger passed: must not raise.
        deleted, failed = run_out_of_scope_prune(meili, ["/Users/phil/Documents"], logger=None)

        assert (deleted, failed) == (1, 0)

    def test_logger_receives_info_on_completion(self):
        meili = _mock_meili(["/Users/phil/OldProject/notes.txt"])
        logger = MagicMock()

        run_out_of_scope_prune(meili, ["/Users/phil/Documents"], logger=logger)

        assert logger.info.called
