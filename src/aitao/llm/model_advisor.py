# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Model advisor — flag chat models that are risky for RAG (US-097).

A *reasoning* model (qwen3.5, deepseek-r1, qwq…) burns minutes of hidden
"thinking" tokens on a long RAG prompt before the first visible word — the user
experiences a stalled answer (measured: ~8 min per answer for qwen3.5 vs seconds
for a non-reasoning model on the same context), and some stall outright. This is
a model-choice problem, not a plumbing bug (timeouts are handled by US-87), so
the fix is advisory: detect a risky configured default and tell the user clearly
at startup instead of letting them discover the stall.

Detection is by model-name pattern — deterministic, no network, conservative
(unknown models are NOT flagged).
"""

import re
from typing import Iterable, Optional, Protocol, runtime_checkable

# Name fragments of model families that reason/think before answering.
# Conservative on purpose: only families whose reasoning behaviour is known —
# qwen3* measured on this stack (US-097 bench): thinking tokens before any
# visible content on the production RAG prompt. Coder variants don't think.
_REASONING_PATTERNS = (
    r"qwen3(?!-coder)",  # qwen3 / qwen3.5 / qwen3-vl — think by default in ollama
    r"gemma4",         # gemma4 (GGUF and MLX builds) — measured thinking on RAG
    r"qwq",            # QwQ — Alibaba reasoning family
    r"deepseek-r1",    # DeepSeek-R1 and distills
    r"(^|[^a-z])r1([^0-9]|$)",  # "…-r1" tags of R1 distills
    r"o1|o3-",         # OpenAI-style reasoning naming used by some GGUF repacks
    r"think",          # "…-thinking" variants
    r"reason",         # "…-reasoning" variants
    r"magistral",      # Mistral reasoning family
)

# Models that are not chat/instruct models at all — wrong tool for RAG chat.
_NON_CHAT_PATTERNS = (
    r"translategemma",  # raw translation model, no instruction following
    r"embed",           # embedding models
)

# Ollama's naming convention for models proxied to a remote/cloud endpoint
# rather than run on local weights (e.g. "qwen3-coder-next:cloud",
# "gemma4:31b-cloud"). These report a near-zero placeholder `size` in
# `ollama list` / `/api/tags` (nothing is actually stored on disk), which
# made them win any "smallest installed model" comparison outright — measured
# live (US-116 verification, 2026-07-21): auto-picking one produced an HTTP
# 410 instead of an answer, the exact class of silent breakage this feature
# exists to prevent. Never auto-picked; an explicit `default_model` naming one
# is untouched (that's the user's own informed choice, not a guess).
_CLOUD_PROXY_SUFFIX = re.compile(r":.*cloud")


def is_reasoning_model(name: str) -> bool:
    """True if the model name matches a known reasoning family."""
    n = (name or "").lower()
    return any(re.search(p, n) for p in _REASONING_PATTERNS)


def is_non_chat_model(name: str) -> bool:
    """True if the model is not an instruct/chat model (e.g. raw translation)."""
    n = (name or "").lower()
    return any(re.search(p, n) for p in _NON_CHAT_PATTERNS)


def is_cloud_proxy_model(name: str) -> bool:
    """True if the model tag names a remote/cloud-proxied endpoint, not local
    weights (Ollama's ``:...cloud`` tag convention — see module docstring)."""
    n = (name or "").lower()
    return bool(_CLOUD_PROXY_SUFFIX.search(n))


def chat_model_advisory(name: str) -> Optional[str]:
    """One-line warning if ``name`` is a risky default for RAG chat, else None."""
    if not name:
        return None
    if is_non_chat_model(name):
        return (
            f"'{name}' is not a chat/instruct model — it cannot follow RAG "
            "instructions. Pick a chat model (e.g. granite4, llama3.1) as "
            "[llm] default_model."
        )
    if is_reasoning_model(name):
        return (
            f"'{name}' is a reasoning model: on RAG prompts it 'thinks' for "
            "minutes before the first visible word (measured on this stack) or "
            "can stall. For fast grounded answers use a non-reasoning chat "
            "model (e.g. granite4, llama3.1) as [llm] default_model."
        )
    return None


@runtime_checkable
class _NamedSizedModel(Protocol):
    """Structural type for what pick_safe_default needs — matches OllamaModel
    (llm.protocols) without importing it, to avoid a plugins/llm <-> llm
    import cycle (plugins/llm/ollama_client.py already imports this module)."""

    name: str
    size: int  # bytes


def pick_safe_default(models: Iterable[_NamedSizedModel]) -> Optional[str]:
    """Pick the smallest installed model that is safe for RAG chat (US-116).

    "Safe" = neither a reasoning model (thinks for minutes instead of
    answering, see ``is_reasoning_model``) nor a non-chat model (translation/
    embedding-only, see ``is_non_chat_model``) nor a cloud-proxied model
    (see ``is_cloud_proxy_model`` — these report a near-zero placeholder
    size and would otherwise win every comparison while not actually being
    local weights). Among safe candidates, the smallest by ``.size`` wins —
    smaller local models answer faster and are more likely to already be
    pulled.

    If no candidate qualifies (only reasoning/non-chat/cloud models
    installed), falls back to the smallest **non-cloud** model if one
    exists, else the smallest overall — the caller is responsible for
    logging a strong warning in that case, since the picked model may be a
    bad fit for RAG chat (or, as a last resort, not actually runnable
    offline).

    Returns None if ``models`` is empty (nothing installed at all).
    """
    models = list(models)
    if not models:
        return None
    safe = [
        m for m in models
        if not is_reasoning_model(m.name)
        and not is_non_chat_model(m.name)
        and not is_cloud_proxy_model(m.name)
    ]
    non_cloud = [m for m in models if not is_cloud_proxy_model(m.name)]
    pool = safe or non_cloud or models
    return min(pool, key=lambda m: m.size).name
