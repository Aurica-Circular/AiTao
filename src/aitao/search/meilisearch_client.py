# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Meilisearch client for full-text search.

This module provides a client for Meilisearch search engine:
- Full-text search with typo tolerance
- Filterable and sortable attributes
- Fast keyword search to complement semantic search
- Hybrid search support with LanceDB
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from meilisearch import Client
    from meilisearch.errors import MeilisearchApiError, MeilisearchCommunicationError
    MEILISEARCH_AVAILABLE = True
except ImportError:
    MEILISEARCH_AVAILABLE = False
    Client = None

from aitao.core.models import Document

try:
    from aitao.core.logger import get_logger
    from aitao.core.config import ConfigManager
except ImportError:
    from aitao.core.logger import get_logger
    from aitao.core.config import ConfigManager

from aitao.search.meilisearch_admin import MeilisearchAdminMixin, DEFAULT_SETTINGS, US094_SETTINGS
from aitao.search.meilisearch_fusion import MeilisearchFusionMixin


class MeilisearchError(Exception):
    """Base exception for Meilisearch operations."""
    pass


class MeilisearchConnectionError(MeilisearchError):
    """Raised when connection to Meilisearch fails."""
    pass


class MeilisearchClient(MeilisearchAdminMixin, MeilisearchFusionMixin):
    """Client for Meilisearch full-text search operations."""
    
    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        config: Optional[ConfigManager] = None,
        timeout: int = 30,
        ensure_embedder: bool = True,
    ):
        """Initialize Meilisearch client.

        ``ensure_embedder`` (ÉPIC-31, US-113): set False to skip configuring
        the userProvided embedder on this index as a side effect of mere
        construction — used ONLY by the US-112 migration tool, which builds a
        read-only client on the LIVE, pre-migration index purely to
        enumerate its documents (see search.meilisearch_fusion for why).
        """
        self._ensure_embedder = ensure_embedder
        if not MEILISEARCH_AVAILABLE:
            raise MeilisearchError(
                "meilisearch-python package not installed. "
                "Run: uv pip install meilisearch"
            )
        
        self.logger = get_logger("meilisearch")
        
        # Load configuration
        try:
            if config:
                self._config = config
            else:
                self._config = ConfigManager("config/config.toml")
        except Exception:
            self._config = None
        
        # Determine connection settings
        # Config uses 'url' key, accept both 'host' param and config 'url'
        ms = self._config.search.meilisearch if self._config else None
        self.host = host or (ms.url if ms else "http://localhost:7700")

        # Handle empty string api_key as None
        config_api_key = (ms.api_key or None) if ms else None
        self.api_key = api_key or config_api_key

        self.index_name = index_name or (
            ms.index_name if ms else "aitao_documents"
        )
        
        self.timeout = timeout
        
        # Connect to Meilisearch
        self.logger.info(
            "Connecting to Meilisearch",
            metadata={"host": self.host, "index": self.index_name}
        )
        
        try:
            self.client = Client(self.host, self.api_key, timeout=timeout)
            
            # Test connection
            health = self.client.health()
            if health.get("status") != "available":
                raise MeilisearchConnectionError(
                    f"Meilisearch not healthy: {health}"
                )
                
        except MeilisearchCommunicationError as e:
            raise MeilisearchConnectionError(
                f"Cannot connect to Meilisearch at {self.host}: {e}"
            )
        except Exception as e:
            raise MeilisearchConnectionError(f"Connection failed: {e}")
        
        # Ensure index exists with proper settings
        self._ensure_index()
        
        self.logger.info(
            "Meilisearch client initialized",
            metadata={
                "host": self.host,
                "index": self.index_name,
            }
        )
    
    def _ensure_index(self) -> None:
        """Create index if it doesn't exist and configure settings."""
        try:
            # Try to get existing index
            try:
                self.index = self.client.get_index(self.index_name)
                self.logger.debug(
                    "Using existing index",
                    metadata={"index": self.index_name}
                )
            except MeilisearchApiError as e:
                if "index_not_found" in str(e):
                    # Create new index
                    task = self.client.create_index(
                        self.index_name,
                        {"primaryKey": "id"}
                    )
                    self._wait_for_task(task.task_uid)
                    self.index = self.client.get_index(self.index_name)

                    # Apply default settings + US-094 ingestion/relevance
                    # settings (ÉPIC-31, US-111) in the SAME call.
                    task = self.index.update_settings({**DEFAULT_SETTINGS, **US094_SETTINGS})
                    self._wait_for_task(task.task_uid)
                    
                    self.logger.info(
                        "Created new index with settings",
                        metadata={"index": self.index_name}
                    )
                else:
                    raise
                    
        except Exception as e:
            raise MeilisearchError(f"Failed to ensure index: {e}")

        # ÉPIC-31 (US-110) — see search/meilisearch_fusion.py; no-op under rrf.
        self._ensure_fusion_embedder()

    def _wait_for_task(self, task_uid: int, timeout_ms: int = 30000) -> Dict:
        """Wait for an async task to complete and return status dict."""
        from aitao.search.meilisearch_admin import wait_for_task_status
        return wait_for_task_status(self.client, task_uid, self.logger, timeout_ms)
    
    def _generate_id(self, path: str) -> str:
        """Generate unique ID from file path (NFC-normalized) using SHA256.

        Must match indexer_helpers.generate_doc_id so a document stored by the
        indexer is found by the same id here (US-RAG-name NFC normalization).
        """
        import unicodedata
        return hashlib.sha256(
            unicodedata.normalize("NFC", path).encode()
        ).hexdigest()
    
    def index_document(
        self, document: Document, vector: Optional[List[float]] = None
    ) -> str:
        """Index a domain Document (preferred API, US-23b).

        Thin adapter that keeps the validated Document object flowing through
        the pipeline; add_document() builds the backend record from its fields.
        ``vector`` (ÉPIC-31, US-111): the doc-level embedding, precomputed once
        by the indexer and reused here under [search] engine="fusion" — see
        ``add_document``.
        """
        return self.add_document(
            path=document.path,
            title=document.title,
            content=document.content,
            category=document.category or "autre",
            language=document.language,
            file_type=document.file_type,
            file_size=document.file_size,
            metadata=document.metadata,
            vector=vector,
        )

    def add_document(
        self,
        path: str,
        title: str,
        content: str,
        category: str = "autre",
        language: str = "unknown",
        file_type: Optional[str] = None,
        file_size: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        vector: Optional[List[float]] = None,
    ) -> str:
        """Add a document to the search index. Returns document ID.

        ``vector`` (ÉPIC-31, US-111): doc-level embedding for the fusion
        engine's ``_vectors.default`` (see ``MeilisearchFusionMixin.
        _fusion_vectors_payload``). Ignored under the default "rrf" engine.
        """
        doc_id = self._generate_id(path)
        now = datetime.now(timezone.utc).isoformat()
        
        # Prepare document
        document = {
            "id": doc_id,
            "path": path,
            "title": title,
            "content": content[:100000] if content else "",  # Limit content size
            "category": category,
            "language": language,
            "file_type": file_type or Path(path).suffix,
            "file_size": file_size,
            "created_at": now,
            "updated_at": now,
            # US-113 — cheap oracle for "does this document carry real
            # content" (replaces the LanceDB-rejects-empty-writes guarantee
            # now that LanceDB is no longer a store — see
            # healthy_doc_ids()/get_incomplete_document_paths()).
            "has_content": bool(content and content.strip()),
        }

        # Add extra metadata fields if provided
        if metadata:
            for key, value in metadata.items():
                if key not in document:
                    # Meilisearch requires JSON-serializable values
                    if isinstance(value, (str, int, float, bool, list)):
                        document[key] = value
                    else:
                        document[key] = str(value)

        # ÉPIC-31 (US-110/US-111) — see search/meilisearch_fusion.py; no-op under rrf.
        vectors_payload = self._fusion_vectors_payload(vector)
        if vectors_payload is not None:
            document["_vectors"] = vectors_payload

        try:
            # Add or update document
            task = self.index.add_documents([document])
            result = self._wait_for_task(task.task_uid)
            
            if result.get("status") == "failed":
                raise MeilisearchError(f"Add failed: {result.get('error')}")
            
            self.logger.info(
                "Document added to index",
                metadata={
                    "id": doc_id,
                    "path": path,
                    "title": title[:50],
                }
            )
            
            return doc_id
            
        except MeilisearchApiError as e:
            raise MeilisearchError(f"Failed to add document: {e}")
    
    def count(self, query: str = "") -> int:
        """Return how many documents match ``query`` (estimatedTotalHits).

        Uses ``limit=0`` so Meilisearch returns only the count, no payloads —
        cheap enough to call per term for query distillation (US-89). ``query=""``
        counts all documents. Returns 0 on any error (the caller treats an unknown
        frequency as "salient", so a failure never drops a term).
        """
        try:
            result = self.index.search(query, {"limit": 0})
            return int(result.get("estimatedTotalHits", 0))
        except Exception as e:
            self.logger.warning("count() failed", metadata={"query": query[:50], "error": str(e)})
            return 0

    def search_titles(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Full-text search restricted to the ``title`` attribute (US-RAG-name).

        Used to resolve a document a user named explicitly ("translate X.pdf"):
        matching against titles only avoids the content of *other* documents
        drowning the filename signal. Returns lightweight {id, path, title} dicts.
        Returns [] on any error (the caller falls back to normal retrieval).
        """
        try:
            result = self.index.search(
                query,
                {
                    "limit": limit,
                    "attributesToSearchOn": ["title"],
                    "attributesToRetrieve": ["id", "path", "title"],
                    # "frequency" tolerates the many non-title words of a natural
                    # question ("translate the document X.pdf, what is it about?");
                    # the default "last" strategy needs all words and returns none.
                    "matchingStrategy": "frequency",
                },
            )
            return [
                {"id": h.get("id"), "path": h.get("path"), "title": h.get("title")}
                for h in result.get("hits", [])
            ]
        except Exception as e:
            self.logger.warning(
                "search_titles() failed",
                metadata={"query": query[:50], "error": str(e)},
            )
            return []

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filter_category: Optional[str] = None,
        filter_language: Optional[str] = None,
        filter_file_type: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Search documents using full-text search."""
        # Build filter string
        filters = []
        if filter_category:
            filters.append(f'category = "{filter_category}"')
        if filter_language:
            filters.append(f'language = "{filter_language}"')
        if filter_file_type:
            filters.append(f'file_type = "{filter_file_type}"')
        
        filter_str = " AND ".join(filters) if filters else None
        
        # Build sort
        sort = None
        if sort_by:
            sort = [f"{sort_by}:{sort_order}"]
        
        try:
            result = self.index.search(
                query,
                {
                    "limit": limit,
                    "offset": offset,
                    "filter": filter_str,
                    "sort": sort,
                    "attributesToRetrieve": [
                        "id", "path", "title", "content", "category",
                        "language", "file_type", "file_size", "created_at"
                    ],
                    "attributesToCrop": ["content"],
                    "cropLength": 200,
                    "attributesToHighlight": ["title", "content"],
                }
            )
            
            # Format results
            formatted = []
            for hit in result.get("hits", []):
                formatted.append({
                    "id": hit.get("id"),
                    "path": hit.get("path"),
                    "title": hit.get("title"),
                    "content": hit.get("_formatted", {}).get("content", hit.get("content", ""))[:500],
                    "category": hit.get("category"),
                    "language": hit.get("language"),
                    "file_type": hit.get("file_type"),
                    "file_size": hit.get("file_size"),
                    "created_at": hit.get("created_at"),
                    "_highlights": hit.get("_formatted", {}),
                })
            
            self.logger.info(
                "Search completed",
                metadata={
                    "query": query[:50],
                    "results_count": len(formatted),
                    "total_hits": result.get("estimatedTotalHits", 0),
                    "processing_time_ms": result.get("processingTimeMs", 0),
                }
            )
            
            return formatted

        except MeilisearchApiError as e:
            raise MeilisearchError(f"Search failed: {e}")

    # search_hybrid() — ÉPIC-31 (US-110) single native hybrid call for the
    # fusion engine — comes from MeilisearchFusionMixin (meilisearch_fusion.py).

    def delete(self, doc_id: str) -> bool:
        """
        Delete a document from the index.
        
        Args:
            doc_id: Document ID (SHA256 hash)
        
        Returns:
            True if deleted successfully
        """
        try:
            task = self.index.delete_document(doc_id)
            result = self._wait_for_task(task.task_uid)
            
            self.logger.info(
                "Document deleted",
                metadata={"id": doc_id}
            )
            
            return result.get("status") == "succeeded"
            
        except MeilisearchApiError as e:
            raise MeilisearchError(f"Delete failed: {e}")
    
    def delete_by_path(self, path: str) -> bool:
        """
        Delete a document by its file path.
        
        Args:
            path: Absolute file path
        
        Returns:
            True if deleted successfully
        """
        doc_id = self._generate_id(path)
        return self.delete(doc_id)
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a document by ID.
        
        Args:
            doc_id: Document ID (SHA256 hash)
        
        Returns:
            Document dict or None if not found
        """
        try:
            doc = self.index.get_document(doc_id)
            return dict(doc)
        except MeilisearchApiError as e:
            if "document_not_found" in str(e):
                return None
            raise MeilisearchError(f"Get document failed: {e}")

    def all_doc_ids(self) -> set:
        """Return the ids of every document in the index.

        Used for orphan reconciliation (US-086). Pages through the index a few
        thousand ids at a time, fetching only the ``id`` field. Returns an empty
        set on any error so reconciliation degrades safely.
        """
        ids: set = set()
        offset = 0
        page = 5000
        try:
            while True:
                res = self.index.get_documents(
                    {"offset": offset, "limit": page, "fields": ["id"]}
                )
                results = getattr(res, "results", None)
                if results is None and isinstance(res, dict):
                    results = res.get("results", [])
                if not results:
                    break
                for doc in results:
                    doc_id = doc.get("id") if isinstance(doc, dict) else getattr(doc, "id", None)
                    if doc_id:
                        ids.add(str(doc_id))
                if len(results) < page:
                    break
                offset += page
        except Exception as e:
            self.logger.warning(f"all_doc_ids failed: {e}")
            return set()
        return ids
