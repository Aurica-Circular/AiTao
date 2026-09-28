# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# path_filter.py — substring path post-filter for search results (US-125).
#
# Meilisearch cannot filter on a substring natively (no CONTAINS operator in
# stable use, and `path` is not even a filterable attribute of the document
# or chunk indexes), so `path_contains` ("the folder filter") is enforced
# here, in Python, as a POST-filter applied AFTER retrieval — uniformly for
# every search mode (semantic / hybrid / fulltext) since all three go
# through the same native hybrid call, just with a different semanticRatio.
#
# Because the post-filter discards hits, callers must over-fetch from
# Meilisearch first (see `overfetch_limit`) so enough in-path hits survive to
# fill the requested `limit`. Used by search.search_executors for both the
# document search stage (search_fusion_sync) and the chunk/RAG retrieval
# stage (search_chunks).

from __future__ import annotations

import unicodedata
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")

# How many extra results to request from the search backend when a path
# filter is active, to compensate for hits the post-filter will discard. A
# constant factor (not adaptive) keeps behaviour predictable. Known
# limitation: if the targeted folder is a tiny slice of the corpus and the
# query otherwise matches heavily outside it, the in-path hits can still be
# under-represented in the over-fetched page and the final result under-fills
# `limit` — acceptable per US-125, documented rather than solved with an
# unbounded fetch.
PATH_FILTER_OVERFETCH_FACTOR = 10

# Upper bound on the over-fetched count, so a large `limit` combined with the
# factor above never asks the backend for an unreasonable page size.
PATH_FILTER_OVERFETCH_CAP = 500


def overfetch_limit(limit: int, path_contains: Optional[str]) -> int:
    """Limit to request from the search backend when a path filter is active.

    Returns ``limit`` unchanged when ``path_contains`` is empty/None (US-125
    point 5: no behaviour change on the default, unfiltered path). Otherwise
    scales it by ``PATH_FILTER_OVERFETCH_FACTOR``, capped at
    ``PATH_FILTER_OVERFETCH_CAP`` (but never below the original ``limit``).
    """
    if not path_contains:
        return limit
    return max(limit, min(limit * PATH_FILTER_OVERFETCH_FACTOR, PATH_FILTER_OVERFETCH_CAP))


def _normalize(value: str) -> str:
    """NFC-normalize + casefold for a robust, accent/case-insensitive compare.

    NFC matters because paths (especially CJK filenames) can reach this code
    in NFC or NFD Unicode form depending on their origin — same precaution as
    ``indexation.prune._normalize``.
    """
    return unicodedata.normalize("NFC", value).casefold()


def _default_path_getter(hit) -> Optional[str]:
    """Path extractor for plain dict hits (Meilisearch document hits)."""
    if isinstance(hit, dict):
        return hit.get("path")
    return None


def filter_hits_by_path(
    hits: List[T],
    path_contains: Optional[str],
    path_getter: Callable[[T], Optional[str]] = _default_path_getter,
) -> List[T]:
    """Keep only hits whose path contains ``path_contains`` (US-125).

    Pure, backend-agnostic post-filter: works on any hit shape via
    ``path_getter`` (defaults to dict hits with a "path" key, e.g.
    Meilisearch document hits; pass a lambda for other shapes, e.g. chunk
    ``(Chunk, score)`` tuples). Comparison is case-insensitive and
    NFC-normalized on both sides — see ``_normalize``. A hit whose path is
    missing/empty is dropped rather than raising. When ``path_contains`` is
    empty/None, ``hits`` is returned unchanged (US-125 point 5: no surprise
    filtering when no folder filter was requested).
    """
    if not path_contains:
        return hits
    needle = _normalize(path_contains)
    kept: List[T] = []
    for hit in hits:
        path = path_getter(hit)
        if not path:
            continue
        if needle in _normalize(path):
            kept.append(hit)
    return kept
