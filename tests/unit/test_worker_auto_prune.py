# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_worker_auto_prune.py — US-126-C: opt-in auto-prune wiring.
#
# `BackgroundWorker._auto_prune_out_of_scope` is called on every periodic
# scan cycle; it must be a strict no-op (never build a Meilisearch client,
# never touch the index) unless `[indexing].auto_prune_out_of_scope` is
# explicitly True. These tests mock Meilisearch and path_manager entirely —
# no real Meilisearch, no real disk.

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.indexation.worker import BackgroundWorker  # noqa: E402


def _worker_with_flag(tmp_path, flag: bool) -> BackgroundWorker:
    """Build a BackgroundWorker with a fake config_manager carrying the flag.

    Bypasses ConfigManager/config.toml entirely so the test does not depend
    on the real (gitignored) live config — only `config_manager.indexing
    .auto_prune_out_of_scope` is read by the method under test.
    """
    from aitao.indexation.queue import TaskQueue

    queue = TaskQueue(queue_file=str(tmp_path / "queue" / "tasks.json"))
    worker = BackgroundWorker(queue=queue)
    worker.config_manager = SimpleNamespace(indexing=SimpleNamespace(auto_prune_out_of_scope=flag))
    return worker


class TestAutoPruneFlagOff:
    def test_default_off_never_builds_meilisearch_client(self, tmp_path):
        worker = _worker_with_flag(tmp_path, flag=False)

        with patch("aitao.storage.repository.make_meilisearch_client") as make_client, \
             patch("aitao.indexation.worker.path_manager.get_include_paths") as get_paths:
            worker._auto_prune_out_of_scope()

        make_client.assert_not_called()
        get_paths.assert_not_called()

    def test_no_config_manager_is_a_no_op(self, tmp_path):
        worker = _worker_with_flag(tmp_path, flag=False)
        worker.config_manager = None

        with patch("aitao.storage.repository.make_meilisearch_client") as make_client:
            worker._auto_prune_out_of_scope()

        make_client.assert_not_called()


class TestAutoPruneFlagOn:
    def test_enabled_calls_helper_with_meili_and_configured_roots(self, tmp_path):
        worker = _worker_with_flag(tmp_path, flag=True)
        fake_meili = MagicMock()

        with patch(
            "aitao.storage.repository.make_meilisearch_client", return_value=fake_meili
        ) as make_client, patch(
            "aitao.indexation.worker.path_manager.get_include_paths",
            return_value=["/Users/phil/Documents"],
        ) as get_paths, patch(
            "aitao.indexation.prune_runner.run_out_of_scope_prune", return_value=(0, 0)
        ) as run_prune:
            worker._auto_prune_out_of_scope()

        make_client.assert_called_once()
        get_paths.assert_called_once_with(existing_only=False)
        run_prune.assert_called_once()
        args, kwargs = run_prune.call_args
        assert args[0] is fake_meili
        assert args[1] == ["/Users/phil/Documents"]

    def test_enabled_but_helper_raises_does_not_propagate(self, tmp_path):
        worker = _worker_with_flag(tmp_path, flag=True)

        with patch(
            "aitao.storage.repository.make_meilisearch_client",
            side_effect=RuntimeError("connection refused"),
        ):
            # Must not raise: a failure here must never break the scan loop.
            worker._auto_prune_out_of_scope()
