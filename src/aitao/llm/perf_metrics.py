# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Performance metrics capture for chat requests (US-STATS-01).

Collects per-request performance metadata — latency, time-to-first-token,
Ollama token counters, throughput — and emits it as a single structured log
entry consumed later by the external stats collector (US-STATS-03).

The heavy lifting lives here so the already-oversized chat routes only add
a few one-line hooks (PerfTracker calls).

Privacy contract (PRD-AITAO-STATS §3, non-negotiable): the metadata built
here contains only numeric counters and categorical fields (model name,
backend, error CLASS name). Question or answer text is never read, stored
or logged.
"""

import json
import time
import uuid
from typing import Any, Dict, Optional

__all__ = ["PerfTracker", "extract_ollama_perf", "get_ollama_version"]


def _tokens_per_second(
    eval_count: Optional[int], eval_duration_ns: Optional[int]
) -> Optional[float]:
    """eval_count / eval_duration in seconds — None on missing/zero input."""
    if not eval_count or not eval_duration_ns:
        return None
    return round(eval_count / (eval_duration_ns / 1e9), 2)


def extract_ollama_perf(raw: Any) -> Dict[str, Any]:
    """Pull Ollama performance counters out of a response or final stream chunk.

    Works on the non-stream response dict and on the parsed ``done: true``
    NDJSON line (both carry the same fields). Absent fields stay None — an
    OpenAI-compatible backend may only provide token counts. Never raises.
    """
    if not isinstance(raw, dict):
        raw = {}
    perf: Dict[str, Any] = {
        "load_duration_ns": raw.get("load_duration"),
        "eval_count": raw.get("eval_count"),
        "eval_duration_ns": raw.get("eval_duration"),
        "prompt_eval_count": raw.get("prompt_eval_count"),
        "prompt_eval_duration_ns": raw.get("prompt_eval_duration"),
    }
    perf["tokens_per_second"] = _tokens_per_second(
        perf["eval_count"], perf["eval_duration_ns"]
    )
    return perf


# ---------------------------------------------------------------------------
# Ollama version — cached so the critical path never gains a network call
# ---------------------------------------------------------------------------

_VERSION_TTL_S = 300.0
# host -> (version_or_None, monotonic_expiry). Failures are cached too, so a
# down server costs at most one probe per TTL window, not one per request.
_version_cache: Dict[str, tuple] = {}


def get_ollama_version(client: Any, ttl_s: float = _VERSION_TTL_S) -> Optional[str]:
    """Ollama server version via GET /api/version, cached per host with a TTL.

    Backends without that endpoint (OpenAI-compatible: no ``.host`` attribute)
    return None. Never raises.
    """
    host = getattr(client, "host", None)
    http = getattr(client, "client", None)
    if not host or http is None:
        return None
    now = time.monotonic()
    cached = _version_cache.get(host)
    if cached and cached[1] > now:
        return cached[0]
    version: Optional[str] = None
    try:
        resp = http.get(f"{host}/api/version", timeout=3.0)
        if resp.status_code == 200:
            version = resp.json().get("version")
    except Exception:
        version = None
    _version_cache[host] = (version, now + ttl_s)
    return version


def _config_backend() -> str:
    """Configured LLM backend name, or 'unknown' outside a configured runtime."""
    try:
        from aitao.core.config import get_config

        return get_config().llm.backend.strip().lower()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Per-request tracker
# ---------------------------------------------------------------------------


class PerfTracker:
    """Accumulates performance facts for one chat request, then logs them once.

    Route-side usage is deliberately terse (chat.py/chat_openai.py are over
    the file-size budget already)::

        perf = PerfTracker(model=request.model, endpoint="/api/chat")
        perf.on_raw_chunk(line)       # streaming: TTFT + final counters
        perf.capture_response(resp)   # non-stream: final counters
        perf.record_error(exc)        # or perf.emit_error(logger, exc)
        perf.emit(logger, client=ollama, context_chunk_count=n)

    ``emit`` is idempotent: exactly one log entry per request.
    """

    def __init__(
        self,
        model: str,
        endpoint: str,
        backend: Optional[str] = None,
        request_type: str = "chat",
    ):
        self.request_id = str(uuid.uuid4())
        self.model = model
        self.endpoint = endpoint
        self.backend = backend or _config_backend()
        self.request_type = request_type
        self._t0 = time.monotonic()
        self._ttft_ms: Optional[float] = None
        self._perf: Dict[str, Any] = extract_ollama_perf({})
        self._success = True
        self._error_type: Optional[str] = None
        self._emitted = False

    def on_chunk(self, data: Any) -> None:
        """Track one parsed stream chunk: TTFT on first content, counters on done."""
        if not isinstance(data, dict):
            return
        if self._ttft_ms is None and (data.get("message") or {}).get("content"):
            self._ttft_ms = round((time.monotonic() - self._t0) * 1000, 2)
        if data.get("done"):
            self._perf = extract_ollama_perf(data)

    def on_raw_chunk(self, line: str) -> None:
        """Track one raw NDJSON stream line (malformed lines are ignored)."""
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            return
        self.on_chunk(data)

    def capture_response(self, response: Any) -> None:
        """Capture counters from a non-streaming backend response."""
        self._perf = extract_ollama_perf(response)

    def record_error(self, exc: BaseException) -> None:
        """Mark the request failed. Only the exception CLASS name is kept —
        the message may quote user text and must never reach the log."""
        self._success = False
        self._error_type = type(exc).__name__

    def emit(
        self,
        logger: Any,
        message: str = "Chat completed",
        *,
        client: Any = None,
        context_chunk_count: int = 0,
        llm_called: bool = True,
    ) -> None:
        """Log the request's performance metadata exactly once."""
        if self._emitted:
            return
        self._emitted = True
        metadata: Dict[str, Any] = {
            "request_id": self.request_id,
            "duration_ms": round((time.monotonic() - self._t0) * 1000, 2),
            "ttft_ms": self._ttft_ms,
            "model": self.model,
            "backend": self.backend,
            "ollama_version": get_ollama_version(client),
            "request_type": self.request_type,
            "source_endpoint": self.endpoint,
            "context_chunk_count": context_chunk_count,
            "llm_called": llm_called,
            "success": self._success,
            "error_type": self._error_type,
        }
        metadata.update(self._perf)
        logger.info(message, metadata=metadata)

    def emit_error(self, logger: Any, exc: BaseException, **kwargs: Any) -> None:
        """record_error + emit in one route-side line."""
        self.record_error(exc)
        self.emit(logger, **kwargs)
