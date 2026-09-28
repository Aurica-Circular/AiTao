# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
OpenAICompatClient: drop-in LLM client speaking the OpenAI-compatible API.

Talks to any OpenAI-compatible local server — llama.cpp's `llama-server`,
LM Studio, vLLM, or Ollama's own `/v1` endpoint — and exposes the SAME public
interface as `OllamaClient` (identical method names and Ollama-shaped return
values), so the API routes can use either backend interchangeably without any
change. Backend selection is config-driven (`[llm] backend`); see
`llm.provider.make_llm_client`.

Why a separate client: AiTao must not depend on a single provider. When one
engine is down, switching to another must be a one-line config change.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx

from aitao.core.config import ConfigManager
from aitao.core.logger import StructuredLogger, get_logger
from aitao.core.plugin_registry import register
from aitao.llm.protocols import (
    OllamaModel,
    OllamaHealth,
    OllamaConnectionError,
    OllamaModelNotFound,
)
# US-116: best-effort auto-selection when the configured default_model is
# absent/not served — see _resolve_model() below for how this degrades on a
# backend whose /v1/models carries no usable size field.
from aitao.llm.model_advisor import pick_safe_default

__all__ = ["OpenAICompatClient"]


def _now_iso() -> str:
    """UTC timestamp in ISO format (matches Ollama's `created_at`)."""
    return datetime.now(timezone.utc).isoformat()


def _msg_to_dict(m: Any) -> Dict[str, str]:
    """Normalize a message (OllamaChatMessage object or dict) to a plain dict."""
    if isinstance(m, dict):
        return {"role": m.get("role", ""), "content": m.get("content", "")}
    return {"role": m.role, "content": m.content}


@register("llm", "openai")
class OpenAICompatClient:
    """OpenAI-compatible LLM client, drop-in compatible with OllamaClient."""

    backend_name = "openai_compat"

    def __init__(self, config: ConfigManager, logger: Optional[StructuredLogger] = None):
        self.config = config
        self.logger = logger or get_logger("llm.openai_compat_client")

        llm = config.llm
        oai = llm.openai

        self.base_url = oai.base_url.rstrip("/")
        self.default_model = oai.model or llm.default_model
        self.api_key = oai.api_key

        timeout_s = llm.request_timeout
        self.request_timeout = timeout_s
        # US-87 p2: max silence (no new token) during streaming before cutting.
        self.stream_idle_timeout = llm.stream_idle_timeout

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self.client = httpx.Client(timeout=timeout_s, follow_redirects=True, headers=headers)
        self.async_client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers)

        self.logger.info(
            "OpenAICompatClient initialized",
            metadata={"base_url": self.base_url, "default_model": self.default_model},
        )

    # ------------------------------------------------------------------
    # Connectivity / health
    # ------------------------------------------------------------------
    def _check_connection(self) -> bool:
        """True if the server answers GET /models."""
        try:
            r = self.client.get(f"{self.base_url}/models", timeout=5.0)
            return r.status_code == 200
        except Exception as e:
            self.logger.error(
                "OpenAI-compatible server unreachable",
                metadata={"error": str(e), "base_url": self.base_url},
            )
            return False

    def is_healthy(self) -> bool:
        return self._check_connection()

    def health(self, inference_timeout: float = 15.0) -> OllamaHealth:
        """Probe reachability + a minimal real generation (mirrors OllamaClient)."""
        try:
            r = self.client.get(f"{self.base_url}/models", timeout=3.0)
            if r.status_code != 200:
                return OllamaHealth(
                    reachable=False,
                    error=f"HTTP {r.status_code} from /models",
                    hint=f"Is your OpenAI-compatible server up at {self.base_url}?",
                )
        except Exception:
            return OllamaHealth(
                reachable=False,
                error="server unreachable",
                hint=f"Start an OpenAI-compatible server (e.g. llama-server) at {self.base_url}",
            )
        try:
            raw = self._chat_raw(
                [{"role": "user", "content": "ok"}],
                model=self.default_model, stream=False,
                max_tokens=1, temperature=0.0, top_p=1.0,
                timeout=inference_timeout,
            )
            _ = raw["choices"][0]["message"]["content"]
            return OllamaHealth(reachable=True, inference_ok=True)
        except Exception as e:
            return OllamaHealth(
                reachable=True, inference_ok=False, error=str(e)[:200],
                hint="Server reachable but generation failed — check the loaded model name",
            )

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------
    def list_models(self) -> List[OllamaModel]:
        """List models via GET /models, mapped to OllamaModel for the routes."""
        try:
            r = self.client.get(f"{self.base_url}/models")
        except httpx.ConnectError as e:
            raise OllamaConnectionError(
                f"Cannot connect to LLM server at {self.base_url}"
            ) from e
        if r.status_code != 200:
            raise OllamaConnectionError(f"LLM server returned status {r.status_code}")

        models: List[OllamaModel] = []
        for m in r.json().get("data", []):
            created = m.get("created")
            modified = ""
            if isinstance(created, int):
                modified = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            models.append(OllamaModel(
                name=m.get("id", ""), size=0, digest="", modified_at=modified,
            ))
        self.logger.info(f"Listed {len(models)} models from {self.base_url}")
        return models

    def show_model(self, model: str) -> Dict[str, Any]:
        """OpenAI-compatible servers expose no rich detail — confirm existence."""
        names = [m.name for m in self.list_models()]
        if names and model not in names:
            raise OllamaModelNotFound(f"Model '{model}' not found. Available: {names}")
        return {"model": model, "details": {}}

    # Alias kept for parity with OllamaClient.get_model_info
    get_model_info = show_model

    def _resolve_model(self, requested: Optional[str]) -> str:
        """Resolve the model name for a request (US-116), best-effort.

        This backend (llama.cpp / LM Studio / vLLM / Ollama's own /v1) is
        secondary in AiTao — Ollama is the default — so this stays simple:
          - Exactly one model served -> use it, unambiguously, regardless of
            what `default_model` says (a stale/misconfigured default must
            never block a server that only offers one choice anyway).
          - Several models served and the requested/configured one matches
            -> use it as-is.
          - Several models served, no match -> `pick_safe_default`. Note:
            OpenAI's `/v1/models` format carries no size field, so
            `list_models()` here always reports `size=0` for every entry;
            `pick_safe_default`'s "smallest" tie-break then degrades
            gracefully to "first non-reasoning/non-chat model in the
            server's own listing order" — still a real filter (never a known
            reasoning model), just not size-ranked.
        If the server can't be listed at all, return the candidate unchanged
        and let the actual request surface the real connection error.
        """
        candidate = requested or self.default_model
        try:
            available = self.list_models()
        except Exception:
            return candidate or ""
        if not available:
            return candidate or ""

        names = [m.name for m in available]
        if len(available) == 1:
            only = available[0].name
            if candidate and candidate != only:
                self.logger.warning(
                    f"Configured model '{candidate}' does not match the single "
                    f"model served at {self.base_url} — using '{only}' instead",
                    metadata={"configured_model": candidate, "served_model": only},
                )
            return only

        if candidate and candidate in names:
            return candidate

        fallback = pick_safe_default(available)
        self.logger.warning(
            f"Configured/requested model '{candidate}' not found among "
            f"{names} — falling back to auto-selected '{fallback}'",
            metadata={
                "requested_model": candidate,
                "fallback_model": fallback,
                "available_models": names,
            },
        )
        return fallback

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Any],
        model: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.7,
        top_p: float = 0.9,
        options: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        """Chat completion. Returns an Ollama-shaped dict (or NDJSON-line iterator)."""
        model = self._resolve_model(model)
        payload_messages = [_msg_to_dict(m) for m in messages]
        max_tokens, temp, tp = self._translate_options(options, temperature, top_p)

        if stream:
            return self._chat_stream(payload_messages, model, max_tokens, temp, tp)

        raw = self._chat_raw(
            payload_messages, model, stream=False,
            max_tokens=max_tokens, temperature=temp, top_p=tp,
        )
        try:
            content = raw["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        result = {
            "model": model,
            "created_at": _now_iso(),
            "message": {"role": "assistant", "content": content},
            "done": True,
        }
        # US-STATS-01: surface token counts/timings under Ollama field names so
        # the perf log works identically whichever backend is configured.
        result.update(self._perf_from_openai(raw))
        return result

    @staticmethod
    def _perf_from_openai(raw: Any) -> Dict[str, Any]:
        """Map OpenAI `usage` (and llama.cpp `timings`) to Ollama perf fields."""
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Any] = {}
        usage = raw.get("usage") or {}
        if usage.get("completion_tokens") is not None:
            out["eval_count"] = usage["completion_tokens"]
        if usage.get("prompt_tokens") is not None:
            out["prompt_eval_count"] = usage["prompt_tokens"]
        timings = raw.get("timings") or {}  # llama.cpp extension (ms floats)
        if timings.get("predicted_ms"):
            out["eval_duration"] = int(timings["predicted_ms"] * 1e6)
        if timings.get("prompt_ms"):
            out["prompt_eval_duration"] = int(timings["prompt_ms"] * 1e6)
        return out

    def _chat_raw(
        self, messages, model, stream, max_tokens=None,
        temperature=0.7, top_p=0.9, timeout=None,
    ) -> Dict[str, Any]:
        """POST /chat/completions (non-streaming), returning the raw OpenAI JSON."""
        payload: Dict[str, Any] = {
            "model": model, "messages": messages, "stream": stream,
            "temperature": temperature, "top_p": top_p,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        try:
            r = self.client.post(
                f"{self.base_url}/chat/completions", json=payload,
                timeout=timeout or self.request_timeout,
            )
        except httpx.ConnectError as e:
            raise OllamaConnectionError(
                f"Cannot connect to LLM server at {self.base_url}"
            ) from e
        if r.status_code == 404:
            raise OllamaModelNotFound(f"Model '{model}' not found on server")
        if r.status_code != 200:
            raise OllamaConnectionError(
                f"Chat request failed with status {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    def _chat_stream(self, messages, model, max_tokens, temperature, top_p) -> Iterator[str]:
        """POST /chat/completions (SSE), translated to Ollama NDJSON line strings.

        Requests `stream_options.include_usage` (supported by llama.cpp,
        LM Studio, vLLM, Ollama /v1) so the final usage chunk — token counts,
        llama.cpp timings — rides the terminating NDJSON line under Ollama
        field names, same contract as OllamaClient (US-STATS-01). The usage
        chunk arrives AFTER the finish_reason chunk, so the done line is held
        until the stream ends.
        """
        payload: Dict[str, Any] = {
            "model": model, "messages": messages, "stream": True,
            "temperature": temperature, "top_p": top_p,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with self.client.stream(
            "POST", f"{self.base_url}/chat/completions", json=payload,
            # US-87 p2: short READ timeout = max silence between tokens.
            timeout=httpx.Timeout(self.request_timeout, read=self.stream_idle_timeout),
        ) as r:
            if r.status_code != 200:
                raise OllamaConnectionError(f"Chat stream failed with status {r.status_code}")
            perf: Dict[str, Any] = {}
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                perf.update(self._perf_from_openai(chunk))
                try:
                    choice = chunk["choices"][0]
                except (KeyError, IndexError, TypeError):
                    continue  # usage-only chunk carries empty choices
                delta = (choice.get("delta") or {}).get("content", "") or ""
                if delta:
                    yield self._ndjson(model, delta, done=False)
            yield self._ndjson(model, "", done=True, extra=perf)

    @staticmethod
    def _ndjson(
        model: str, content: str, done: bool,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """One Ollama-style NDJSON chat line (what chat routes expect to parse)."""
        out: Dict[str, Any] = {
            "model": model,
            "created_at": _now_iso(),
            "message": {"role": "assistant", "content": content},
            "done": done,
        }
        if extra:
            out.update(extra)
        return json.dumps(out)

    @staticmethod
    def _translate_options(options, temperature, top_p):
        """Map Ollama-style `options` (temperature/top_p/num_predict) to OpenAI params."""
        max_tokens = None
        temp, tp = temperature, top_p
        if options:
            temp = options.get("temperature", temp)
            tp = options.get("top_p", tp)
            max_tokens = options.get("num_predict", options.get("max_tokens"))
        return max_tokens, temp, tp

    # ------------------------------------------------------------------
    # Generate (non-chat) — implemented over /chat/completions for portability
    # ------------------------------------------------------------------
    def generate(self, prompt: str, model: Optional[str] = None, stream: bool = False, **kwargs):
        """Text generation, returned in Ollama generate shape (`{"response": ...}`)."""
        model = model or self.default_model
        msgs = [{"role": "user", "content": prompt}]
        max_tokens, temp, tp = self._translate_options(
            kwargs.get("options"), kwargs.get("temperature", 0.7), kwargs.get("top_p", 0.9),
        )
        if stream:
            def _gen() -> Iterator[str]:
                for line in self._chat_stream(msgs, model, max_tokens, temp, tp):
                    try:
                        yield json.loads(line).get("message", {}).get("content", "")
                    except json.JSONDecodeError:
                        continue
            return _gen()
        raw = self._chat_raw(msgs, model, stream=False, max_tokens=max_tokens,
                             temperature=temp, top_p=tp)
        try:
            content = raw["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        return {"model": model, "created_at": _now_iso(), "response": content, "done": True}

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    def embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        """Generate an embedding vector via POST /embeddings."""
        model = model or self.default_model
        try:
            r = self.client.post(
                f"{self.base_url}/embeddings", json={"model": model, "input": text},
            )
        except httpx.ConnectError as e:
            raise OllamaConnectionError(
                f"Cannot connect to LLM server at {self.base_url}"
            ) from e
        if r.status_code != 200:
            raise OllamaConnectionError(f"Embeddings request failed with status {r.status_code}")
        data = r.json().get("data", [])
        return (data[0].get("embedding", []) if data else [])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self):
        self.client.close()

    async def aclose(self):
        await self.async_client.aclose()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()
