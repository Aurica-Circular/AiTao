# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# batch_indexer.py — grouped-write batching for DocumentIndexer (ÉPIC-31,
# US-111, absorbs US-093).
#
# DocumentIndexer.index_file() processes one file end-to-end, including one
# blocking Meilisearch add+wait per store — the worst case for Meilisearch,
# which is built to swallow batches in a single indexing job. This module adds
# the batched alternative: prepare up to `batch_size` files independently
# (indexation.item_preparer.prepare_item() — index_file()'s own per-file
# steps, so single-file semantics never change), then write the WHOLE ready
# group with ONE Meilisearch task for documents (add_documents_batch) and ONE
# for chunks, instead of N blocking calls.
#
# "1 file = 1 state" rule (backlog note above US-093/094): the chunk store's
# old excerpts for a group are deleted ONLY as a precondition to adding the
# new ones — if the delete step fails, the WHOLE group is excluded from the
# add step and reported as failed (never left half old / half new). A group
# whose Meilisearch write fails entirely is reported failed for every file in
# it — the caller (BackgroundWorker) requeues them; no silent partial state.
#
# batch_size <= 1 (or a lone file) always calls DocumentIndexer.index_file()
# directly — bit-identical to pre-US-111 behaviour, no batching machinery
# involved at all.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from aitao.indexation.indexer_helpers import BatchIndexResult, IndexResult
from aitao.indexation.interfaces import Chunk
from aitao.indexation.item_preparer import PreparedItem, prepare_item

if TYPE_CHECKING:
    from aitao.core.models import Document
    from aitao.indexation.indexer import DocumentIndexer

__all__ = ["PreparedItem", "prepare_item", "index_files_batched"]


@dataclass
class _ReadyItem:
    """A PreparedItem past the early-exit gate, with its chunks computed
    (pure — not yet stored) so the flush phase can batch the store writes."""

    prepared: PreparedItem
    chunks: List[Chunk] = field(default_factory=list)
    chunk_error: Optional[str] = None


def index_files_batched(
    indexer: "DocumentIndexer",
    file_paths: List[str],
    force: bool = False,
    batch_size: Optional[int] = None,
) -> BatchIndexResult:
    """Index files, grouping writes by ``batch_size`` (US-093).

    Contract kept identical to ``DocumentIndexer.index_files()``: the
    returned ``results`` list has exactly one ``IndexResult`` per requested
    path, in the SAME order — callers (e.g. the worker) can zip ``file_paths``
    with the result to know which file succeeded/failed, without relying on
    path-string matching (paths get NFC-normalized internally).
    """
    size = batch_size if batch_size and batch_size > 0 else 1

    batch_start = time.perf_counter()
    results: List[IndexResult] = []

    for offset in range(0, len(file_paths), size):
        group = file_paths[offset : offset + size]
        if len(group) <= 1:
            # Bit-identical to pre-US-111 behaviour — also covers batch_size=1.
            results.extend(indexer.index_file(p, force=force) for p in group)
            continue
        results.extend(_flush_group(indexer, group, force))

    batch_result = BatchIndexResult(total=len(file_paths), results=results)
    for r in results:
        if r.success:
            if r.error and "Already indexed" in r.error:
                batch_result.skipped += 1
            else:
                batch_result.successful += 1
        else:
            batch_result.failed += 1
    batch_result.total_time_ms = (time.perf_counter() - batch_start) * 1000

    indexer.logger.info(
        f"Batched indexing complete: {batch_result.successful} indexed, "
        f"{batch_result.skipped} skipped, {batch_result.failed} failed "
        f"(batch_size={size})"
    )
    return batch_result


def _compute_chunks(
    indexer: "DocumentIndexer", item: PreparedItem
) -> Tuple[List[Chunk], Optional[str]]:
    """Pure chunk computation (no store write) — mirrors the chunking half of
    ``indexer_helpers.chunk_and_store``, split out so the store write
    (delete + add) can be batched across an entire group."""
    document = item.document
    if not (indexer.chunking_pipeline and indexer.chunk_store and document.content):
        return [], None
    try:
        result = indexer.chunking_pipeline.chunk_document(
            text=document.content, doc_id=item.doc_id, path=document.path,
            title=document.title,
            metadata={
                "category": document.category,
                "language": document.language,
                "file_type": document.file_type,
            },
        )
        if result.success and result.chunks:
            return result.chunks, None
        if not result.success:
            return [], f"Chunking: {result.error}"
        return [], None
    except Exception as exc:  # pragma: no cover - defensive, mirrors chunk_and_store
        return [], f"Chunking: {exc}"


def _flush_group(
    indexer: "DocumentIndexer", file_paths: List[str], force: bool
) -> List[IndexResult]:
    """Prepare every file in the group, then write the ready ones as ONE batch."""
    prepared = [prepare_item(indexer, p, force=force) for p in file_paths]

    results: List[Optional[IndexResult]] = [None] * len(prepared)
    ready: List[_ReadyItem] = []
    ready_idx: List[int] = []

    for i, item in enumerate(prepared):
        if item.early_result is not None:
            results[i] = item.early_result
            continue
        chunks, chunk_error = _compute_chunks(indexer, item)
        ready.append(_ReadyItem(prepared=item, chunks=chunks, chunk_error=chunk_error))
        ready_idx.append(i)

    if ready:
        written = _write_ready_batch(indexer, ready)
        for idx, result in zip(ready_idx, written):
            results[idx] = result

    return results  # type: ignore[return-value]


def _document_record(document: "Document") -> Dict[str, Any]:
    """Build the plain dict ``MeilisearchAdminMixin.add_documents_batch``
    expects, from a validated ``Document`` (US-23b keeps this object flowing
    through the single-file path; the batch path needs the same fields)."""
    return {
        "path": document.path,
        "title": document.title,
        "content": document.content,
        "category": document.category or "autre",
        "language": document.language,
        "file_type": document.file_type,
        "file_size": document.file_size,
        "metadata": document.metadata,
    }


def _write_ready_batch(
    indexer: "DocumentIndexer", items: List[_ReadyItem]
) -> List[IndexResult]:
    """Write a whole group with ONE Meilisearch task for documents and ONE
    for chunks (US-093), instead of index_file()'s per-file blocking calls."""
    from aitao.core.events import DOCUMENT_INDEXED, event_bus
    from aitao.indexation.indexer_helpers import index_in_meilisearch

    logger = indexer.logger

    # --- Meilisearch documents: ONE add_documents_batch task for the group.
    meili_ok: Dict[str, bool] = {it.prepared.doc_id: False for it in items}
    meili_error: Optional[str] = None
    if indexer.meilisearch and hasattr(indexer.meilisearch, "add_documents_batch"):
        try:
            records = [_document_record(it.prepared.document) for it in items]
            vectors = [it.prepared.doc_vector for it in items]
            indexer.meilisearch.add_documents_batch(records, vectors=vectors)
            for it in items:
                meili_ok[it.prepared.doc_id] = True
        except Exception as e:
            meili_error = f"Meilisearch batch: {e}"
            logger.error(meili_error)
    elif indexer.meilisearch:
        # Repository double injected (tests) without add_documents_batch —
        # degrade to per-file writes rather than silently dropping the group.
        for it in items:
            ok, err = index_in_meilisearch(
                indexer.meilisearch, it.prepared.document, logger,
                vector=it.prepared.doc_vector,
            )
            meili_ok[it.prepared.doc_id] = ok
            if err:
                logger.warning(f"Per-file Meilisearch fallback failed for {it.prepared.path}: {err}")

    # --- Chunk store: ONE delete-by-filter + ONE add for the WHOLE group.
    # "1 file = 1 state": the delete must be confirmed before any new chunk
    # is added, so a delete failure excludes the WHOLE group from the add
    # step rather than risking old+new chunks coexisting for a file.
    chunks_ok: Dict[str, int] = {it.prepared.doc_id: 0 for it in items}
    chunk_error: Optional[str] = None
    store = indexer.chunk_store
    if store is not None:
        doc_ids = [it.prepared.doc_id for it in items]
        try:
            if hasattr(store, "delete_by_doc_ids"):
                store.delete_by_doc_ids(doc_ids)
            else:
                for doc_id in doc_ids:
                    store.delete_by_doc_id(doc_id)
            all_chunks = [c for it in items for c in it.chunks]
            if all_chunks:
                store.add_chunks(all_chunks)
            for it in items:
                chunks_ok[it.prepared.doc_id] = len(it.chunks)
        except Exception as e:
            chunk_error = f"ChunkStore batch: {e}"
            logger.error(chunk_error)
            # chunks_ok stays 0 for every file — surfaced as an error below so
            # the caller requeues them; never reported as silently succeeded.

    results: List[IndexResult] = []
    for it in items:
        errors: List[str] = []
        if it.chunk_error:
            errors.append(it.chunk_error)
        if meili_error:
            errors.append(meili_error)
        if chunk_error:
            errors.append(chunk_error)

        ok = meili_ok[it.prepared.doc_id] or indexer.skip_meilisearch
        result = IndexResult(
            path=it.prepared.path, doc_id=it.prepared.doc_id, success=ok,
            meilisearch_indexed=meili_ok[it.prepared.doc_id],
            chunks_indexed=chunks_ok[it.prepared.doc_id],
            error="; ".join(errors) if errors else None,
            extraction_time_ms=it.prepared.extraction_time_ms,
            word_count=it.prepared.word_count, language=it.prepared.language,
        )
        results.append(result)
        if result.success:
            event_bus.publish(
                DOCUMENT_INDEXED, doc_id=it.prepared.doc_id, path=it.prepared.path,
                chunks_indexed=result.chunks_indexed, language=result.language,
            )
    return results
