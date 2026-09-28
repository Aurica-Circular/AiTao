# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
LLM Backend Protocol Definitions.

This module defines the protocol (interface) that all LLM backends must implement.
This enables interchangeable backends (Ollama, MLX) with a unified API.

Protocols:
- LLMBackendProtocol: Core generation/chat interface
- EmbeddingBackendProtocol: Optional embedding support

Usage:
    class MyBackend(LLMBackendProtocol):
        def generate(self, prompt, model, **kwargs) -> str:
            ...

Architecture:
    BackendRouter uses these protocols to route requests to the appropriate
    backend (MLX on Apple Silicon, Ollama elsewhere).
"""

from abc import abstractmethod
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict

# ChatMessage is the canonical chat turn, defined once in core.models (US-23).
# Re-exported so `from llm.protocols import ChatMessage` keeps working.
from aitao.core.models import ChatMessage  # noqa: F401


# ============================================================================
# Data Classes
# ============================================================================

# ChatMessage lives in core.models (imported above) — the canonical chat turn.


class GenerationResult(BaseModel):
    """Result from a generation request."""

    model_config = ConfigDict(extra="ignore")

    text: str
    model: str
    backend: str  # "mlx" or "ollama"
    tokens_generated: int = 0
    generation_time_ms: float = 0.0
    tokens_per_second: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "text_length": len(self.text),
            "model": self.model,
            "backend": self.backend,
            "tokens_generated": self.tokens_generated,
            "generation_time_ms": round(self.generation_time_ms, 2),
            "tokens_per_second": round(self.tokens_per_second, 2),
        }


# ============================================================================
# Client wire types & errors (shared by all backends — US-25b)
#
# Ollama-shaped on purpose: every backend (Ollama native, OpenAI-compatible)
# accepts/returns these so switching provider is a config change, not a code
# change. Backends live in src/plugins/llm/; this module is their contract.
# ============================================================================

@dataclass
class OllamaModel:
    """Represents an available Ollama model."""
    name: str
    size: int          # bytes
    digest: str
    modified_at: str


@dataclass
class OllamaChatMessage:
    """Chat message format for Ollama API."""
    role: str          # "user", "assistant", "system"
    content: str


@dataclass
class OllamaHealth:
    """Result of an LLM-backend health probe (API reachability + real inference)."""
    reachable: bool                  # /api/version answered
    version: Optional[str] = None
    inference_ok: bool = False       # a minimal generation succeeded
    error: Optional[str] = None      # error detail when not healthy
    hint: Optional[str] = None       # suggested remediation

    @property
    def ok(self) -> bool:
        return self.reachable and self.inference_ok


class OllamaConnectionError(Exception):
    """Raised when the LLM backend server is unreachable."""


class OllamaModelNotFound(Exception):
    """Raised when the requested model is not available on the backend."""


# ============================================================================
# Backend Protocol
# ============================================================================

@runtime_checkable
class LLMBackendProtocol(Protocol):
    """
    Protocol defining the interface for LLM backends.
    
    All backends (Ollama, MLX, etc.) must implement this interface
    to be compatible with the BackendRouter.
    """
    
    @property
    def backend_name(self) -> str:
        """Return the backend identifier (e.g., 'mlx', 'ollama')."""
        ...
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this backend is available on the current platform.
        
        Returns:
            True if backend can be used, False otherwise.
        """
        ...
    
    @abstractmethod
    def list_models(self) -> List[str]:
        """
        List available models for this backend.
        
        Returns:
            List of model identifiers.
        """
        ...
    
    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> GenerationResult:
        """
        Generate text from a prompt.
        
        Args:
            prompt: Input prompt for generation.
            model: Model to use (uses default if None).
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = deterministic).
            **kwargs: Additional backend-specific parameters.
            
        Returns:
            GenerationResult with generated text and metadata.
        """
        ...
    
    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> GenerationResult:
        """
        Generate chat completion from messages.
        
        Args:
            messages: List of ChatMessage (conversation history).
            model: Model to use (uses default if None).
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            **kwargs: Additional backend-specific parameters.
            
        Returns:
            GenerationResult with assistant's response.
        """
        ...
    
    def generate_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> Iterator[str]:
        """
        Stream generation token by token.
        
        Default implementation yields full result at once.
        Backends can override for true streaming.
        
        Args:
            prompt: Input prompt for generation.
            model: Model to use.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            **kwargs: Additional parameters.
            
        Yields:
            Generated text chunks.
        """
        result = self.generate(prompt, model, max_tokens, temperature, **kwargs)
        yield result.text


@runtime_checkable
class EmbeddingBackendProtocol(Protocol):
    """
    Optional protocol for backends that support embeddings.
    
    Not all backends support embeddings (e.g., some MLX models).
    """
    
    @abstractmethod
    def embed(
        self,
        text: str,
        model: Optional[str] = None,
    ) -> List[float]:
        """
        Generate embedding vector for text.
        
        Args:
            text: Text to embed.
            model: Embedding model to use.
            
        Returns:
            List of floats representing the embedding vector.
        """
        ...
    
    @abstractmethod
    def embed_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed.
            model: Embedding model to use.
            
        Returns:
            List of embedding vectors.
        """
        ...
