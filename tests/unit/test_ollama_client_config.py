# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for OllamaClient configuration (request timeout).

The request timeout must be configurable so slow / cold-starting local models
do not time out at a tight default. See [llm] request_timeout in config.toml.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from aitao.plugins.llm.ollama_client import OllamaClient


class _Cfg:
    """Minimal ConfigManager stand-in exposing typed .llm attribute (US-22)."""

    def __init__(self, llm: dict):
        self._llm = llm
        self.llm = SimpleNamespace(
            backend=llm.get("backend", "ollama"),
            default_model=llm.get("default_model", "qwen2.5-coder:7b"),
            ollama_url=llm.get("ollama_url", "http://localhost:11434"),
            request_timeout=float(llm.get("request_timeout") or llm.get("timeout") or 120.0),
            stream_idle_timeout=float(llm.get("stream_idle_timeout") or 90.0),
        )

    def get_section(self, name):
        return self._llm if name == "llm" else {}

    def get(self, key, default=None):
        return default


def test_request_timeout_defaults_to_120():
    client = OllamaClient(
        _Cfg({"ollama_url": "http://localhost:11434", "default_model": "x"}), None
    )
    assert client.request_timeout == 120.0


def test_request_timeout_from_config():
    client = OllamaClient(
        _Cfg(
            {
                "ollama_url": "http://localhost:11434",
                "default_model": "x",
                "request_timeout": 200,
            }
        ),
        None,
    )
    assert client.request_timeout == 200.0


def test_stream_idle_timeout_from_config():
    """US-87 p2 — the inter-token (idle) streaming timeout is configurable."""
    client = OllamaClient(
        _Cfg({"default_model": "x", "stream_idle_timeout": 45}), None
    )
    assert client.stream_idle_timeout == 45.0


# ---------------------------------------------------------------------------
# health() probe — reachability + real inference
# ---------------------------------------------------------------------------

def _client():
    return OllamaClient(
        _Cfg({"ollama_url": "http://localhost:11434", "default_model": "x"}), None
    )


def test_health_ok():
    c = _client()
    c.client = MagicMock()
    c.client.get.return_value = MagicMock(status_code=200, json=lambda: {"version": "0.30.3"})
    c.client.post.return_value = MagicMock(json=lambda: {"response": "ok", "done": True})
    health = c.health()
    assert health.ok is True
    assert health.version == "0.30.3"
    assert health.inference_ok is True


def test_health_inference_error_surfaces_hint():
    """The broken-install case: API up, but generation returns an error."""
    c = _client()
    c.client = MagicMock()
    c.client.get.return_value = MagicMock(status_code=200, json=lambda: {"version": "0.30.3"})
    c.client.post.return_value = MagicMock(
        json=lambda: {"error": "error starting llama-server: binary not found"}
    )
    health = c.health()
    assert health.reachable is True
    assert health.inference_ok is False
    assert "llama-server" in health.error
    assert health.hint  # remediation suggested


def test_health_unreachable():
    c = _client()
    c.client = MagicMock()
    c.client.get.side_effect = Exception("connection refused")
    health = c.health()
    assert health.reachable is False
    assert health.ok is False
    assert health.hint


def test_health_inference_timeout():
    c = _client()
    c.client = MagicMock()
    c.client.get.return_value = MagicMock(status_code=200, json=lambda: {"version": "0.30.3"})
    c.client.post.side_effect = httpx.TimeoutException("timeout")
    health = c.health()
    assert health.reachable is True
    assert health.inference_ok is False
    assert "within" in health.error
