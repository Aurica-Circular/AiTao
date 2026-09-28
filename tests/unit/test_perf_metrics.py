# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for llm.perf_metrics (US-STATS-01).

Covers Ollama counter extraction, the tokens/s division-by-zero guard, TTFT
capture on streamed chunks, the per-host version cache (no network call in
the critical path), the llm_called marker, emit idempotence, and the privacy
non-regression: the emitted log metadata must never contain message text.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import aitao.llm.perf_metrics as pm
from aitao.llm.perf_metrics import PerfTracker, extract_ollama_perf, get_ollama_version


def _tracker() -> PerfTracker:
    """Tracker with an explicit backend so tests never touch the real config."""
    return PerfTracker(model="test-model", endpoint="/api/chat", backend="ollama")


def _emitted_metadata(tracker: PerfTracker, **emit_kwargs) -> dict:
    logger = Mock()
    tracker.emit(logger, **emit_kwargs)
    return logger.info.call_args.kwargs["metadata"]


# ---------------------------------------------------------------------------
# extract_ollama_perf
# ---------------------------------------------------------------------------

class TestExtractOllamaPerf:
    def test_full_response(self):
        perf = extract_ollama_perf({
            "eval_count": 100,
            "eval_duration": 2_000_000_000,  # 2s in ns
            "prompt_eval_count": 50,
            "prompt_eval_duration": 500_000_000,
            "load_duration": 42,
        })
        assert perf["eval_count"] == 100
        assert perf["eval_duration_ns"] == 2_000_000_000
        assert perf["prompt_eval_count"] == 50
        assert perf["prompt_eval_duration_ns"] == 500_000_000
        assert perf["load_duration_ns"] == 42
        assert perf["tokens_per_second"] == 50.0

    def test_tokens_per_second_zero_duration_guard(self):
        perf = extract_ollama_perf({"eval_count": 100, "eval_duration": 0})
        assert perf["tokens_per_second"] is None

    def test_missing_fields_stay_none(self):
        perf = extract_ollama_perf({"message": {"content": "hi"}, "done": True})
        assert perf["eval_count"] is None
        assert perf["tokens_per_second"] is None

    def test_non_dict_input_never_raises(self):
        assert extract_ollama_perf(None)["eval_count"] is None
        assert extract_ollama_perf("garbage")["tokens_per_second"] is None


# ---------------------------------------------------------------------------
# Version cache
# ---------------------------------------------------------------------------

def _fake_ollama_client(host: str, version: str = "0.31.0"):
    resp = Mock(status_code=200)
    resp.json.return_value = {"version": version}
    http = Mock()
    http.get.return_value = resp
    return SimpleNamespace(host=host, client=http)


class TestVersionCache:
    def setup_method(self):
        pm._version_cache.clear()

    def test_fetches_then_caches(self):
        client = _fake_ollama_client("http://cache-test:11434")
        assert get_ollama_version(client) == "0.31.0"
        assert get_ollama_version(client) == "0.31.0"
        assert client.client.get.call_count == 1  # second hit from cache

    def test_failure_is_cached_too(self):
        client = _fake_ollama_client("http://down-test:11434")
        client.client.get.side_effect = RuntimeError("connection refused")
        assert get_ollama_version(client) is None
        assert get_ollama_version(client) is None
        assert client.client.get.call_count == 1  # no per-request retry storm

    def test_openai_backend_without_host_returns_none(self):
        client = SimpleNamespace(client=Mock())  # no .host attribute
        assert get_ollama_version(client) is None
        client.client.get.assert_not_called()

    def test_none_client_is_safe(self):
        assert get_ollama_version(None) is None


# ---------------------------------------------------------------------------
# PerfTracker
# ---------------------------------------------------------------------------

def _chunk(content: str, done: bool = False, **extra) -> str:
    return json.dumps({"message": {"content": content}, "done": done, **extra})


class TestPerfTracker:
    def test_metadata_has_all_required_fields(self):
        meta = _emitted_metadata(_tracker(), context_chunk_count=3)
        for key in (
            "request_id", "duration_ms", "ttft_ms", "model", "backend",
            "ollama_version", "request_type", "source_endpoint",
            "load_duration_ns", "eval_count", "eval_duration_ns",
            "prompt_eval_count", "tokens_per_second", "context_chunk_count",
            "llm_called", "success", "error_type",
        ):
            assert key in meta, f"missing field: {key}"
        assert meta["request_type"] == "chat"
        assert meta["source_endpoint"] == "/api/chat"
        assert meta["context_chunk_count"] == 3
        assert meta["success"] is True
        assert meta["error_type"] is None
        assert len(meta["request_id"]) == 36  # uuid4

    def test_ttft_captured_on_first_content_chunk(self):
        tracker = _tracker()
        tracker.on_raw_chunk(_chunk("Bon"))
        tracker.on_raw_chunk(_chunk("jour"))
        meta = _emitted_metadata(tracker)
        assert meta["ttft_ms"] is not None
        assert meta["ttft_ms"] >= 0

    def test_ttft_null_when_non_stream(self):
        tracker = _tracker()
        tracker.capture_response({"eval_count": 10, "eval_duration": 1_000_000_000})
        meta = _emitted_metadata(tracker)
        assert meta["ttft_ms"] is None
        assert meta["eval_count"] == 10
        assert meta["tokens_per_second"] == 10.0

    def test_done_chunk_carries_counters(self):
        tracker = _tracker()
        tracker.on_raw_chunk(_chunk("Hello"))
        tracker.on_raw_chunk(_chunk(
            "", done=True, eval_count=20, eval_duration=4_000_000_000,
            prompt_eval_count=7, load_duration=11,
        ))
        meta = _emitted_metadata(tracker)
        assert meta["eval_count"] == 20
        assert meta["tokens_per_second"] == 5.0
        assert meta["prompt_eval_count"] == 7
        assert meta["load_duration_ns"] == 11

    def test_malformed_chunk_is_ignored(self):
        tracker = _tracker()
        tracker.on_raw_chunk("not json{")
        tracker.on_raw_chunk(None)
        assert _emitted_metadata(tracker)["success"] is True

    def test_error_logs_category_not_message(self):
        tracker = _tracker()
        tracker.record_error(ValueError("SECRET user question text"))
        meta = _emitted_metadata(tracker)
        assert meta["success"] is False
        assert meta["error_type"] == "ValueError"
        assert "SECRET" not in json.dumps(meta)

    def test_emit_error_one_liner(self):
        logger = Mock()
        _tracker().emit_error(logger, TimeoutError("boom"))
        meta = logger.info.call_args.kwargs["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "TimeoutError"

    def test_llm_called_false_for_refusals(self):
        meta = _emitted_metadata(_tracker(), llm_called=False)
        assert meta["llm_called"] is False
        assert meta["eval_count"] is None

    def test_emit_is_idempotent(self):
        tracker = _tracker()
        logger = Mock()
        tracker.emit(logger)
        tracker.emit(logger)
        assert logger.info.call_count == 1

    def test_request_ids_are_unique(self):
        assert _tracker().request_id != _tracker().request_id


# ---------------------------------------------------------------------------
# Privacy non-regression (PRD-AITAO-STATS §3 — non-negotiable)
# ---------------------------------------------------------------------------

SENTINEL_QUESTION = "QUESTION_PRIVEE_XYZZY_A_NE_JAMAIS_LOGGER"
SENTINEL_ANSWER = "REPONSE_PRIVEE_PLUGH_A_NE_JAMAIS_LOGGER"


class TestPrivacyNonRegression:
    def test_log_line_never_contains_message_text(self):
        """Full flow: streamed answer text + user text in an exception message.

        The complete logger.info call (message + metadata) must not contain
        either sentinel, in any form.
        """
        tracker = _tracker()
        tracker.on_raw_chunk(_chunk(SENTINEL_ANSWER))
        tracker.on_raw_chunk(_chunk(SENTINEL_ANSWER, done=True, eval_count=5,
                                    eval_duration=1_000_000_000))
        tracker.record_error(RuntimeError(f"failed on: {SENTINEL_QUESTION}"))
        logger = Mock()
        tracker.emit(logger, context_chunk_count=2)

        logged = json.dumps({
            "message": logger.info.call_args.args[0],
            "metadata": logger.info.call_args.kwargs["metadata"],
        }, ensure_ascii=False, default=str)
        assert SENTINEL_QUESTION not in logged
        assert SENTINEL_ANSWER not in logged
