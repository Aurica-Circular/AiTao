# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_search_chunks_path_filter.py -- US-125: the chunk-level RAG retrieval
# helper (search.search_executors.search_chunks, used by the chat/RAG context
# builder) must honour an optional path_contains folder filter the same way
# the fusion document search does -- Python post-filter + over-fetch, since
# Meilisearch cannot filter chunk paths natively either. Fully mocked
# chunk_store -- no live Meilisearch.

import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.indexation.interfaces import Chunk  # noqa: E402
from aitao.search import search_executors  # noqa: E402
from aitao.search.path_filter import PATH_FILTER_OVERFETCH_FACTOR  # noqa: E402


def _chunk(chunk_id, path, doc_id=None):
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id or chunk_id,
        path=path,
        title=chunk_id,
        content="some text",
        chunk_index=0,
        total_chunks=1,
        offset_start=0,
        offset_end=9,
    )


MIXED_CHUNKS = [
    (_chunk("in1", "/data/factures/2024/janvier.pdf"), 0.95),
    (_chunk("out1", "/data/contrats/bail.pdf"), 0.90),
    (_chunk("in2", "/data/factures/2023/decembre.pdf"), 0.85),
    (_chunk("out2", "/data/notes/divers.md"), 0.80),
]


def _mock_chunk_store(results=None):
    store = Mock()
    store.search = Mock(return_value=results if results is not None else list(MIXED_CHUNKS))
    return store


class TestSearchChunksPathFilter:
    def test_only_in_path_chunks_come_back(self):
        store = _mock_chunk_store()

        response = search_executors.search_chunks(store, "query", limit=10, path_contains="factures")

        paths = {c.path for c in response.chunks}
        assert paths == {"/data/factures/2024/janvier.pdf", "/data/factures/2023/decembre.pdf"}

    def test_truncates_to_requested_limit(self):
        store = _mock_chunk_store()

        response = search_executors.search_chunks(store, "query", limit=1, path_contains="factures")

        assert len(response.chunks) == 1
        assert "factures" in response.chunks[0].path

    def test_overfetch_requested_when_path_filter_active(self):
        store = _mock_chunk_store()

        search_executors.search_chunks(store, "query", limit=5, path_contains="factures")

        requested_limit = store.search.call_args.kwargs["limit"]
        # Base over-fetch (limit * 2, existing behaviour for min_score
        # filtering) is itself scaled by the path-filter over-fetch factor.
        assert requested_limit == (5 * 2) * PATH_FILTER_OVERFETCH_FACTOR


class TestSearchChunksNoPathFilterIsANoOp:
    def test_default_limit_unchanged_and_no_chunk_dropped(self):
        store = _mock_chunk_store()

        response = search_executors.search_chunks(store, "query", limit=4)

        requested_limit = store.search.call_args.kwargs["limit"]
        assert requested_limit == 4 * 2  # unchanged pre-existing over-fetch, no path factor
        assert len(response.chunks) == 4  # all chunks kept, none filtered out
