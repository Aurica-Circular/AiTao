# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
LLM backend factory.

Selects the LLM client implementation from configuration so AiTao never depends
on a single provider. `[llm] backend` chooses:

    "ollama" (default), "auto", "mlx" (legacy)  → OllamaClient (native Ollama API)
    "openai" / "llama.cpp" / "lmstudio" / "vllm" → OpenAICompatClient (OpenAI API)

Switching providers is therefore a one-line config change — when Ollama is down,
point `backend = "openai"` at a running `llama-server` and keep working.

Both clients expose the same public interface, so callers (the API routes) use
the returned object identically.
"""

from typing import Any, Optional

from aitao.core.config import ConfigManager
from aitao.core.logger import StructuredLogger
from aitao.core.plugin_registry import discover_plugins, registry

# Backend identifiers that route to the OpenAI-compatible client.
_OPENAI_BACKENDS = {
    "openai", "openai_compat", "openai-compatible",
    "llama.cpp", "llamacpp", "llama_cpp", "llama-cpp",
    "lmstudio", "lm-studio", "vllm",
}


def make_llm_client(
    config: ConfigManager, logger: Optional[StructuredLogger] = None
) -> Any:
    """Return the configured LLM client (OllamaClient or OpenAICompatClient)."""
    # All backends (built-ins included) live in src/plugins/llm/ — discovery
    # imports them so their @register decorators run (US-25b).
    discover_plugins()

    backend = config.llm.backend.strip().lower()
    # A plugin backend registered under this exact name wins; otherwise resolve
    # the configured alias to a canonical built-in name.
    if registry.is_registered("llm", backend):
        name = backend
    else:
        name = "openai" if backend in _OPENAI_BACKENDS else "ollama"
    return registry.get("llm", name)(config, logger)
