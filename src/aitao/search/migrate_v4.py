# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# migrate_v4.py — US-112 migration orchestration: dry-run / swap / rollback.
#
# ÉPIC-31, US-112: reads the EXISTING stores (LanceDB `chunks`/doc-level table
# + the LIVE Meilisearch documents index — see search.migrate_v4_source),
# classifies every document into "copy" (vectors reused verbatim) or
# "reindex" (CJK-gluing changed its content, see 89-6/US-111), builds
# "<name>_next" indices via search.index_rebuild.IndexRebuilder (US-111's
# rebuild-alongside tool), and only swaps them onto the live names once the
# operator has reviewed the dry-run report and confirmed.
#
# THREE explicit phases, dry-run the default and only one with zero live-index
# side effects:
#   - dry_run()  — build "_next" fully, verify samples, report. NEVER swaps.
#   - swap()     — build "_next" fully (fresh), then swapIndexes(docs) and
#                  swapIndexes(chunks); ONLY THEN requeues "reindex" documents
#                  whose source file still exists (never before the swap —
#                  the worker must write to the now-live index, not the
#                  about-to-be-replaced one).
#   - rollback() — re-swap the same pairs (Meilisearch swapIndexes toggles),
#                  restoring the pre-migration content. No rebuild.
#
# US-113: fusion is the only engine since v4.0 — the migration no longer
# needs to reason about "which engine is active"; the LIVE-index READ step's
# side-effect-free guarantee now comes from MeilisearchClient's own
# ``ensure_embedder=False`` constructor flag (search/meilisearch_fusion.py),
# not from forcing a config view. The engine mode recorded in the report is
# now always the literal string "fusion" (kept as a report field for
# backward-compat visibility only).

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aitao.core.logger import get_logger
from aitao.core.pathmanager import path_manager
from aitao.indexation.chunk_store_meili import MeiliChunkStore
from aitao.search.index_rebuild import IndexRebuilder, IndexRebuildError
from aitao.search.meilisearch_admin import DEFAULT_SETTINGS, US094_SETTINGS
from aitao.search.migrate_v4_source import (
    chunks_for_copy_population,
    classify_documents,
    read_live_documents,
    read_source_chunks,
    read_source_doc_vectors,
)


class MigrationConfirmationRequired(Exception):
    """Raised by ``swap()``/``rollback()`` when called without confirmation."""


@dataclass
class SampleCheck:
    """One probed document's verification outcome (DoD sample checks)."""

    doc_id: str
    path: str
    present: bool
    has_vector: bool
    search_found: bool
    query_used: str


@dataclass
class MigrationReport:
    """Outcome of a ``dry_run()``/``swap()``/``rollback()`` call."""

    mode: str
    engine_mode_at_run: str
    docs_total: int = 0
    docs_copied: int = 0
    docs_without_vector: int = 0
    docs_needing_reindex: int = 0
    docs_requeued: int = 0
    docs_source_missing: int = 0
    chunks_total_source: int = 0
    chunks_copied: int = 0
    chunks_excluded_pending_reindex: int = 0
    next_docs_index: str = ""
    next_chunks_index: str = ""
    swapped: bool = False
    sample_checks: List[SampleCheck] = field(default_factory=list)
    reindex_paths: List[str] = field(default_factory=list)
    source_missing_paths: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["sample_checks"] = [dict(c.__dict__) for c in self.sample_checks]
        return d


class MigrationV4Runner:
    """Drives the dry-run/swap/rollback phases of the US-112 migration.

    Every store-pointing parameter (``docs_index_name``, ``chunks_index_name``,
    ``lancedb_path``) defaults to the CURRENT config's production values when
    omitted — this class has no built-in notion of "test" vs "production",
    that isolation is entirely the test suite's responsibility (tests/conftest.py
    patches this constructor during pytest sessions so an un-overridden — i.e.
    production-pointing — instantiation fails loudly there; see US-112's
    prod-store guard extension).
    """

    DOCS_EMBEDDER_NAME = "default"
    CHUNKS_EMBEDDER_NAME = MeiliChunkStore.EMBEDDER_NAME

    def __init__(
        self,
        config: Optional[Any] = None,
        docs_index_name: Optional[str] = None,
        chunks_index_name: Optional[str] = None,
        lancedb_path: Optional[str] = None,
        lancedb_table_name: Optional[str] = None,
        docs_next_name: Optional[str] = None,
        chunks_next_name: Optional[str] = None,
        task_queue: Optional[Any] = None,
        sample_size: int = 5,
    ):
        from aitao.core.config import get_config

        self.config = config or get_config()
        ms = self.config.search.meilisearch
        ldb = self.config.search.lancedb

        self.docs_index_name = docs_index_name or ms.index_name
        self.chunks_index_name = chunks_index_name or ms.chunks_index
        self.lancedb_path = str(lancedb_path or path_manager.get_vector_db_path())
        self.lancedb_table_name = lancedb_table_name or ldb.table_name
        self.docs_next_name = docs_next_name or f"{self.docs_index_name}_next"
        self.chunks_next_name = chunks_next_name or f"{self.chunks_index_name}_next"
        # US-113: dimension now lives in [search.embedding] (neutral name —
        # [search.lancedb] no longer describes a real store); table_name
        # stays on [search.lancedb], the one legacy key this migration still
        # needs (which LanceDB table to read from the pre-4.0 store on disk).
        self.dimension = self.config.search.embedding.dimension
        self.sample_size = sample_size
        self.task_queue = task_queue
        self.logger = get_logger("search.migrate_v4")

    # ------------------------------------------------------------------
    # Public phases
    # ------------------------------------------------------------------

    def dry_run(self) -> MigrationReport:
        """Read + classify + build "_next" fully. NEVER swaps, NEVER requeues."""
        report = self._build(requeue=False)
        report.mode = "dry_run"
        return report

    def swap(self, confirmed: bool = False) -> MigrationReport:
        """Build "_next" fresh, swap onto the live names, THEN requeue.

        Raises ``MigrationConfirmationRequired`` unless ``confirmed=True`` —
        the CLI sets this only after an interactive Y/N or ``--yes``.
        """
        if not confirmed:
            raise MigrationConfirmationRequired(
                "swap() requires confirmed=True — review the dry-run report first."
            )
        report = self._build(requeue=False)
        rebuilder = IndexRebuilder(self._raw_client(), logger=self.logger)
        rebuilder.swap_back(self.docs_index_name, self.docs_next_name)
        rebuilder.swap_back(self.chunks_index_name, self.chunks_next_name)
        requeued = self._requeue(report.reindex_paths)
        report.mode = "swap"
        report.swapped = True
        report.docs_requeued = len(requeued)
        return report

    def rollback(self, confirmed: bool = False) -> MigrationReport:
        """Re-swap the same pairs — undoes a previous ``swap()``. No rebuild."""
        if not confirmed:
            raise MigrationConfirmationRequired(
                "rollback() requires confirmed=True."
            )
        raw = self._raw_client()
        # Refuse to "roll back" when there is nothing to roll back to: if a
        # "_next" index is missing, no prior swap() happened under these names
        # — letting _ensure_swap_pair_exists() proceed would create an EMPTY
        # placeholder and swap the LIVE data out into "_next".
        for name in (self.docs_next_name, self.chunks_next_name):
            try:
                raw.get_index(name)
            except Exception:
                raise IndexRebuildError(
                    f"rollback: index '{name}' does not exist — no prior swap to undo."
                )
        rebuilder = IndexRebuilder(raw, logger=self.logger)
        rebuilder.swap_back(self.docs_index_name, self.docs_next_name)
        rebuilder.swap_back(self.chunks_index_name, self.chunks_next_name)
        return MigrationReport(
            mode="rollback",
            engine_mode_at_run="fusion",
            next_docs_index=self.docs_next_name,
            next_chunks_index=self.chunks_next_name,
            swapped=True,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _raw_client(self) -> Any:
        import meilisearch

        ms = self.config.search.meilisearch
        return meilisearch.Client(ms.url, ms.api_key or None)

    def _build(self, requeue: bool) -> MigrationReport:
        t0 = time.time()

        doc_vectors = read_source_doc_vectors(self.lancedb_path, self.lancedb_table_name)
        chunk_rows = read_source_chunks(self.lancedb_path)

        from aitao.search.meilisearch_client import MeilisearchClient

        # ensure_embedder=False: this is a READ of the LIVE, pre-migration
        # index — merely enumerating its documents must never have the side
        # effect of configuring an embedder on it (see meilisearch_fusion.py).
        docs_client = MeilisearchClient(
            index_name=self.docs_index_name, config=self.config, ensure_embedder=False,
        )
        documents = read_live_documents(docs_client)

        classifications = classify_documents(documents, doc_vectors)
        copy_ids = {c.doc_id for c in classifications if c.population == "copy"}
        reindex = [c for c in classifications if c.population == "reindex"]
        to_requeue = [c for c in reindex if c.source_exists]
        source_missing = [c for c in reindex if not c.source_exists]

        chunk_records = chunks_for_copy_population(chunk_rows, copy_ids)

        raw_client = self._raw_client()
        rebuilder = IndexRebuilder(raw_client, logger=self.logger)

        rebuilder.rebuild(
            self.docs_index_name,
            settings={**DEFAULT_SETTINGS, **US094_SETTINGS},
            documents=[c.next_record for c in classifications],
            primary_key="id",
            embedder=(self.DOCS_EMBEDDER_NAME, self.dimension),
            next_index_name=self.docs_next_name,
            swap=False,
        )
        rebuilder.rebuild(
            self.chunks_index_name,
            settings={
                "searchableAttributes": MeiliChunkStore.SEARCHABLE_ATTRIBUTES,
                "filterableAttributes": MeiliChunkStore.FILTERABLE_ATTRIBUTES,
                "typoTolerance": {
                    "enabled": True,
                    "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
                },
                **US094_SETTINGS,
            },
            documents=chunk_records,
            primary_key="chunk_id",
            embedder=(self.CHUNKS_EMBEDDER_NAME, self.dimension),
            next_index_name=self.chunks_next_name,
            swap=False,
        )

        samples = self._sample_checks(classifications, raw_client)

        requeued_paths: List[str] = []
        if requeue and to_requeue:
            requeued_paths = self._requeue([c.path for c in to_requeue])

        return MigrationReport(
            mode="build",
            engine_mode_at_run="fusion",
            docs_total=len(classifications),
            docs_copied=len(copy_ids),
            docs_without_vector=sum(
                1 for c in classifications if c.population == "copy" and not c.has_vector
            ),
            docs_needing_reindex=len(reindex),
            docs_requeued=len(requeued_paths),
            docs_source_missing=len(source_missing),
            chunks_total_source=len(chunk_rows),
            chunks_copied=len(chunk_records),
            chunks_excluded_pending_reindex=len(chunk_rows) - len(chunk_records),
            next_docs_index=self.docs_next_name,
            next_chunks_index=self.chunks_next_name,
            swapped=False,
            sample_checks=samples,
            reindex_paths=[c.path for c in to_requeue],
            source_missing_paths=[c.path for c in source_missing],
            elapsed_s=round(time.time() - t0, 2),
        )

    def _sample_checks(self, classifications, raw_client: Any) -> List[SampleCheck]:
        """Probe up to ``sample_size`` "copy" documents against "_next": is the
        document present, does it carry a (non-null) vector, does a hybrid
        search using its own title/content find it back."""
        candidates = [
            c for c in classifications if c.population == "copy" and c.has_vector
        ][: self.sample_size]

        index = raw_client.index(self.docs_next_name)
        checks: List[SampleCheck] = []
        for c in candidates:
            try:
                index.get_document(c.doc_id)
                present = True
            except Exception:
                present = False

            title = (c.next_record.get("title") or "").strip()
            query = title or (c.next_record.get("content") or "")[:30]
            found = False
            try:
                result = index.search(query, {
                    "hybrid": {"semanticRatio": 0.5, "embedder": self.DOCS_EMBEDDER_NAME},
                    "vector": c.next_record["_vectors"]["default"],
                    "limit": 5,
                })
                hits = result.get("hits", []) if isinstance(result, dict) else result.hits
                found = any(
                    (h.get("id") if isinstance(h, dict) else h.get("id")) == c.doc_id
                    for h in hits
                )
            except Exception as e:
                self.logger.warning(f"Sample search failed for {c.doc_id}: {e}")

            checks.append(SampleCheck(
                doc_id=c.doc_id, path=c.path, present=present, has_vector=True,
                search_found=found, query_used=query[:60],
            ))
        return checks

    def _requeue(self, paths: List[str]) -> List[str]:
        """Enqueue a full reindex task for every CJK-affected document whose
        source file still exists — ONLY called after the swap has committed
        (never during dry_run), so the worker's eventual write always targets
        the now-live index, never the about-to-be-replaced one."""
        if not paths:
            return []
        queue = self.task_queue
        if queue is None:
            from aitao.indexation.queue import TaskQueue

            queue = TaskQueue()
        from aitao.indexation.queue_models import TaskType

        requeued = []
        for path in paths:
            queue.add_task(
                path, task_type=TaskType.REINDEX.value,
                metadata={"force": True, "reason": "migration_v4_cjk_glue"},
            )
            requeued.append(path)
        return requeued
