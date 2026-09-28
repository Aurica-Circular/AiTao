# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Result building for the fusion hybrid search engine.

ÉPIC-31 (US-113): v4.0 is fusion-only (decision D1) — a single native
Meilisearch hybrid call already returns hits pre-ranked by Meilisearch's own
blended relevance, so the Reciprocal Rank Fusion / weighted-average merge
strategies this module used to provide (merge_results_rrf,
merge_results_weighted, calculate_rrf_score, normalize_score) are removed;
they had zero consumers left outside the "rrf" engine path that used them.

Used by HybridSearchEngine (search.hybrid_fusion) to turn native-hybrid
Meilisearch hits into SearchResult objects.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from aitao.search.search_models import SearchResult


def _build_search_result(doc: Dict[str, Any], score: float,
                         semantic: float, fulltext: float,
                         extra_metadata: Optional[Dict] = None) -> SearchResult:
    """Build a SearchResult from a raw document dict."""
    content = doc.get("content", "")
    summary = content[:500] + "..." if len(content) > 500 else content

    modified_at = doc.get("modified_at") or doc.get("created_at")
    if isinstance(modified_at, str):
        try:
            modified_at = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
        except ValueError:
            modified_at = None

    metadata = doc.get("metadata", {})
    if extra_metadata:
        metadata = {**metadata, **extra_metadata}

    return SearchResult(
        id=doc.get("id", doc.get("path", "")),
        path=doc.get("path", ""),
        title=doc.get("title") or doc.get("path", "").split("/")[-1],
        content=summary,
        score=round(score, 4),
        semantic_score=round(semantic, 4),
        fulltext_score=round(fulltext, 4),
        category=doc.get("category"),
        language=doc.get("language"),
        file_size=doc.get("file_size"),
        modified_at=modified_at,
        metadata=metadata,
    )


def build_fusion_results(hits: List[Dict[str, Any]], limit: int) -> List[SearchResult]:
    """Turn native-hybrid Meilisearch hits into SearchResult objects (US-110).

    No RRF here: a single native hybrid call already returns hits pre-ranked
    by Meilisearch's own blended relevance (``_score``, filled from the hit's
    ``_rankingScore`` by ``MeilisearchClient.search_hybrid``), reused directly
    as the SearchResult score. ``semantic_score``/``fulltext_score`` both
    mirror that same score — fusion no longer distinguishes the two legs.
    """
    results: List[SearchResult] = []
    for hit in hits[:limit]:
        score = float(hit.get("_score", 0.0))
        results.append(_build_search_result(hit, score, score, score))
    return results
