# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Meilisearch admin and maintenance operations.

This module provides a mixin class with admin/maintenance methods for
the MeilisearchClient: index statistics, health checks, settings
management, document listing, and bulk operations.

Also provides wait_for_task_status(), a small standalone helper factored out
so both MeilisearchClient and indexation.chunk_store_meili.MeiliChunkStore
(ÉPIC-31, US-110) can wait on async tasks without duplicating the Task-object
normalization logic.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from meilisearch.errors import MeilisearchApiError
except ImportError:
    MeilisearchApiError = Exception

try:
    from aitao.core.registry import StatsKeys
except ImportError:
    from aitao.core.registry import StatsKeys

from aitao.search.meilisearch_settings import (  # noqa: F401  — re-exported (meilisearch_client.py, chunk_store_meili.py import US094_SETTINGS from here)
    MeilisearchSettingsMixin,
    US094_SETTINGS,
)


def wait_for_task_status(client: Any, task_uid: int, logger: Any, timeout_ms: int = 30000) -> Dict[str, Any]:
    """Wait for a Meilisearch async task and return its status as a plain dict.

    Shared by MeilisearchClient._wait_for_task and MeiliChunkStore._wait_for_task
    (ÉPIC-31, US-110) — normalizes across meilisearch-python SDK versions, whose
    wait_for_task() has returned a plain dict, a .dict()-able, or a Pydantic
    model depending on version.
    """
    try:
        task = client.wait_for_task(task_uid, timeout_ms)
        if hasattr(task, "model_dump"):
            return task.model_dump()
        if hasattr(task, "dict"):
            return task.dict()
        if isinstance(task, dict):
            return task
        return {"status": getattr(task, "status", "unknown")}
    except Exception as e:
        logger.warning(
            "Task wait timeout",
            metadata={"task_uid": task_uid, "error": str(e)},
        )
        return {"status": "timeout"}


# Default index settings for aitao documents
DEFAULT_SETTINGS: Dict[str, Any] = {
    "searchableAttributes": [
        "title",
        "content",
        "path",
    ],
    "filterableAttributes": [
        "category",
        "language",
        "file_type",
        "created_at",
        # US-113 — cheap "has this document got real content" flag (replaces
        # the old LanceDB-based healthy_doc_ids oracle, see
        # MeilisearchClient.healthy_doc_ids()/get_incomplete_document_paths()).
        "has_content",
    ],
    "sortableAttributes": [
        "created_at",
        "file_size",
        "title",
    ],
    "rankingRules": [
        "words",
        "typo",
        "proximity",
        "attribute",
        "sort",
        "exactness",
    ],
    "typoTolerance": {
        "enabled": True,
        "minWordSizeForTypos": {
            "oneTypo": 4,
            "twoTypos": 8,
        },
    },
    "pagination": {
        "maxTotalHits": 5000,
    },
}


class MeilisearchAdminMixin(MeilisearchSettingsMixin):
    """
    Mixin providing admin and maintenance methods for MeilisearchClient.

    Expects the host class to provide: client, index, index_name, host,
    logger, _wait_for_task(), and the MeilisearchError exception.

    Inherits ``apply_ingestion_settings()`` from ``MeilisearchSettingsMixin``
    (ÉPIC-31, US-094/US-111) so every host class (MeilisearchClient,
    MeiliChunkStore) gets it for free.
    """

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the index.

        Returns:
            Dict with document count, field distribution, etc.
        """
        from aitao.search.meilisearch_client import MeilisearchError

        try:
            stats = self.index.get_stats()

            # meilisearch-python 0.40+ returns IndexStats Pydantic model
            # Convert to dict or access attributes directly
            if hasattr(stats, 'model_dump'):
                stats_dict = stats.model_dump()
            elif hasattr(stats, 'dict'):
                stats_dict = stats.dict()
            elif isinstance(stats, dict):
                stats_dict = stats
            else:
                # Access attributes directly
                stats_dict = {
                    "numberOfDocuments": getattr(stats, 'number_of_documents', 0),
                    "isIndexing": getattr(stats, 'is_indexing', False),
                    "fieldDistribution": getattr(stats, 'field_distribution', {}),
                }

            return {
                StatsKeys.TOTAL_DOCUMENTS: stats_dict.get(
                    "number_of_documents",
                    stats_dict.get("numberOfDocuments", 0),
                ),
                StatsKeys.IS_INDEXING: stats_dict.get(
                    "is_indexing",
                    stats_dict.get("isIndexing", False),
                ),
                StatsKeys.FIELD_DISTRIBUTION: stats_dict.get(
                    "field_distribution",
                    stats_dict.get("fieldDistribution", {}),
                ),
                StatsKeys.INDEX_NAME: self.index_name,
                StatsKeys.HOST: self.host,
            }

        except MeilisearchApiError as e:
            raise MeilisearchError(f"Get stats failed: {e}")

    def get_all_document_paths(self) -> List[str]:
        """
        Return all document paths stored in the Meilisearch index.

        Uses offset-based pagination to retrieve every document.

        Returns:
            List of absolute file paths indexed in Meilisearch

        Raises:
            MeilisearchError: If the listing fails
        """
        from aitao.search.meilisearch_client import MeilisearchError

        paths: List[str] = []
        offset, limit = 0, 1000
        while True:
            try:
                result = self.index.get_documents(
                    {"limit": limit, "offset": offset, "fields": ["path"]}
                )
            except MeilisearchApiError as e:
                raise MeilisearchError(f"Failed to list documents: {e}")

            docs = (
                result.results
                if hasattr(result, "results")
                else (result if isinstance(result, list) else [])
            )
            for doc in docs:
                p = (
                    doc.get("path")
                    if isinstance(doc, dict)
                    else getattr(doc, "path", None)
                )
                if p:
                    paths.append(p)

            if not docs or len(docs) < limit:
                break
            offset += limit

        return paths

    def get_incomplete_document_paths(self) -> List[str]:
        """Paths of documents stored WITHOUT real content (US-113).

        Meilisearch-only replacement for the pre-4.0 "documents missing a
        LanceDB vector" check (``aitao scan reindex``): a document whose
        extraction/OCR yielded nothing is still written (title-only, no
        semantic leg), flagged with ``has_content = false`` at write time
        (see ``add_document``/``add_documents_batch``). These paths are
        exactly the ones worth re-queuing once a better extractor/OCR pass
        might succeed. A pre-4.0 document indexed before this field existed
        also has no ``has_content`` value and is treated the same way
        (conservatively re-queued) — see ``healthy_doc_ids`` for the
        matching id-based oracle used by scanner reconciliation.

        Returns an empty list on any error (degrades safely, like
        ``get_all_document_paths``).
        """
        from aitao.search.meilisearch_client import MeilisearchError

        paths: List[str] = []
        offset, limit = 0, 1000
        while True:
            try:
                result = self.index.get_documents(
                    {"limit": limit, "offset": offset, "fields": ["path", "has_content"]}
                )
            except MeilisearchApiError as e:
                raise MeilisearchError(f"Failed to list documents: {e}")

            docs = (
                result.results
                if hasattr(result, "results")
                else (result if isinstance(result, list) else [])
            )
            for doc in docs:
                d = doc if isinstance(doc, dict) else {
                    "path": getattr(doc, "path", None),
                    "has_content": getattr(doc, "has_content", None),
                }
                if not d.get("has_content") and d.get("path"):
                    paths.append(d["path"])

            if not docs or len(docs) < limit:
                break
            offset += limit

        return paths

    def healthy_doc_ids(self) -> "set[str]":
        """Ids of documents that carry real (non-empty) content (US-113).

        Meilisearch-only replacement for ``DocumentIndexer.healthy_doc_ids``'s
        former LanceDB-based oracle: LanceDB used to reject empty-content
        writes outright, so presence there meant "has real content" for free.
        Meilisearch does not reject anything, so this is tracked explicitly
        via the ``has_content`` field (see ``add_document``). Same pagination
        pattern as ``all_doc_ids``; returns an empty set on any error.
        """
        ids: "set[str]" = set()
        offset, page = 0, 5000
        try:
            while True:
                res = self.index.get_documents(
                    {"offset": offset, "limit": page, "fields": ["id", "has_content"]}
                )
                results = getattr(res, "results", None)
                if results is None and isinstance(res, dict):
                    results = res.get("results", [])
                if not results:
                    break
                for doc in results:
                    d = doc if isinstance(doc, dict) else {
                        "id": getattr(doc, "id", None),
                        "has_content": getattr(doc, "has_content", None),
                    }
                    if d.get("has_content"):
                        doc_id = d.get("id")
                        if doc_id:
                            ids.add(str(doc_id))
                if len(results) < page:
                    break
                offset += page
        except Exception as e:
            self.logger.warning(f"healthy_doc_ids failed: {e}")
            return set()
        return ids

    def clear(self) -> int:
        """
        Delete all documents from the index.

        Returns:
            Number of documents deleted
        """
        from aitao.search.meilisearch_client import MeilisearchError

        try:
            stats = self.get_stats()
            count = stats.get("total_documents", 0)

            task = self.index.delete_all_documents()
            self._wait_for_task(task.task_uid, timeout_ms=60000)

            self.logger.warning(
                "Index cleared",
                metadata={"deleted_count": count}
            )

            return count

        except MeilisearchApiError as e:
            raise MeilisearchError(f"Clear failed: {e}")

    def update_settings(self, settings: Dict[str, Any]) -> bool:
        """
        Update index settings.

        Args:
            settings: Dict of settings to update

        Returns:
            True if updated successfully
        """
        from aitao.search.meilisearch_client import MeilisearchError

        try:
            task = self.index.update_settings(settings)
            result = self._wait_for_task(task.task_uid)

            self.logger.info(
                "Settings updated",
                metadata={"settings": list(settings.keys())}
            )

            return result.get("status") == "succeeded"

        except MeilisearchApiError as e:
            raise MeilisearchError(f"Update settings failed: {e}")

    def configure_embedder(
        self, dimensions: int, embedder_name: str = "default"
    ) -> bool:
        """Configure (create or update) a userProvided embedder on this index.

        ÉPIC-31 (US-110) — the fusion engine computes query/document vectors
        locally (bge-m3, reusing the existing embedding path) and pushes them
        verbatim; Meilisearch itself never re-embeds ("userProvided" source).
        Idempotent — safe to call again with the same dimensions. Uses the
        meilisearch-python SDK natively (0.40.0 in this project's lockfile
        already models embedders/hybrid search; no experimental flag needed,
        confirmed interactively by the US-106 study, see
        US-106-radiographie/task5_experimental_features.json).
        """
        return self.update_settings({
            "embedders": {
                embedder_name: {"source": "userProvided", "dimensions": dimensions},
            },
        })

    def is_healthy(self) -> bool:
        """Check if Meilisearch server is healthy."""
        try:
            health = self.client.health()
            return health.get("status") == "available"
        except Exception:
            return False

    def get_version(self) -> str:
        """Get Meilisearch server version."""
        try:
            version = self.client.get_version()
            return version.get("pkgVersion", "unknown")
        except Exception:
            return "unknown"

    def add_documents_batch(
        self,
        documents: List[Dict[str, Any]],
        vectors: Optional[List[Optional[List[float]]]] = None,
    ) -> List[str]:
        """Add multiple documents in a SINGLE Meilisearch task (US-093).

        Returns the list of document IDs, same order as ``documents``.

        ``vectors`` (ÉPIC-31, US-111), same length/order as ``documents``:
        the doc-level fusion vector for each document, precomputed once by
        the indexer (``None`` entries fall back to the US-110 null opt-out —
        see ``MeilisearchFusionMixin._fusion_vectors_payload``). Entirely
        ignored under [search] engine="rrf".

        Each ``documents[i]`` dict may carry a ``metadata`` sub-dict (mtime,
        pages, ...) merged into the record exactly like ``add_document()`` —
        this was previously dropped here (this method existed but was never
        wired into production), which would have silently defeated the
        mtime-based staleness check (US-17) for any document indexed through
        this batched path.
        """
        from aitao.search.meilisearch_client import MeilisearchError

        prepared = []
        doc_ids = []
        now = datetime.now(timezone.utc).isoformat()

        for i, doc in enumerate(documents):
            path = doc.get("path", "")
            doc_id = self._generate_id(path)
            doc_ids.append(doc_id)

            record = {
                "id": doc_id,
                "path": path,
                "title": doc.get("title", ""),
                "content": (doc.get("content", "") or "")[:100000],
                "category": doc.get("category", "autre"),
                "language": doc.get("language", "unknown"),
                "file_type": doc.get("file_type") or Path(path).suffix,
                "file_size": doc.get("file_size", 0),
                "created_at": now,
                "updated_at": now,
                # US-113 — see MeilisearchClient.add_document for rationale.
                "has_content": bool(doc.get("content") and doc.get("content").strip()),
            }

            metadata = doc.get("metadata")
            if metadata:
                for key, value in metadata.items():
                    if key not in record:
                        if isinstance(value, (str, int, float, bool, list)):
                            record[key] = value
                        else:
                            record[key] = str(value)

            # ÉPIC-31 (US-110/US-111) — real vector when available, else the
            # null opt-out; see meilisearch_fusion.py. getattr guard: this
            # mixin is also used standalone by callers without the fusion
            # mixin (duck-typed, no hard dependency).
            vector = vectors[i] if vectors and i < len(vectors) else None
            vectors_payload = getattr(self, "_fusion_vectors_payload", lambda v=None: None)(vector)
            if vectors_payload is not None:
                record["_vectors"] = vectors_payload
            prepared.append(record)

        try:
            task = self.index.add_documents(prepared)
            result = self._wait_for_task(task.task_uid, timeout_ms=120000)

            if result.get("status") == "failed":
                raise MeilisearchError(f"Batch add failed: {result.get('error')}")

            self.logger.info(
                "Batch documents added",
                metadata={"count": len(prepared)}
            )

            return doc_ids

        except MeilisearchApiError as e:
            raise MeilisearchError(f"Failed to add documents batch: {e}")
