# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for RAGEngine.

Tests cover:
- Initialization and configuration
- Context search functionality
- Prompt enrichment
- Token estimation and truncation
- Context formatting
- Chat message enrichment
- Error handling
"""

import pytest
from unittest.mock import Mock, patch
from dataclasses import dataclass

from aitao.llm.rag_engine import (
    RAGEngine,
    RAGResult,
    ContextDocument,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Mock ConfigManager with RAG configuration (US-22 typed API)."""
    config = Mock()
    config.rag.max_context_docs = 5
    config.rag.max_context_chunks = 5
    config.rag.context_max_tokens = 2000
    config.rag.min_relevance_score = 0.3
    config.rag.include_metadata = True
    config.rag.use_chunks = True
    config.rag.pin_exact_token = True  # US-89-2
    config.rag.pin_max_docs = 7
    return config


@pytest.fixture
def mock_config_defaults():
    """Mock ConfigManager using RAGEngine class-level defaults (US-22 typed API)."""
    config = Mock()
    config.rag.max_context_docs = RAGEngine.DEFAULT_MAX_CONTEXT_DOCS
    config.rag.max_context_chunks = RAGEngine.DEFAULT_MAX_CONTEXT_CHUNKS
    config.rag.context_max_tokens = RAGEngine.DEFAULT_CONTEXT_MAX_TOKENS
    config.rag.min_relevance_score = RAGEngine.DEFAULT_MIN_RELEVANCE_SCORE
    config.rag.include_metadata = RAGEngine.DEFAULT_INCLUDE_METADATA
    config.rag.use_chunks = RAGEngine.DEFAULT_USE_CHUNKS
    config.rag.pin_exact_token = True  # US-89-2
    config.rag.pin_max_docs = 7
    return config


@pytest.fixture
def mock_logger():
    """Mock StructuredLogger."""
    logger = Mock()
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def rag_engine(mock_config, mock_logger):
    """Create RAGEngine with mocked dependencies."""
    return RAGEngine(mock_config, mock_logger)


@pytest.fixture
def sample_search_results():
    """Sample search results for mocking HybridSearchEngine."""
    @dataclass
    class MockSearchResult:
        id: str
        path: str
        title: str
        content: str
        score: float
        category: str = None
        language: str = None
        metadata: dict = None
        
        def __post_init__(self):
            if self.metadata is None:
                self.metadata = {}
    
    @dataclass
    class MockSearchResponse:
        results: list
        search_time_ms: float = 50.0
    
    results = [
        MockSearchResult(
            id="abc123",
            path="/docs/report.pdf",
            title="Annual Report 2025",
            content="This is the annual financial report with detailed analysis...",
            score=0.95,
            category="finance",
            language="en",
        ),
        MockSearchResult(
            id="def456",
            path="/docs/manual.md",
            title="User Manual",
            content="Installation guide and usage instructions for the system...",
            score=0.72,
            category="documentation",
            language="en",
        ),
        MockSearchResult(
            id="ghi789",
            path="/code/utils.py",
            title="Utility Functions",
            content="def parse_data(input): # Parses input data...",
            score=0.45,
            category="code",
            language="en",
        ),
    ]
    
    return MockSearchResponse(results=results)


# ============================================================================
# Initialization Tests
# ============================================================================

class TestRAGEngineInit:
    """Test RAGEngine initialization."""
    
    def test_init_with_config(self, mock_config, mock_logger):
        """Test initialization loads config correctly."""
        engine = RAGEngine(mock_config, mock_logger)
        
        assert engine.max_context_docs == 5
        assert engine.context_max_tokens == 2000
        assert engine.min_relevance_score == 0.3
        assert engine.include_metadata is True
        mock_logger.info.assert_called()
    
    def test_init_with_defaults(self, mock_config_defaults, mock_logger):
        """Test initialization uses defaults when no config."""
        engine = RAGEngine(mock_config_defaults, mock_logger)
        
        assert engine.max_context_docs == RAGEngine.DEFAULT_MAX_CONTEXT_DOCS
        assert engine.context_max_tokens == RAGEngine.DEFAULT_CONTEXT_MAX_TOKENS
        assert engine.min_relevance_score == RAGEngine.DEFAULT_MIN_RELEVANCE_SCORE
    
    def test_search_engine_lazy_load(self, rag_engine):
        """Test search engine is lazily loaded."""
        assert rag_engine._search_engine is None


# ============================================================================
# Token Estimation Tests
# ============================================================================

class TestTokenEstimation:
    """Test token estimation functionality."""
    
    def test_estimate_tokens(self, rag_engine):
        """Test token estimation from text."""
        text = "This is a test string with about forty characters."
        tokens = rag_engine._estimate_tokens(text)
        
        # ~50 chars / 4 chars per token = ~12 tokens
        assert 10 <= tokens <= 15
    
    def test_truncate_to_tokens(self, rag_engine):
        """Test text truncation to token limit."""
        long_text = "A" * 1000  # 1000 chars = ~250 tokens
        truncated = rag_engine._truncate_to_tokens(long_text, 50)
        
        # 50 tokens * 4 chars = 200 chars + "..."
        assert len(truncated) <= 210
        assert truncated.endswith("...")
    
    def test_truncate_short_text(self, rag_engine):
        """Test no truncation for short text."""
        short_text = "Hello world"
        result = rag_engine._truncate_to_tokens(short_text, 100)
        
        assert result == short_text


# ============================================================================
# Context Formatting Tests
# ============================================================================

class TestContextFormatting:
    """Test context document formatting."""
    
    def test_format_context_document(self, rag_engine):
        """Test single document formatting."""
        doc = ContextDocument(
            id="abc123",
            path="/docs/test.pdf",
            title="Test Document",
            content="This is test content.",
            score=0.85,
            category="test",
        )
        
        formatted = rag_engine._format_context_document(doc, 1)
        
        assert "[1] Test Document" in formatted
        assert "Path: /docs/test.pdf" in formatted
        assert "Category: test" in formatted
        # C-02 (US-103): the RRF score is a rank, not relevance (I-12) — it
        # must never be rendered to the model/user, even with metadata on.
        assert "Relevance" not in formatted
        assert "85%" not in formatted
        assert "Content: This is test content" in formatted
    
    def test_format_context_chunk_renders_no_relevance_percent(self):
        """C-02 (US-103): chunk formatting must not render the rank score either."""
        from aitao.llm.rag_context_formatter import format_context_chunk
        from aitao.llm.rag_models import ContextChunk

        chunk = ContextChunk(
            chunk_id="c1",
            doc_id="d1",
            path="/docs/test.pdf",
            title="Test Document",
            content="Chunk content.",
            chunk_index=0,
            total_chunks=2,
            score=0.92,
        )
        formatted = format_context_chunk(chunk, 1, include_metadata=True)
        assert "Source: /docs/test.pdf" in formatted
        assert "Relevance" not in formatted
        assert "92%" not in formatted

    def test_format_without_metadata(self, mock_config, mock_logger):
        """Test formatting with metadata disabled."""
        mock_config.rag.include_metadata = False
        engine = RAGEngine(mock_config, mock_logger)
        
        doc = ContextDocument(
            id="abc123",
            path="/docs/test.pdf",
            title="Test Document",
            content="Content here",
            score=0.85,
        )
        
        formatted = engine._format_context_document(doc, 1)
        
        assert "[1] Test Document" in formatted
        assert "Path:" not in formatted
        assert "Relevance:" not in formatted
    
    def test_build_context_section_empty(self, rag_engine):
        """Test context section with no documents."""
        section = rag_engine._build_context_section([])
        assert section == ""
    
    def test_build_context_section(self, rag_engine):
        """Test building context section with documents."""
        docs = [
            ContextDocument(
                id="1",
                path="/a.txt",
                title="Doc A",
                content="Content A",
                score=0.9,
            ),
            ContextDocument(
                id="2",
                path="/b.txt",
                title="Doc B",
                content="Content B",
                score=0.7,
            ),
        ]
        
        section = rag_engine._build_context_section(docs)
        
        assert "CONTEXT FROM YOUR DOCUMENTS" in section
        assert "[1] Doc A" in section
        assert "[2] Doc B" in section
        assert "END OF CONTEXT (2 documents)" in section


# ============================================================================
# Search Context Tests
# ============================================================================

class TestSearchContext:
    """Test context search functionality."""
    
    def test_search_context_success(self, rag_engine, sample_search_results):
        """Test successful context search."""
        # Mock the search engine
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        docs = rag_engine.search_context("find financial report")
        
        # Should filter by min_relevance_score (0.3)
        assert len(docs) == 3  # All 3 have score > 0.3
        assert docs[0].score == 0.95
        assert docs[0].title == "Annual Report 2025"
    
    def test_search_context_filters_low_scores(self, rag_engine, sample_search_results):
        """Test that low-score results are filtered."""
        # Modify to have one below threshold
        sample_search_results.results[2].score = 0.2  # Below 0.3
        
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        docs = rag_engine.search_context("query")
        
        assert len(docs) == 2  # One filtered out
    
    def test_search_context_with_filters(self, rag_engine, sample_search_results):
        """Test search with category filter."""
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        rag_engine.search_context(
            "query",
            filters={"category": "finance"},
        )

        # Verify filter was passed
        call_args = mock_search.search_sync.call_args
        assert call_args.kwargs["filters"] is not None
    
    def test_search_context_error_handling(self, rag_engine, mock_logger):
        """Test graceful handling of search errors."""
        mock_search = Mock()
        mock_search.search_sync.side_effect = Exception("Search failed")
        rag_engine._search_engine = mock_search
        
        # Should not raise, should return empty list
        docs = rag_engine.search_context("query")
        
        assert docs == []
        mock_logger.error.assert_called()


# ============================================================================
# Prompt Enrichment Tests
# ============================================================================

class TestEnrichPrompt:
    """Test prompt enrichment functionality."""
    
    def test_enrich_prompt_basic(self, rag_engine, sample_search_results):
        """Test basic prompt enrichment."""
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        result = rag_engine.enrich_prompt("What is in the annual report?")
        
        assert isinstance(result, RAGResult)
        assert result.original_prompt == "What is in the annual report?"
        assert "CONTEXT FROM YOUR DOCUMENTS" in result.enriched_prompt
        assert "What is in the annual report?" in result.enriched_prompt
        assert len(result.context_docs) > 0
        assert result.search_time_ms >= 0
    
    def test_enrich_prompt_no_context(self, rag_engine):
        """Test enrichment when no context found."""
        # Mock empty results
        mock_response = Mock()
        mock_response.results = []
        mock_response.search_time_ms = 10.0
        
        mock_search = Mock()
        mock_search.search_sync.return_value = mock_response
        rag_engine._search_engine = mock_search
        
        result = rag_engine.enrich_prompt("Random query")
        
        assert result.original_prompt == "Random query"
        assert result.enriched_prompt == "Random query"  # No context added
        assert len(result.context_docs) == 0
    
    def test_enrich_prompt_with_system_instruction(self, rag_engine, sample_search_results):
        """Test enrichment with system instruction."""
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        result = rag_engine.enrich_prompt(
            "What is the revenue?",
            system_instruction="You are a financial analyst.",
        )
        
        assert "You are a financial analyst." in result.enriched_prompt
        assert result.enriched_prompt.startswith("You are a financial analyst.")


# ============================================================================
# Chat Message Enrichment Tests
# ============================================================================

class TestEnrichMessages:
    """Test chat message enrichment."""
    
    def test_enrich_messages_basic(self, rag_engine, sample_search_results):
        """Test enriching chat messages."""
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is the annual report about?"},
        ]
        
        enriched, docs, time_ms = rag_engine.enrich_messages(messages)
        
        assert len(enriched) == 2
        assert enriched[0]["content"] == "You are helpful."  # Unchanged
        assert "CONTEXT FROM YOUR DOCUMENTS" in enriched[1]["content"]
        assert len(docs) > 0
    
    def test_enrich_messages_empty(self, rag_engine):
        """Test with empty messages."""
        enriched, docs, time_ms = rag_engine.enrich_messages([])
        
        assert enriched == []
        assert docs == []
        assert time_ms == 0.0
    
    def test_enrich_messages_no_user_message(self, rag_engine):
        """Test with no user message."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "assistant", "content": "Hello!"},
        ]
        
        enriched, docs, time_ms = rag_engine.enrich_messages(messages)
        
        assert enriched == messages  # Unchanged
        assert docs == []
    
    def test_enrich_messages_multi_turn(self, rag_engine, sample_search_results):
        """Test enrichment preserves conversation history."""
        mock_search = Mock()
        mock_search.search_sync.return_value = sample_search_results
        rag_engine._search_engine = mock_search
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Tell me about the report"},
        ]
        
        enriched, docs, time_ms = rag_engine.enrich_messages(messages)
        
        # First two messages unchanged
        assert enriched[0]["content"] == "Hello"
        assert enriched[1]["content"] == "Hi there!"
        # Last user message enriched
        assert "CONTEXT FROM YOUR DOCUMENTS" in enriched[2]["content"]


class TestEnrichMessagesContextUnification:
    """Regression: enrich_messages must surface retrieved context in BOTH modes.

    In chunk mode (the default) result.context_docs is empty — the context
    lives in result.context_chunks. enrich_messages must still return that
    context, otherwise the adequacy gate wrongly refuses every chunk-based
    retrieval.
    """

    def test_returns_chunk_context_in_chunk_mode(self, rag_engine):
        from aitao.llm.rag_models import ContextChunk, RAGResult

        chunk = ContextChunk(
            chunk_id="c1", doc_id="d1", path="/x/source.md", title="source",
            content="passage", chunk_index=0, total_chunks=1, score=0.6,
        )
        fake = RAGResult(
            original_prompt="q", enriched_prompt="CONTEXT...\nq",
            context_docs=[], context_chunks=[chunk], mode="chunks",
        )
        with patch.object(rag_engine, "enrich_prompt", return_value=fake):
            _, context, _ = rag_engine.enrich_messages(
                [{"role": "user", "content": "q"}]
            )

        assert len(context) == 1
        assert context[0].path == "/x/source.md"
        assert context[0].score == 0.6

    def test_returns_docs_in_document_mode(self, rag_engine):
        from aitao.llm.rag_models import ContextDocument, RAGResult

        doc = ContextDocument(
            id="d1", path="/x/doc.md", title="doc", content="c", score=0.8
        )
        fake = RAGResult(
            original_prompt="q", enriched_prompt="...",
            context_docs=[doc], context_chunks=[], mode="documents",
        )
        with patch.object(rag_engine, "enrich_prompt", return_value=fake):
            _, context, _ = rag_engine.enrich_messages(
                [{"role": "user", "content": "q"}]
            )

        assert len(context) == 1
        assert context[0].path == "/x/doc.md"


class TestMultiTurnRetrieval:
    """US-12 — strategy A: search with the question alone, fall back to recent
    conversation turns only when nothing anchored."""

    def _msgs(self):
        return [
            {"role": "user", "content": "parle-moi du projet Zeta"},
            {"role": "assistant", "content": "Le projet Zeta..."},
            {"role": "user", "content": "et le budget ?"},
        ]

    def test_recent_user_query_joins_last_user_turns(self, rag_engine):
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        # Only user turns, in order, last HISTORY_TURNS (=3)
        assert rag_engine._recent_user_query(messages, 3) == "A B C"

    def test_recent_user_query_caps_window(self, rag_engine):
        messages = [{"role": "user", "content": c} for c in ["A", "B", "C", "D"]]
        # Oldest ("A") dropped — window is HISTORY_TURNS
        assert rag_engine._recent_user_query(messages, 3) == "B C D"

    def test_pass_is_weak_aligns_with_gate(self, rag_engine):
        from aitao.llm.rag_models import ContextDocument
        q = "Quel est le budget du projet ?"
        # factual question, no docs → gate refuses → weak
        assert rag_engine._pass_is_weak(q, []) is True
        # a strongly-scored doc → gate answers → not weak
        assert rag_engine._pass_is_weak(
            q, [ContextDocument(id="d", path="/p", title="t", content="c", score=0.8)]
        ) is False
        # a greeting is never "weak" (no history search needed)
        assert rag_engine._pass_is_weak("Bonjour !", []) is False

    def test_followup_triggers_history_fallback(self, rag_engine):
        from aitao.llm.rag_models import ContextDocument, RAGResult

        weak = RAGResult(original_prompt="et le budget ?", enriched_prompt="W",
                         context_docs=[], context_chunks=[], mode="chunks")
        strong = RAGResult(original_prompt="et le budget ?", enriched_prompt="S",
                           context_docs=[], context_chunks=[], mode="chunks")
        calls = []

        def fake_enrich(prompt, search_query=None, **kw):
            calls.append(search_query)
            return strong if (search_query and "Zeta" in search_query) else weak

        def fake_score(result, anchor):
            return ([ContextDocument(id="d", path="/p", title="t",
                                     content="c", score=0.6)]
                    if result is strong else [])

        with patch.object(rag_engine, "enrich_prompt", side_effect=fake_enrich), \
             patch.object(rag_engine, "_score_context", side_effect=fake_score):
            enriched, context, _ = rag_engine.enrich_messages(self._msgs())

        assert calls[0] is None                    # pass 1: question alone
        assert calls[1] and "Zeta" in calls[1]     # pass 2: widened with history
        assert len(context) == 1 and context[0].score == 0.6
        assert enriched[2]["content"] == "S"       # adopted the stronger result

    def test_no_fallback_when_pass1_succeeds(self, rag_engine):
        from aitao.llm.rag_models import ContextDocument, RAGResult

        strong = RAGResult(original_prompt="q", enriched_prompt="S",
                           context_docs=[], context_chunks=[], mode="chunks")
        calls = []

        def fake_enrich(prompt, search_query=None, **kw):
            calls.append(search_query)
            return strong

        def fake_score(result, anchor):
            return [ContextDocument(id="d", path="/p", title="t",
                                    content="c", score=0.6)]

        with patch.object(rag_engine, "enrich_prompt", side_effect=fake_enrich), \
             patch.object(rag_engine, "_score_context", side_effect=fake_score):
            rag_engine.enrich_messages(self._msgs())

        assert len(calls) == 1   # standalone success → no second pass


class TestKeywordPinning:
    """US-30 — filename + exact-phrase docs are pinned to the context."""

    def _engine_with_meili(self, rag_engine, hits_by_query, docs_by_id):
        """Wire a fake Meilisearch client (search + get_document)."""
        from unittest.mock import Mock
        client = Mock()
        client.search.side_effect = lambda q, limit=5: hits_by_query.get(q, [])
        client.get_document.side_effect = lambda doc_id: docs_by_id.get(doc_id)
        engine = Mock()
        engine.meilisearch_client = client
        rag_engine._search_engine = engine
        return client

    def test_word_windows(self, rag_engine):
        assert rag_engine._word_windows("a B c d", 3) == ["a b c", "b c d"]
        assert rag_engine._word_windows("one two", 3) == []

    def test_named_file_pinned(self, rag_engine):
        from aitao.indexation.indexer_helpers import generate_doc_id
        path = "/docs/jobs.txt"
        did = generate_doc_id(path)
        self._engine_with_meili(
            rag_engine,
            hits_by_query={"jobs": [{"path": path, "title": "jobs"}]},
            docs_by_id={did: {"id": did, "path": path, "title": "jobs",
                              "content": "job offers from Micron and Giant"}},
        )
        pinned = rag_engine._named_file_docs("de quoi parle jobs ?")
        assert [d.path for d in pinned] == [path]
        assert pinned[0].score == 1.0
        assert "Micron" in pinned[0].content  # raw content, not the excerpt

    def test_named_file_from_previous_turn_stays_pinned(self, rag_engine):
        """US-RAG-name volet C: a file named in turn 1 stays pinned in turn 2.

        The follow-up ("complète ta traduction") does not repeat the filename;
        the named-doc resolution must widen to the recent user turns.
        """
        from aitao.llm.rag_models import ContextDocument, RAGResult

        doc = ContextDocument(id="d", path="/vol/taiwan.pdf", title="taiwan",
                              content="中文內容", score=1.0)
        empty = RAGResult(original_prompt="q", enriched_prompt="E",
                          context_docs=[], context_chunks=[], mode="chunks")
        msgs = [
            {"role": "user",
             "content": "Traduis 20260701_Assurance Maladie taiwan.pdf"},
            {"role": "assistant", "content": "Voici la page 1…"},
            {"role": "user", "content": "Complète ta traduction"},
        ]
        with patch.object(rag_engine, "enrich_prompt", return_value=empty), \
             patch.object(rag_engine, "_score_context", return_value=[]), \
             patch.object(rag_engine, "_pass_is_weak", return_value=False), \
             patch.object(rag_engine, "_keyword_pinned_docs", return_value=[]), \
             patch.object(rag_engine, "_resolved_named_docs",
                          return_value=[doc]) as resolved:
            _, context, _ = rag_engine.enrich_messages(msgs)

        assert [d.path for d in context] == ["/vol/taiwan.pdf"]
        # The resolver ran on the history window, which carries the turn-1 name.
        resolved.assert_called_once()
        assert "taiwan.pdf" in resolved.call_args[0][0]

    def test_pinned_doc_included_in_full(self):
        """US-RAG-name: a named/pinned doc keeps its full content, not a 500-char
        preview, so "translate this file" sees every page."""
        from aitao.llm.rag_context_formatter import build_context_section
        from aitao.llm.rag_models import ContextDocument

        long_content = "PAGE1 " + "à" * 800 + " 成立通知書 " + "é" * 800 + " FIN_PAGE2"
        doc = ContextDocument(id="d", path="/vol/contrat.pdf", title="contrat",
                              content=long_content, score=1.0)

        preview = build_context_section([doc], max_tokens=4000)
        assert "成立通知書" not in preview          # capped at 500 chars by default
        assert "FIN_PAGE2" not in preview

        full = build_context_section([doc], max_tokens=4000,
                                     full_content_paths={"/vol/contrat.pdf"})
        assert "成立通知書" in full                  # full content for the named doc
        assert "FIN_PAGE2" in full

    def test_multiword_named_file_pinned(self, rag_engine):
        """US-RAG-name: a multi-word filename buried in a question is pinned.

        _named_file_docs cannot match this (its stem has spaces, no single query
        term equals it); _resolved_named_docs resolves it via title coverage.
        """
        from aitao.indexation.indexer_helpers import generate_doc_id
        path = "/docs/20260701_Assurance Maladie taiwan.pdf"
        did = generate_doc_id(path)
        title = "20260701_Assurance Maladie taiwan"
        client = self._engine_with_meili(
            rag_engine,
            hits_by_query={},
            docs_by_id={did: {"id": did, "path": path, "title": title,
                              "content": "衛生福利部中央健康保險署 函 投保單位"}},
        )
        client.search_titles = lambda q, limit=10: [
            {"id": did, "path": path, "title": title}
        ]
        pinned = rag_engine._resolved_named_docs(
            "Traduis le document 20260701_Assurance Maladie taiwan.pdf, de quoi s'agit-il ?"
        )
        assert [d.path for d in pinned] == [path]
        assert "衛生福利部" in pinned[0].content

    def test_resolved_named_docs_ignores_ordinary_query(self, rag_engine):
        """An ordinary question (title not covered) pins nothing."""
        client = self._engine_with_meili(rag_engine, hits_by_query={}, docs_by_id={})
        client.search_titles = lambda q, limit=10: [
            {"id": "x", "path": "/docs/Rapport Annuel 2024 Taiwan.pdf",
             "title": "Rapport Annuel 2024 Taiwan"}
        ]
        assert rag_engine._resolved_named_docs("quelle est la météo demain ?") == []

    def test_verbatim_phrase_pinned(self, rag_engine):
        from aitao.indexation.indexer_helpers import generate_doc_id
        path = "/docs/report.md"
        did = generate_doc_id(path)
        phrase = "the quarterly revenue grew by twenty percent"
        self._engine_with_meili(
            rag_engine,
            hits_by_query={phrase: [{"path": path, "title": "report"}]},
            docs_by_id={did: {"id": did, "path": path, "title": "report",
                              "content": f"Intro. {phrase}. More text."}},
        )
        pinned = rag_engine._verbatim_phrase_docs(phrase)
        assert [d.path for d in pinned] == [path]

    def test_no_verbatim_match_returns_empty(self, rag_engine):
        from aitao.indexation.indexer_helpers import generate_doc_id
        path = "/docs/other.md"
        did = generate_doc_id(path)
        # hit exists but content does NOT contain the phrase → not pinned
        self._engine_with_meili(
            rag_engine,
            hits_by_query={"a totally unrelated five word query":
                           [{"path": path, "title": "other"}]},
            docs_by_id={did: {"id": did, "path": path, "title": "other",
                              "content": "nothing relevant here at all"}},
        )
        assert rag_engine._verbatim_phrase_docs(
            "a totally unrelated five word query"
        ) == []

    def test_short_query_skips_phrase_search(self, rag_engine):
        client = self._engine_with_meili(rag_engine, {}, {})
        assert rag_engine._verbatim_phrase_docs("too short") == []
        client.search.assert_not_called()

    # --- US-89-2: exact-token pinning ---

    def test_exact_token_pins_every_doc(self, rag_engine):
        """A distinctive token pins EVERY doc that contains it (not just top-1)."""
        from aitao.indexation.indexer_helpers import generate_doc_id
        email = "k3x7@acmemotor.com.tw"
        paths = ["/hr/rules.md", "/hr/harassment.md", "/hr/work.md"]
        ids = {p: generate_doc_id(p) for p in paths}
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": p} for p in paths]},
            docs_by_id={
                ids[p]: {"id": ids[p], "path": p,
                         "content": f"Contact HR at {email} for any request."}
                for p in paths
            },
        )
        pinned = rag_engine._exact_token_docs(
            f"Dans quel document apparait l'email {email} ?"
        )
        assert {d.path for d in pinned} == set(paths)

    def test_exact_token_respects_cap(self, rag_engine):
        from aitao.indexation.indexer_helpers import generate_doc_id
        rag_engine.pin_max_docs = 2
        email = "x@y.zz"
        paths = [f"/d/{i}.md" for i in range(5)]
        ids = {p: generate_doc_id(p) for p in paths}
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": p} for p in paths]},
            docs_by_id={
                ids[p]: {"id": ids[p], "path": p, "content": f"see {email}"}
                for p in paths
            },
        )
        assert len(rag_engine._exact_token_docs(f"où est {email} ?")) == 2

    def test_exact_token_confirms_verbatim(self, rag_engine):
        """A Meili hit whose raw content lacks the token verbatim is not pinned."""
        from aitao.indexation.indexer_helpers import generate_doc_id
        email = "a@b.cc"
        path = "/d/noise.md"
        did = generate_doc_id(path)
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": path}]},
            docs_by_id={did: {"id": did, "path": path,
                              "content": "this file does not contain the address"}},
        )
        assert rag_engine._exact_token_docs(f"où est {email} ?") == []

    def test_plain_question_pins_no_token(self, rag_engine):
        client = self._engine_with_meili(rag_engine, {}, {})
        assert rag_engine._exact_token_docs("de quoi parle ce document ?") == []
        client.search.assert_not_called()

    def test_exact_token_disabled_by_config(self, rag_engine):
        rag_engine.pin_exact_token = False
        client = self._engine_with_meili(rag_engine, {}, {})
        assert rag_engine._exact_token_docs("où est a@b.cc ?") == []
        client.search.assert_not_called()

    # --- US-90: token excerpt + exhaustive-citation rule ---

    def test_excerpt_centers_on_token(self, rag_engine):
        content = ("x" * 1000) + "TARGET" + ("y" * 1000)
        out = rag_engine._excerpt_around(content, "target", radius=50)
        assert "TARGET" in out  # token visible
        assert out.startswith("…") and out.endswith("…")  # cropped both sides
        assert len(out) < 200  # a small window, not the whole doc

    def test_excerpt_head_when_token_absent(self, rag_engine):
        # Never raises; falls back to the document head.
        out = rag_engine._excerpt_around("short body without it", "zzz", radius=50)
        assert isinstance(out, str) and out

    def test_pinned_doc_carries_token_excerpt(self, rag_engine):
        """US-90 — a long pinned doc is reduced to an excerpt showing the token."""
        from aitao.indexation.indexer_helpers import generate_doc_id
        email = "x@y.zz"
        path = "/d/big.md"
        did = generate_doc_id(path)
        # token buried far past the formatter's 500-char crop
        content = ("A" * 2000) + f" reach us at {email} anytime " + ("B" * 2000)
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": path}]},
            docs_by_id={did: {"id": did, "path": path, "content": content}},
        )
        pinned = rag_engine._exact_token_docs(f"où est {email} ?")
        assert len(pinned) == 1
        assert email in pinned[0].content  # token visible in the excerpt
        assert len(pinned[0].content) < len(content)  # excerpt, not whole doc

    def test_grounding_rules_demand_all_sources(self):
        from aitao.llm.rag_context_formatter import GROUNDING_RULES
        assert "ALL of them" in GROUNDING_RULES
        assert "single source" in GROUNDING_RULES

    def test_count_token_bearers_not_capped(self, rag_engine):
        """US-90-5 — the true count ignores pin_max_docs."""
        from aitao.indexation.indexer_helpers import generate_doc_id
        rag_engine.pin_max_docs = 2  # cap must NOT limit the count
        email = "x@y.zz"
        paths = [f"/d/{i}.md" for i in range(5)]
        ids = {p: generate_doc_id(p) for p in paths}
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": p} for p in paths]},
            docs_by_id={
                ids[p]: {"id": ids[p], "path": p, "content": f"see {email} in {p}"}
                for p in paths
            },
        )
        assert rag_engine.count_token_bearers(email) == 5

    def test_dedup_by_content_keeps_first(self, rag_engine):
        """US-90-3 — same content under two paths is kept once (the first)."""
        a = ContextDocument(id="1", path="/x/a.md", title="a", content="same body", score=1.0)
        b = ContextDocument(id="2", path="/y/b.md", title="b", content="same body", score=1.0)
        c = ContextDocument(id="3", path="/z/c.md", title="c", content="other body", score=1.0)
        out = rag_engine._dedup_by_content([a, b, c])
        assert [d.path for d in out] == ["/x/a.md", "/z/c.md"]

    def test_dedup_keeps_empty_content(self, rag_engine):
        a = ContextDocument(id="1", path="/x/a.md", title="a", content="", score=1.0)
        b = ContextDocument(id="2", path="/y/b.md", title="b", content="", score=1.0)
        out = rag_engine._dedup_by_content([a, b])
        assert len(out) == 2  # empties are not treated as duplicates

    def test_count_token_bearers_dedup_content(self, rag_engine):
        """US-90-3 — two paths with identical content count as one."""
        from aitao.indexation.indexer_helpers import generate_doc_id
        email = "x@y.zz"
        paths = ["/a/copy1.md", "/b/copy1.md", "/c/other.md"]
        ids = {p: generate_doc_id(p) for p in paths}
        self._engine_with_meili(
            rag_engine,
            hits_by_query={email: [{"path": p} for p in paths]},
            docs_by_id={
                ids["/a/copy1.md"]: {"id": "1", "path": "/a/copy1.md", "content": f"ref {email}"},
                ids["/b/copy1.md"]: {"id": "2", "path": "/b/copy1.md", "content": f"ref {email}"},
                ids["/c/other.md"]: {"id": "3", "path": "/c/other.md",
                                     "content": f"see {email} elsewhere"},
            },
        )
        assert rag_engine.count_token_bearers(email) == 2  # copies counted once


class TestMetaQuestionReduction:
    """I-17 — meta-question shape reduction wired into RAGEngine (étude US-106)."""

    def test_reduces_meta_shape_to_subject(self, rag_engine):
        out = rag_engine._reduce_meta_question(
            "Quels sont les documents qui parlent d'enseignants ?"
        )
        assert out == "enseignants"

    def test_leaves_ordinary_question_untouched(self, rag_engine):
        raw = "Quel est le montant total dû sur la facture INV-2026-0042 ?"
        assert rag_engine._reduce_meta_question(raw) == raw

    def test_runs_even_when_distill_query_disabled(self, rag_engine):
        # US-89-1 rarity gate off (e.g. a tiny corpus) must NOT disable this
        # rule — it needs no frequency oracle, unlike _distill_query.
        rag_engine.distill_query = False
        out = rag_engine._reduce_meta_question("Y a-t-il des documents sur RGPD ?")
        assert out == "RGPD"

    def test_logs_info_when_reliability_debug_on(self, rag_engine, mock_logger):
        # RAGEngine.__init__ already logged once — reset so this test only
        # sees calls made by the method under test.
        mock_logger.reset_mock()
        rag_engine.reliability_debug = True
        rag_engine._reduce_meta_question("Quels documents parlent de X ?")
        mock_logger.info.assert_called_once()
        assert mock_logger.info.call_args[0][0] == "Meta-question shape reduced (I-17)"
        mock_logger.debug.assert_not_called()

    def test_logs_debug_when_reliability_debug_off(self, rag_engine, mock_logger):
        mock_logger.reset_mock()
        rag_engine.reliability_debug = False
        rag_engine._reduce_meta_question("Quels documents parlent de X ?")
        mock_logger.debug.assert_called_once()
        assert mock_logger.debug.call_args[0][0] == "Meta-question shape reduced (I-17)"
        mock_logger.info.assert_not_called()

    def test_near_miss_logs_debug_only_never_info(self, rag_engine, mock_logger):
        mock_logger.reset_mock()
        rag_engine.reliability_debug = True
        rag_engine._reduce_meta_question("Quels enseignants sont mentionnés ?")
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_called_once()

    def test_no_log_for_unrelated_question(self, rag_engine, mock_logger):
        mock_logger.reset_mock()
        rag_engine._reduce_meta_question("Le document parle de X")
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_not_called()
