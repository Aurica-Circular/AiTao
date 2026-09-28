# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
US-088 volet 2 — the RAG must never feed a system-temp (dead/test) path to the model.

Defence-in-depth with the indexer guard (volet 1): even if a temp-path chunk leaked
into a store, the context builder drops it. Scoped to the system temp dir so it does
not hide legitimately deleted files kept by the trash feature (US-28). Hermetic: tests
the pure quarantine predicate, no stores/services.
"""

import os
import tempfile

from aitao.llm.rag_engine import RAGEngine


def test_temp_path_is_quarantined():
    probe = os.path.join(tempfile.gettempdir(), "aitao_probe.txt")
    assert RAGEngine._is_quarantined_path(probe) is True


def test_real_path_is_not_quarantined():
    assert RAGEngine._is_quarantined_path(str(os.path.expanduser("~"))) is False
    assert RAGEngine._is_quarantined_path("") is False
