# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Embeddings API route for AiTao.

Provides an OpenAI-compatible embeddings endpoint that proxies requests
to the Ollama backend. This allows clients like Askimo to generate
embeddings for their own RAG indexing pipeline.

Endpoints:
- POST /v1/embeddings  - OpenAI-compatible embeddings generation

The endpoint accepts single strings or arrays of strings and returns
embedding vectors in the standard OpenAI response format.
"""

from typing import Any, List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from aitao.core.logger import get_logger
from aitao.llm.protocols import OllamaConnectionError

logger = get_logger("api.embeddings")

# Router
openai_router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])


# ============================================================================
# Shared Components
# ============================================================================

# The configured LLM backend (OllamaClient or OpenAICompatClient — drop-ins)
_ollama_client: Optional[Any] = None


def get_ollama_client():
    """Get or create the configured LLM client (Ollama or OpenAI-compatible)."""
    global _ollama_client
    if _ollama_client is None:
        from aitao.core.config import get_config
        from aitao.llm.provider import make_llm_client
        _ollama_client = make_llm_client(get_config(), logger)
    return _ollama_client


# ============================================================================
# Request / Response Schemas
# ============================================================================

class EmbeddingRequest(BaseModel):
    """OpenAI-compatible embedding request."""
    model_config = ConfigDict(extra="ignore")

    input: Union[str, List[str]] = Field(
        ..., description="Input text(s) to embed"
    )
    model: str = Field(
        ..., description="Embedding model name"
    )
    encoding_format: Optional[str] = Field(
        default="float", description="Encoding format (float or base64)"
    )


class EmbeddingData(BaseModel):
    """Single embedding result."""
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingUsage(BaseModel):
    """Token usage for embedding request."""
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResponse(BaseModel):
    """OpenAI-compatible embedding response."""
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: EmbeddingUsage


# ============================================================================
# OpenAI-Compatible Endpoint
# ============================================================================

@openai_router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    """
    Generate embeddings (OpenAI-compatible format).

    Accepts a single string or a list of strings and returns
    embedding vectors via the Ollama backend.
    """
    # Normalize input to list
    texts = request.input if isinstance(request.input, list) else [request.input]

    logger.info(
        "Embedding request",
        metadata={"model": request.model, "count": len(texts)},
    )

    try:
        ollama = get_ollama_client()
        data: List[EmbeddingData] = []

        for idx, text in enumerate(texts):
            vector = ollama.embeddings(text=text, model=request.model)
            data.append(EmbeddingData(
                embedding=vector,
                index=idx,
            ))

        total_tokens = sum(len(t.split()) for t in texts)

        logger.info(
            "Embedding response",
            metadata={"model": request.model, "vectors": len(data)},
        )

        return EmbeddingResponse(
            data=data,
            model=request.model,
            usage=EmbeddingUsage(
                prompt_tokens=total_tokens,
                total_tokens=total_tokens,
            ),
        )

    except OllamaConnectionError as e:
        logger.error(f"Cannot connect to Ollama: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"LLM backend unavailable: {e}",
        )
    except Exception as e:
        logger.error(
            "Embeddings error",
            metadata={"error": str(e), "model": request.model},
        )
        raise HTTPException(status_code=500, detail=str(e))
