# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# chunk_store_meili.py — Meilisearch-backed excerpt store, THE chunk backend
# since v4.0 (ÉPIC-31, US-110/US-113 — fusion-only, decision D1).
#
# Owns the dedicated Meilisearch CHUNK index (primaryKey "chunk_id", embedder
# "default" configured as userProvided, dim = search.embedding.dimension): a
# single native Meilisearch hybrid call (index.search(query, {"hybrid": ...,
# "vector": ...})) is the chat/RAG retrieval path — no fan-out, no RRF merge.
# Vectors are embedded once, locally, with bge-m3 (US-110 point 4 — no new
# embedding stack) and pushed verbatim; Meilisearch never re-embeds.
#
# Mirrors the subset of the former LanceDB ChunkStore's interface actually
# consumed by indexation.indexer_helpers.chunk_and_store() and
# search.search_executors.search_chunks(): add_chunks(), delete_by_doc_id(),
# search(). Reuses MeilisearchAdminMixin (settings/embedder/task-wait) rather
# than duplicating that plumbing — see search/meilisearch_admin.py.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from meilisearch import Client
    from meilisearch.errors import MeilisearchApiError
    MEILISEARCH_AVAILABLE = True
except ImportError:
    MEILISEARCH_AVAILABLE = False
    Client = None
    MeilisearchApiError = Exception

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from aitao.core.config import ConfigManager
from aitao.core.logger import get_logger
from aitao.indexation.interfaces import Chunk
from aitao.search.meilisearch_admin import MeilisearchAdminMixin, US094_SETTINGS, wait_for_task_status


class MeiliChunkStoreError(Exception):
    """Base exception for MeiliChunkStore operations."""


class MeiliChunkStore(MeilisearchAdminMixin):
    """Meilisearch-backed chunk store for the fusion engine (ÉPIC-31, US-110)."""

    EMBEDDER_NAME = "default"
    SEARCHABLE_ATTRIBUTES = ["title", "content", "path"]
    FILTERABLE_ATTRIBUTES = ["doc_id"]

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        config: Optional[ConfigManager] = None,
        dimension: Optional[int] = None,
        embedding_model: Optional[str] = None,
        semantic_ratio: Optional[float] = None,
    ):
        if not MEILISEARCH_AVAILABLE:
            raise MeiliChunkStoreError(
                "meilisearch-python package not installed. Run: uv pip install meilisearch"
            )

        self.logger = get_logger("meilisearch.chunks")

        try:
            self._config = config or ConfigManager("config/config.toml")
        except Exception:
            self._config = None

        ms = self._config.search.meilisearch if self._config else None
        self.host = url or (ms.url if ms else "http://localhost:7700")
        config_api_key = (ms.api_key or None) if ms else None
        self.api_key = api_key or config_api_key
        self.index_name = index_name or (ms.chunks_index if ms else "aitao_chunks")

        self.dimension = dimension or (
            self._config.search.embedding.dimension if self._config else 1024
        )
        self._semantic_ratio = (
            semantic_ratio
            if semantic_ratio is not None
            else (self._config.search.semantic_ratio_chunks if self._config else 0.5)
        )

        self._embedding_model_name = embedding_model or (
            self._config.search.embedding.embedding_model if self._config else "BAAI/bge-m3"
        )
        if SentenceTransformer is None:
            raise MeiliChunkStoreError("sentence-transformers not installed")
        self.logger.info(f"Loading embedding model: {self._embedding_model_name}")
        self._embedding_model = SentenceTransformer(self._embedding_model_name)

        try:
            self.client = Client(self.host, self.api_key)
        except Exception as e:
            raise MeiliChunkStoreError(f"Connection failed: {e}")

        self._ensure_index()

        self.logger.info(
            "MeiliChunkStore initialized",
            metadata={"index": self.index_name, "dimension": self.dimension},
        )

    def _wait_for_task(self, task_uid: int, timeout_ms: int = 30000) -> Dict[str, Any]:
        return wait_for_task_status(self.client, task_uid, self.logger, timeout_ms)

    def _ensure_index(self) -> None:
        """Create the chunk index (once) with searchable/filterable attrs and
        the userProvided embedder. An already-existing index is reused as-is
        (settings are applied only at creation, matching MeilisearchClient's
        pattern — see meilisearch_client.py:_ensure_index)."""
        try:
            self.index = self.client.get_index(self.index_name)
            self.logger.debug("Using existing chunk index", metadata={"index": self.index_name})
            return
        except MeilisearchApiError as e:
            if "index_not_found" not in str(e):
                raise MeiliChunkStoreError(f"Failed to ensure chunk index: {e}")
        except Exception as e:
            raise MeiliChunkStoreError(f"Failed to ensure chunk index: {e}")

        task = self.client.create_index(self.index_name, {"primaryKey": "chunk_id"})
        self._wait_for_task(task.task_uid, timeout_ms=30000)
        self.index = self.client.get_index(self.index_name)

        self.update_settings({
            "searchableAttributes": self.SEARCHABLE_ATTRIBUTES,
            "filterableAttributes": self.FILTERABLE_ATTRIBUTES,
            "typoTolerance": {
                "enabled": True,
                "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
            },
            # US-094 (ÉPIC-31, absorbed into US-111) — same ingestion-speed +
            # multilingual settings as the document index; see
            # search/meilisearch_admin.py:US094_SETTINGS for the rationale
            # and the golden-bench gate each key is subject to.
            **US094_SETTINGS,
        })
        self.configure_embedder(self.dimension, self.EMBEDDER_NAME)
        self.logger.info("Created chunk index with embedder", metadata={"index": self.index_name})

    def _embed_text(self, text: str) -> List[float]:
        """Generate an embedding vector for text (mirrors ChunkStore._embed_text)."""
        if not text or not text.strip():
            return [0.0] * self.dimension
        embedding = self._embedding_model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_document(self, text: str) -> List[float]:
        """Public embedding hook for the doc-level vector (ÉPIC-31, US-111).

        The indexer calls this to compute a document's whole-content vector
        ONCE, reusing this store's already-loaded bge-m3 model — instead of
        loading a second SentenceTransformer instance (LanceDBClient's own)
        just to embed the same text again. The result is reused for BOTH the
        LanceDB doc-level write (still active during the branch) and the
        Meilisearch document `_vectors` (replacing the US-110 null opt-out).
        """
        return self._embed_text(text)

    def add_chunks(self, chunks: List[Chunk]) -> int:
        """Add multiple chunks (embedding any that arrive without a vector)."""
        if not chunks:
            return 0

        records = []
        for chunk in chunks:
            vector = chunk.embedding if chunk.embedding is not None else self._embed_text(chunk.content)
            vector = vector.tolist() if hasattr(vector, "tolist") else list(vector)
            records.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title or "",
                "path": chunk.path or "",
                "content": chunk.content or "",
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
                "_vectors": {self.EMBEDDER_NAME: vector},
            })

        try:
            task = self.index.add_documents(records)
            result = self._wait_for_task(task.task_uid, timeout_ms=120000)
            if result.get("status") == "failed":
                raise MeiliChunkStoreError(f"add_chunks failed: {result.get('error')}")
            doc_ids = {c.doc_id for c in chunks}
            label = next(iter(doc_ids)) if len(doc_ids) == 1 else f"{len(doc_ids)} docs"
            self.logger.info(f"Added {len(records)} chunks for doc_id={label}")
            return len(records)
        except MeilisearchApiError as e:
            raise MeiliChunkStoreError(f"Failed to add chunks: {e}")

    def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all chunks for a document. Returns 0 (task is async; unlike
        LanceDB's synchronous delete, an exact pre/post count is not cheap to
        obtain here and no caller currently relies on the return value)."""
        try:
            task = self.index.delete_documents(filter=f'doc_id = "{doc_id}"')
            self._wait_for_task(task.task_uid)
            return 0
        except Exception as e:
            self.logger.error(f"Failed to delete chunks for doc_id={doc_id}: {e}")
            return 0

    def delete_by_doc_ids(self, doc_ids: List[str]) -> int:
        """Delete all chunks for MULTIPLE documents in ONE Meilisearch task
        (ÉPIC-31, US-111/US-093): the batched ingestion path's precondition
        for adding a group's new chunks — see indexation.batch_indexer.
        Raises on failure (unlike delete_by_doc_id) so the caller can exclude
        the WHOLE group from the add step rather than risk old+new chunks
        coexisting for a file ("1 file = 1 state")."""
        if not doc_ids:
            return 0
        ids_filter = ", ".join(f'"{doc_id}"' for doc_id in doc_ids)
        task = self.index.delete_documents(filter=f"doc_id IN [{ids_filter}]")
        result = self._wait_for_task(task.task_uid, timeout_ms=60000)
        if result.get("status") != "succeeded":
            raise MeiliChunkStoreError(
                f"delete_by_doc_ids failed: {result.get('error', result.get('status'))}"
            )
        return 0

    def count_chunks(self, doc_id: Optional[str] = None) -> int:
        """Count chunks in the store, optionally filtered by doc_id."""
        try:
            if doc_id:
                result = self.index.search("", {"filter": f'doc_id = "{doc_id}"', "limit": 0})
                return int(result.get("estimatedTotalHits", 0))
            stats = self.index.get_stats()
            return int(getattr(stats, "number_of_documents", 0))
        except Exception as e:
            self.logger.error(f"Failed to count chunks: {e}")
            return 0

    def search(
        self,
        query: str,
        limit: int = 10,
        doc_id: Optional[str] = None,
        min_score: float = 0.0,
    ) -> List[Tuple[Chunk, float]]:
        """Single native hybrid search (US-110 point 2). Returns (Chunk, score)
        tuples like ChunkStore.search(), score = Meilisearch's own
        _rankingScore (0-1) for the hybrid query, the best available relevance
        proxy without re-implementing scoring."""
        try:
            query_vector = self._embed_text(query)
            opts: Dict[str, Any] = {
                "hybrid": {"semanticRatio": self._semantic_ratio, "embedder": self.EMBEDDER_NAME},
                "vector": query_vector,
                "limit": limit,
                "showRankingScore": True,
            }
            if doc_id:
                opts["filter"] = f'doc_id = "{doc_id}"'

            result = self.index.search(query, opts)
        except Exception as e:
            self.logger.error(f"Chunk hybrid search failed: {e}")
            return []

        chunks_with_scores: List[Tuple[Chunk, float]] = []
        for hit in result.get("hits", []):
            score = float(hit.get("_rankingScore", 0.0))
            if score < min_score:
                continue
            content = hit.get("content") or ""
            chunk = Chunk(
                chunk_id=hit.get("chunk_id", ""),
                doc_id=hit.get("doc_id", ""),
                path=hit.get("path", ""),
                title=hit.get("title", ""),
                content=content,
                chunk_index=hit.get("chunk_index", 0),
                total_chunks=hit.get("total_chunks", 1),
                offset_start=0,
                offset_end=len(content),
            )
            chunks_with_scores.append((chunk, score))

        self.logger.debug(
            f"Chunk hybrid search returned {len(chunks_with_scores)} chunks for query: {query[:50]}..."
        )
        return chunks_with_scores
