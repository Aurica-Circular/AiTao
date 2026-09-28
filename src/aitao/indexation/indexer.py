# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
DocumentIndexer - Orchestrate document indexing pipeline.

This module provides the main DocumentIndexer class that coordinates:
1. Text extraction (TextExtractor)
2. Meilisearch indexing (full-text + semantic vector, fusion engine)
3. Deduplication via SHA256
4. RAG chunking

Data models (IndexResult, BatchIndexResult) and pure helper functions
live in indexer_helpers.py to keep this file focused on orchestration.

ÉPIC-31 (US-113): LanceDB is no longer part of the live write path — v4.0
ships fusion-only (decision D1). A document's semantic representation is now
a vector on the Meilisearch document itself (``_vectors.default``), computed
once in ``indexation.item_preparer.prepare_item`` and reused here.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aitao.core.events import DOCUMENT_DELETED, DOCUMENT_INDEXED, event_bus
from aitao.indexation.text_extractor import TextExtractor
from aitao.indexation.chunker import ChunkingPipeline
from aitao.indexation.chunk_store_meili import MeiliChunkStore
from aitao.indexation.interfaces import ChunkingConfig
from aitao.indexation.indexer_helpers import (
    IndexResult,
    BatchIndexResult,
    generate_doc_id,
    get_document_category,
    index_in_meilisearch,
    chunk_and_store,
)
from aitao.indexation.batch_indexer import PreparedItem
from aitao.indexation import batch_indexer as _batch_indexer
from aitao.storage.repository import (
    DocumentRepository,
    make_meilisearch_repository,
)

try:
    from aitao.core.logger import get_logger
    from aitao.core.config import ConfigManager
except ImportError:
    def get_logger(name):  # noqa: E731
        return logging.getLogger(name)
    ConfigManager = None

# Lazy import to avoid hard-dependency when OCR packages are not installed
def _get_ocr_router():
    from aitao.ocr.router import OCRRouter  # type: ignore[import]
    return OCRRouter()

# Re-export for backward compatibility
__all__ = ["DocumentIndexer", "IndexResult", "BatchIndexResult", "index_file"]


class DocumentIndexer:
    """
    Main document indexing orchestrator.

    Coordinates the full indexing pipeline:
    1. Extract text using TextExtractor
    2. Index full-text + semantic vector in Meilisearch
    3. Handle deduplication and updates
    """

    def __init__(
        self,
        meilisearch_client: Optional[DocumentRepository] = None,
        text_extractor: Optional[TextExtractor] = None,
        chunk_store: Optional[MeiliChunkStore] = None,
        config: Optional[ConfigManager] = None,
        skip_meilisearch: bool = False,
        skip_chunking: bool = False,
        allow_temp_paths: bool = False,
    ):
        self.logger = get_logger("indexer")
        self.config = config

        # Initialize components
        self.text_extractor = text_extractor or TextExtractor()

        self.skip_meilisearch = skip_meilisearch
        self.skip_chunking = skip_chunking
        # US-088: refuse files under the system temp dir (prevents temp/test paths
        # from polluting prod stores). Tests that index temp files opt in explicitly.
        self.allow_temp_paths = allow_temp_paths

        # Lazy initialization for search clients
        self._meilisearch_client = meilisearch_client
        self._chunk_store = chunk_store
        self._chunking_pipeline: Optional[ChunkingPipeline] = None

        if config and not config.chunking.enabled:
            self.skip_chunking = True

    @property
    def meilisearch(self) -> Optional[DocumentRepository]:
        """Get or create the full-text repository."""
        if self.skip_meilisearch:
            return None
        if self._meilisearch_client is None:
            try:
                self._meilisearch_client = make_meilisearch_repository(config=self.config)
            except Exception as e:
                self.logger.error(f"Failed to initialize Meilisearch: {e}")
                return None
        return self._meilisearch_client
    
    @property
    def chunk_store(self) -> Optional[MeiliChunkStore]:
        """Get or create the excerpt store for RAG chunks.

        ÉPIC-31 (US-113): delegates to ``storage.repository.make_chunk_store``,
        which builds ``MeiliChunkStore`` — the dedicated Meilisearch excerpt
        index, the only chunk backend since LanceDB was removed from the live
        path. Same construction ``HybridSearchEngine`` uses on the read side
        (search/hybrid_engine.py).
        """
        if self.skip_chunking:
            return None
        if self._chunk_store is None:
            try:
                from aitao.storage.repository import make_chunk_store

                self._chunk_store = make_chunk_store(config=self.config)
            except Exception as e:
                self.logger.error(f"Failed to initialize chunk store: {e}")
                return None
        return self._chunk_store
    
    @property
    def chunking_pipeline(self) -> Optional[ChunkingPipeline]:
        """Get or create ChunkingPipeline."""
        if self.skip_chunking:
            return None
        if self._chunking_pipeline is None:
            # Load config from config manager
            chunk_config = ChunkingConfig()
            if self.config:
                c = self.config.chunking
                chunk_config = ChunkingConfig.from_dict({
                    "chunk_size": c.chunk_size,
                    "chunk_overlap": c.chunk_overlap,
                    "min_chunk_size": c.min_chunk_size,
                    "max_chunk_size": c.max_chunk_size,
                    "split_on_sentences": c.split_on_sentences,
                    "embedding_model": c.embedding_model,
                })
            self._chunking_pipeline = ChunkingPipeline(chunk_config)
        return self._chunking_pipeline
    
    def _apply_ocr_fallback(self, path: Path, extraction: Any) -> None:
        """Step 1b — OCR for scanned PDFs and images, mutating ``extraction``
        in place. Runs when text extraction yielded nothing AND the extractor
        flagged ``needs_ocr=True``. Gated behind Premium licence: non-premium
        users skip OCR with a warning, the document is still indexed (without
        OCR text). Kept as its own indexer.py method (rather than inlined in
        ``indexation.batch_indexer.prepare_item``, which calls it) so tests
        can keep patching ``indexation.indexer._get_ocr_router`` directly
        (see test_ocr_pipeline.py).
        """
        if not (extraction.metadata.get("needs_ocr") and not extraction.text):
            return
        try:
            ocr_router = _get_ocr_router()
            # The language detected from the native layer is unreliable here:
            # we only reach this branch because that layer was empty or garbage
            # (e.g. a scan's junk text layer that langdetect read as "en").
            # Passing it would force a single wrong-language OCR pass and defeat
            # the router's confidence-based candidate selection (US-086 v6).
            # Pass None so the router tries the configured ocr.languages and
            # keeps the most confident pass (zh-Hant for a Chinese scan).
            ocr_result = ocr_router.extract_sync(path, lang=None)
            if not ocr_result.is_empty:
                extraction.text = ocr_result.text
                extraction.metadata["extraction_method"] = f"ocr:{ocr_result.provider_used}"
                extraction.metadata["ocr_provider"] = ocr_result.provider_used
                extraction.metadata["word_count"] = ocr_result.word_count
                # The native language guess was made on garbage (e.g. "en" for
                # a Chinese scan). The OCR pass that won by confidence is the
                # ground truth — adopt it so the stored language is correct
                # (US-086 v6). Falls back to the native guess if the provider
                # reported none.
                if ocr_result.lang_detected:
                    extraction.metadata["language"] = ocr_result.lang_detected
                self.logger.info(
                    "OCR applied",
                    metadata={
                        "path": str(path),
                        "provider": ocr_result.provider_used,
                        "words": ocr_result.word_count,
                    },
                )
            else:
                self.logger.warning(
                    "OCR returned empty text",
                    metadata={"path": str(path)},
                )
        except Exception as exc:
            # Catches PremiumFeatureError (non-premium edition) AND runtime OCR errors.
            # Document is still indexed without OCR text — no hard failure.
            exc_type = type(exc).__name__
            self.logger.warning(
                "OCR skipped — indexing without text content",
                metadata={
                    "path": str(path),
                    "reason": exc_type,
                    "error": str(exc),
                },
            )

    def _prepare_item(
        self, file_path: str | Path, force: bool = False
    ) -> PreparedItem:
        """Extract/OCR/license-gate/dedup ONE file — everything index_file()
        does before writing to any store (ÉPIC-31, US-111). Shared by
        index_file() (prepare, then write immediately) and
        index_files_batched() (prepare every file in a group first, then
        write the group in ONE combined pass). The bulk of the logic lives in
        ``indexation.batch_indexer.prepare_item`` (kept out of this
        already-large file); this method only delegates.
        """
        return _batch_indexer.prepare_item(self, file_path, force=force)

    def index_file(
        self,
        file_path: str | Path,
        force: bool = False,
    ) -> IndexResult:
        """Index a single file through the full pipeline."""
        import time

        item = self._prepare_item(file_path, force=force)
        if item.early_result is not None:
            return item.early_result

        document = item.document
        doc_id = item.doc_id
        path = Path(item.path)

        # Step 3: Index in Meilisearch (full-text + semantic vector)
        index_start = time.perf_counter()
        errors: list[str] = []

        meilisearch_ok = False
        if self.meilisearch:
            meilisearch_ok, err = index_in_meilisearch(
                self.meilisearch, document, self.logger, vector=item.doc_vector
            )
            if err:
                errors.append(err)

        # Step 4: Chunk for RAG
        chunks_count = 0
        if self.chunking_pipeline and self.chunk_store and document.content:
            chunks_count, err = chunk_and_store(
                self.chunking_pipeline, self.chunk_store, doc_id, path,
                document.title, document.content, document.category,
                document.language, document.file_type,
                self.logger,
            )
            if err:
                errors.append(err)

        index_time = (time.perf_counter() - index_start) * 1000
        success = meilisearch_ok or self.skip_meilisearch

        result = IndexResult(
            path=item.path, doc_id=doc_id, success=success,
            meilisearch_indexed=meilisearch_ok,
            chunks_indexed=chunks_count,
            error="; ".join(errors) if errors else None,
            extraction_time_ms=item.extraction_time_ms, indexing_time_ms=index_time,
            word_count=item.word_count, language=item.language,
        )
        # Notify subscribers (stats, future automations) — never blocks indexing.
        if success:
            event_bus.publish(
                DOCUMENT_INDEXED,
                doc_id=doc_id, path=item.path,
                chunks_indexed=chunks_count, language=item.language,
            )
        return result

    def index_files_batched(
        self,
        file_paths: List[str | Path],
        force: bool = False,
        batch_size: Optional[int] = None,
    ) -> BatchIndexResult:
        """Index files with grouped Meilisearch writes (ÉPIC-31, US-111 —
        absorbs US-093): one add_documents_batch task for the documents of a
        group and one combined write for their chunks, instead of one
        blocking add+wait per file. ``batch_size`` defaults to
        ``[indexing] batch_size`` (1 = today's per-file behaviour). See
        ``indexation.batch_indexer`` for the grouping/flush logic.
        """
        if batch_size is None:
            batch_size = self.config.indexing.batch_size if self.config else 1
        return _batch_indexer.index_files_batched(
            self, [str(p) for p in file_paths], force=force, batch_size=batch_size
        )
    
    def _is_already_indexed(
        self, doc_id: str, file_path: Optional[Path] = None
    ) -> bool:
        """Check if document is already indexed AND still up to date.

        When ``file_path`` is given, the stored ``mtime`` is compared with the
        file on disk: a mismatch means the file changed since indexing, so it
        does NOT count as indexed and will be re-indexed (US-17 hotfix — the
        scanner detected modifications but the indexer skipped them forever).
        """
        if self.meilisearch:
            try:
                doc = self.meilisearch.get_document(doc_id)
                if doc:
                    return self._is_up_to_date(doc, file_path)
            except Exception:
                pass

        return False

    @staticmethod
    def _is_up_to_date(stored_doc: Any, file_path: Optional[Path]) -> bool:
        """True when the stored document matches the file's current mtime.

        Documents indexed before mtime tracking (no ``mtime`` field) are
        treated as up to date — refreshing them requires ``force=True`` —
        so the fix cannot trigger a mass re-indexing of the whole corpus.
        """
        if file_path is None:
            return True
        try:
            stored_mtime = stored_doc.get("mtime")
        except AttributeError:
            stored_mtime = getattr(stored_doc, "mtime", None)
        if stored_mtime is None:
            return True
        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            return True
        return abs(float(stored_mtime) - current_mtime) < 1e-6

    def is_indexed(self, file_path: str) -> bool:
        """Check if a file is already indexed (public API for ingest route)."""
        path = Path(file_path)
        return self._is_already_indexed(generate_doc_id(str(path)), path)

    # ------------------------------------------------------------------
    # Backward compat: delegation methods for tests
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_id(path: str) -> str:
        """Backward compat: delegate to indexer_helpers.generate_doc_id."""
        return generate_doc_id(path)

    @staticmethod
    def _get_category(path) -> str:
        """Backward compat: delegate to indexer_helpers.get_document_category."""
        from pathlib import Path as _Path
        return get_document_category(_Path(path) if not isinstance(path, _Path) else path)
    
    def index_files(
        self,
        file_paths: List[str | Path],
        force: bool = False,
        on_progress: Optional[callable] = None,
    ) -> BatchIndexResult:
        """Index multiple files."""
        import time
        
        batch_start = time.perf_counter()
        
        result = BatchIndexResult(total=len(file_paths))
        
        for i, file_path in enumerate(file_paths):
            index_result = self.index_file(file_path, force=force)
            result.results.append(index_result)
            
            if index_result.success:
                if index_result.error and "Already indexed" in index_result.error:
                    result.skipped += 1
                else:
                    result.successful += 1
            else:
                result.failed += 1
            
            if on_progress:
                on_progress(i + 1, len(file_paths), index_result)
        
        result.total_time_ms = (time.perf_counter() - batch_start) * 1000
        
        self.logger.info(
            f"Batch indexing complete: {result.successful} indexed, "
            f"{result.skipped} skipped, {result.failed} failed"
        )
        
        return result
    
    def index_directory(
        self,
        directory: str | Path,
        recursive: bool = True,
        force: bool = False,
        on_progress: Optional[callable] = None,
    ) -> BatchIndexResult:
        """Index all supported files in a directory."""
        dir_path = Path(directory)
        
        if not dir_path.exists() or not dir_path.is_dir():
            return BatchIndexResult(
                total=0,
                results=[IndexResult(
                    path=str(dir_path),
                    doc_id="",
                    success=False,
                    error=f"Directory not found: {dir_path}"
                )]
            )
        
        # Find all supported files
        supported = self.text_extractor.get_supported_extensions()
        
        if recursive:
            files = [f for f in dir_path.rglob("*") if f.is_file() and f.suffix.lower() in supported]
        else:
            files = [f for f in dir_path.iterdir() if f.is_file() and f.suffix.lower() in supported]
        
        return self.index_files(files, force=force, on_progress=on_progress)
    
    def delete_document(self, file_path: str | Path) -> Tuple[bool, str]:
        """Delete a document from every store, chunks included.

        Removes the document from the Meilisearch document index AND the
        chunk-level excerpt index. Skipping the chunk purge here is what left
        orphan chunks behind (US-088): the RAG engine reads the chunk index,
        so a stale chunk would resurface a deleted document.
        """
        path = str(file_path)
        doc_id = generate_doc_id(path)

        meilisearch_ok = True
        chunks_ok = True
        errors = []

        if self.meilisearch:
            try:
                self.meilisearch.delete(doc_id)
            except Exception as e:
                meilisearch_ok = False
                errors.append(f"Meilisearch: {e}")

        # Purge chunk-level rows so no orphan survives the document deletion.
        if self.chunk_store:
            try:
                self.chunk_store.delete_by_doc_id(doc_id)
            except Exception as e:
                chunks_ok = False
                errors.append(f"ChunkStore: {e}")

        if meilisearch_ok and chunks_ok:
            event_bus.publish(DOCUMENT_DELETED, doc_id=doc_id, path=path)
            return True, f"Deleted document: {doc_id}"
        else:
            return False, "; ".join(errors)

    def indexed_doc_ids(self) -> set:
        """Doc ids present in the Meilisearch document index.

        Powers the scanner's orphan reconciliation (US-086): a file the scanner
        has already 'seen' (recorded in its state) but whose id is NOT in this
        set never actually landed in the store — a failed index or an unmounted
        volume — and must be re-enqueued instead of being skipped forever.
        """
        if self.meilisearch is None:
            return set()
        try:
            return self.meilisearch.all_doc_ids()
        except Exception as e:
            self.logger.warning(f"indexed_doc_ids: store query failed: {e}")
            return set()

    def healthy_doc_ids(self) -> set:
        """Doc ids that are indexed AND have real content (US-086 v1b).

        This is the oracle the scanner reconciles against. Unlike
        ``indexed_doc_ids`` (mere presence), it must EXCLUDE documents stored
        with empty content — a scanned PDF/image whose OCR yielded nothing is
        indexed by title only, so it would otherwise look "present" and never
        be re-OCR'd.

        ÉPIC-31 (US-113): LanceDB used to provide this for free (it rejected
        empty-content writes outright). Now that it is gone, Meilisearch
        tracks it explicitly via the ``has_content`` field written at index
        time (see ``search.meilisearch_client.MeilisearchClient.add_document``
        and ``.healthy_doc_ids()``).
        """
        if self.meilisearch is None or not hasattr(self.meilisearch, "healthy_doc_ids"):
            return set()
        try:
            return self.meilisearch.healthy_doc_ids()
        except Exception as e:
            self.logger.warning(f"healthy_doc_ids: store query failed: {e}")
            return set()

    def get_stats(self) -> Dict[str, Any]:
        """Get indexing statistics.

        ``lancedb`` stays in the returned dict (Optional, always None) so
        callers built against the pre-4.0 contract (e.g. ``/api/stats``) keep
        working unchanged — see ÉPIC-31, US-112/US-113.
        """
        stats: Dict[str, Any] = {
            "lancedb": None,
            "meilisearch": None,
        }

        if self.meilisearch:
            try:
                stats["meilisearch"] = self.meilisearch.get_stats()
            except Exception as e:
                stats["meilisearch"] = {"error": str(e)}

        return stats


# Convenience function
def index_file(file_path: str | Path) -> IndexResult:
    """Index a single file (convenience function)."""
    indexer = DocumentIndexer()
    return indexer.index_file(file_path)
