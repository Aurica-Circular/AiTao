# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the named-document resolver (US-RAG-name).

The resolver must find a document a user references by name even when the
surrounding question buries the filename in unrelated words, and must stay silent
for queries that name no file — so the RAG never pins an irrelevant document.
"""

from aitao.llm.named_doc_resolver import (
    _search_text,
    named_reference_advisory,
    query_names_a_file,
    resolve_named_documents,
)


class _Doc:
    def __init__(self, title, path="/x"):
        self.title = title
        self.path = path


class FakeSearcher:
    """Stand-in for MeilisearchClient.search_titles.

    Returns any indexed title that shares at least one token with the query
    (a permissive superset of Meili's "frequency" strategy), so the resolver's
    own coverage filter is what the tests exercise.
    """

    def __init__(self, docs):
        self._docs = docs  # list of {"path","title"}

    def search_titles(self, query, limit=10):
        from aitao.llm.named_doc_resolver import _tokens
        q = set(_tokens(query))
        hits = [d for d in self._docs if set(_tokens(d["title"])) & q]
        return hits[:limit]


TAIWAN = {"path": "/vol/20260701_Assurance Maladie taiwan.pdf",
          "title": "20260701_Assurance Maladie taiwan"}
CREATION = {"path": "/vol/Assurance Maladie taiwan_CréationEmployeur.pdf",
            "title": "Assurance Maladie taiwan_CréationEmployeur"}
# Same file indexed under macOS NFD and NFC forms (different path bytes).
CREATION_NFD = {"path": "/vol/Assurance Maladie taiwan_CréationEmployeur.pdf",
                "title": "Assurance Maladie taiwan_CréationEmployeur"}
REGLEMENT = {"path": "/vol/LIST-0100 - Règlement intérieur.txt",
             "title": "LIST-0100 - Règlement intérieur"}
CORPUS = [TAIWAN, CREATION, CREATION_NFD, REGLEMENT]


class TestQueryNamesAFile:
    def test_explicit_pdf(self):
        assert query_names_a_file("translate 20260701_Assurance Maladie taiwan.pdf") is True

    def test_no_extension(self):
        assert query_names_a_file("summarize the taiwan health insurance letter") is False

    def test_unknown_extension_ignored(self):
        assert query_names_a_file("open example.zip please") is False


class TestResolveNamedDocuments:
    def test_resolves_named_pdf_buried_in_question(self):
        q = "Traduis moi en français le document 20260701_Assurance Maladie taiwan.pdf. De quoi s'agit-il ?"
        matches = resolve_named_documents(q, FakeSearcher(CORPUS))
        assert [m.title for m in matches] == ["20260701_Assurance Maladie taiwan"]
        assert matches[0].coverage == 1.0

    def test_resolves_despite_page_number_noise(self):
        # Regression: "des 2 premières pages" — the bare "2" must not derail the
        # title search (it matched every "第2版" title before denoising).
        q = "Donne moi une traduction en français des 2 premières pages de ce fichier 20260701_Assurance Maladie taiwan.pdf"
        matches = resolve_named_documents(q, FakeSearcher(CORPUS))
        assert [m.title for m in matches] == ["20260701_Assurance Maladie taiwan"]

    def test_pins_all_matches_including_unicode_duplicate(self):
        q = "De quoi parle Assurance Maladie taiwan_CréationEmployeur.pdf ?"
        paths = {m.path for m in resolve_named_documents(q, FakeSearcher(CORPUS))}
        assert paths == {CREATION["path"], CREATION_NFD["path"]}

    def test_no_match_for_vague_content_query(self):
        # Names no file and only partially covers a title -> nothing pinned.
        assert resolve_named_documents("résume le règlement intérieur", FakeSearcher(CORPUS)) == []

    def test_no_match_for_unrelated_query(self):
        assert resolve_named_documents("Quelle est la météo demain ?", FakeSearcher(CORPUS)) == []

    def test_generic_only_title_not_matched(self):
        # A title made only of generic tokens must never be pinned on a stray hit.
        searcher = FakeSearcher([{"path": "/vol/document.pdf", "title": "document"}])
        assert resolve_named_documents("montre le document", searcher) == []

    def test_partial_coverage_below_threshold_excluded(self):
        q = "parle moi de taiwan"  # only 1 of 4 title tokens -> 0.25 < 0.8
        assert resolve_named_documents(q, FakeSearcher([TAIWAN])) == []

    def test_none_searcher_returns_empty(self):
        assert resolve_named_documents("anything.pdf", None) == []

    def test_max_docs_capped(self):
        docs = [{"path": f"/vol/Rapport Annuel {i} Taiwan.pdf",
                 "title": f"Rapport Annuel {i} Taiwan"} for i in range(10)]
        q = "Rapport Annuel Taiwan " + " ".join(str(i) for i in range(10))
        matches = resolve_named_documents(q, FakeSearcher(docs), max_docs=3)
        assert len(matches) == 3


class TestSearchText:
    def test_drops_stopwords_and_short_tokens(self):
        out = _search_text(
            "Donne moi une traduction en français des 2 premières pages de ce fichier 20260701_Assurance Maladie taiwan.pdf"
        )
        toks = set(out.split())
        assert "2" not in toks and "de" not in toks and "en" not in toks
        assert {"20260701", "assurance", "maladie", "taiwan"} <= toks

    def test_falls_back_when_all_stripped(self):
        # A query made only of stopwords/short tokens keeps the raw tokens.
        assert _search_text("de la le") == "de la le"


class TestNamedReferenceAdvisory:
    INCLUDE = ["${HOME}/Downloads/_Volumes/"]

    def test_none_when_no_file_named(self):
        assert named_reference_advisory("quelle est la météo ?", [], self.INCLUDE) is None

    def test_none_when_named_file_is_in_context(self):
        ctx = [_Doc("20260701_Assurance Maladie taiwan")]
        q = "Traduis 20260701_Assurance Maladie taiwan.pdf, de quoi s'agit-il ?"
        assert named_reference_advisory(q, ctx, self.INCLUDE) is None

    def test_bare_filename_not_indexed(self):
        msg = named_reference_advisory("résume rapport_secret.pdf", [], self.INCLUDE)
        assert msg is not None and "pas présent" in msg

    def test_path_inside_watched_folder(self):
        import os
        p = f"{os.path.expanduser('~')}/Downloads/_Volumes/contrat 2024.pdf"
        msg = named_reference_advisory(f"résume {p}", [], self.INCLUDE)
        assert msg is not None and "dossier surveillé" in msg
        assert "contrat 2024.pdf" in msg

    def test_path_outside_watched_folders(self):
        msg = named_reference_advisory(
            "ouvre /Users/phil/Bureau/facture.pdf", [], self.INCLUDE
        )
        assert msg is not None and "include_paths" in msg
        assert "/Users/phil/Bureau/facture.pdf" in msg
