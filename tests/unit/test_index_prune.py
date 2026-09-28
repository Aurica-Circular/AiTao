# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_index_prune.py — US-126-A: `aitao index prune`.
#
# Priority is reliability: the out-of-scope detection MUST be based only on
# the configured include_paths (never on disk presence), or unplugging a
# volume that is still listed in the config would make every document under
# it look orphaned and trigger a catastrophic bulk delete. These tests pin
# that guardrail down at the pure-function level, then check the CLI wiring
# (dry-run / confirmation / --yes / empty-config refusal) against a mocked
# Meilisearch client — never a real index.

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.indexation.prune import find_out_of_scope_paths, group_by_root  # noqa: E402
from aitao.cli.commands.index import app  # noqa: E402

runner = CliRunner()


class TestFindOutOfScopePaths:
    def test_path_under_configured_root_is_not_orphan(self):
        result = find_out_of_scope_paths(
            ["/Users/phil/Documents/report.pdf"],
            ["/Users/phil/Documents"],
        )
        assert result == []

    def test_path_under_removed_root_is_orphan(self):
        result = find_out_of_scope_paths(
            ["/Users/phil/OldProject/notes.txt"],
            ["/Users/phil/Documents"],
        )
        assert result == ["/Users/phil/OldProject/notes.txt"]

    def test_unmounted_but_still_configured_root_is_not_orphan(self):
        """The critical guardrail: a root that is still in include_paths (even
        if the volume happens to be unplugged right now) must NOT flag its
        documents as orphaned. This test emulates that by passing the root as
        present in configured_roots regardless of any disk state — exactly
        what get_include_paths(existing_only=False) guarantees upstream."""
        indexed = [
            "/Users/phil/Downloads/_Volumes/ExternalDrive/scan1.pdf",
            "/Users/phil/Downloads/_Volumes/ExternalDrive/sub/scan2.pdf",
        ]
        configured = ["/Users/phil/Downloads/_Volumes/ExternalDrive"]
        assert find_out_of_scope_paths(indexed, configured) == []

    def test_empty_configured_roots_returns_no_orphans(self):
        """Safety net: an empty include_paths must never be read as
        'everything is orphaned'."""
        indexed = ["/Users/phil/Documents/a.pdf", "/Users/phil/Downloads/b.pdf"]
        assert find_out_of_scope_paths(indexed, []) == []

    def test_nfc_nfd_unicode_mismatch_still_matches(self):
        # "é" is the classic case: macOS/HFS+ historically stores filenames
        # in NFD (e + combining acute accent) while config files / most other
        # sources use precomposed NFC. Most CJK ideographs have no canonical
        # decomposition, so a Latin accented character is what actually
        # exercises the NFC/NFD divergence end to end.
        root_nfc = unicodedata.normalize("NFC", "/Users/phil/Décomptes")
        path_nfd = unicodedata.normalize("NFD", "/Users/phil/Décomptes/facture.pdf")
        # Sanity check: the two forms really are different byte sequences,
        # otherwise this test would not exercise NFC normalization at all.
        assert path_nfd != unicodedata.normalize("NFC", path_nfd)
        result = find_out_of_scope_paths([path_nfd], [root_nfc])
        assert result == []

    def test_deeply_nested_subfolder_is_in_scope(self):
        indexed = ["/Users/phil/Documents/a/b/c/d/e/f/deep.pdf"]
        configured = ["/Users/phil/Documents"]
        assert find_out_of_scope_paths(indexed, configured) == []

    def test_mixed_in_scope_and_out_of_scope(self):
        indexed = [
            "/Users/phil/Documents/keep.pdf",
            "/Users/phil/OldStuff/drop.pdf",
            "/Users/phil/Documents/sub/keep2.pdf",
        ]
        configured = ["/Users/phil/Documents"]
        assert find_out_of_scope_paths(indexed, configured) == ["/Users/phil/OldStuff/drop.pdf"]

    def test_root_itself_is_in_scope(self):
        # Edge case: the indexed path IS the configured root (unlikely for a
        # file, but the comparison must not crash and must count it in-scope).
        assert find_out_of_scope_paths(["/Users/phil/Documents"], ["/Users/phil/Documents"]) == []


class TestGroupByRoot:
    def test_groups_by_parent_directory(self):
        orphans = [
            "/Users/phil/Old/a.pdf",
            "/Users/phil/Old/b.pdf",
            "/Users/phil/Other/c.pdf",
        ]
        groups = group_by_root(orphans, [])
        assert len(groups) == 2
        assert sum(len(v) for v in groups.values()) == 3

    def test_empty_input(self):
        assert group_by_root([], ["/Users/phil/Documents"]) == {}


def _fake_meili(paths):
    meili = MagicMock()
    meili.get_all_document_paths.return_value = paths
    meili.delete_by_path.return_value = True
    return meili


class TestPruneCliCommand:
    def test_empty_include_paths_refuses_to_delete(self):
        meili = _fake_meili(["/Users/phil/Anything/x.pdf"])
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch("aitao.core.pathmanager.path_manager.get_include_paths", return_value=[]):
            result = runner.invoke(app, ["prune", "--yes"])

        assert result.exit_code == 0, result.output
        meili.delete_by_path.assert_not_called()

    def test_no_orphans_is_a_clean_noop(self):
        meili = _fake_meili(["/Users/phil/Documents/a.pdf"])
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch(
                 "aitao.core.pathmanager.path_manager.get_include_paths",
                 return_value=["/Users/phil/Documents"],
             ):
            result = runner.invoke(app, ["prune", "--yes"])

        assert result.exit_code == 0, result.output
        meili.delete_by_path.assert_not_called()

    def test_dry_run_lists_but_never_deletes(self):
        meili = _fake_meili(["/Users/phil/Old/a.pdf"])
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch(
                 "aitao.core.pathmanager.path_manager.get_include_paths",
                 return_value=["/Users/phil/Documents"],
             ):
            result = runner.invoke(app, ["prune", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Old" in result.output or "a.pdf" in result.output
        meili.delete_by_path.assert_not_called()

    def test_yes_flag_deletes_without_prompt(self):
        meili = _fake_meili(["/Users/phil/Old/a.pdf", "/Users/phil/Old/b.pdf"])
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch(
                 "aitao.core.pathmanager.path_manager.get_include_paths",
                 return_value=["/Users/phil/Documents"],
             ):
            result = runner.invoke(app, ["prune", "--yes"])

        assert result.exit_code == 0, result.output
        assert meili.delete_by_path.call_count == 2

    def test_declining_confirmation_deletes_nothing(self):
        meili = _fake_meili(["/Users/phil/Old/a.pdf"])
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch(
                 "aitao.core.pathmanager.path_manager.get_include_paths",
                 return_value=["/Users/phil/Documents"],
             ):
            result = runner.invoke(app, ["prune"], input="n\n")

        assert result.exit_code == 0, result.output
        meili.delete_by_path.assert_not_called()

    def test_listing_failure_aborts_without_deleting(self):
        meili = MagicMock()
        meili.get_all_document_paths.side_effect = RuntimeError("meili down")
        with patch("aitao.storage.repository.make_meilisearch_client", return_value=meili), \
             patch(
                 "aitao.core.pathmanager.path_manager.get_include_paths",
                 return_value=["/Users/phil/Documents"],
             ):
            result = runner.invoke(app, ["prune", "--yes"])

        assert result.exit_code != 0
        meili.delete_by_path.assert_not_called()
