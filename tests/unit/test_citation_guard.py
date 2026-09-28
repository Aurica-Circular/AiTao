# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the anti-fabrication citation guard (US-17c).

Covers:
- find_fabricated_citations: cited files absent from retrieved sources
- sanitize_citations: replacement with the explicit marker, legit cites kept
- build_stream_citation_warning: trailing warning for streamed answers
"""

from types import SimpleNamespace

from aitao.llm.citation_guard import (
    REMOVED_MARKER,
    build_stream_citation_warning,
    find_fabricated_citations,
    sanitize_citations,
)


def _doc(path: str, title: str = "") -> SimpleNamespace:
    return SimpleNamespace(path=path, title=title)


CONTEXT = [
    _doc("/Users/phil/docs/PRD-liaotao-cli.md", "PRD-liaotao-cli"),
    _doc("/Users/phil/factures/facture-edf-2025.pdf", "Facture EDF 2025"),
]


class TestFindFabricatedCitations:
    def test_legit_citation_by_filename(self):
        answer = "La date est 2026-06-04, source : PRD-liaotao-cli.md."
        assert find_fabricated_citations(answer, CONTEXT) == []

    def test_legit_citation_by_full_path(self):
        answer = "Voir /Users/phil/docs/PRD-liaotao-cli.md pour le détail."
        assert find_fabricated_citations(answer, CONTEXT) == []

    def test_fabricated_citation_detected(self):
        # The June 5th demo fabrication, verbatim
        answer = (
            "D'après MCP-Generated-Prompt-for-Humane-Inventions.txt, "
            "nous sommes le 21 octobre 2023."
        )
        removed = find_fabricated_citations(answer, CONTEXT)
        assert removed == ["MCP-Generated-Prompt-for-Humane-Inventions.txt"]

    def test_case_insensitive_match(self):
        answer = "Source : prd-liaotao-cli.MD"
        assert find_fabricated_citations(answer, CONTEXT) == []

    def test_no_citations_no_findings(self):
        assert find_fabricated_citations("Aucune source citée ici.", CONTEXT) == []

    def test_empty_context_flags_everything(self):
        answer = "Voir rapport-annuel.pdf."
        assert find_fabricated_citations(answer, []) == ["rapport-annuel.pdf"]

    def test_spaced_filename_tail_not_flagged(self):
        # US-20 follow-up: a filename with spaces is captured only as its tail
        # ("Dunod.pdf"), which must still count as the retrieved source.
        ctx = [_doc("/MEGA/EBOOK/Astuces/Ergonomie des interfaces - Dunod.pdf",
                    "Ergonomie des interfaces - Dunod")]
        answer = 'Selon "Astuces/Ergonomie des interfaces - Dunod.pdf", ...'
        assert find_fabricated_citations(answer, ctx) == []

    def test_suffix_match_respects_word_boundary(self):
        # "report.pdf" must NOT be accepted as a tail of "finalreport.pdf"
        ctx = [_doc("/docs/finalreport.pdf", "finalreport")]
        answer = "Voir report.pdf pour les chiffres."
        assert find_fabricated_citations(answer, ctx) == ["report.pdf"]


class TestSanitizeCitations:
    def test_replaces_fabricated_with_marker(self):
        answer = "Selon invented-doc.pdf, le montant est 42 €."
        clean, removed = sanitize_citations(answer, CONTEXT)
        assert removed == ["invented-doc.pdf"]
        assert "invented-doc.pdf" not in clean
        assert REMOVED_MARKER in clean
        assert "42 €" in clean

    def test_keeps_legit_citations_untouched(self):
        answer = "Source : facture-edf-2025.pdf (page 2)."
        clean, removed = sanitize_citations(answer, CONTEXT)
        assert removed == []
        assert clean == answer

    def test_mixed_citations(self):
        answer = "Voir facture-edf-2025.pdf et aussi fantome.docx."
        clean, removed = sanitize_citations(answer, CONTEXT)
        assert removed == ["fantome.docx"]
        assert "facture-edf-2025.pdf" in clean
        assert "fantome.docx" not in clean


class TestStreamWarning:
    def test_warning_names_the_sources(self):
        warning = build_stream_citation_warning(["a.pdf", "b.md"])
        assert "a.pdf" in warning and "b.md" in warning
        assert "⚠️" in warning


class TestDeletedCitations:
    """US-28a — deterministic flagging of cited sources that are in trash."""

    def test_cited_trashed_file_is_flagged(self, tmp_path, monkeypatch):
        import aitao.indexation.trash as trash_module
        from aitao.indexation.trash import TrashRegistry

        registry = TrashRegistry(state_file=tmp_path / "trash.json")
        registry.mark(["/Users/phil/docs/PRD-liaotao-cli.md"])
        monkeypatch.setattr(trash_module, "_registry", registry)

        from aitao.llm.citation_guard import (
            build_deleted_files_notice,
            find_deleted_citations,
        )

        answer = "La date figure dans PRD-liaotao-cli.md."
        deleted = find_deleted_citations(answer, CONTEXT)
        assert deleted == ["PRD-liaotao-cli.md"]
        notice = build_deleted_files_notice(deleted)
        assert "PRD-liaotao-cli.md" in notice
        assert "n'existe" in notice

    def test_live_file_not_flagged(self, tmp_path, monkeypatch):
        import aitao.indexation.trash as trash_module
        from aitao.indexation.trash import TrashRegistry

        registry = TrashRegistry(state_file=tmp_path / "trash.json")  # empty
        monkeypatch.setattr(trash_module, "_registry", registry)

        from aitao.llm.citation_guard import find_deleted_citations

        answer = "Source : facture-edf-2025.pdf"
        assert find_deleted_citations(answer, CONTEXT) == []

    def test_trashed_but_not_cited_is_not_flagged(self, tmp_path, monkeypatch):
        import aitao.indexation.trash as trash_module
        from aitao.indexation.trash import TrashRegistry

        registry = TrashRegistry(state_file=tmp_path / "trash.json")
        registry.mark(["/Users/phil/docs/PRD-liaotao-cli.md"])
        monkeypatch.setattr(trash_module, "_registry", registry)

        from aitao.llm.citation_guard import find_deleted_citations

        # Answer does not mention the trashed file
        answer = "Je n'ai pas cette information."
        assert find_deleted_citations(answer, CONTEXT) == []


class TestMultiSourceNotice:
    """US-90-4 — deterministic note listing every doc holding the token."""

    def _doc(self, path: str, content: str) -> SimpleNamespace:
        return SimpleNamespace(path=path, title=path.split("/")[-1], content=content)

    def test_lists_all_bearers(self):
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [
            self._doc("/a/rules.pdf", "contact x@y.zz for HR"),
            self._doc("/b/work.pdf", "email x@y.zz also here"),
            self._doc("/c/other.pdf", "nothing relevant at all"),
        ]
        note = build_multi_source_notice("où est x@y.zz ?", docs)
        assert "2 documents" in note
        assert "/a/rules.pdf" in note and "/b/work.pdf" in note  # full paths
        assert "other.pdf" not in note

    def test_empty_when_single_bearer(self):
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [
            self._doc("/a/rules.pdf", "contact x@y.zz here"),
            self._doc("/b/other.pdf", "nothing relevant"),
        ]
        assert build_multi_source_notice("où est x@y.zz ?", docs) == ""

    def test_empty_when_no_distinctive_token(self):
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [self._doc("/a/x.pdf", "foo bar"), self._doc("/b/y.pdf", "foo bar")]
        assert build_multi_source_notice("de quoi parle ce document ?", docs) == ""

    def test_cjk_token_bearers(self):
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [
            self._doc("/a/rule1.pdf", "公司規定 事假扣薪 政策"),
            self._doc("/b/rule2.pdf", "事假扣薪 適用範圍"),
        ]
        note = build_multi_source_notice("事假扣薪", docs)
        assert "2 documents" in note
        assert "rule1.pdf" in note and "rule2.pdf" in note

    def test_states_true_total_when_capped(self):
        # US-90-5 — counter reports more bearers than listed → "au moins N".
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [self._doc("/a/r1.pdf", "x@y.zz"), self._doc("/b/r2.pdf", "x@y.zz")]
        note = build_multi_source_notice(
            "où est x@y.zz ?", docs, bearer_counter=lambda t: 35
        )
        assert "au moins 35 documents" in note
        assert "/a/r1.pdf" in note and "/b/r2.pdf" in note

    def test_exact_count_when_counter_not_greater(self):
        from aitao.llm.citation_guard import build_multi_source_notice

        docs = [self._doc("/a/r1.pdf", "x@y.zz"), self._doc("/b/r2.pdf", "x@y.zz")]
        note = build_multi_source_notice(
            "où est x@y.zz ?", docs, bearer_counter=lambda t: 2
        )
        assert "dans 2 documents" in note
        assert "au moins" not in note


class TestAbbreviatedTitleCitation:
    """US-17c — a cited title (prefix of <title>-version-date) is recognised,
    without letting an invented name through."""

    def _ctx(self):
        long_name = "外籍從業人員管理辦法-第1版-20160901.pdf"
        return [SimpleNamespace(path=f"/hr/{long_name}", title=long_name[:-4])]

    def test_abbreviated_chinese_title_recognised(self):
        # Title without the -version-date suffix must NOT be flagged (the bug).
        assert find_fabricated_citations(
            "Source : 外籍從業人員管理辦法.pdf", self._ctx()
        ) == []

    def test_full_chinese_name_still_recognised(self):
        assert find_fabricated_citations(
            "Source : 外籍從業人員管理辦法-第1版-20160901.pdf", self._ctx()
        ) == []

    def test_invented_chinese_name_still_flagged(self):
        # A different (invented) doc name is still caught — no regression.
        assert find_fabricated_citations(
            "Source : 完全虛構的文件.pdf", self._ctx()
        ) == ["完全虛構的文件.pdf"]

    def test_generic_suffix_word_not_validated(self):
        # "管理辦法" is a suffix-word, NOT a prefix of the indexed name → flagged.
        assert find_fabricated_citations(
            "Source : 管理辦法.pdf", self._ctx()
        ) == ["管理辦法.pdf"]

    def test_latin_prefix_needs_boundary(self):
        # "data" is a prefix of "database.pdf" but NOT at a separator → flagged;
        # "rapport" before "-2024" is a real title prefix → recognised.
        ctx = [
            SimpleNamespace(path="/d/database.pdf", title="database"),
            SimpleNamespace(path="/d/rapport-2024.pdf", title="rapport-2024"),
        ]
        assert find_fabricated_citations("voir data.pdf", ctx) == ["data.pdf"]
        assert find_fabricated_citations("voir rapport.pdf", ctx) == []
