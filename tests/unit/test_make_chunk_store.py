# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_make_chunk_store.py — unit tests for storage.repository.make_chunk_store
# (ÉPIC-31, US-111, simplified US-113): the single construction point for the
# excerpt (chunk) store — always ``indexation.chunk_store_meili.MeiliChunkStore``
# since v4.0 ships fusion-only (decision D1; the former LanceDB-backed
# ``ChunkStore`` was removed), same pattern as ``make_meilisearch_client``.
#
# Mocked at the class-constructor level (monkeypatch on the lazily-imported
# MeiliChunkStore) — no real Meilisearch/embedding model.

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aitao.storage.repository as repo  # noqa: E402


class _FakeConfig:
    """Stand-in ConfigManager — make_chunk_store forwards it verbatim to
    MeiliChunkStore (which reads its own config sections lazily), unlike the
    pre-4.0 LanceDB ChunkStore, which took a plain ChunkingConfig instead."""


def test_builds_meili_chunk_store(monkeypatch):
    fake_meili_cls = MagicMock()
    monkeypatch.setattr("aitao.indexation.chunk_store_meili.MeiliChunkStore", fake_meili_cls)

    cfg = _FakeConfig()
    result = repo.make_chunk_store(config=cfg, index_name="test_chunks")

    fake_meili_cls.assert_called_once_with(config=cfg, index_name="test_chunks")
    assert result is fake_meili_cls.return_value


def test_default_config_uses_get_config(monkeypatch):
    fake_meili_cls = MagicMock()
    monkeypatch.setattr("aitao.indexation.chunk_store_meili.MeiliChunkStore", fake_meili_cls)
    fake_config = _FakeConfig()
    # make_chunk_store imports get_config lazily from core.config — patch there.
    import aitao.core.config as core_config_mod

    monkeypatch.setattr(core_config_mod, "get_config", lambda: fake_config)

    repo.make_chunk_store()

    fake_meili_cls.assert_called_once_with(config=fake_config)
