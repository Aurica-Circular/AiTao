# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the intent router (US-105, ÉPIC-30 phase 2bis, incident I-16).

Exercises classify_intent's parsing and its fail-open contract (disabled,
missing llm_call, empty question, timeout, exception, unparsable response —
all must fall back to DOCUMENTARY, never GENERAL) with a fake injected
llm_call — no real model, no network. Every path is asserted to log a
verdict, mirroring test_context_gate.py's logging checks.
"""

import time
from typing import List

import pytest

from aitao.llm.intent_router import (
    DOCUMENTARY,
    GENERAL,
    GENERAL_ANSWER_BANNER,
    IntentVerdict,
    classify_intent,
)


class _FakeLogger:
    def __init__(self):
        self.info_calls: List[tuple] = []
        self.debug_calls: List[tuple] = []

    def info(self, message, metadata=None):
        self.info_calls.append((message, metadata))

    def debug(self, message, metadata=None):
        self.debug_calls.append((message, metadata))


def _call(text: str):
    """A fake llm_call that always returns ``text``."""
    return lambda messages: text


class TestBannerText:
    def test_banner_is_the_exact_specified_text(self):
        assert GENERAL_ANSWER_BANNER == (
            "ℹ️ Réponse générale — pas issue de vos documents.\n\n"
        )


class TestStrictParsing:
    def test_documentary_token(self):
        v = classify_intent("Quel est le montant ?", llm_call=_call("DOCUMENTARY"))
        assert v.route == DOCUMENTARY

    def test_general_token(self):
        v = classify_intent("Combien font 15% de 240 ?", llm_call=_call("GENERAL"))
        assert v.route == GENERAL

    def test_case_insensitive(self):
        v = classify_intent("q", llm_call=_call("general"))
        assert v.route == GENERAL

    def test_whitespace_and_punctuation_tolerated(self):
        v = classify_intent("q", llm_call=_call("  GENERAL.\n"))
        assert v.route == GENERAL

    def test_token_embedded_in_prose_still_parses(self):
        # Small models sometimes ignore the "one word only" instruction; the
        # first matched token wins rather than failing open on a technicality.
        v = classify_intent("q", llm_call=_call("The answer is GENERAL, clearly."))
        assert v.route == GENERAL


class TestFailOpenToDocumentary:
    """The dangerous mistake is a wrongful GENERAL verdict — every non-nominal
    path must resolve to DOCUMENTARY, never GENERAL (module contract)."""

    def test_disabled_by_config(self):
        v = classify_intent("q", llm_call=_call("GENERAL"), enabled=False)
        assert v.route == DOCUMENTARY
        assert "disabled" in v.reason

    def test_no_llm_call_provided(self):
        v = classify_intent("q", llm_call=None)
        assert v.route == DOCUMENTARY
        assert "no LLM call" in v.reason

    def test_empty_question(self):
        v = classify_intent("   ", llm_call=_call("GENERAL"))
        assert v.route == DOCUMENTARY
        assert "empty" in v.reason

    def test_exception_in_llm_call(self):
        def boom(messages):
            raise RuntimeError("backend down")

        v = classify_intent("q", llm_call=boom)
        assert v.route == DOCUMENTARY
        assert "error" in v.reason

    def test_timeout(self):
        def slow(messages):
            time.sleep(0.3)
            return "GENERAL"

        v = classify_intent("q", llm_call=slow, timeout_s=0.05)
        assert v.route == DOCUMENTARY
        assert "timeout" in v.reason

    def test_unparsable_response_empty(self):
        v = classify_intent("q", llm_call=_call(""))
        assert v.route == DOCUMENTARY
        assert "unparsable" in v.reason

    def test_unparsable_response_prose_without_token(self):
        v = classify_intent("q", llm_call=_call("I'm not entirely sure about that."))
        assert v.route == DOCUMENTARY
        assert "unparsable" in v.reason

    def test_unparsable_response_neither_word(self):
        v = classify_intent("q", llm_call=_call("MAYBE"))
        assert v.route == DOCUMENTARY


class TestLatencyAndLogging:
    def test_latency_is_measured_and_non_negative(self):
        v = classify_intent("q", llm_call=_call("DOCUMENTARY"))
        assert isinstance(v, IntentVerdict)
        assert v.latency_ms >= 0

    def test_verdict_logged_at_info_when_debug_true(self):
        logger = _FakeLogger()
        classify_intent("q", llm_call=_call("GENERAL"), logger=logger, debug=True)
        assert len(logger.info_calls) == 1
        assert logger.debug_calls == []
        message, metadata = logger.info_calls[0]
        assert metadata["route"] == GENERAL

    def test_verdict_logged_at_debug_when_debug_false(self):
        logger = _FakeLogger()
        classify_intent("q", llm_call=_call("DOCUMENTARY"), logger=logger, debug=False)
        assert len(logger.debug_calls) == 1
        assert logger.info_calls == []

    def test_never_silent_even_on_fail_open(self):
        # 5th invariant: every verdict is traced, including fail-open paths.
        logger = _FakeLogger()
        classify_intent("q", llm_call=None, logger=logger, debug=True)
        assert len(logger.info_calls) == 1

    def test_no_logger_is_safe(self):
        # logger=None must never raise (mirrors context_gate/conversation_dossier).
        classify_intent("q", llm_call=_call("DOCUMENTARY"), logger=None)


class TestRecentTurnsContext:
    def test_recent_turns_do_not_crash_and_are_passed_through(self):
        seen_messages = {}

        def spy(messages):
            seen_messages["messages"] = messages
            return "DOCUMENTARY"

        history = [
            {"role": "user", "content": "Quel document parle du bail ?"},
            {"role": "assistant", "content": "fr_bail.md"},
        ]
        v = classify_intent("Quel est son titre ?", recent_turns=history, llm_call=spy)
        assert v.route == DOCUMENTARY
        user_msg = seen_messages["messages"][-1]["content"]
        assert "Quel est son titre ?" in user_msg
        assert "fr_bail.md" in user_msg  # recent history reached the prompt

    def test_empty_recent_turns_do_not_crash(self):
        v = classify_intent("q", recent_turns=[], llm_call=_call("DOCUMENTARY"))
        assert v.route == DOCUMENTARY

    def test_recent_turns_none_do_not_crash(self):
        v = classify_intent("q", recent_turns=None, llm_call=_call("DOCUMENTARY"))
        assert v.route == DOCUMENTARY

    def test_current_question_never_shown_as_its_own_history(self):
        # US-105.2 field incident: routes pass the request messages, whose
        # last entry IS the question being classified — shown twice, a small
        # model reliably flips to DOCUMENTARY. The guard drops that trailing
        # duplicate; older turns must still get through.
        seen = {}

        def spy(messages):
            seen["user"] = messages[-1]["content"]
            return "GENERAL"

        question = "Quel est le pourcentage de jours entre deux lundis ?"
        history = [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Bonjour !"},
            {"role": "user", "content": question},  # the current turn itself
        ]
        classify_intent(question, recent_turns=history, llm_call=spy)
        assert seen["user"].count(question) == 1  # only the "Q:" line
        assert "Bonjour" in seen["user"]  # older turns still shown

    def test_fresh_session_question_alone_yields_no_history_block(self):
        seen = {}

        def spy(messages):
            seen["user"] = messages[-1]["content"]
            return "GENERAL"

        question = "Combien font 17% de 380 ?"
        classify_intent(
            question,
            recent_turns=[{"role": "user", "content": question}],
            llm_call=spy,
        )
        assert "Recent turns" not in seen["user"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
