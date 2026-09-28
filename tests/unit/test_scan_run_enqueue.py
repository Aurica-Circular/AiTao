# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Test for the `scan run` enqueue fix (US-086 v2).

The scanner records a file as 'seen' the moment it detects it. A manual
`scan run` that saved state but did NOT enqueue therefore consumed the detection
and hid the file from the daemon. `scan run` now queues its new + modified files;
these tests pin that the right files are enqueued (and a dry run queues nothing).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aitao.cli.commands.scan import _enqueue_detected


def _fi(path):
    return SimpleNamespace(path=path)


def _result(new=None, modified=None):
    return SimpleNamespace(
        new_files=new or [],
        modified_files=modified or [],
    )


def test_enqueues_new_and_modified():
    result = _result(new=[_fi("/a.pdf")], modified=[_fi("/b.docx")])
    fake_queue = MagicMock()
    with patch("aitao.indexation.queue.TaskQueue", return_value=fake_queue) as ctor:
        count = _enqueue_detected("cfg.toml", result)

    assert count == 2
    ctor.assert_called_once_with(config_path="cfg.toml")
    enqueued = {c.args[0] for c in fake_queue.add_task.call_args_list}
    assert enqueued == {"/a.pdf", "/b.docx"}


def test_nothing_to_enqueue_does_not_touch_queue():
    result = _result()
    with patch("aitao.indexation.queue.TaskQueue") as ctor:
        count = _enqueue_detected("cfg.toml", result)

    assert count == 0
    ctor.assert_not_called()
