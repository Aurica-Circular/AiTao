# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
US-088 — defensive guard: the indexer must refuse files under the system temp dir.

Root cause of the 2303 orphan chunks (2026-06-19): tests wrote documents whose
path was under tempfile.gettempdir() into the PROD stores; those temp paths later
vanished, leaving dead-path chunks served to the model.

This is the TEST-FIRST red checkpoint for US-088 step 1. Expected to FAIL on the
current code (the indexer accepts temp paths) and to pass once index_file refuses
any path under the system temp directory.

NOTE (next session): run this and align the assertion with the real IndexResult
API (success/skipped/reason fields) before implementing the guard.
"""

import tempfile
from pathlib import Path


def test_index_file_refuses_system_temp_path():
    """A file located under the system temp dir must not be indexed into prod stores."""
    from aitao.indexation.indexer import DocumentIndexer

    # A real file, but under tempfile.gettempdir() (the forbidden zone).
    temp_root = Path(tempfile.gettempdir())
    temp_file = temp_root / "aitao_us088_guard_probe.txt"
    temp_file.write_text("This is test content for indexing.", encoding="utf-8")
    try:
        result = DocumentIndexer().index_file(str(temp_file))
        # The guard must refuse: the file under the system temp dir is NOT indexed.
        # (Adjust to the real IndexResult API: e.g. result.success is False / skipped.)
        assert getattr(result, "success", True) is False, (
            "Indexer accepted a file under the system temp dir — temp paths must be "
            "refused so tests can never pollute the prod stores (US-088)."
        )
    finally:
        temp_file.unlink(missing_ok=True)
