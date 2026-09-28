# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# item_preparer.py — per-file prepare phase shared by DocumentIndexer.index_file()
# and the batched ingestion path (ÉPIC-31, US-111, absorbs US-093).
#
# Extraction/OCR/license-gate/dedup/Document-construction — everything
# index_file() does BEFORE writing anything to a store. Split out of
# batch_indexer.py (which owns the write/flush side) and out of indexer.py
# (already large) purely to respect the project's per-file line-count
# convention; there is no behavioural reason for the split.
#
# The OCR sub-step is delegated back to DocumentIndexer._apply_ocr_fallback()
# (defined in indexer.py) rather than inlined here, so existing tests can keep
# patching ``indexation.indexer._get_ocr_router`` directly (see
# test_ocr_pipeline.py) — a call made from a different module's namespace
# would not be visible to that patch.

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from aitao.core.cjk_glue import glue_cjk_content
from aitao.core.language_tags import normalize_language
from aitao.core.license import LicenseManager, PremiumFeatureError
from aitao.core.models import Document
from aitao.indexation.extraction_timeout import ExtractionTimeoutError, extract_with_timeout
from aitao.indexation.indexer_helpers import (
    IndexResult,
    generate_doc_id,
    get_document_category,
    get_document_title,
    normalize_path,
    prepare_document_metadata,
)

if TYPE_CHECKING:
    from aitao.indexation.indexer import DocumentIndexer


@dataclass
class PreparedItem:
    """One file's outcome after ``prepare_item()``: extraction, OCR, license
    gate, dedup and Document construction — everything index_file() does
    before touching any store. ``early_result`` is set when the file should
    short-circuit (not found, already indexed, Premium-gated, extraction
    failed): the caller must return it as-is, never write it."""

    path: str
    doc_id: str
    early_result: Optional[IndexResult] = None
    document: Optional["Document"] = None
    doc_vector: Optional[List[float]] = None
    extraction_time_ms: float = 0.0
    word_count: int = 0
    language: Optional[str] = None


def prepare_item(
    indexer: "DocumentIndexer", file_path: str, force: bool = False
) -> PreparedItem:
    """Extract/OCR/license-gate/dedup ONE file — everything index_file() does
    before writing to any store. Bulk logic for
    ``DocumentIndexer._prepare_item()`` (delegated there to keep indexer.py
    under the project's line-count convention); never touches LanceDB /
    Meilisearch / the chunk store, so it is always safe to run ahead of time
    for a whole group before any write happens.
    """
    # NFC-normalize the path up front (US-RAG-name): a macOS NFD filename and
    # its NFC form must not index the same file twice. Everything downstream
    # (id, stored path, metadata) then uses one canonical form.
    path = Path(normalize_path(str(file_path)))
    doc_id = generate_doc_id(str(path))

    def _early(result: IndexResult) -> PreparedItem:
        return PreparedItem(path=str(path), doc_id=doc_id, early_result=result)

    # Validate file
    if not path.exists():
        return _early(IndexResult(
            path=str(path), doc_id=doc_id, success=False,
            error=f"File not found: {path}",
        ))
    if not path.is_file():
        return _early(IndexResult(
            path=str(path), doc_id=doc_id, success=False,
            error=f"Not a file: {path}",
        ))
    # Defensive guard (US-088): never index files under the system temp dir —
    # this is how test artifacts leaked dead-path chunks into the prod stores.
    # Tests that legitimately index temp files set allow_temp_paths=True.
    if not indexer.allow_temp_paths:
        temp_root = Path(tempfile.gettempdir()).resolve()
        try:
            path.resolve().relative_to(temp_root)
            return _early(IndexResult(
                path=str(path), doc_id=doc_id, success=False,
                error=f"Refused: path under system temp dir ({temp_root})",
            ))
        except ValueError:
            pass

    # US-28a — the file demonstrably exists again: lift any trash flag
    # (restored file or remounted volume). Done before the dedup check so
    # an unchanged restored file (same mtime → skip) is unflagged too.
    try:
        from aitao.indexation.trash import get_trash_registry
        get_trash_registry().unmark(str(path))
    except Exception:
        pass

    # Check deduplication — a file modified since its last indexing is NOT
    # considered indexed (stored mtime comparison), so scanner-detected
    # changes actually reach the index (US-17 hotfix).
    if not force and indexer._is_already_indexed(doc_id, path):
        return _early(IndexResult(
            path=str(path), doc_id=doc_id, success=True,
            meilisearch_indexed=True,
            error="Already indexed (use force=True to re-index)",
        ))

    # Premium document perimeter (PRD §5): advanced formats (docx, xlsx, odt,
    # epub…) require a Premium licence to be indexed. Text formats stay Core;
    # scanned PDF / images are gated separately at the OCR step (1b).
    #
    # aitao.core.license always ships with the core (US-138-1: it is now a
    # small facade with no secret in it) — no need for a tolerant import.
    # Without the separately installed aitao-premium package registered,
    # LicenseManager().require_premium() always raises, so an unlicensed
    # install behaves exactly like a Core-only user.
    if LicenseManager.is_premium_extension(path.suffix):
        try:
            LicenseManager().require_premium("advanced_formats")
        except PremiumFeatureError:
            indexer.logger.info(
                "Premium format skipped (Core edition)",
                metadata={"path": str(path), "ext": path.suffix.lower()},
            )
            return _early(IndexResult(
                path=str(path), doc_id=doc_id, success=False,
                error="Premium format — advanced document formats require a "
                      "Premium licence",
            ))

    # Step 1: Extract text — bounded by a per-file timeout (US-113 volet 2):
    # a pathological file (e.g. a text-less vector-drawing PDF on a
    # not-yet-materialized cloud mount) can block on I/O forever with zero
    # CPU, freezing the whole worker. See indexation.extraction_timeout for
    # the thread-vs-process trade-off this makes.
    extract_start = time.perf_counter()
    timeout_s = (
        indexer.config.indexing.extraction_timeout_s if indexer.config else 300
    )
    try:
        extraction = extract_with_timeout(indexer.text_extractor, path, timeout_s)
    except ExtractionTimeoutError as exc:
        extract_time = (time.perf_counter() - extract_start) * 1000
        indexer.logger.error(
            "Extraction timeout — skipping file, worker continues",
            metadata={"path": str(path), "timeout_s": timeout_s},
        )
        return _early(IndexResult(
            path=str(path), doc_id=doc_id, success=False,
            error=f"Extraction timeout: {exc}",
            extraction_time_ms=extract_time,
        ))
    extract_time = (time.perf_counter() - extract_start) * 1000

    if not extraction.success:
        return _early(IndexResult(
            path=str(path), doc_id=doc_id, success=False,
            error=f"Extraction failed: {extraction.error}",
            extraction_time_ms=extract_time,
        ))

    # Step 1b: OCR fallback (mutates extraction in place) — see module docstring.
    indexer._apply_ocr_fallback(path, extraction)

    # Step 1c (ÉPIC-31, US-111, absorbs backlog 89-6): glue stray CJK gaps in
    # the extracted/OCR'd text BEFORE chunking/embedding/writing — see
    # core.cjk_glue for the exact rules. Applies to BOTH engines (rrf and
    # fusion): this is a content fix, not a fusion-only one. Re-indexing
    # documents already stored before this fix ships is out of scope here
    # (US-112 migration).
    if extraction.text:
        extraction.text = glue_cjk_content(extraction.text)

    # Step 2: Prepare document data
    title = get_document_title(path, extraction)
    category = get_document_category(path)
    # US-85c: collapse the raw detected tag (langdetect / Apple Vision / Tesseract
    # notations) to a canonical short code so the `language` filter behaves the
    # same for native and scanned documents. The precise tag is preserved in
    # metadata below so nothing is lost (e.g. zh-Hant vs zh-Hans).
    raw_language = extraction.language
    language = normalize_language(raw_language)
    file_type = path.suffix.lower()
    file_size = path.stat().st_size
    metadata = prepare_document_metadata(path, extraction)
    if raw_language and raw_language.strip().lower() != language:
        metadata["language_precise"] = raw_language

    # Validated domain object that flows into the search clients (US-23b)
    document = Document(
        id=doc_id, path=str(path), title=title, content=extraction.text,
        language=language, category=category, file_type=file_type,
        file_size=file_size, metadata=metadata,
    )

    # ÉPIC-31 (US-111/US-113): compute the doc-level vector ONCE here
    # (reusing the chunk store's already-loaded bge-m3 model) so it can be
    # reused for the Meilisearch document `_vectors` (replacing the US-110
    # null opt-out) — never embedding the same content twice.
    #
    # US-113 finding (a): this used to be gated behind
    # ``indexer._is_fusion_engine()``, which reads ``indexer.config`` — an
    # attribute NOT set by any real production call site (worker.py,
    # worker_batch.py, api/routes/ingest.py, the CLI all build a bare
    # ``DocumentIndexer()``). Since fusion is now the only engine (the
    # "[search] engine" flag is gone), the gate is removed outright: whenever
    # a chunk store capable of embedding is available, the vector is
    # computed, regardless of whether a ConfigManager was passed to the
    # constructor. ``indexer.chunk_store`` already falls back to the global
    # config on its own (storage.repository.make_chunk_store), so this now
    # works from every real call site, not just tests that inject a config.
    doc_vector: Optional[List[float]] = None
    if document.content and document.content.strip():
        store = indexer.chunk_store
        if store is not None and hasattr(store, "embed_document"):
            try:
                doc_vector = store.embed_document(document.content)
            except Exception as exc:
                indexer.logger.warning(f"Doc-level embedding failed, falling back: {exc}")

    return PreparedItem(
        path=str(path), doc_id=doc_id, document=document, doc_vector=doc_vector,
        extraction_time_ms=extract_time,
        word_count=extraction.word_count, language=language,
    )
