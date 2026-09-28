# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Storage repository contract for AiTao (US-24).

`DocumentRepository` is the backend-agnostic interface the business layers
(indexer, search engine) depend on for document storage: index / search /
fetch / delete / stats. Its concrete implementation is
`search.meilisearch_client.MeilisearchClient`, which satisfies this contract
structurally — so a layer can be handed a real client or a test double with no
code change.

ÉPIC-31 (US-113): the LanceDB-backed repository factories
(`make_lancedb_client`/`make_lancedb_repository`) are removed — v4.0 ships
fusion-only (decision D1), Meilisearch is the only live store. The migration
tool (`search.migrate_v4_source`) reads the legacy LanceDB store directly via
the raw `lancedb` package (lazy import), not through this module.

Construction is centralized in the `make_*_repository` factories, which lazily
import the heavy client modules to keep startup fast.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

from aitao.core.models import Document

if TYPE_CHECKING:
    from aitao.search.meilisearch_client import MeilisearchClient


@runtime_checkable
class DocumentRepository(Protocol):
    """Backend-agnostic contract for document storage and retrieval.

    Only the operations the business layers actually use are part of the
    contract; backend-specific extras (admin/maintenance) stay on the concrete
    clients and are intentionally out of scope here.
    """

    def index_document(self, document: Document) -> str:
        """Persist (add or update) a document; return its storage id."""
        ...

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_category: Optional[str] = None,
        filter_language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return raw backend matches for a query (caller merges/filters)."""
        ...

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a stored document by id, or None if absent."""
        ...

    def delete(self, doc_id: str) -> bool:
        """Delete a document by id; return True on success."""
        ...

    def delete_by_path(self, path: str) -> bool:
        """Delete a document by its source path; return True on success."""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """Return backend statistics (counts, names, sizes)."""
        ...

    def all_doc_ids(self) -> "set[str]":
        """Return the ids of every document currently stored.

        Used for orphan reconciliation (US-086): the scanner compares the set
        of indexed ids against the files it has already 'seen' to detect docs
        that never actually landed in the store.
        """
        ...


def make_meilisearch_client(**kwargs: Any) -> MeilisearchClient:
    """Build a Meilisearch client — the single construction point for that backend.

    Returns the full client surface (incl. admin/diagnostic ops), for callers
    that need more than the DocumentRepository contract (CLI tools, health).
    """
    from aitao.search.meilisearch_client import MeilisearchClient

    return MeilisearchClient(**kwargs)


def make_meilisearch_repository(**kwargs: Any) -> DocumentRepository:
    """Build the Meilisearch-backed repository (full-text search)."""
    return make_meilisearch_client(**kwargs)


def make_chunk_store(config: Optional[Any] = None, **kwargs: Any) -> Any:
    """Build the excerpt (chunk) store (ÉPIC-31, US-111, simplified US-113).

    Same factory pattern as ``make_meilisearch_client``: the single
    construction point, so the worker, the ingestion API and tests all build
    the excerpt store from one place instead of duplicating it (previously
    inlined in both ``indexation.indexer.DocumentIndexer.chunk_store`` and
    ``search.hybrid_engine.HybridSearchEngine.chunk_store``).

    Always ``indexation.chunk_store_meili.MeiliChunkStore`` — the dedicated
    Meilisearch chunk index (real userProvided vectors) is the only excerpt
    backend since v4.0 (fusion-only, decision D1; the former LanceDB
    ``ChunkStore`` was removed). ``config`` (a ``ConfigManager``, default the
    global singleton) and any extra ``kwargs`` (e.g. ``index_name``) are
    forwarded verbatim.
    """
    from aitao.core.config import get_config
    from aitao.indexation.chunk_store_meili import MeiliChunkStore

    cfg = config or get_config()
    return MeiliChunkStore(config=cfg, **kwargs)
