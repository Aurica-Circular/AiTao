# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
OllamaClient: Wrapper for Ollama LLM server API.

Provides a client for Ollama (local LLM server) supporting model listing,
chat completion, text generation, embeddings, and streaming.
"""

import json
from typing import Optional, Dict, Any, List, Iterator
import httpx

from aitao.core.config import ConfigManager
from aitao.core.logger import StructuredLogger, get_logger
from aitao.core.plugin_registry import register

# Wire types & errors live in llm.protocols (the backend contract — US-25b).
from aitao.llm.protocols import (
    OllamaChatMessage,  # noqa: F401  — part of this backend's call signatures
    OllamaConnectionError,
    OllamaHealth,
    OllamaModel,
    OllamaModelNotFound,
)
# US-116: no hardcoded default model is guaranteed installed on a given
# machine — when the configured/requested model is missing, fall back to the
# smallest installed model that is safe for RAG chat (never a reasoning model
# that silently "thinks" for minutes instead of answering).
from aitao.llm.model_advisor import pick_safe_default

__all__ = ["OllamaClient"]


@register("llm", "ollama")
class OllamaClient:
    """Client for interacting with Ollama LLM server."""

    def __init__(self, config: ConfigManager, logger: Optional[StructuredLogger] = None):
        """Initialize OllamaClient with config and an optional logger.

        If logger is None, a default structured logger is created automatically.
        This makes OllamaClient safe to instantiate anywhere without a pre-built logger.
        """
        self.config = config
        self.logger = logger or get_logger("llm.ollama_client")
        
        llm = config.llm
        self.host = llm.ollama_url
        self.default_model = llm.default_model

        # HTTP client with a configurable timeout. Local LLMs need more than a
        # tight 60s: a cold model load (first request) plus generation on a
        # busy machine can easily exceed it. Override via [llm] request_timeout.
        timeout_s = llm.request_timeout
        self.request_timeout = timeout_s
        # US-87 p2: max silence (no new token) during streaming before cutting.
        self.stream_idle_timeout = llm.stream_idle_timeout
        self.client = httpx.Client(timeout=timeout_s, follow_redirects=True)
        self.async_client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
        
        self.logger.info(
            "OllamaClient initialized",
            metadata={"host": self.host, "default_model": self.default_model}
        )

    def health(self, inference_timeout: float = 15.0) -> "OllamaHealth":
        """Probe Ollama: API reachability + a minimal real inference.

        A TCP/port ping is not enough — Ollama can answer /api/version while its
        inference runner is broken (e.g. a missing llama-server binary after a
        partial upgrade) or stuck. We therefore attempt a 1-token generation and
        surface the underlying error plus a remediation hint.
        """
        # 1. API reachable? (also reports the running version)
        try:
            resp = self.client.get(f"{self.host}/api/version", timeout=3.0)
            if resp.status_code != 200:
                return OllamaHealth(
                    reachable=False,
                    error=f"HTTP {resp.status_code} from /api/version",
                    hint="Start it: brew services start ollama",
                )
            version = resp.json().get("version")
        except Exception:
            return OllamaHealth(
                reachable=False,
                error="API unreachable",
                hint="Start it: brew services start ollama",
            )

        # 2. Inference probe — catches a broken/stuck runner a port ping misses
        try:
            resp = self.client.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.default_model,
                    "prompt": "ok",
                    "stream": False,
                    "options": {"num_predict": 1},
                },
                timeout=inference_timeout,
            )
            data = resp.json()
            if data.get("error"):
                return OllamaHealth(
                    reachable=True, version=version, inference_ok=False,
                    error=str(data["error"])[:200],
                    hint="Broken/partial install — try: "
                         "brew upgrade ollama && brew services restart ollama",
                )
            return OllamaHealth(reachable=True, version=version, inference_ok=True)
        except httpx.TimeoutException:
            return OllamaHealth(
                reachable=True, version=version, inference_ok=False,
                error=f"inference did not respond within {int(inference_timeout)}s "
                      "(model loading or stuck)",
                hint="If it persists: brew services restart ollama",
            )
        except Exception as e:
            return OllamaHealth(
                reachable=True, version=version, inference_ok=False,
                error=str(e)[:200],
                hint="Try: brew services restart ollama",
            )

    def _check_connection(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            response = self.client.get(f"{self.host}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            self.logger.error(
                "Ollama connection failed",
                metadata={"error": str(e), "host": self.host}
            )
            return False
    
    def list_models(self) -> List[OllamaModel]:
        """List all available models on Ollama server."""
        try:
            response = self.client.get(f"{self.host}/api/tags")
            
            if response.status_code != 200:
                raise OllamaConnectionError(
                    f"Ollama returned status {response.status_code}"
                )
            
            data = response.json()
            models = []
            
            for model_data in data.get("models", []):
                model = OllamaModel(
                    name=model_data["name"],
                    size=model_data.get("size", 0),
                    digest=model_data.get("digest", ""),
                    modified_at=model_data.get("modified_at", "")
                )
                models.append(model)
            
            self.logger.info(
                f"Listed {len(models)} models from Ollama",
                metadata={"models": [m.name for m in models]}
            )
            return models
            
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.host}") from e
        except Exception as e:
            self.logger.error(
                "Error listing models",
                metadata={"error": str(e)}
            )
            raise

    def _resolve_model(self, requested: Optional[str]) -> str:
        """Resolve the model name to actually use for a request (US-116).

        Order: ``requested`` (if given and installed) -> ``self.default_model``
        (if installed) -> the smallest installed model that is safe for RAG
        chat (``model_advisor.pick_safe_default`` — never a reasoning model
        that silently "thinks" for minutes instead of answering, see US-097).

        This used to be a hard failure (``OllamaModelNotFound``) whenever the
        configured default was not installed — exactly the incident of
        2026-07-20, where a config rebuilt from the template pointed at a
        model nobody had pulled and broke chat outright. Now it degrades to a
        logged warning and an automatic, safe substitution. The only case
        that still raises is genuinely unrecoverable: no model installed at
        all, so there is nothing to fall back to.
        """
        candidate = requested or self.default_model
        available_models = self.list_models()
        if not available_models:
            raise OllamaModelNotFound(
                f"No models installed on Ollama at {self.host}. "
                "Install one first: ollama pull <model-name>"
            )
        available_names = [m.name for m in available_models]
        if candidate and candidate in available_names:
            return candidate

        fallback = pick_safe_default(available_models)
        if candidate:
            self.logger.warning(
                f"Configured/requested model '{candidate}' is not installed — "
                f"falling back to auto-selected safe default '{fallback}'",
                metadata={
                    "requested_model": candidate,
                    "fallback_model": fallback,
                    "available_models": available_names,
                },
            )
        else:
            self.logger.warning(
                f"No default model configured — auto-selecting the smallest "
                f"installed safe chat model: '{fallback}'",
                metadata={
                    "fallback_model": fallback,
                    "available_models": available_names,
                },
            )
        return fallback

    def chat(
        self,
        messages: List[OllamaChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.7,
        top_p: float = 0.9,
        **kwargs
    ) -> Dict[str, Any] | Iterator[str]:
        """Chat completion endpoint with optional streaming."""
        # US-116: resolve to an installed, safe model instead of hard-failing
        # when the configured/requested model is missing.
        model = self._resolve_model(model)

        # Format messages for Ollama API
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        payload = {
            "model": model,
            "messages": formatted_messages,
            "stream": stream,
            "temperature": temperature,
            "top_p": top_p,
            **kwargs
        }
        
        try:
            if stream:
                return self._chat_stream(payload)
            else:
                return self._chat_sync(payload)
        except Exception as e:
            self.logger.error(
                "Chat error",
                metadata={"error": str(e), "model": model}
            )
            raise
    
    def _chat_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute synchronous chat request."""
        response = self.client.post(
            f"{self.host}/api/chat",
            json=payload
        )
        
        if response.status_code != 200:
            raise OllamaConnectionError(
                f"Chat request failed with status {response.status_code}"
            )
        
        return response.json()
    
    def _chat_stream(self, payload: Dict[str, Any]) -> Iterator[str]:
        """Execute streaming chat request (returns JSON lines as strings)."""
        with self.client.stream(
            "POST",
            f"{self.host}/api/chat",
            json=payload,
            # US-87 p2: short READ timeout = max silence between tokens (resets on
            # each token). connect/write/pool keep the global request_timeout.
            timeout=httpx.Timeout(self.request_timeout, read=self.stream_idle_timeout),
        ) as response:
            if response.status_code != 200:
                raise OllamaConnectionError(
                    f"Chat stream failed with status {response.status_code}"
                )
            
            for line in response.iter_lines():
                if line.strip():
                    # Yield raw JSON line for caller to parse
                    yield line
    
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs
    ) -> Dict[str, Any] | Iterator[str]:
        """Text generation endpoint (non-chat mode) with optional streaming."""
        # US-116 scope decision: unlike chat() (the path actually exercised by
        # AiTao's RAG engine), generate() is not on a hot/critical path today
        # (no in-repo caller). Wiring _resolve_model() here would add an
        # unconditional list_models() round-trip even when the caller already
        # names a model, for no real safety gain — left as a plain default
        # substitution, matching the low-priority note in US-116.
        if not model:
            model = self.default_model

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            **kwargs
        }
        
        try:
            if stream:
                return self._generate_stream(payload)
            else:
                return self._generate_sync(payload)
        except Exception as e:
            self.logger.error(
                "Generate error",
                metadata={"error": str(e), "model": model}
            )
            raise
    
    def _generate_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute synchronous generate request."""
        try:
            response = self.client.post(
                f"{self.host}/api/generate",
                json=payload
            )
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Failed to connect to Ollama: {e}") from e
        
        if response.status_code != 200:
            raise OllamaConnectionError(
                f"Generate request failed with status {response.status_code}"
            )
        
        return response.json()
    
    def _generate_stream(self, payload: Dict[str, Any]) -> Iterator[str]:
        """Execute streaming generate request."""
        with self.client.stream(
            "POST",
            f"{self.host}/api/generate",
            json=payload
        ) as response:
            if response.status_code != 200:
                raise OllamaConnectionError(
                    f"Generate stream failed with status {response.status_code}"
                )
            
            for line in response.iter_text():
                if line.strip():
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            yield data["response"]
                    except json.JSONDecodeError:
                        self.logger.debug(f"Skipping malformed JSON: {line}")
    
    def embeddings(
        self,
        text: str,
        model: Optional[str] = None,
    ) -> List[float]:
        """Generate embeddings for the given text."""
        # US-116 scope decision: same reasoning as generate() above — the real
        # embeddings API route (api/routes/embeddings.py) always passes an
        # explicit model, so this branch is not on a path worth an extra
        # list_models() round-trip. Left as a plain default substitution.
        if not model:
            model = self.default_model

        payload = {
            "model": model,
            "prompt": text
        }
        
        try:
            response = self.client.post(
                f"{self.host}/api/embeddings",
                json=payload
            )
            
            if response.status_code != 200:
                raise OllamaConnectionError(
                    f"Embeddings request failed with status {response.status_code}"
                )
            
            data = response.json()
            return data.get("embedding", [])
            
        except Exception as e:
            self.logger.error(
                "Embeddings error",
                metadata={"error": str(e), "model": model}
            )
            raise
    
    def get_model_info(self, model: str) -> Dict[str, Any]:
        """Get detailed info about a specific model."""
        payload = {"name": model}
        
        try:
            response = self.client.post(
                f"{self.host}/api/show",
                json=payload
            )
            
            if response.status_code != 200:
                raise OllamaModelNotFound(f"Model '{model}' not found")
            
            return response.json()
            
        except Exception as e:
            self.logger.error(
                "Model info error",
                metadata={"error": str(e), "model": model}
            )
            raise
    
    def delete_model(self, model: str) -> bool:
        """Delete a model from Ollama. Returns True if successful."""
        if not self._check_connection():
            raise OllamaConnectionError(
                f"Cannot delete model: Ollama not reachable at {self.host}"
            )
        
        try:
            response = self.client.delete(
                f"{self.host}/api/delete",
                json={"name": model}
            )
            response.raise_for_status()
            self.logger.info(
                "Model deleted from Ollama",
                metadata={"model": model}
            )
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.warning(
                    "Model not found in Ollama",
                    metadata={"model": model}
                )
                return False
            raise
        except Exception as e:
            self.logger.error(
                "Model deletion error",
                metadata={"error": str(e), "model": model}
            )
            raise

    def is_healthy(self) -> bool:
        """Check if Ollama server is healthy."""
        return self._check_connection()
    
    def close(self):
        """Close HTTP client connections."""
        self.client.close()
    
    async def aclose(self):
        """Close async HTTP client connections."""
        await self.async_client.aclose()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()
