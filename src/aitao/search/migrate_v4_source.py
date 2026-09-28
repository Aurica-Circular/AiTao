# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# migrate_v4_source.py — US-112 migration: reading the EXISTING stores and
# classifying every document into the two migration populations.
#
# Source of truth for the migration (ÉPIC-31, US-112 — CADRAGE.md "US-112"):
#   - LanceDB `chunks` table            -> excerpt vectors (chat/RAG stage).
#   - LanceDB doc-level table           -> per-document vectors (/api/search
#     stage), table name from `[search.lancedb] table_name` (aitao_embeddings).
#   - the LIVE Meilisearch documents index -> full-text content, already
#     correct for search TODAY (rrf never needed a vector there).
# Nothing here ever re-embeds: every vector copied into the "_next" indices
# (built by search.migrate_v4.MigrationV4Runner via search.index_rebuild) is
# read verbatim from LanceDB — proven at real scale by the US-110 gate study
# (EPIC-31-fusion-v4/scripts/build_us110_{chunks,docs}.py).
#
# CJK gluing (89-6, US-111) changed how CONTENT is produced at ingestion time
# from now on — documents stored BEFORE that fix may carry stray spaces
# between CJK ideographs, which means their stored vector was computed
# against the WRONG (gappy) text. glue_cjk_content() is idempotent and
# deterministic (core.cjk_glue): applying it to already-clean content is a
# no-op, so it doubles as the exact test for "is this document affected".
# Two populations fall out of that single per-document check:
#   - COPY: gluing changes nothing -> the stored vector is trustworthy, copy
#     content + vector into "_next" verbatim, zero re-embedding.
#   - REINDEX: gluing changes the content -> the stored vector was computed
#     on the wrong text and must NOT be copied. The document still needs an
#     entry in "_next" so it does not vanish from search while a real
#     reindex (extraction -> glue -> chunk -> embed, US-111's own pipeline)
#     catches up post-swap — pushed with the GLUED content (best available
#     text) and an explicit null-vector opt-out (see
#     search.meilisearch_fusion._fusion_vectors_payload). Its excerpts are
#     excluded from the chunk migration entirely (see migrate_v4.py):
#     serving stale, wrongly-embedded chunks is worse than a temporary gap
#     the real reindex fills in.
#
# Raw ``lancedb.connect()`` is used here (not LanceDBClient/ChunkStore) to
# mirror the study's proven read method — READ ONLY, ``table.to_pandas()``,
# never opened for write. tests/conftest.py's prod-store guard patches
# ``lancedb.connect`` itself (ÉPIC-31, US-112 addition) so a test pointing
# this at the production directory fails exactly like it would for the
# wrapped clients.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from aitao.core.cjk_glue import glue_cjk_content
from aitao.core.logger import get_logger

logger = get_logger("search.migrate_v4_source")

# Document fields copied verbatim from the live index into "_next" (superset
# tolerant — extra metadata keys present on a document are preserved too via
# **doc, only these are ever overwritten/interpreted).
_KNOWN_DOC_FIELDS = (
    "id", "path", "title", "content", "category", "language",
    "file_type", "file_size", "created_at", "updated_at",
)


def read_source_doc_vectors(lancedb_path: str, table_name: str) -> Dict[str, List[float]]:
    """Doc-level vectors keyed by id, read verbatim from LanceDB (no re-embed).

    Returns an empty dict if the table/database does not exist (a fresh
    install that never ran the "rrf" engine) — the migration then treats
    every document as vector-less, which is a valid (if degraded) outcome.
    """
    import lancedb

    try:
        db = lancedb.connect(str(lancedb_path))
        table = db.open_table(table_name)
    except Exception as e:
        logger.warning(f"No source doc-level table ({table_name}): {e}")
        return {}

    df = table.to_pandas()
    vectors: Dict[str, List[float]] = {}
    for row in df.to_dict(orient="records"):
        vec = row.get("vector")
        if vec is None:
            continue
        vectors[row["id"]] = vec.tolist() if hasattr(vec, "tolist") else list(vec)
    return vectors


def read_source_chunks(lancedb_path: str, table_name: str = "chunks") -> List[Dict[str, Any]]:
    """Excerpt rows read verbatim from the LanceDB ``chunks`` table.

    Returns an empty list if the table/database does not exist.
    """
    import lancedb

    try:
        db = lancedb.connect(str(lancedb_path))
        table = db.open_table(table_name)
    except Exception as e:
        logger.warning(f"No source chunks table ({table_name}): {e}")
        return []

    df = table.to_pandas()
    rows: List[Dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        vec = row.get("vector")
        vector = vec.tolist() if hasattr(vec, "tolist") else (list(vec) if vec is not None else None)
        rows.append({
            "chunk_id": row.get("chunk_id"),
            "doc_id": row.get("doc_id"),
            "path": row.get("path") or "",
            "title": row.get("title") or "",
            "content": row.get("content") or "",
            "chunk_index": row.get("chunk_index", 0),
            "total_chunks": row.get("total_chunks", 1),
            "vector": vector,
        })
    return rows


def read_live_documents(docs_client: Any, page_size: int = 1000) -> List[Dict[str, Any]]:
    """Every document currently in the LIVE Meilisearch documents index.

    ``docs_client`` is a (guarded) ``search.meilisearch_client.MeilisearchClient``
    pointed at the live index — its ``.index`` (raw meilisearch-python Index)
    is paginated here for full records (unlike
    ``MeilisearchAdminMixin.get_all_document_paths``, which only fetches the
    ``path`` field). Read-only: ``index.get_documents()`` never writes.
    """
    docs: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = docs_client.index.get_documents({"limit": page_size, "offset": offset})
        results = page.results if hasattr(page, "results") else page.get("results", page)
        batch = [r if isinstance(r, dict) else dict(r) for r in results]
        if not batch:
            break
        docs.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return docs


@dataclass
class DocClassification:
    """One document's migration outcome (ÉPIC-31, US-112)."""

    doc_id: str
    path: str
    population: str  # "copy" | "reindex"
    has_vector: bool
    source_exists: Optional[bool] = None  # only set for "reindex"
    next_record: Dict[str, Any] = field(default_factory=dict)


def classify_documents(
    documents: List[Dict[str, Any]],
    doc_vectors: Dict[str, List[float]],
) -> List[DocClassification]:
    """Classify every live document into population "copy" or "reindex".

    See the module docstring for the exact rule (CJK gluing changes the
    stored content -> "reindex"; unchanged -> "copy"). Builds the record
    ready to push into the "_next" documents index for EITHER population —
    the caller (search.migrate_v4) only decides swap timing and requeueing.
    """
    out: List[DocClassification] = []
    for doc in documents:
        doc_id = doc.get("id", "")
        path = doc.get("path", "") or ""
        content = doc.get("content", "") or ""
        glued = glue_cjk_content(content)

        base = {k: doc.get(k) for k in _KNOWN_DOC_FIELDS if k in doc}
        # Preserve any extra metadata fields verbatim (US-17 mtime etc.).
        extra = {k: v for k, v in doc.items() if k not in _KNOWN_DOC_FIELDS}

        if glued == content:
            vector = doc_vectors.get(doc_id)
            record = {
                **extra, **base, "_vectors": {"default": vector},
                "has_content": bool(content.strip()),
            }
            out.append(DocClassification(
                doc_id=doc_id, path=path, population="copy",
                has_vector=vector is not None, next_record=record,
            ))
        else:
            source_exists = bool(path) and Path(path).exists()
            record = {
                **extra, **base, "content": glued, "_vectors": {"default": None},
                "has_content": bool(glued.strip()),
            }
            out.append(DocClassification(
                doc_id=doc_id, path=path, population="reindex",
                has_vector=False, source_exists=source_exists, next_record=record,
            ))
    return out


def chunks_for_copy_population(
    chunks: List[Dict[str, Any]], copy_doc_ids: set,
) -> List[Dict[str, Any]]:
    """Excerpts belonging to a "copy" document, ready for the "_next" chunk
    index (chunk_id primary key + userProvided vector) — verbatim, no re-embed.

    Excerpts of a "reindex" document are deliberately dropped here (see the
    module docstring): they would carry the same stale-embedding problem as
    their parent document's vector, and the real reindex pipeline (US-111)
    rewrites them wholesale (delete_by_doc_id + add_chunks) once it processes
    the requeued file, post-swap.
    """
    out: List[Dict[str, Any]] = []
    for row in chunks:
        if row.get("doc_id") not in copy_doc_ids:
            continue
        out.append({
            "chunk_id": row["chunk_id"],
            "doc_id": row["doc_id"],
            "title": row["title"],
            "path": row["path"],
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "total_chunks": row["total_chunks"],
            "_vectors": {"default": row["vector"]},
        })
    return out
