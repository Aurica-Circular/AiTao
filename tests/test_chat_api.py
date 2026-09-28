# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for chat API endpoints.

Tests the /api/chat (Ollama-compatible) and /v1/chat/completions (OpenAI-compatible)
endpoints with mocked OllamaClient and RAGEngine dependencies.
"""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_ollama_client():
    """Create a mock OllamaClient."""
    with patch("aitao.api.routes.chat.get_ollama_client") as mock_get:
        client = MagicMock()
        mock_get.return_value = client
        yield client


@pytest.fixture
def mock_rag_engine():
    """Create a mock RAGEngine."""
    with patch("aitao.api.routes.chat.get_rag_engine") as mock_get:
        engine = MagicMock()
        mock_get.return_value = engine
        yield engine


@pytest.fixture
def mock_context_docs():
    """Create mock context documents."""
    from aitao.llm.rag_engine import ContextDocument
    return [
        ContextDocument(
            id="doc1",
            path="/test/file1.md",
            title="Test Document 1",
            content="This is test content for document 1.",
            score=0.95,
            category="documentation",
        ),
        ContextDocument(
            id="doc2",
            path="/test/file2.py",
            title="Test Document 2",
            content="def hello(): return 'world'",
            score=0.85,
            category="code",
        ),
    ]


@pytest.fixture
def chat_app():
    """Create test client for the main app."""
    # Need to reset global state
    import aitao.api.routes.chat as chat_module
    chat_module._ollama_client = None
    chat_module._rag_engine = None
    
    from aitao.api.main import app
    return TestClient(app)


# ============================================================================
# /api/chat Endpoint Tests
# ============================================================================

class TestChatEndpoint:
    """Tests for /api/chat (Ollama-compatible)."""
    
    def test_chat_basic_request(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test basic chat request."""
        # Setup mock
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Hello"}],
            [],
            ""
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "qwen2.5-coder:7b"
        assert data["message"]["role"] == "assistant"
        assert "Hello" in data["message"]["content"]
        assert data["done"] is True
    
    def test_chat_with_rag_context(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """Test chat with RAG context enrichment."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Based on the docs..."},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [
                {"role": "system", "content": "Context: test content"},
                {"role": "user", "content": "What does the doc say?"},
            ],
            mock_context_docs,
            "Context: test content",
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "What does the doc say?"}],
            "stream": False,
            "rag_enabled": True,
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["rag_context"] is not None
        assert len(data["rag_context"]) == 2
        assert data["rag_context"][0]["id"] == "doc1"
        assert data["rag_context"][0]["score"] == 0.95
    
    def test_chat_rag_disabled(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test chat with RAG disabled."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Simple response"},
            "done": True,
        }
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
            "rag_enabled": False,
        })
        
        assert response.status_code == 200
        data = response.json()
        # RAG engine should not be called
        mock_rag_engine.enrich_messages.assert_not_called()
        assert data["rag_context"] is None
    
    def test_chat_multiple_messages(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test chat with multiple messages (conversation)."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Here's the answer..."},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "What is Python?"},
            ],
            [],
            "",
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "What is Python?"},
            ],
            "stream": False,
        })
        
        assert response.status_code == 200
        # Verify all messages were passed (may include injected system message)
        call_args = mock_rag_engine.enrich_messages.call_args
        assert len(call_args[0][0]) >= 3
    
    def test_chat_with_options(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """Test chat with model options."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Creative response"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Be creative"}],
            mock_context_docs,
            "",
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Be creative"}],
            "stream": False,
            "options": {"temperature": 0.9, "top_p": 0.95},
        })
        
        assert response.status_code == 200
        # Verify options passed to Ollama
        call_args = mock_ollama_client.chat.call_args
        assert call_args[1].get("options") == {"temperature": 0.9, "top_p": 0.95}
    
    def test_chat_ollama_connection_error(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test handling of Ollama connection error."""
        from aitao.llm.protocols import OllamaConnectionError
        mock_ollama_client.chat.side_effect = OllamaConnectionError("Cannot connect")
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Hello"}],
            [],
            "",
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        
        assert response.status_code == 503
        assert "unavailable" in response.json()["message"].lower()
    
    def test_chat_model_not_found(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test handling of model not found error."""
        from aitao.llm.protocols import OllamaModelNotFound
        mock_ollama_client.chat.side_effect = OllamaModelNotFound("unknown-model")
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Hello"}],
            [],
            "",
        )
        
        response = chat_app.post("/api/chat", json={
            "model": "unknown-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        
        assert response.status_code == 404
        assert "unknown-model" in response.json()["message"]
    
    def test_chat_rag_failure_graceful(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test graceful handling when RAG fails."""
        mock_rag_engine.enrich_messages.side_effect = Exception("RAG error")
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Response without RAG"},
            "done": True,
        }
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        
        # Should succeed even if RAG fails
        assert response.status_code == 200


# ============================================================================
# /v1/chat/completions Endpoint Tests (OpenAI-compatible)
# ============================================================================

class TestOpenAIChatEndpoint:
    """Tests for /v1/chat/completions (OpenAI-compatible)."""
    
    def test_openai_basic_request(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test basic OpenAI-format chat request."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Hello there!"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Hello"}],
            [],
            "",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert "id" in data
        assert data["model"] == "qwen2.5-coder:7b"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
    
    def test_openai_with_temperature(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """Test OpenAI request with temperature parameter."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Response"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Test"}],
            mock_context_docs,
            "",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Test"}],
            "temperature": 0.7,
        })
        
        assert response.status_code == 200
        # Verify temperature passed to Ollama
        call_args = mock_ollama_client.chat.call_args
        assert call_args[1].get("options", {}).get("temperature") == 0.7
    
    def test_openai_with_max_tokens(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """Test OpenAI request with max_tokens parameter."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Short"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Test"}],
            mock_context_docs,
            "",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Test"}],
            "max_tokens": 100,
        })
        
        assert response.status_code == 200
        # max_tokens should be converted to num_predict for Ollama
        call_args = mock_ollama_client.chat.call_args
        assert call_args[1].get("options", {}).get("num_predict") == 100
    
    def test_openai_response_format(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test OpenAI response has correct format."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Response content"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Test"}],
            [],
            "",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Test"}],
        })
        
        data = response.json()
        
        # Verify OpenAI format
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert data["object"] == "chat.completion"
        assert "created" in data
        assert isinstance(data["created"], int)
        assert data["choices"][0]["index"] == 0
    
    def test_openai_with_rag_context(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """Test OpenAI response includes RAG context extension."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Based on context..."},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [
                {"role": "system", "content": "Context info"},
                {"role": "user", "content": "Question"},
            ],
            mock_context_docs,
            "Context info",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Question"}],
            "aitao": {"rag": True},
        })

        data = response.json()
        assert data["rag_context"] is not None
        assert len(data["rag_context"]) == 2
    
    def test_openai_rag_disabled(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test OpenAI request with RAG disabled."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Response"},
            "done": True,
        }
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "aitao": {"rag": False},
        })

        assert response.status_code == 200
        mock_rag_engine.enrich_messages.assert_not_called()
    
    def test_openai_context_default_on(self, chat_app, mock_ollama_client, mock_rag_engine, mock_context_docs):
        """v3.1 / US-DEMO-7: context is ON by default when no `aitao` field is sent.

        This is the OnlyOffice case — the client cannot send `aitao.rag`, yet
        AiTao must still enrich with context.
        """
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Answer"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Question"}],
            mock_context_docs,
            "ctx",
        )

        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Question"}],
        })

        assert response.status_code == 200
        # Tier 2 retrieval ran even though no aitao.rag flag was provided
        mock_rag_engine.enrich_messages.assert_called_once()
        assert response.json()["rag_context"] is not None

    def test_openai_tier1_survives_rag_failure(self, chat_app, mock_ollama_client):
        """Tier 1 (identity + config) is injected even if the RAG engine fails.

        Guarantees AiTao's identity and indexed-paths awareness in Core edition
        or when document retrieval is down — the v3.1 always-on context contract.
        """
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Je suis AiTao"},
            "done": True,
        }
        with patch(
            "aitao.api.routes.chat.get_rag_engine",
            side_effect=Exception("engine down"),
        ):
            response = chat_app.post("/v1/chat/completions", json={
                "model": "qwen2.5-coder:7b",
                "messages": [{"role": "user", "content": "Qui es-tu ?"}],
            })

        assert response.status_code == 200
        # A Tier 1 system message reached Ollama despite the RAG failure
        sent_messages = mock_ollama_client.chat.call_args.kwargs["messages"]
        system_text = " ".join(m.content for m in sent_messages if m.role == "system")
        assert "AiTao" in system_text                    # who_is_aitao (identity)
        assert "ABOUT THE USER" in system_text           # role separation (US-DEMO-8)
        assert "INDEXED LOCATIONS" in system_text        # include_paths section

    def test_openai_config_directive_for_user_identity(self, chat_app, mock_ollama_client):
        """US-DEMO-9: 'qui suis-je ?' injects a config directive grounded in who_are_you."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Vous êtes Phil"},
            "done": True,
        }
        with patch(
            "aitao.api.routes.chat.get_rag_engine",
            side_effect=Exception("no rag"),
        ):
            response = chat_app.post("/v1/chat/completions", json={
                "model": "qwen2.5-coder:7b",
                "messages": [{"role": "user", "content": "et moi, qui suis-je ?"}],
            })

        assert response.status_code == 200
        sent_messages = mock_ollama_client.chat.call_args.kwargs["messages"]
        system_text = " ".join(m.content for m in sent_messages if m.role == "system")
        assert "DIRECT INSTRUCTION" in system_text   # config directive injected
        assert "Phil" in system_text                 # grounded in who_are_you

    def test_openai_config_question_skips_document_search(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Config questions answer from config — Tier 2 document search is skipped."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Mes dossiers indexés sont…"},
            "done": True,
        }
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "quels volumes peux-tu indexer ?"}],
        })
        assert response.status_code == 200
        mock_rag_engine.enrich_messages.assert_not_called()  # no document search
        mock_ollama_client.chat.assert_called_once()

    def test_openai_refuses_when_no_relevant_context(self, chat_app, mock_ollama_client, mock_rag_engine):
        """US-DEMO-10: a factual question with no relevant doc is refused — no
        GENERATION call. US-105: the intent router's own probe call still runs
        first (it decides whether to search at all) — here it fails open to
        "documentary" (the mock has no real model behind it), so retrieval and
        the refusal proceed exactly as before; only the answer generation call
        is skipped."""
        question = "Quelles sont les charges de copropriété 2025 ?"
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": question}],
            [],   # no relevant documents retrieved
            "",
        )
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": question}],
        })
        assert response.status_code == 200
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        assert "documents" in content.lower()        # refusal wording
        assert data["context_source"] == "none"
        # Exactly one chat() call: the US-105 intent router probe — never a
        # generation call for the refused answer.
        assert mock_ollama_client.chat.call_count == 1
        probe_messages = mock_ollama_client.chat.call_args.kwargs["messages"]
        assert "DOCUMENTARY" in probe_messages[0].content

    def test_openai_config_question_not_refused_without_docs(self, chat_app, mock_ollama_client, mock_rag_engine):
        """A config question is answered (not refused) even with zero documents."""
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": "Je suis AiTao"},
            "done": True,
        }
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "qui es-tu ?"}],
            [],
            "",
        )
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "qui es-tu ?"}],
        })
        assert response.status_code == 200
        mock_ollama_client.chat.assert_called_once()  # config question -> LLM answers

    def test_openai_connection_error(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test OpenAI endpoint handles connection error."""
        from aitao.llm.protocols import OllamaConnectionError
        mock_ollama_client.chat.side_effect = OllamaConnectionError("Ollama down")
        mock_rag_engine.enrich_messages.return_value = (
            [{"role": "user", "content": "Hello"}],
            [],
            "",
        )
        
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        
        assert response.status_code == 503


# ============================================================================
# Request Validation Tests
# ============================================================================

class TestRequestValidation:
    """Tests for request validation."""
    
    def test_missing_model(self, chat_app):
        """Test request without model field."""
        response = chat_app.post("/api/chat", json={
            "messages": [{"role": "user", "content": "Hello"}],
        })
        
        assert response.status_code == 422  # Validation error
    
    def test_missing_messages(self, chat_app):
        """Test request without messages field."""
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
        })
        
        assert response.status_code == 422
    
    def test_empty_messages(self, chat_app, mock_ollama_client, mock_rag_engine):
        """Test request with empty messages array."""
        mock_rag_engine.enrich_messages.return_value = ([], [], "")
        mock_ollama_client.chat.return_value = {
            "message": {"role": "assistant", "content": ""},
            "done": True,
        }
        
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [],
            "stream": False,
        })
        
        # Empty messages should still work (model handles it)
        assert response.status_code == 200
    
    def test_invalid_message_format(self, chat_app):
        """Test request with invalid message format."""
        response = chat_app.post("/api/chat", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"invalid": "format"}],
        })
        
        assert response.status_code == 422
    
    def test_openai_invalid_temperature(self, chat_app):
        """Test OpenAI request with invalid temperature."""
        response = chat_app.post("/v1/chat/completions", json={
            "model": "qwen2.5-coder:7b",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 3.0,  # Invalid: max is 2
        })
        
        assert response.status_code == 422


# ============================================================================
# Helper Function Tests
# ============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""
    
    def test_context_docs_to_dict(self, mock_context_docs):
        """Test conversion of ContextDocument to dict."""
        from aitao.api.routes.chat import context_docs_to_dict
        
        result = context_docs_to_dict(mock_context_docs)
        
        assert len(result) == 2
        assert result[0]["id"] == "doc1"
        assert result[0]["path"] == "/test/file1.md"
        assert result[0]["score"] == 0.95
        assert result[0]["category"] == "documentation"
        assert "content" not in result[0]  # Content should not be included
    
    def test_context_docs_to_dict_empty(self):
        """Test conversion with empty list."""
        from aitao.api.routes.chat import context_docs_to_dict
        
        result = context_docs_to_dict([])
        assert result == []
