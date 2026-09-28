# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_search_config_engine.py — unit tests for the ÉPIC-31 (US-113) removal
# of the "[search] engine" rrf/fusion dev-only toggle (decision D1: 4.0 ships
# fusion-only). Covers: the per-stage semanticRatio defaults/independence
# (unaffected by the removal), the legacy-tolerance behaviour for a
# config.toml that still sets ``engine`` (soft warning, no crash — this
# machine's own config.toml still has it), and the new
# [search.embedding]/legacy [search.lancedb] fallback for the embedding
# model identity.

import logging
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.core.config_schema import SearchConfig, Settings  # noqa: E402


class TestNoMoreEngineField:
    def test_engine_is_not_a_real_field(self):
        """The dev-only rrf/fusion toggle is gone — fusion is the only engine."""
        assert not hasattr(SearchConfig(), "engine")

    def test_legacy_engine_key_is_ignored_without_crash(self):
        """A config.toml still setting [search] engine="fusion" (this
        machine's own config.toml does) must validate cleanly — extra="ignore"
        drops the unknown key, the before-validator only adds a log warning."""
        cfg = SearchConfig.model_validate({"engine": "fusion"})
        assert not hasattr(cfg, "engine")

    def test_legacy_engine_key_logs_a_soft_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="aitao.config"):
            SearchConfig.model_validate({"engine": "rrf"})
        assert any("engine" in rec.message.lower() for rec in caplog.records)

    def test_no_engine_key_logs_nothing(self, caplog):
        with caplog.at_level(logging.WARNING, logger="aitao.config"):
            SearchConfig.model_validate({})
        assert not any("engine" in rec.message.lower() for rec in caplog.records)


class TestSemanticRatioDefaults:
    def test_default_semantic_ratios(self):
        cfg = SearchConfig()
        assert cfg.semantic_ratio_chunks == 0.5
        assert cfg.semantic_ratio_documents == 0.5

    def test_ratios_are_independent(self):
        """US-106 volets 2/2bis: the optimal ratio does not transpose between
        stages (0.8 breaks exact-word recall at the document stage only) —
        the two fields must be settable independently of one another."""
        cfg = SearchConfig(semantic_ratio_chunks=0.8, semantic_ratio_documents=0.2)
        assert cfg.semantic_ratio_chunks == 0.8
        assert cfg.semantic_ratio_documents == 0.2

    def test_default_chunks_index_name(self):
        assert SearchConfig().meilisearch.chunks_index == "aitao_chunks"


class TestEmbeddingConfigNeutralSection:
    """US-113: model name/dimension moved from [search.lancedb] to the
    neutral [search.embedding], with a fallback to the legacy keys."""

    def test_defaults(self):
        cfg = SearchConfig()
        assert cfg.embedding.embedding_model == "BAAI/bge-m3"
        assert cfg.embedding.dimension == 1024

    def test_new_section_wins_when_present(self):
        cfg = SearchConfig.model_validate({
            "embedding": {"embedding_model": "new-model", "dimension": 42},
            "lancedb": {"embedding_model": "old-model", "dimension": 7},
        })
        assert cfg.embedding.embedding_model == "new-model"
        assert cfg.embedding.dimension == 42

    def test_falls_back_to_legacy_lancedb_keys_when_embedding_absent(self):
        """An existing v3.x-shaped config.toml (only [search.lancedb] set)
        must keep using its configured model/dimension unchanged."""
        cfg = SearchConfig.model_validate({
            "lancedb": {"embedding_model": "custom/model", "dimension": 768, "offline_mode": True},
        })
        assert cfg.embedding.embedding_model == "custom/model"
        assert cfg.embedding.dimension == 768
        assert cfg.embedding.offline_mode is True

    def test_legacy_table_name_still_available_for_migration(self):
        """search.migrate_v4 needs to know which LanceDB table to read from
        the pre-4.0 store — table_name stays on [search.lancedb]."""
        cfg = SearchConfig.model_validate({"lancedb": {"table_name": "aitao_embeddings"}})
        assert cfg.lancedb.table_name == "aitao_embeddings"


class TestFullSettingsToleratesRealMachineConfig:
    def test_settings_validates_with_legacy_engine_and_lancedb_section(self):
        """Mirrors this machine's actual config.toml shape: [search] engine
        + [search.lancedb] with the old key names — must not crash."""
        raw = {
            "search": {
                "engine": "fusion",
                "meilisearch": {"url": "http://localhost:7700"},
                "lancedb": {
                    "embedding_model": "BAAI/bge-m3",
                    "table_name": "aitao_embeddings",
                    "dimension": 1024,
                    "top_k": 20,
                    "min_score": 0.45,
                    "offline_mode": True,
                },
            }
        }
        settings = Settings.model_validate(raw)
        assert settings.search.embedding.embedding_model == "BAAI/bge-m3"
        assert settings.search.embedding.dimension == 1024
        assert settings.search.lancedb.table_name == "aitao_embeddings"
