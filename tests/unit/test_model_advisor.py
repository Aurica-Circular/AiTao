# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the model advisor (US-097).

Known reasoning families and non-chat models must be flagged; ordinary chat
models must never be (the heuristic is conservative: unknown = fine).
"""

from dataclasses import dataclass

from aitao.llm.model_advisor import (
    chat_model_advisory,
    is_cloud_proxy_model,
    is_non_chat_model,
    is_reasoning_model,
    pick_safe_default,
)


@dataclass
class _FakeModel:
    """Minimal stand-in for OllamaModel — only .name/.size are needed."""
    name: str
    size: int


class TestIsReasoningModel:
    def test_known_reasoning_families_flagged(self):
        for name in (
            "qwen3.5:latest",
            "qwen3-vl:2b",      # measured: thinking tokens on the RAG prompt
            "qwen3:8b",
            "gemma4:12b",       # measured: thinking on RAG (GGUF build)
            "gemma4:12b-mlx",   # measured: thinking on RAG (MLX build)
            "qwq:32b",
            "deepseek-r1:8b",
            "llama-r1:latest",
            "phi4-reasoning",
            "gemma-thinking:2b",
            "magistral:latest",
        ):
            assert is_reasoning_model(name), name

    def test_safe_chat_models_not_flagged(self):
        for name in (
            "granite4:latest",
            "llama3.1:8b",
            "qwen2.5-coder:latest",
            "qwen3-coder-next:cloud",  # coder variants don't think
            "mistral:7b",
        ):
            assert not is_reasoning_model(name), name

    def test_empty_name(self):
        assert not is_reasoning_model("")


class TestIsNonChatModel:
    def test_translation_and_embedding_models_flagged(self):
        assert is_non_chat_model("translategemma:latest")
        assert is_non_chat_model("nomic-embed-text")

    def test_chat_model_not_flagged(self):
        assert not is_non_chat_model("granite4:latest")


class TestIsCloudProxyModel:
    def test_cloud_tags_flagged(self):
        assert is_cloud_proxy_model("qwen3-coder-next:cloud")
        assert is_cloud_proxy_model("gemma4:31b-cloud")

    def test_local_tags_not_flagged(self):
        assert not is_cloud_proxy_model("granite4:latest")
        assert not is_cloud_proxy_model("gemma4:12b-mlx")


class TestChatModelAdvisory:
    def test_reasoning_default_gets_warning(self):
        msg = chat_model_advisory("qwen3.5:latest")
        assert msg is not None and "reasoning" in msg
        assert "granite4" in msg  # points to a safe alternative

    def test_non_chat_default_gets_warning(self):
        msg = chat_model_advisory("translategemma:latest")
        assert msg is not None and "not a chat" in msg

    def test_safe_default_silent(self):
        assert chat_model_advisory("granite4:latest") is None
        assert chat_model_advisory("") is None


class TestPickSafeDefault:
    def test_empty_list_returns_none(self):
        assert pick_safe_default([]) is None

    def test_prefers_safe_model_over_smaller_reasoning_model(self):
        """Reproduces the real US-116 incident case: a small reasoning model
        must NEVER win just because it is smaller — granite4 (safe, bigger)
        must be picked over qwen3-vl (reasoning, smaller)."""
        models = [
            _FakeModel(name="qwen3-vl:2b", size=2_000_000_000),   # smaller, reasoning
            _FakeModel(name="granite4:latest", size=4_000_000_000),  # bigger, safe
        ]
        assert pick_safe_default(models) == "granite4:latest"

    def test_never_picks_cloud_proxy_model_even_though_smallest(self):
        """Reproduces a real failure found live (US-116 verification,
        2026-07-21): cloud-proxied Ollama tags (":...cloud") report a
        near-zero placeholder size and would win any size comparison, but
        they are not local weights — auto-picking one on Phil's real Ollama
        produced an HTTP 410 instead of an answer. Real fixture values from
        `ollama list` on the machine where this broke."""
        models = [
            _FakeModel(name="qwen3-coder-next:cloud", size=382),   # placeholder, "smallest"
            _FakeModel(name="gemma4:31b-cloud", size=342),          # placeholder, reasoning family too
            _FakeModel(name="granite4:latest", size=2_099_520_825),  # real local weights, safe
        ]
        assert pick_safe_default(models) == "granite4:latest"

    def test_falls_back_to_smallest_non_cloud_when_only_cloud_and_risky_installed(self):
        models = [
            _FakeModel(name="qwen3-coder-next:cloud", size=382),
            _FakeModel(name="qwen3.5:latest", size=5_000_000_000),  # reasoning, but at least local
        ]
        # Cloud placeholder must lose even to a risky-but-local model.
        assert pick_safe_default(models) == "qwen3.5:latest"

    def test_falls_back_to_smallest_when_only_risky_models_installed(self):
        models = [
            _FakeModel(name="qwen3.5:latest", size=5_000_000_000),
            _FakeModel(name="translategemma:latest", size=1_000_000_000),
            _FakeModel(name="deepseek-r1:8b", size=8_000_000_000),
        ]
        # No safe candidate at all -> smallest overall, even though it is risky.
        assert pick_safe_default(models) == "translategemma:latest"

    def test_picks_smallest_among_multiple_safe_models(self):
        models = [
            _FakeModel(name="llama3.1:8b", size=5_000_000_000),
            _FakeModel(name="granite4:latest", size=2_000_000_000),
            _FakeModel(name="mistral:7b", size=4_000_000_000),
        ]
        assert pick_safe_default(models) == "granite4:latest"
