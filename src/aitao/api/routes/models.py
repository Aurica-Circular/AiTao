# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Models API route for AiTao.

Pure proxy to Ollama — returns real models only.
No virtual model naming convention (removed in v3).

Endpoints:
- GET /api/tags      — Ollama-compatible model list
- GET /v1/models     — OpenAI-compatible model list
- GET /v1/models/:id — single model info
"""

import time
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aitao.core.config import get_config
from aitao.core.logger import get_logger
from aitao.llm.protocols import OllamaConnectionError
from aitao.llm.provider import make_llm_client

logger = get_logger("api.models")

# Routers
router = APIRouter(prefix="/api", tags=["Models"])
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
        _ollama_client = make_llm_client(get_config(), logger)
    return _ollama_client


# ============================================================================
# Response Schemas
# ============================================================================

class OllamaModelDetails(BaseModel):
    """Ollama model details."""
    format: Optional[str] = None
    family: Optional[str] = None
    families: Optional[List[str]] = None
    parameter_size: Optional[str] = None
    quantization_level: Optional[str] = None


class OllamaModel(BaseModel):
    """Single model in Ollama format."""
    name: str = Field(..., description="Model name (e.g., 'qwen2.5-coder:7b')")
    modified_at: str = Field(..., description="Last modified timestamp")
    size: int = Field(..., description="Model size in bytes")
    digest: str = Field(..., description="Model digest/hash")
    details: Optional[OllamaModelDetails] = None


class OllamaModelsResponse(BaseModel):
    """Ollama-compatible models list response."""
    models: List[OllamaModel]


class OpenAIModel(BaseModel):
    """Single model in OpenAI format."""
    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model")
    created: int = Field(..., description="Creation timestamp")
    owned_by: str = Field(default="aitao")


class OpenAIModelsResponse(BaseModel):
    """OpenAI-compatible models list response."""
    object: str = Field(default="list")
    data: List[OpenAIModel]


# ============================================================================
# Ollama-Compatible Endpoints
# ============================================================================

@router.get("/tags", response_model=OllamaModelsResponse)
async def list_models_ollama():
    """
    List available models (Ollama-compatible format).
    
    Returns all models available in the Ollama backend.
    This endpoint is compatible with Ollama API clients.
    """
    logger.info("Listing models (Ollama format)")
    
    try:
        ollama = get_ollama_client()
        ollama_models = ollama.list_models()
        
        # list_models() returns List[OllamaModel] — convert to response schema
        models = []
        for model_obj in ollama_models:
            models.append(OllamaModel(
                name=model_obj.name,
                modified_at=model_obj.modified_at or "",
                size=model_obj.size or 0,
                digest=model_obj.digest or "",
                details=None,
            ))
        
        logger.info(f"Found {len(models)} models")
        return OllamaModelsResponse(models=models)
        
    except OllamaConnectionError as e:
        logger.error(f"Cannot connect to Ollama: {e}")
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list models: {e}")


@router.get("/show/{model_name}")
async def show_model_info(model_name: str):
    """
    Get detailed information about a specific model.
    
    Ollama-compatible endpoint for model details.
    """
    logger.info(f"Getting info for model: {model_name}")
    
    try:
        ollama = get_ollama_client()
        model_info = ollama.show_model(model_name)
        return model_info
        
    except OllamaConnectionError as e:
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except Exception as e:
        logger.error(f"Error getting model info: {e}")
        raise HTTPException(status_code=404, detail=f"Model not found: {model_name}")


# ============================================================================
# OpenAI-Compatible Endpoints
# ============================================================================

@openai_router.get("/models", response_model=OpenAIModelsResponse)
async def list_models_openai():
    """
    List available models (OpenAI-compatible format).

    Returns real Ollama models only — no virtual model naming.
    Use `"aitao": {"rag": true}` in the chat request to enable RAG.
    """
    logger.info("Listing models (OpenAI format)")

    try:
        ollama = get_ollama_client()
        ollama_models = ollama.list_models()
        models = []

        for model_obj in ollama_models:
            created = int(time.time())
            if model_obj.modified_at:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(
                        model_obj.modified_at.replace("Z", "+00:00")
                    )
                    created = int(dt.timestamp())
                except (ValueError, TypeError):
                    pass
            models.append(OpenAIModel(
                id=model_obj.name,
                object="model",
                created=created,
                owned_by="ollama",
            ))

        logger.info(f"Returning {len(models)} models")
        return OpenAIModelsResponse(object="list", data=models)

    except OllamaConnectionError as e:
        logger.error(f"Cannot connect to Ollama: {e}")
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@openai_router.get("/models/{model_id}")
async def get_model_openai(model_id: str):
    """Get a specific model (OpenAI-compatible format). Proxies to Ollama."""
    logger.info(f"Getting model: {model_id}")

    try:
        ollama = get_ollama_client()
        ollama.show_model(model_id)  # raises if not found
        return OpenAIModel(
            id=model_id,
            object="model",
            created=int(time.time()),
            owned_by="ollama",
        )
    except OllamaConnectionError as e:
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {e}")
    except Exception:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
