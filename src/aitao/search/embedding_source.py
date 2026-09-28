# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# embedding_source.py — shared bge-m3 text-embedding source, decoupled from
# LanceDB (ÉPIC-31, US-112).
#
# Two consumers used to reach into search.lancedb_client.LanceDBClient purely
# for its already-loaded SentenceTransformer model, with no interest at all in
# its LanceDB table:
#   - llm.rag_engine.RAGEngine.embed_texts() — used by answer_validator's
#     post-generation grounding check to embed answer sentences/context chunks
#     for comparison. Pure inference, nothing to do with vector storage.
#   - search.search_executors.search_fusion_sync() — the document-stage fusion
#     query vector (via HybridSearchEngine.lancedb_client._embed_text()).
#
# Under [search] engine="fusion" this forced HybridSearchEngine to build a
# real LanceDBClient (disk connection + model load) just to reach
# ._embed_text()/.embed_texts() — the ONLY direct LanceDB dependency left in
# the reliability layer (see EPIC-31-fusion-v4/BRIEF-CADRAGE.md §B.3). This
# module provides the same two methods, loading bge-m3 the same way
# LanceDBClient/ChunkStore/MeiliChunkStore already do (same config key, same
# offline-mode handling), with no LanceDB import and no on-disk table.
#
# The model instance is cached at module scope (process-wide) so repeated
# calls (one per grounding check, one per fusion document query) never reload
# it — mirroring the "load once" intent of the classes it replaces here.

from __future__ import annotations

from typing import Any, List, Optional, Sequence

_cached_model: Any = None
_cached_model_name: Optional[str] = None


def _load_model(model_name: str, offline_mode: bool) -> Any:
    """Load (once) the SentenceTransformer backing this module's embeddings.

    Mirrors ``search.lancedb_client.LanceDBClient.__init__``'s own loading —
    same HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE handling — so a config change to
    the embedding model name is honoured identically either way. Re-loads only
    when the requested model name differs from the cached one (a config
    change mid-process, e.g. across tests).
    """
    global _cached_model, _cached_model_name
    if _cached_model is not None and _cached_model_name == model_name:
        return _cached_model

    from sentence_transformers import SentenceTransformer

    if offline_mode:
        import os

        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    _cached_model = SentenceTransformer(model_name, local_files_only=offline_mode)
    _cached_model_name = model_name
    return _cached_model


def reset_cache() -> None:
    """Drop the cached model (tests only — forces the next call to reload)."""
    global _cached_model, _cached_model_name
    _cached_model = None
    _cached_model_name = None


class SharedEmbedder:
    """``embed_texts()``/``_embed_text()`` facade, backend-agnostic.

    Duck-types the subset of ``search.lancedb_client.LanceDBClient`` that
    ``RAGEngine.embed_texts`` and ``search_executors.search_fusion_sync``
    actually consume — nothing else (no ``.search()``, no table access).
    """

    def __init__(self, model: Any):
        self._model = model
        self.dimension = model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: Sequence[str]) -> Any:
        """Encode a batch of texts. Returns a (len(texts), dim) numpy array."""
        return self._model.encode(list(texts), convert_to_numpy=True)

    def _embed_text(self, text: str, allow_empty: bool = False) -> List[float]:
        """Encode one text. Zero vector on empty text when ``allow_empty``."""
        if not text or not text.strip():
            if allow_empty:
                return [0.0] * self.dimension
            raise ValueError("Cannot embed empty text")
        return self._model.encode(text, convert_to_numpy=True).tolist()


def get_shared_embedder(config: Optional[Any] = None) -> SharedEmbedder:
    """Build (or reuse) the shared embedder for the current config.

    ``config``: a ``ConfigManager`` (or None to read the global singleton) —
    only ``search.embedding.embedding_model``/``offline_mode`` are read (ÉPIC-31,
    US-113: moved out of the former ``[search.lancedb]`` section, which no
    longer describes a real store; only the model identity is shared with
    MeiliChunkStore by convention, so an embedding computed here is identical
    to one computed there).
    """
    from aitao.core.config import get_config

    cfg = config or get_config()
    emb = cfg.search.embedding
    model = _load_model(emb.embedding_model, emb.offline_mode)
    return SharedEmbedder(model)
