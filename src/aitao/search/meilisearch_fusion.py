# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# meilisearch_fusion.py — US-110 (ÉPIC-31) native hybrid search + userProvided
# embedder wiring for MeilisearchClient's DOCUMENT index (/api/search stage).
#
# Since v4.0 (US-113, decision D1) fusion is the ONLY engine: this mixin's
# behaviour is unconditional — adds search_hybrid() (the single native
# Meilisearch call HybridSearchEngine uses, no fan-out, no RRF merge) and the
# plumbing to configure a userProvided embedder on the document index.
#
# EXCEPTION — ``ensure_embedder=False`` (see MeilisearchClient.__init__): the
# US-112 migration tool builds a read-only client pointed at the LIVE,
# pre-migration index purely to enumerate its documents; that read must never
# have the side effect of mutating the live index's settings (adding an
# embedder) before the operator has reviewed/confirmed anything. This is the
# ONLY opt-out — every other construction configures the embedder.
#
# Kept separate from meilisearch_client.py to respect the project's file-size
# convention. Expects the host class to provide: index, index_name, logger,
# _config (Optional[ConfigManager]), and (from MeilisearchAdminMixin)
# configure_embedder().

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from meilisearch.errors import MeilisearchApiError
except ImportError:
    MeilisearchApiError = Exception


class MeilisearchFusionMixin:
    """Document-stage fusion additions to MeilisearchClient (US-110)."""

    def _ensure_fusion_embedder(self) -> None:
        """Configure the userProvided embedder on the document index.

        Called from ``_ensure_index()`` on EVERY ensure (not just at index
        creation) so an index created before this code shipped still gets
        the embedder retroactively — idempotent either way. Skipped when the
        host was built with ``ensure_embedder=False`` (see module docstring).
        """
        if not getattr(self, "_ensure_embedder", True):
            return
        dimension = (
            self._config.search.embedding.dimension if self._config else 1024
        )
        try:
            self.configure_embedder(dimension)
        except Exception as e:
            self.logger.warning(
                "Failed to configure fusion embedder on document index",
                metadata={"index": self.index_name, "error": str(e)},
            )

    def _fusion_vectors_payload(
        self, vector: Optional[List[float]] = None
    ) -> Optional[Dict[str, Any]]:
        """``_vectors`` payload to merge into every document write.

        Once a userProvided embedder is configured (``_ensure_fusion_embedder``),
        Meilisearch REJECTS any document that omits ``_vectors.default``
        entirely — it must be given the vector or an explicit ``null`` opt-out.

        US-111 replaced the US-110 prototype's always-null opt-out with the
        REAL doc-level vector (computed once by the indexer and reused —
        see ``indexation.item_preparer.prepare_item``): pass it as ``vector``
        and it flows straight into ``_vectors.default``. Callers that have no
        vector yet (e.g. an empty-content document) still get the explicit
        null opt-out, so writes keep working — that document simply never
        surfaces on the semantic leg of a hybrid search (the lexical leg is
        unaffected).
        """
        return {"default": vector}

    def search_hybrid(
        self,
        query: str,
        vector: List[float],
        limit: int = 10,
        offset: int = 0,
        semantic_ratio: float = 0.5,
        embedder_name: str = "default",
        filter_category: Optional[str] = None,
        filter_language: Optional[str] = None,
        filter_file_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Single native Meilisearch hybrid search (ÉPIC-31, US-110 fusion engine).

        Replaces the parallel LanceDB+Meilisearch fan-out + RRF merge for the
        document stage with ONE call carrying both the lexical query and a
        locally computed bge-m3 ``vector`` (userProvided embedder — this index
        never re-embeds). Requires the "default" embedder to be configured on
        this index (see ``_ensure_fusion_embedder``); under the default "rrf"
        engine this method is never called.
        Returns hit dicts shaped like ``search()``'s, plus a ``_score`` taken
        from Meilisearch's own ``_rankingScore`` for the fusion result builder
        (``search.result_merger.build_fusion_results``).
        """
        from aitao.search.meilisearch_client import MeilisearchError

        filters = []
        if filter_category:
            filters.append(f'category = "{filter_category}"')
        if filter_language:
            filters.append(f'language = "{filter_language}"')
        if filter_file_type:
            filters.append(f'file_type = "{filter_file_type}"')
        filter_str = " AND ".join(filters) if filters else None

        try:
            result = self.index.search(
                query,
                {
                    "hybrid": {"semanticRatio": semantic_ratio, "embedder": embedder_name},
                    "vector": vector,
                    "limit": limit,
                    "offset": offset,
                    "filter": filter_str,
                    "showRankingScore": True,
                    "attributesToRetrieve": [
                        "id", "path", "title", "content", "category",
                        "language", "file_type", "file_size", "created_at",
                    ],
                },
            )
        except MeilisearchApiError as e:
            raise MeilisearchError(f"Hybrid search failed: {e}")

        formatted = []
        for hit in result.get("hits", []):
            formatted.append({
                "id": hit.get("id"),
                "path": hit.get("path"),
                "title": hit.get("title"),
                "content": (hit.get("content") or "")[:500],
                "category": hit.get("category"),
                "language": hit.get("language"),
                "file_type": hit.get("file_type"),
                "file_size": hit.get("file_size"),
                "created_at": hit.get("created_at"),
                "_score": float(hit.get("_rankingScore", 0.0)),
            })

        self.logger.info(
            "Hybrid search completed",
            metadata={
                "query": query[:50],
                "results_count": len(formatted),
                "semantic_ratio": semantic_ratio,
                "processing_time_ms": result.get("processingTimeMs", 0),
            }
        )
        return formatted
