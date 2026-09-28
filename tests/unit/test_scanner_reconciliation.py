# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Orphan reconciliation tests for US-086 volet 1.

A file the scanner has already 'seen' (recorded in its state) but that never
actually landed in the stores — a failed index, an unmounted volume — used to be
skipped forever. The scanner now reconciles its state against an injected oracle
of indexed doc ids: a seen-but-absent file is re-enqueued. These tests pin that
contract, plus the safety rule that an EMPTY/absent inventory disables
reconciliation (so a cold or broken store never re-enqueues the whole corpus).
"""

import hashlib
import unicodedata

from aitao.indexation.indexer_helpers import generate_doc_id
from aitao.indexation.scanner import FilesystemScanner, _doc_id


def _make_scanner(tmp_path, inventory):
    """Scanner with a temp state file and an injected inventory callable."""
    state = tmp_path / "scanner_state.json"
    return FilesystemScanner(
        state_file=str(state),
        index_inventory=inventory,
    )


def test_doc_id_matches_canonical_formula():
    # The local _doc_id MUST equal indexer_helpers.generate_doc_id byte-for-byte
    # (NFC-normalized sha256 of path) — the whole point of the duplication.
    p = "/some/file.pdf"
    assert _doc_id(p) == generate_doc_id(p) == hashlib.sha256(p.encode()).hexdigest()


def test_doc_id_nfc_normalizes_like_generate_doc_id():
    """US-113 (ÉPIC-31 volet 3) — root cause of the endless-reindex bug.

    A real installation's worker.log showed the same handful of accented/CJK
    filenames reprocessed ~48 times in 24h on a ~10-minute cycle: the scanner
    hashed the RAW path (as returned by os.scandir(), NFD on macOS for any
    decomposable character — accented Latin letters here) while the store's
    id was computed from the NFC-normalized path (indexer_helpers.
    generate_doc_id, via normalize_path) — permanent mismatch, so
    reconciliation NEVER found a match for that file, however many times it
    was re-indexed. _doc_id() must normalize exactly like generate_doc_id().
    """
    nfc_path = unicodedata.normalize("NFC", "/docs/Génerative Report.pdf")
    nfd_path = unicodedata.normalize("NFD", nfc_path)
    assert nfc_path != nfd_path, "fixture must actually exercise a decomposable character"

    # The indexer always stores ids from the NFC form (item_preparer.prepare_item
    # normalizes before generating the id) — the scanner walks the RAW (NFD-on-macOS)
    # path but must still land on the SAME id.
    stored_id = generate_doc_id(nfc_path)
    assert _doc_id(nfd_path) == stored_id


class TestOrphanReconciliationNfcPaths:
    """End-to-end proof (via the scanner's own reconciliation) that an
    NFD-vs-NFC path mismatch no longer causes a permanent "orphan"."""

    def test_nfd_path_matches_nfc_stored_id_no_endless_reconciliation(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        # Emulate the incident's shape: create the file with an accented,
        # NFD-decomposed name (macOS's on-disk form is not what matters for
        # this test — we control the string handed to the scanner directly
        # by scanning a path recorded with the NFD form explicitly).
        nfc_name = unicodedata.normalize("NFC", "Génerative.txt")
        nfd_name = unicodedata.normalize("NFD", nfc_name)
        (docs / nfd_name).write_text("hello")

        # The store's inventory holds the id computed from the NFC form —
        # exactly what item_preparer.prepare_item()/generate_doc_id() produce.
        nfc_equivalent_path = str(docs / nfc_name)
        inv = {"ids": {generate_doc_id(nfc_equivalent_path)}}
        scanner = _make_scanner(tmp_path, lambda: inv["ids"])

        r1 = scanner.scan(paths=[str(docs)])
        assert len(r1.new_files) == 1

        # Second scan: file unchanged. Pre-fix, the scanner's un-normalized
        # _doc_id would never match the NFC-derived stored id -> reconciled
        # (orphan) forever. Post-fix, it matches -> nothing to reconcile.
        r2 = scanner.scan(paths=[str(docs)])
        assert r2.reconciled_files == [], (
            "NFC/NFD path mismatch: the scanner thinks an already-indexed "
            "file is an orphan and would re-enqueue it forever."
        )


class TestOrphanReconciliation:
    def test_seen_but_absent_file_is_reconciled(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("hello")
        inv = {"ids": {"unrelated-doc-id"}}  # non-empty, but lacks our file
        scanner = _make_scanner(tmp_path, lambda: inv["ids"])

        # First scan: brand-new file -> new_files, seeds state.
        r1 = scanner.scan(paths=[str(docs)])
        assert len(r1.new_files) == 1
        assert r1.reconciled_files == []
        seen_path = r1.new_files[0].path

        # Second scan: file unchanged and STILL absent from the stores -> orphan.
        r2 = scanner.scan(paths=[str(docs)])
        assert r2.new_files == []
        assert r2.modified_files == []
        assert len(r2.reconciled_files) == 1
        assert r2.reconciled_files[0].path == seen_path
        assert r2.has_changes is True

    def test_indexed_file_is_not_reconciled(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("hello")
        inv = {"ids": {"unrelated"}}
        scanner = _make_scanner(tmp_path, lambda: inv["ids"])

        r1 = scanner.scan(paths=[str(docs)])
        seen_path = r1.new_files[0].path

        # The store now reports our doc id as present -> nothing to reconcile.
        inv["ids"] = {_doc_id(seen_path)}
        r2 = scanner.scan(paths=[str(docs)])
        assert r2.reconciled_files == []

    def test_empty_inventory_disables_reconciliation(self, tmp_path):
        # An empty snapshot is treated as "store unavailable": no reconciliation,
        # so a transient outage can never re-enqueue everything.
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("hello")
        inv = {"ids": {"seed"}}
        scanner = _make_scanner(tmp_path, lambda: inv["ids"])

        scanner.scan(paths=[str(docs)])  # seed state
        inv["ids"] = set()               # store reports empty -> untrusted
        r2 = scanner.scan(paths=[str(docs)])
        assert r2.reconciled_files == []

    def test_failing_inventory_is_swallowed(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("hello")

        def _boom():
            raise RuntimeError("store down")

        scanner = _make_scanner(tmp_path, _boom)
        scanner.scan(paths=[str(docs)])
        r2 = scanner.scan(paths=[str(docs)])  # must not raise
        assert r2.reconciled_files == []

    def test_no_inventory_behaves_as_before(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("hello")
        scanner = FilesystemScanner(
            state_file=str(tmp_path / "s.json"),
            index_inventory=None,
        )
        r1 = scanner.scan(paths=[str(docs)])
        assert len(r1.new_files) == 1
        r2 = scanner.scan(paths=[str(docs)])
        assert r2.reconciled_files == []
        assert r2.has_changes is False
