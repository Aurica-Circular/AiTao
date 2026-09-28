# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_chat_streaming.py — US-87: streaming chat generators must ALWAYS terminate.
#
# A strict SSE client (e.g. OnlyOffice) waits for the terminator before it
# releases the UI. If the LLM backend stalls (request_timeout), drops, or ends
# the stream without a final done=True chunk, the generator must still emit the
# terminator — otherwise the client hangs forever (the "frozen stop button" bug).

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# The streaming generators sit behind an import chain that pulls in heavy ML deps
# (lancedb, sentence_transformers) absent from the lightweight CI gate. We import
# them in a SANDBOX: stub the missing heavies, import, then drop every module the
# import added back out of sys.modules. The two function objects keep working via
# their captured module globals, while sibling tests (test_lancedb_client,
# test_ollama_client) re-import the real chain — no leaked MagicMocks, and we
# never touch certifi/httpx (stubbing those corrupts SSL for the HTTP tests).
_before = set(sys.modules)
for _m in ["lancedb", "pyarrow", "pyarrow.parquet", "sentence_transformers"]:
    sys.modules.setdefault(_m, MagicMock())

from aitao.api.routes.chat_openai import _stream_openai_response  # noqa: E402
from aitao.api.routes.chat import _stream_chat_response  # noqa: E402

for _name in set(sys.modules) - _before:
    del sys.modules[_name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain(agen) -> str:
    """Run an async generator to completion and join its yields."""
    async def _run():
        return [item async for item in agen]
    return "".join(asyncio.run(_run()))


def _backend_yielding(chunks):
    """Fake LLM backend whose .chat() yields the given chunk strings."""
    backend = MagicMock()
    backend.chat.return_value = iter(chunks)
    return backend


def _backend_raising(chunks, exc):
    """Fake LLM backend whose .chat() yields some chunks then raises (a stall)."""
    def gen(*_a, **_k):
        for c in chunks:
            yield c
        raise exc
    backend = MagicMock()
    backend.chat.side_effect = gen
    return backend


def _ollama_chunk(content: str, done: bool) -> str:
    return json.dumps({"message": {"content": content}, "done": done})


_REQ = SimpleNamespace(model="test-model", options=None)


# ---------------------------------------------------------------------------
# OpenAI SSE format (/v1/chat/completions) — used by OnlyOffice / Open WebUI
# ---------------------------------------------------------------------------

class TestOpenAIStreamAlwaysTerminates:
    def _run(self, backend) -> str:
        return _drain(_stream_openai_response(backend, [], _REQ, {}, [], guard=False))

    def test_normal_completion_sends_done_exactly_once(self):
        out = self._run(_backend_yielding([
            _ollama_chunk("Hello", False),
            _ollama_chunk(" world", True),
        ]))
        assert out.count("data: [DONE]") == 1
        assert out.rstrip().endswith("[DONE]")

    def test_backend_stall_midstream_still_sends_done(self):
        """request_timeout / dropped connection mid-stream must not hang."""
        out = self._run(_backend_raising(
            [_ollama_chunk("Partial answer", False)], RuntimeError("read timeout")
        ))
        assert "data: [DONE]" in out
        assert out.rstrip().endswith("[DONE]")

    def test_stream_ends_without_done_still_sends_done(self):
        """Backend closed without a final done=True chunk → we terminate anyway."""
        out = self._run(_backend_yielding([_ollama_chunk("No final flag", False)]))
        assert out.count("data: [DONE]") == 1

    def test_idle_timeout_shows_stall_message(self):
        """US-87 p2 — a read/idle timeout surfaces a clear message, then [DONE]."""
        import httpx
        out = self._run(_backend_raising(
            [_ollama_chunk("Partial", False)], httpx.ReadTimeout("idle")
        ))
        assert "silence prolong" in out  # stall message (ASCII core survives json)
        assert out.rstrip().endswith("[DONE]")


# ---------------------------------------------------------------------------
# Native Ollama NDJSON format (/api/chat)
# ---------------------------------------------------------------------------

class TestNativeStreamAlwaysTerminates:
    def _run(self, backend) -> str:
        return _drain(_stream_chat_response(backend, [], _REQ, [], guard=False))

    def test_backend_stall_emits_done_terminator(self):
        out = self._run(_backend_raising(
            [_ollama_chunk("Partial", False)], RuntimeError("read timeout")
        ))
        assert '"done": true' in out

    def test_stream_ends_without_done_emits_terminator(self):
        out = self._run(_backend_yielding([_ollama_chunk("No final flag", False)]))
        assert '"done": true' in out

    def test_idle_timeout_shows_stall_message(self):
        """US-87 p2 — a read/idle timeout surfaces a clear message before done."""
        import httpx
        out = self._run(_backend_raising(
            [_ollama_chunk("Partial", False)], httpx.ReadTimeout("idle")
        ))
        assert "silence prolong" in out
        assert '"done": true' in out


class TestStreamPerfTracking:
    """US-STATS-01 — streaming generators feed the PerfTracker and always emit."""

    @staticmethod
    def _perf():
        from unittest.mock import Mock as _Mock

        from aitao.llm.perf_metrics import PerfTracker

        perf = PerfTracker(model="m", endpoint="/api/chat", backend="ollama")
        perf.emit = _Mock()  # spy: the generator logs via the module logger
        return perf

    def test_native_stream_captures_ttft_and_done_counters(self):
        perf = self._perf()
        done_line = json.dumps({
            "message": {"content": ""}, "done": True,
            "eval_count": 10, "eval_duration": 1_000_000_000,
        })
        _drain(_stream_chat_response(
            _backend_yielding([_ollama_chunk("Hello", False), done_line]),
            [], _REQ, [], guard=False, perf=perf,
        ))
        assert perf._ttft_ms is not None
        assert perf._perf["eval_count"] == 10
        assert perf.emit.call_count == 1

    def test_native_stream_records_error_then_emits(self):
        perf = self._perf()
        _drain(_stream_chat_response(
            _backend_raising([_ollama_chunk("Partial", False)], RuntimeError("x")),
            [], _REQ, [], guard=False, perf=perf,
        ))
        assert perf._success is False
        assert perf._error_type == "RuntimeError"
        assert perf.emit.call_count == 1

    def test_openai_stream_captures_ttft_and_emits(self):
        perf = self._perf()
        _drain(_stream_openai_response(
            _backend_yielding([
                _ollama_chunk("Hi", False), _ollama_chunk("", True),
            ]),
            [], _REQ, {}, [], guard=False, perf=perf,
        ))
        assert perf._ttft_ms is not None
        assert perf.emit.call_count == 1


class TestIdleTimeoutDetection:
    """US-87 p2 — only true read/idle timeouts trigger the stall message."""

    def test_detects_timeout_exceptions(self):
        import httpx
        from aitao.api.routes.chat import _is_idle_timeout
        assert _is_idle_timeout(httpx.ReadTimeout("x"))
        assert _is_idle_timeout(httpx.TimeoutException("x"))
        assert not _is_idle_timeout(RuntimeError("read timeout"))  # name, not type
        assert not _is_idle_timeout(ValueError("nope"))
