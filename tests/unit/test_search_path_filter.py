# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_search_path_filter.py -- unit tests for the US-125 folder filter fix:
# search.path_filter.filter_hits_by_path (pure post-filter) and
# overfetch_limit (how much extra to request from Meilisearch when a path
# filter is active). No Meilisearch involved -- pure functions only.

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aitao.search.path_filter import (  # noqa: E402
    PATH_FILTER_OVERFETCH_CAP,
    PATH_FILTER_OVERFETCH_FACTOR,
    filter_hits_by_path,
    overfetch_limit,
)


def _hit(path):
    return {"id": path, "path": path}


class TestFilterHitsByPath:
    def test_keeps_only_hits_whose_path_contains_the_substring(self):
        hits = [_hit("/docs/factures/2024/a.pdf"), _hit("/docs/contrats/b.pdf")]

        result = filter_hits_by_path(hits, "factures")

        assert result == [_hit("/docs/factures/2024/a.pdf")]

    def test_case_insensitive(self):
        hits = [_hit("/Docs/FACTURES/a.pdf")]

        assert filter_hits_by_path(hits, "factures") == hits

    def test_nfc_nfd_normalization(self):
        # A precomposed accented codepoint (NFC) vs. the decomposed form
        # (base letter + combining mark, NFD) -- same visible path, different
        # byte sequences (mirrors the CJK NFC/NFD mismatch documented in
        # indexation.prune). The hit's path is stored NFC; the folder filter
        # argument arrives NFD (e.g. typed on macOS, which normalizes
        # filenames to NFD).
        word_nfc = unicodedata.normalize("NFC", "été")  # "ete" with acute accents
        word_nfd = unicodedata.normalize("NFD", "été")
        assert word_nfc != word_nfd  # sanity: genuinely different byte sequences
        hits = [_hit(f"/docs/{word_nfc}/a.pdf")]

        assert filter_hits_by_path(hits, word_nfd) == hits

    def test_hit_without_path_is_dropped_not_raised(self):
        hits = [{"id": "no-path"}, _hit("/docs/factures/a.pdf")]

        result = filter_hits_by_path(hits, "factures")

        assert result == [_hit("/docs/factures/a.pdf")]

    def test_none_path_contains_returns_hits_unchanged(self):
        hits = [_hit("/docs/factures/a.pdf"), _hit("/docs/contrats/b.pdf")]

        assert filter_hits_by_path(hits, None) is hits

    def test_empty_string_path_contains_returns_hits_unchanged(self):
        hits = [_hit("/docs/factures/a.pdf")]

        assert filter_hits_by_path(hits, "") is hits

    def test_no_match_returns_empty_list(self):
        hits = [_hit("/docs/contrats/b.pdf")]

        assert filter_hits_by_path(hits, "factures") == []

    def test_custom_path_getter_for_non_dict_hits(self):
        # Chunk search hits are (Chunk, score) tuples -- search_chunks passes
        # a lambda extracting chunk.path instead of the dict default.
        class _Chunk:
            def __init__(self, path):
                self.path = path

        hits = [(_Chunk("/docs/factures/a.pdf"), 0.9), (_Chunk("/docs/contrats/b.pdf"), 0.8)]

        result = filter_hits_by_path(hits, "factures", path_getter=lambda pair: pair[0].path)

        assert len(result) == 1
        assert result[0][0].path == "/docs/factures/a.pdf"


class TestOverfetchLimit:
    def test_none_path_contains_returns_limit_unchanged(self):
        assert overfetch_limit(10, None) == 10

    def test_empty_path_contains_returns_limit_unchanged(self):
        assert overfetch_limit(10, "") == 10

    def test_scales_by_overfetch_factor(self):
        assert overfetch_limit(10, "factures") == 10 * PATH_FILTER_OVERFETCH_FACTOR

    def test_capped_for_large_limits(self):
        big_limit = PATH_FILTER_OVERFETCH_CAP  # * FACTOR would blow past the cap
        assert overfetch_limit(big_limit, "factures") == PATH_FILTER_OVERFETCH_CAP

    def test_never_goes_below_the_original_limit(self):
        # Even if a future cap/factor tweak made the formula misbehave, the
        # over-fetched count must never be smaller than what was asked for.
        assert overfetch_limit(1000, "factures") >= 1000
