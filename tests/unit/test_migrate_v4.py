# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_migrate_v4.py — unit tests for the US-112 migration core
# (search/migrate_v4_source.py classification + search/migrate_v4.py
# orchestration), fully mocked — no live Meilisearch, no LanceDB on disk.
#
# The live-store integration proof (real Meilisearch, temp LanceDB, golden
# corpus, dry-run + swap) lives in tests/integration/test_migrate_v4.py.

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search.migrate_v4 import (  # noqa: E402
    MigrationConfirmationRequired,
    MigrationV4Runner,
)
from aitao.search.migrate_v4_source import (  # noqa: E402
    chunks_for_copy_population,
    classify_documents,
)


# ---------------------------------------------------------------------------
# Classification (CJK copy vs reindex populations)
# ---------------------------------------------------------------------------

class TestClassifyDocuments:
    def test_clean_document_with_vector_is_copied(self):
        docs = [{"id": "d1", "path": "/a.md", "title": "A", "content": "hello world"}]
        vectors = {"d1": [0.1, 0.2]}

        out = classify_documents(docs, vectors)

        assert len(out) == 1
        c = out[0]
        assert c.population == "copy"
        assert c.has_vector is True
        assert c.next_record["_vectors"] == {"default": [0.1, 0.2]}
        assert c.next_record["content"] == "hello world"

    def test_clean_document_without_vector_still_copied_with_null_optout(self):
        docs = [{"id": "d1", "path": "/a.md", "title": "A", "content": "hello"}]

        out = classify_documents(docs, {})

        assert out[0].population == "copy"
        assert out[0].has_vector is False
        assert out[0].next_record["_vectors"] == {"default": None}

    def test_cjk_gapped_document_goes_to_reindex_without_vector(self, tmp_path):
        # "承 擔" -> gluing changes the content -> stored vector was wrong.
        src = tmp_path / "zh.md"
        src.write_text("承 擔", encoding="utf-8")
        docs = [{"id": "d1", "path": str(src), "title": "zh", "content": "承 擔"}]
        vectors = {"d1": [0.9, 0.9]}  # exists but must NOT be copied

        out = classify_documents(docs, vectors)

        c = out[0]
        assert c.population == "reindex"
        assert c.has_vector is False
        assert c.source_exists is True
        # Pushed with the GLUED content and the explicit null opt-out.
        assert c.next_record["content"] == "承擔"
        assert c.next_record["_vectors"] == {"default": None}

    def test_cjk_document_with_missing_source_is_flagged(self):
        docs = [{"id": "d1", "path": "/gone/forever.md", "title": "zh", "content": "承 擔"}]

        out = classify_documents(docs, {})

        assert out[0].population == "reindex"
        assert out[0].source_exists is False

    def test_extra_metadata_fields_are_preserved(self):
        docs = [{
            "id": "d1", "path": "/a.md", "title": "A", "content": "x",
            "mtime": 1234.5, "pages": 3,
        }]

        out = classify_documents(docs, {})

        assert out[0].next_record["mtime"] == 1234.5
        assert out[0].next_record["pages"] == 3

    def test_non_cjk_content_never_lands_in_reindex(self):
        docs = [
            {"id": "d1", "path": "/a.md", "content": "Un texte français normal."},
            {"id": "d2", "path": "/b.md", "content": "Plain English text, 2 lines.\nSecond."},
        ]

        out = classify_documents(docs, {})

        assert all(c.population == "copy" for c in out)


class TestChunksForCopyPopulation:
    def _rows(self):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "path": "/a", "title": "A",
             "content": "x", "chunk_index": 0, "total_chunks": 1, "vector": [0.1]},
            {"chunk_id": "c2", "doc_id": "d2", "path": "/b", "title": "B",
             "content": "y", "chunk_index": 0, "total_chunks": 1, "vector": [0.2]},
        ]

    def test_only_copy_population_chunks_are_kept(self):
        out = chunks_for_copy_population(self._rows(), {"d1"})

        assert [r["chunk_id"] for r in out] == ["c1"]
        assert out[0]["_vectors"] == {"default": [0.1]}

    def test_reindex_doc_chunks_are_dropped_entirely(self):
        out = chunks_for_copy_population(self._rows(), set())
        assert out == []


# ---------------------------------------------------------------------------
# Runner orchestration (mocked stores)
# ---------------------------------------------------------------------------

def _make_runner(tmp_path, **kwargs):
    """A guard-compliant runner: every store-pointing parameter overridden."""
    cfg = Mock()
    cfg.search.meilisearch.url = "http://localhost:7700"
    cfg.search.meilisearch.api_key = ""
    cfg.search.meilisearch.index_name = "test_migv4_docs"
    cfg.search.meilisearch.chunks_index = "test_migv4_chunks"
    cfg.search.lancedb.table_name = "aitao_embeddings"
    # US-113: dimension moved to [search.embedding] (neutral name — [search.lancedb]
    # no longer describes a real store; table_name is the one legacy key the
    # migration still needs, to read the pre-4.0 LanceDB table off disk).
    cfg.search.embedding.dimension = 4
    return MigrationV4Runner(
        config=cfg,
        docs_index_name="test_migv4_docs",
        chunks_index_name="test_migv4_chunks",
        lancedb_path=str(tmp_path / "lance"),
        **kwargs,
    )


class TestRunnerPhaseGates:
    def test_swap_refuses_without_confirmation(self, tmp_path):
        runner = _make_runner(tmp_path)
        with pytest.raises(MigrationConfirmationRequired):
            runner.swap(confirmed=False)

    def test_rollback_refuses_without_confirmation(self, tmp_path):
        runner = _make_runner(tmp_path)
        with pytest.raises(MigrationConfirmationRequired):
            runner.rollback(confirmed=False)

    def test_rollback_swaps_both_pairs_and_never_rebuilds(self, tmp_path):
        runner = _make_runner(tmp_path)
        fake_rebuilder = Mock()
        with patch("aitao.search.migrate_v4.IndexRebuilder", return_value=fake_rebuilder), \
             patch.object(runner, "_raw_client", return_value=Mock()):
            report = runner.rollback(confirmed=True)

        assert fake_rebuilder.swap_back.call_count == 2
        pairs = [c.args for c in fake_rebuilder.swap_back.call_args_list]
        assert ("test_migv4_docs", "test_migv4_docs_next") in pairs
        assert ("test_migv4_chunks", "test_migv4_chunks_next") in pairs
        fake_rebuilder.rebuild.assert_not_called()
        assert report.mode == "rollback"
        assert report.swapped is True


class TestRunnerDryRun:
    def _run_dry(self, tmp_path, documents, doc_vectors, chunk_rows, queue=None):
        runner = _make_runner(tmp_path, task_queue=queue)
        fake_rebuilder = Mock()
        raw_index = Mock()
        raw_index.get_document = Mock(return_value={"id": "whatever"})
        raw_index.search = Mock(return_value={"hits": [{"id": d["id"]} for d in documents]})
        raw_client = Mock()
        raw_client.index = Mock(return_value=raw_index)

        with patch("aitao.search.migrate_v4.read_source_doc_vectors", return_value=doc_vectors), \
             patch("aitao.search.migrate_v4.read_source_chunks", return_value=chunk_rows), \
             patch("aitao.search.migrate_v4.read_live_documents", return_value=documents), \
             patch("aitao.search.meilisearch_client.MeilisearchClient") as fake_ms, \
             patch("aitao.search.migrate_v4.IndexRebuilder", return_value=fake_rebuilder), \
             patch.object(runner, "_raw_client", return_value=raw_client):
            fake_ms.return_value = Mock()
            report = runner.dry_run()
        return report, fake_rebuilder

    def test_dry_run_counts_populations_and_never_swaps(self, tmp_path):
        src = tmp_path / "zh.md"
        src.write_text("承 擔", encoding="utf-8")
        documents = [
            {"id": "d1", "path": "/a.md", "title": "A", "content": "clean text"},
            {"id": "d2", "path": str(src), "title": "zh", "content": "承 擔"},
            {"id": "d3", "path": "/gone.md", "title": "zh2", "content": "門 口"},
        ]
        doc_vectors = {"d1": [0.1] * 4, "d2": [0.2] * 4}
        chunk_rows = [
            {"chunk_id": "c1", "doc_id": "d1", "path": "/a.md", "title": "A",
             "content": "clean text", "chunk_index": 0, "total_chunks": 1,
             "vector": [0.1] * 4},
            {"chunk_id": "c2", "doc_id": "d2", "path": str(src), "title": "zh",
             "content": "承 擔", "chunk_index": 0, "total_chunks": 1,
             "vector": [0.2] * 4},
        ]

        report, fake_rebuilder = self._run_dry(tmp_path, documents, doc_vectors, chunk_rows)

        assert report.mode == "dry_run"
        assert report.docs_total == 3
        assert report.docs_copied == 1
        assert report.docs_needing_reindex == 2
        assert report.docs_source_missing == 1
        assert report.docs_requeued == 0  # dry-run NEVER requeues
        assert report.chunks_total_source == 2
        assert report.chunks_copied == 1  # d2's chunk excluded
        assert report.chunks_excluded_pending_reindex == 1
        assert report.swapped is False

        # Both rebuilds happened with swap=False, and no swap_back was issued.
        assert fake_rebuilder.rebuild.call_count == 2
        for call in fake_rebuilder.rebuild.call_args_list:
            assert call.kwargs["swap"] is False
        fake_rebuilder.swap_back.assert_not_called()

    def test_dry_run_never_touches_the_queue(self, tmp_path):
        queue = Mock()
        documents = [{"id": "d1", "path": str(tmp_path / "x.md"), "content": "承 擔"}]
        (tmp_path / "x.md").write_text("承 擔", encoding="utf-8")

        self._run_dry(tmp_path, documents, {}, [], queue=queue)

        queue.add_task.assert_not_called()

    def test_dry_run_rebuild_configures_embedders_and_primary_keys(self, tmp_path):
        documents = [{"id": "d1", "path": "/a.md", "content": "clean"}]

        _, fake_rebuilder = self._run_dry(tmp_path, documents, {"d1": [0.1] * 4}, [])

        calls = {c.args[0]: c.kwargs for c in fake_rebuilder.rebuild.call_args_list}
        docs_call = calls["test_migv4_docs"]
        chunks_call = calls["test_migv4_chunks"]
        assert docs_call["primary_key"] == "id"
        assert docs_call["embedder"] == ("default", 4)
        assert chunks_call["primary_key"] == "chunk_id"
        assert chunks_call["embedder"] == ("default", 4)


class TestRunnerSwap:
    def test_swap_swaps_both_pairs_then_requeues_with_force(self, tmp_path):
        src = tmp_path / "zh.md"
        src.write_text("承 擔", encoding="utf-8")
        documents = [{"id": "d1", "path": str(src), "title": "zh", "content": "承 擔"}]
        queue = Mock()
        runner = _make_runner(tmp_path, task_queue=queue)

        fake_rebuilder = Mock()
        raw_index = Mock()
        raw_index.get_document = Mock(return_value={})
        raw_index.search = Mock(return_value={"hits": []})
        raw_client = Mock()
        raw_client.index = Mock(return_value=raw_index)

        with patch("aitao.search.migrate_v4.read_source_doc_vectors", return_value={}), \
             patch("aitao.search.migrate_v4.read_source_chunks", return_value=[]), \
             patch("aitao.search.migrate_v4.read_live_documents", return_value=documents), \
             patch("aitao.search.meilisearch_client.MeilisearchClient"), \
             patch("aitao.search.migrate_v4.IndexRebuilder", return_value=fake_rebuilder), \
             patch.object(runner, "_raw_client", return_value=raw_client):
            report = runner.swap(confirmed=True)

        assert report.mode == "swap"
        assert report.swapped is True
        assert fake_rebuilder.swap_back.call_count == 2
        # Requeue happened AFTER the swap, with force metadata.
        queue.add_task.assert_called_once()
        args, kwargs = queue.add_task.call_args
        assert args[0] == str(src)
        assert kwargs["task_type"] == "reindex"
        assert kwargs["metadata"]["force"] is True
        assert report.docs_requeued == 1


# ---------------------------------------------------------------------------
# Prod-store guard (US-112 extension) — a production-pointing runner must fail
# ---------------------------------------------------------------------------

class TestProdStoreGuard:
    def test_runner_with_no_overrides_is_refused(self):
        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            MigrationV4Runner()

    def test_runner_with_partial_overrides_is_refused(self, tmp_path):
        # docs index overridden, chunks index left at production default.
        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            MigrationV4Runner(
                docs_index_name="test_x",
                lancedb_path=str(tmp_path),
            )

    def test_raw_lancedb_connect_on_prod_path_is_refused(self):
        import lancedb
        from aitao.core.pathmanager import path_manager

        prod = str(path_manager.get_vector_db_path())
        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            lancedb.connect(prod)
