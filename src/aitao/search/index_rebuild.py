# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# index_rebuild.py — rebuild-alongside + swapIndexes utility (ÉPIC-31,
# US-111, socle for the real migration done in US-112).
#
# Applying a setting that affects existing documents (embedder, tokenizer,
# proximityPrecision...) to a LIVE Meilisearch index means Meilisearch has to
# re-index every document under the new rules — doing that in place would
# leave the live index degraded (partial ranking rules) for the duration.
# IndexRebuilder instead builds a throwaway "<name>_next" index with the
# target settings, pushes documents into it, then calls Meilisearch's
# swapIndexes to atomically swap the "_next" content onto the LIVE index
# name — zero read/write downtime, and the swap is its own rollback (swap
# the same pair again to undo it).
#
# This module only provides the TOOL. US-112 is the one that will point it
# at real production documents (existing chunks/embeddings) to run the
# actual migration; here it ships mocked-only (no live Meilisearch needed).

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from aitao.core.logger import get_logger


class IndexRebuildError(Exception):
    """Base exception for IndexRebuilder operations."""


@dataclass
class RebuildResult:
    """Outcome of an ``IndexRebuilder.rebuild()`` call."""

    next_index_name: str
    documents_written: int
    swapped: bool


def _chunks(items: Iterable[Dict[str, Any]], size: int) -> Iterator[List[Dict[str, Any]]]:
    """Yield successive ``size``-sized lists from ``items`` (also accepts a
    plain list — a generator works too, e.g. a paginated production read)."""
    batch: List[Dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class IndexRebuilder:
    """Builds ``<live_index_name>_next`` with the desired settings/embedder,
    fills it with documents, then swaps it onto ``live_index_name`` via
    Meilisearch's ``swapIndexes`` — no coupure. Rollback: call ``swap_back()``
    (swapping the same pair a second time undoes the first swap).

    Takes a raw ``meilisearch.Client`` (or anything exposing the same
    ``create_index``/``get_index``/``index``/``delete_index``/
    ``swap_indexes`` surface) rather than one of this project's own
    ``MeilisearchClient``/``MeiliChunkStore`` wrappers: those each own ONE
    fixed index name, while a rebuild inherently juggles two index names
    (live + "_next") on the same server connection.
    """

    def __init__(self, client: Any, logger: Optional[Any] = None):
        self.client = client
        self.logger = logger or get_logger("search.index_rebuild")

    def _wait(self, task_uid: int, timeout_ms: int = 120000) -> Dict[str, Any]:
        from aitao.search.meilisearch_admin import wait_for_task_status

        return wait_for_task_status(self.client, task_uid, self.logger, timeout_ms)

    def _delete_if_exists(self, index_name: str) -> None:
        try:
            self.client.delete_index(index_name)
        except Exception:
            pass

    def rebuild(
        self,
        live_index_name: str,
        settings: Dict[str, Any],
        documents: Optional[Iterable[Dict[str, Any]]] = None,
        primary_key: str = "id",
        embedder: Optional[Tuple[str, int]] = None,
        batch_size: int = 1000,
        next_index_name: Optional[str] = None,
        swap: bool = True,
    ) -> RebuildResult:
        """Build the "_next" index, populate it, then (by default) swap it
        onto ``live_index_name``.

        Args:
            live_index_name: the PRODUCTION index name that will receive the
                new content once swapped (untouched until the swap itself).
            settings: index settings to apply to the new index (e.g.
                ``search.meilisearch_admin.DEFAULT_SETTINGS`` merged with
                ``US094_SETTINGS``).
            documents: records to push, batched by ``batch_size``. None/empty
                is valid (an empty "_next" index — useful for a settings-only
                rebuild test).
            embedder: optional ``(name, dimensions)`` to configure a
                userProvided embedder on the new index (ÉPIC-31 fusion index).
            next_index_name: override the default ``f"{live_index_name}_next"``.
            swap: set False to build+populate WITHOUT swapping yet (e.g. to
                inspect/validate the "_next" index before committing).

        Returns:
            RebuildResult with the next index name, document count written,
            and whether the swap happened.

        Raises:
            IndexRebuildError if index creation, settings, population or the
            swap itself fails — the LIVE index is untouched in every case
            (the failure always happens on the "_next" side, before swap).
        """
        next_name = next_index_name or f"{live_index_name}_next"

        # Idempotent/retriable: drop any stale leftover from a previous
        # failed attempt before starting fresh.
        self._delete_if_exists(next_name)

        try:
            task = self.client.create_index(next_name, {"primaryKey": primary_key})
            result = self._wait(task.task_uid)
            if result.get("status") == "failed":
                raise IndexRebuildError(f"Failed to create {next_name}: {result.get('error')}")

            index = self.client.index(next_name)

            all_settings = dict(settings)
            if embedder:
                embedder_name, dimensions = embedder
                all_settings = dict(all_settings)
                all_settings["embedders"] = {
                    **all_settings.get("embedders", {}),
                    embedder_name: {"source": "userProvided", "dimensions": dimensions},
                }
            task = index.update_settings(all_settings)
            result = self._wait(task.task_uid)
            if result.get("status") == "failed":
                raise IndexRebuildError(f"Failed to configure {next_name}: {result.get('error')}")

            written = 0
            for batch in _chunks(documents or [], batch_size):
                task = index.add_documents(batch)
                result = self._wait(task.task_uid, timeout_ms=120000)
                if result.get("status") == "failed":
                    raise IndexRebuildError(f"Failed to populate {next_name}: {result.get('error')}")
                written += len(batch)

            swapped = False
            if swap:
                self._swap(live_index_name, next_name)
                swapped = True

            self.logger.info(
                "Index rebuilt" + (" and swapped" if swapped else ""),
                metadata={
                    "live_index": live_index_name, "next_index": next_name,
                    "documents_written": written,
                },
            )
            return RebuildResult(
                next_index_name=next_name, documents_written=written, swapped=swapped,
            )
        except IndexRebuildError:
            raise
        except Exception as e:
            raise IndexRebuildError(f"Rebuild of {live_index_name} failed: {e}")

    def swap_back(self, live_index_name: str, next_index_name: Optional[str] = None) -> None:
        """Rollback a previous ``rebuild()``: swap the same pair again.

        Meilisearch's ``swapIndexes`` swaps which index UID serves which
        content — swapping the same pair TWICE restores the original
        assignment, so this is the entire rollback: no data is copied back,
        the pre-rebuild content is still sitting under ``next_index_name``
        (now un-swapped) exactly as ``rebuild()`` left the OLD content after
        its own swap.
        """
        next_name = next_index_name or f"{live_index_name}_next"
        self._swap(live_index_name, next_name)

    def _swap(self, index_a: str, index_b: str) -> None:
        self._ensure_swap_pair_exists(index_a, index_b)
        task = self.client.swap_indexes([{"indexes": [index_a, index_b]}])
        result = self._wait(task.task_uid)
        if result.get("status") == "failed":
            raise IndexRebuildError(
                f"swapIndexes({index_a}, {index_b}) failed: {result.get('error')}"
            )

    def _existing_index(self, name: str) -> Optional[Any]:
        try:
            return self.client.get_index(name)
        except Exception:
            return None

    def _ensure_swap_pair_exists(self, index_a: str, index_b: str) -> None:
        """Work around a real gap: Meilisearch's ``swapIndexes`` rejects the
        call outright (``index_not_found``) if EITHER named index is missing
        — confirmed against a live server, not just documentation. This bites
        the FIRST ``swap()`` of the US-112 migration on a fresh "rrf"-only
        install: the live excerpt index (``chunks_index_name``, e.g.
        ``aitao_chunks``) has never been created, since under "rrf" excerpts
        live only in LanceDB (``indexation.chunk_store.ChunkStore``) — the
        Meilisearch chunk index is only ever touched by ``MeiliChunkStore``,
        which is instantiated under "fusion". Without this, the DOCUMENT swap
        would succeed while the CHUNKS swap fails right after it, leaving the
        migration half-applied.

        Create an empty placeholder for whichever side is missing, using the
        OTHER side's primaryKey (the "_next" side, just rebuilt, always has
        one) — swapping an empty index into "_next" loses nothing, since
        there was no live data under that name to begin with.
        """
        a_index = self._existing_index(index_a)
        b_index = self._existing_index(index_b)
        if a_index is not None and b_index is not None:
            return
        if a_index is None and b_index is None:
            raise IndexRebuildError(
                f"Cannot swap {index_a}/{index_b}: neither index exists."
            )
        missing, known = (index_a, b_index) if a_index is None else (index_b, a_index)
        primary_key = getattr(known, "primary_key", None) or "id"
        task = self.client.create_index(missing, {"primaryKey": primary_key})
        result = self._wait(task.task_uid)
        if result.get("status") == "failed":
            raise IndexRebuildError(
                f"Failed to create placeholder {missing} for swap: {result.get('error')}"
            )
