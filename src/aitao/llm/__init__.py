# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
LLM module: Ollama client and RAG integration.

Provides:
- OllamaClient: Interface to Ollama local LLM server
- RAGEngine: Retrieval-Augmented Generation engine
- Protocols: Standard interfaces for backends

Note (v3): ModelManager, BackendRouter, MLXBackend, IntentRouter and
Summarizer have been removed. Model management is delegated to Ollama.
"""

from .rag_engine import (
    RAGEngine,
    RAGResult,
    ContextDocument,
)

from .protocols import (
    LLMBackendProtocol,
    EmbeddingBackendProtocol,
    ChatMessage,
    GenerationResult,
    OllamaModel,
    OllamaChatMessage,
    OllamaHealth,
    OllamaConnectionError,
    OllamaModelNotFound,
)

__all__ = [
    # RAG Engine
    "RAGEngine",
    "RAGResult",
    "ContextDocument",
    # Protocols + client wire types (backend implementations are plugins,
    # see src/plugins/llm/ — selected via llm.provider.make_llm_client)
    "LLMBackendProtocol",
    "EmbeddingBackendProtocol",
    "ChatMessage",
    "GenerationResult",
    "OllamaModel",
    "OllamaChatMessage",
    "OllamaHealth",
    "OllamaConnectionError",
    "OllamaModelNotFound",
]
