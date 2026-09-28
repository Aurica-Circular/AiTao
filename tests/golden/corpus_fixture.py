# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/golden/corpus_fixture.py — shared isolated-store corpus indexer (US-101).
#
# Factors the "index a committed corpus into throwaway stores" routine shared
# by the golden suites: a dedicated (test_*) Meilisearch documents index + a
# dedicated Meilisearch chunk index, so retrieval assertions run against a
# known, versioned fixture rather than production data.
#
# ÉPIC-31 (US-113): fusion is the only search engine since v4.0 (decision D1)
# — LanceDB is gone from the live path entirely. This fixture used to also
# build a temp-dir LanceDB client (doc-level vectors + the "rrf" chunk store);
# both are removed. The chunk store is now always MeiliChunkStore.

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple


def reset_meilisearch_index(index_name: str, url: str = "http://localhost:7700") -> None:
    """Delete ``index_name`` if present, so every run starts from a clean slate."""
    import meilisearch

    raw = meilisearch.Client(url)
    try:
        raw.delete_index(index_name)
    except Exception:
        pass


def make_chunk_store(index_name: str, chunks_index_name: Optional[str] = None):
    """Build the excerpt store (MeiliChunkStore) for a test document index.

    ÉPIC-31 (US-113): the engine choice itself is delegated to the production
    factory (``storage.repository.make_chunk_store``) so this fixture and
    production code can never drift — only the test-only index-name plumbing
    (and the reset-before-use side effect) stays here.
    """
    from aitao.storage.repository import make_chunk_store as make_chunk_store_prod

    chunks_index = chunks_index_name or f"{index_name}_chunks"
    reset_meilisearch_index(chunks_index)
    return make_chunk_store_prod(index_name=chunks_index)


def index_corpus(corpus_dir: Path, index_name: str) -> Tuple:
    """Index every ``*.md`` file of ``corpus_dir`` into isolated stores.

    Returns ``(meilisearch_client, chunk_store, files)`` ready to be wired
    into a ``HybridSearchEngine``. Blocks briefly until every document is
    searchable (Meilisearch indexes asynchronously).
    """
    from aitao.indexation.indexer import DocumentIndexer
    from aitao.storage.repository import make_meilisearch_client

    reset_meilisearch_index(index_name)

    meili = make_meilisearch_client(index_name=index_name)
    chunks = make_chunk_store(index_name)

    indexer = DocumentIndexer(meilisearch_client=meili, chunk_store=chunks)
    files: List[Path] = sorted(corpus_dir.glob("*.md"))
    for path in files:
        indexer.index_file(str(path), force=True)

    for _ in range(40):
        if meili.count("") >= len(files):
            break
        time.sleep(0.25)

    return meili, chunks, files
