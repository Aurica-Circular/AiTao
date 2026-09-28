# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_embedding_source.py — unit tests for the shared, LanceDB-free
# embedding source (ÉPIC-31, US-112: search/embedding_source.py) and its
# wiring into HybridSearchEngine.lancedb_client. Fusion is the ONLY engine
# since v4.0 (US-113, decision D1) — the property is unconditionally the
# shared embedder, no more "rrf" branch building a real LanceDBClient.
#
# No real SentenceTransformer is ever loaded: the model is faked, and
# get_shared_embedder's loader is patched where needed.

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search import embedding_source  # noqa: E402
from aitao.search.embedding_source import SharedEmbedder, get_shared_embedder  # noqa: E402


class _FakeModel:
    """Duck-typed SentenceTransformer: encode() + dimension query."""

    def __init__(self, dim=4):
        self._dim = dim
        self.encode_calls = []

    def get_sentence_embedding_dimension(self):
        return self._dim

    def encode(self, texts, convert_to_numpy=True):
        import numpy as np

        self.encode_calls.append(texts)
        if isinstance(texts, str):
            return np.ones(self._dim)
        return np.ones((len(texts), self._dim))


class TestSharedEmbedder:
    def test_embed_texts_returns_matrix(self):
        emb = SharedEmbedder(_FakeModel(dim=3))

        out = emb.embed_texts(["a", "b"])

        assert out.shape == (2, 3)

    def test_embed_text_single(self):
        emb = SharedEmbedder(_FakeModel(dim=3))

        out = emb._embed_text("hello")

        assert out == [1.0, 1.0, 1.0]

    def test_empty_text_with_allow_empty_returns_zero_vector(self):
        emb = SharedEmbedder(_FakeModel(dim=3))

        assert emb._embed_text("  ", allow_empty=True) == [0.0, 0.0, 0.0]

    def test_empty_text_without_allow_empty_raises(self):
        emb = SharedEmbedder(_FakeModel(dim=3))

        with pytest.raises(ValueError):
            emb._embed_text("")


class TestGetSharedEmbedder:
    def test_reads_model_name_from_config_and_caches(self):
        cfg = Mock()
        cfg.search.embedding.embedding_model = "fake/model"
        cfg.search.embedding.offline_mode = False
        fake = _FakeModel()

        embedding_source.reset_cache()
        try:
            with patch.object(embedding_source, "_load_model", return_value=fake) as loader:
                emb1 = get_shared_embedder(config=cfg)
                emb2 = get_shared_embedder(config=cfg)

            assert loader.call_count == 2  # loader itself handles caching
            assert loader.call_args.args == ("fake/model", False)
            assert emb1.dimension == 4
            assert emb2.dimension == 4
        finally:
            embedding_source.reset_cache()

    def test_model_cache_avoids_reload_for_same_name(self):
        embedding_source.reset_cache()
        try:
            fake = _FakeModel()
            with patch("sentence_transformers.SentenceTransformer", return_value=fake) as st:
                m1 = embedding_source._load_model("some/model", False)
                m2 = embedding_source._load_model("some/model", False)

            assert m1 is m2
            assert st.call_count == 1
        finally:
            embedding_source.reset_cache()


class TestFusionEngineUsesSharedEmbedder:
    """HybridSearchEngine.lancedb_client is unconditionally the shared
    embedder (ÉPIC-31, US-113: fusion is the only engine since v4.0, decision
    D1) — no LanceDBClient construction, no repository factory call. The
    "lancedb_client" name is kept only for API/test compatibility (see
    search/hybrid_engine.py's property docstring)."""

    def _fake_config(self):
        cfg = Mock()
        cfg.search.semantic_ratio_documents = 0.5
        return cfg

    def test_lazy_lancedb_client_is_shared_embedder(self):
        from aitao.search.hybrid_engine import HybridSearchEngine

        engine = HybridSearchEngine(config=self._fake_config())
        fake = SharedEmbedder(_FakeModel())

        with patch(
            "aitao.search.embedding_source.get_shared_embedder", return_value=fake
        ) as getter:
            client = engine.lancedb_client

        assert client is fake
        getter.assert_called_once()

    def test_injected_repo_bypasses_the_lazy_build(self):
        from aitao.search.hybrid_engine import HybridSearchEngine

        injected = Mock()
        engine = HybridSearchEngine(
            lancedb_repo=injected, config=self._fake_config()
        )

        assert engine.lancedb_client is injected

    def test_rag_engine_embed_texts_reaches_shared_embedder(self):
        """RAGEngine.embed_texts -> search_engine.lancedb_client.embed_texts:
        that client is always the shared embedder, so the grounding check
        works with zero LanceDB involvement."""
        from aitao.search.hybrid_engine import HybridSearchEngine
        from aitao.llm.rag_engine import RAGEngine

        fake = SharedEmbedder(_FakeModel(dim=4))
        engine = HybridSearchEngine(config=self._fake_config())
        with patch("aitao.search.embedding_source.get_shared_embedder", return_value=fake):
            _ = engine.lancedb_client  # trigger lazy build -> shared embedder

        rag = RAGEngine.__new__(RAGEngine)  # skip __init__ (config-heavy)
        rag._search_engine = engine
        rag.logger = Mock()

        out = rag.embed_texts(["one sentence", "another"])

        assert out.shape == (2, 4)
