# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for reader_appeal (US-104, part C — LLM appeal court).

Covers grouped-prompt parsing (permissive separators, <think> stripping),
and every fail-open path: disabled, no callable, empty/unparsable response,
timeout, exception — none of these may ever ADD a flag, only remove one
already raised by the deterministic pass.
"""

from aitao.llm.reader_appeal import (
    AFFIRMATION,
    HABILLAGE_ECHO,
    apply_appeal,
    build_appeal_messages,
    parse_appeal,
    run_appeal,
)

SENTENCES = [
    "C'est le premier document du contexte.",
    "Le total est de 999 USD.",
]


class TestBuildMessages:
    def test_numbers_every_sentence(self):
        messages = build_appeal_messages(SENTENCES)
        user = messages[-1]["content"]
        assert "1. " + SENTENCES[0] in user
        assert "2. " + SENTENCES[1] in user


class TestParseAppeal:
    def test_parses_both_verdicts(self):
        verdicts = parse_appeal("1: HABILLAGE\n2: AFFIRMATION", SENTENCES)
        assert verdicts[0].verdict == HABILLAGE_ECHO
        assert verdicts[1].verdict == AFFIRMATION

    def test_permissive_separators(self):
        for sep_text in ("1) HABILLAGE", "1 - HABILLAGE", "1. HABILLAGE", "<1>: HABILLAGE"):
            verdicts = parse_appeal(sep_text, SENTENCES[:1])
            assert verdicts[0].verdict == HABILLAGE_ECHO, sep_text

    def test_missing_verdict_defaults_to_affirmation(self):
        # Fail-open: no clear verdict must never remove a flag.
        verdicts = parse_appeal("1: HABILLAGE", SENTENCES)
        assert verdicts[0].verdict == HABILLAGE_ECHO
        assert verdicts[1].verdict == AFFIRMATION

    def test_strips_think_block(self):
        raw = "<think>reasoning about it...</think>1: HABILLAGE\n2: AFFIRMATION"
        verdicts = parse_appeal(raw, SENTENCES)
        assert verdicts[0].verdict == HABILLAGE_ECHO

    def test_unparsable_response_defaults_all_to_affirmation(self):
        verdicts = parse_appeal("je ne sais pas répondre à cela", SENTENCES)
        assert all(v.verdict == AFFIRMATION for v in verdicts)


class TestRunAppealFailOpen:
    def test_disabled_returns_empty(self):
        assert run_appeal(SENTENCES, lambda m: "1: HABILLAGE", enabled=False) == []

    def test_no_llm_call_returns_empty(self):
        assert run_appeal(SENTENCES, None) == []

    def test_no_sentences_returns_empty(self):
        assert run_appeal([], lambda m: "1: HABILLAGE") == []

    def test_exception_returns_empty(self):
        def boom(messages):
            raise RuntimeError("model unavailable")

        assert run_appeal(SENTENCES, boom) == []

    def test_timeout_returns_empty(self):
        import time

        def slow(messages):
            time.sleep(5)
            return "1: HABILLAGE"

        assert run_appeal(SENTENCES, slow, timeout_s=0.05) == []

    def test_empty_response_returns_empty(self):
        assert run_appeal(SENTENCES, lambda m: "") == []

    def test_nominal_call_returns_verdicts(self):
        verdicts = run_appeal(SENTENCES, lambda m: "1: HABILLAGE\n2: AFFIRMATION")
        assert len(verdicts) == 2


class TestApplyAppeal:
    def test_habillage_echo_unflags(self):
        verdicts = run_appeal(SENTENCES, lambda m: "1: HABILLAGE\n2: AFFIRMATION")
        kept = apply_appeal(SENTENCES, verdicts)
        assert kept == [SENTENCES[1]]

    def test_no_verdicts_keeps_everything_flagged(self):
        # Mirrors run_appeal(..., llm_call=None) -> [] verdicts: the appeal
        # can only shrink the flagged list, never silently empty it out.
        assert apply_appeal(SENTENCES, []) == SENTENCES

    def test_all_affirmation_keeps_everything_flagged(self):
        verdicts = run_appeal(SENTENCES, lambda m: "1: AFFIRMATION\n2: AFFIRMATION")
        assert apply_appeal(SENTENCES, verdicts) == SENTENCES
