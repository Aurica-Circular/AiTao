# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for OpenAICompatClient and the LLM backend factory (US-DEMO-18).

Verifies the OpenAI-compatible client is a true drop-in for OllamaClient:
same method names, Ollama-shaped return values, same exceptions — so switching
provider (Ollama ↔ llama.cpp) is a config change, not a code change.
"""

import httpx
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from aitao.plugins.llm.openai_compat_client import OpenAICompatClient
from aitao.llm.protocols import OllamaChatMessage, OllamaConnectionError
from aitao.llm.provider import make_llm_client


class FakeConfig:
    """Minimal ConfigManager stand-in exposing typed .llm attribute (US-22)."""

    def __init__(self, llm: dict):
        self._llm = llm
        oai = llm.get("openai", {})
        self.llm = SimpleNamespace(
            backend=llm.get("backend", "openai"),
            default_model=llm.get("default_model", "local-model"),
            ollama_url=llm.get("ollama_url", "http://localhost:11434"),
            request_timeout=float(llm.get("request_timeout", 120.0)),
            stream_idle_timeout=float(llm.get("stream_idle_timeout", 90.0)),
            openai=SimpleNamespace(
                base_url=oai.get("base_url", "http://localhost:8080/v1"),
                model=oai.get("model", "local-model"),
                api_key=oai.get("api_key", ""),
            ),
        )

    def get_section(self, name: str) -> dict:
        return self._llm if name == "llm" else {}

    def get(self, key, default=None):
        return default


def _client(llm=None) -> OpenAICompatClient:
    cfg = FakeConfig(llm or {"openai": {"base_url": "http://test/v1", "model": "m"}})
    return OpenAICompatClient(cfg)


def _resp(status=200, payload=None) -> Mock:
    r = Mock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = ""
    return r


def test_chat_returns_ollama_shaped_content():
    """Routes read response['message']['content'] — must be Ollama-shaped."""
    client = _client()
    client.client = Mock()
    client.client.post.return_value = _resp(
        200, {"choices": [{"message": {"role": "assistant", "content": "Bonjour Phil"}}]}
    )

    out = client.chat([OllamaChatMessage("user", "salut")], model="m")

    assert out["message"]["content"] == "Bonjour Phil"
    assert out["done"] is True


def test_chat_maps_usage_and_timings_to_ollama_perf_fields():
    """US-STATS-01: token counts/timings surface under Ollama field names."""
    client = _client()
    client.client = Mock()
    client.client.post.return_value = _resp(200, {
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {"completion_tokens": 42, "prompt_tokens": 17},
        "timings": {"predicted_ms": 2000.0, "prompt_ms": 100.0},  # llama.cpp
    })

    out = client.chat([OllamaChatMessage("user", "salut")], model="m")

    assert out["eval_count"] == 42
    assert out["prompt_eval_count"] == 17
    assert out["eval_duration"] == 2_000_000_000  # ns
    assert out["prompt_eval_duration"] == 100_000_000

def test_chat_without_usage_stays_ollama_shaped():
    """Servers that omit `usage` must not break the response contract."""
    client = _client()
    client.client = Mock()
    client.client.post.return_value = _resp(
        200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    )

    out = client.chat([OllamaChatMessage("user", "x")], model="m")

    assert out["message"]["content"] == "ok"
    assert "eval_count" not in out

def _stream_ctx(lines):
    """Mock httpx stream() context manager yielding the given SSE lines."""
    from unittest.mock import MagicMock

    cm = MagicMock()
    cm.__enter__.return_value = SimpleNamespace(
        status_code=200, iter_lines=lambda: iter(lines)
    )
    cm.__exit__.return_value = False
    return cm

def test_chat_stream_attaches_usage_to_final_done_line():
    """US-STATS-01: the usage chunk (after finish_reason) rides the done line."""
    import json

    client = _client()
    client.client = Mock()
    client.client.stream.return_value = _stream_ctx([
        'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"completion_tokens":7,"prompt_tokens":3}}',
        "data: [DONE]",
    ])

    out = [json.loads(line) for line in client.chat([], model="m", stream=True)]

    assert out[0]["message"]["content"] == "Hi"
    final = out[-1]
    assert final["done"] is True
    assert final["eval_count"] == 7
    assert final["prompt_eval_count"] == 3
    # include_usage was requested so the server sends the usage chunk
    payload = client.client.stream.call_args.kwargs["json"]
    assert payload["stream_options"] == {"include_usage": True}

def test_chat_stream_without_usage_still_terminates():
    """A server ignoring stream_options must still produce a done line."""
    import json

    client = _client()
    client.client = Mock()
    client.client.stream.return_value = _stream_ctx([
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ])

    out = [json.loads(line) for line in client.chat([], model="m", stream=True)]

    assert out[-1]["done"] is True
    assert "eval_count" not in out[-1]

def test_list_models_maps_openai_to_ollama():
    client = _client()
    client.client = Mock()
    client.client.get.return_value = _resp(
        200, {"data": [{"id": "qwen2.5", "created": 1700000000}]}
    )

    models = client.list_models()

    assert [m.name for m in models] == ["qwen2.5"]


def test_chat_connection_error_maps_to_ollama_error():
    """A connection failure must surface as OllamaConnectionError (routes catch it -> 503)."""
    client = _client()
    client.client = Mock()
    client.client.post.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(OllamaConnectionError):
        client.chat([{"role": "user", "content": "x"}], model="m")


def test_embeddings_extracts_vector():
    client = _client()
    client.client = Mock()
    client.client.post.return_value = _resp(
        200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    )

    assert client.embeddings("hello") == [0.1, 0.2, 0.3]


def test_chat_single_served_model_wins_over_stale_default():
    """US-116: a server exposing exactly ONE model is unambiguous — use it
    even if `default_model`/the requested model names something else."""
    client = _client({"openai": {"base_url": "http://test/v1", "model": "stale-name"}})
    client.client = Mock()
    client.client.get.return_value = _resp(200, {"data": [{"id": "the-only-model"}]})
    client.client.post.return_value = _resp(
        200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    )

    client.chat([OllamaChatMessage("user", "salut")])

    payload = client.client.post.call_args.kwargs["json"]
    assert payload["model"] == "the-only-model"


def test_chat_multiple_models_falls_back_to_safe_default():
    """US-116: several models served, none matching -> pick_safe_default,
    never a known reasoning model."""
    client = _client({"openai": {"base_url": "http://test/v1", "model": "unknown-model"}})
    client.client = Mock()
    client.client.get.return_value = _resp(
        200, {"data": [{"id": "qwen3-vl:2b"}, {"id": "granite4:latest"}]}
    )
    client.client.post.return_value = _resp(
        200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    )

    client.chat([OllamaChatMessage("user", "salut")])

    payload = client.client.post.call_args.kwargs["json"]
    assert payload["model"] == "granite4:latest"


def test_factory_selects_backend_from_config():
    """The one-line switch: `[llm] backend` picks the implementation."""
    openai_client = make_llm_client(
        FakeConfig({"backend": "openai", "openai": {"base_url": "http://test/v1"}})
    )
    assert type(openai_client).__name__ == "OpenAICompatClient"

    ollama_client = make_llm_client(
        FakeConfig({"backend": "ollama", "ollama_url": "http://localhost:11434"})
    )
    assert type(ollama_client).__name__ == "OllamaClient"
