# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for answer_validator_llm (US-076 phase 2-C — the LLM verification pass).

A fake llm_call returns a canned '<n>: <verdict>' block, so these tests run fast
and never call a real model. They cover fact selection (FR + CJK + digits), the
grouped prompt, verdict parsing (incl. malformed answers), and the warning.
"""

from aitao.llm.answer_validator_llm import (
    ClaimVerdict,
    build_llm_warning,
    build_verification_messages,
    has_verifiable_fact,
    parse_verdicts,
    select_claims,
    verify_claims,
)


class TestHasVerifiableFact:
    def test_digits(self):
        assert has_verifiable_fact("Le total TTC est de 4 200 euros.")

    def test_spelled_out_french(self):
        assert has_verifiable_fact("Le préavis est de trois mois.")

    def test_cjk_numeral(self):
        assert has_verifiable_fact("試用期為六個月。")

    def test_article_un_is_not_a_fact(self):
        # "un"/"une" are excluded — otherwise almost every sentence would qualify.
        assert not has_verifiable_fact("Le bailleur délivre un logement décent.")

    def test_no_fact(self):
        assert not has_verifiable_fact("Le locataire entretient le logement.")

    def test_select_keeps_only_fact_bearing(self):
        kept = select_claims([
            "Le préavis est de trois mois.",
            "Le logement est décent.",
            "La TVA est de 20 %.",
        ])
        assert kept == ["Le préavis est de trois mois.", "La TVA est de 20 %."]


class TestPrompt:
    def test_messages_number_claims_and_docs(self):
        msgs = build_verification_messages(["A.", "B."], ["ctx1", "ctx2"])
        assert msgs[0]["role"] == "system"
        user = msgs[1]["content"]
        assert "1. A." in user and "2. B." in user
        assert "[Doc 1] ctx1" in user and "[Doc 2] ctx2" in user


class TestParseVerdicts:
    def test_parses_each_line(self):
        claims = ["c1", "c2", "c3"]
        resp = "1: SUPPORTED\n2: CONTRADICTED\n3: ABSENT"
        verdicts = parse_verdicts(resp, claims)
        assert [v.verdict for v in verdicts] == ["supported", "contradicted", "absent"]

    def test_tolerates_formatting_variants(self):
        verdicts = parse_verdicts("1) contradicted\n2 - Absent", ["c1", "c2"])
        assert verdicts[0].verdict == "contradicted"
        assert verdicts[1].verdict == "absent"

    def test_tolerates_angle_bracketed_number(self):
        # Observed on granite4: "<1>: CONTRADICTED" — must not silently default
        # to 'supported' and hide the contradiction.
        verdicts = parse_verdicts("<1>: CONTRADICTED\n<2>: ABSENT", ["c1", "c2"])
        assert verdicts[0].verdict == "contradicted"
        assert verdicts[1].verdict == "absent"

    def test_missing_line_defaults_to_supported(self):
        # No news = no flag: a missing verdict must never invent a flag.
        verdicts = parse_verdicts("1: CONTRADICTED", ["c1", "c2"])
        assert verdicts[0].verdict == "contradicted"
        assert verdicts[1].verdict == "supported"

    def test_garbage_response_defaults_all_supported(self):
        verdicts = parse_verdicts("désolé je ne sais pas", ["c1", "c2"])
        assert all(v.verdict == "supported" for v in verdicts)

    def test_strips_reasoning_think_block(self):
        # A reasoning model wraps its scaffolding in <think>…</think>; the verdict
        # that follows must still parse, and verdict-like noise inside the block
        # must not leak through.
        resp = "<think>peut-être 1: SUPPORTED ? non…</think>\n1: CONTRADICTED"
        verdicts = parse_verdicts(resp, ["c1"])
        assert verdicts[0].verdict == "contradicted"

    def test_unbalanced_think_keeps_tail(self):
        resp = "<think>raisonnement sans fermeture</think>1: ABSENT"
        verdicts = parse_verdicts(resp, ["c1"])
        assert verdicts[0].verdict == "absent"

    def test_unparsed_claims_are_logged(self, monkeypatch):
        # The silent default must remain observable: a format-breaking model
        # that hides every contradiction has to leave a trace.
        import aitao.llm.answer_validator_llm as mod

        calls = []
        monkeypatch.setattr(mod.logger, "warning", lambda *a, **k: calls.append((a, k)))
        parse_verdicts("1: CONTRADICTED", ["c1", "c2"])
        assert calls and calls[0][1].get("metadata", {}).get("missing_claims") == [2]

    def test_full_parse_does_not_log(self, monkeypatch):
        import aitao.llm.answer_validator_llm as mod

        calls = []
        monkeypatch.setattr(mod.logger, "warning", lambda *a, **k: calls.append(a))
        parse_verdicts("1: SUPPORTED\n2: ABSENT", ["c1", "c2"])
        assert not calls


class TestVerifyClaims:
    def test_happy_path(self):
        def fake_llm(messages):
            return "1: SUPPORTED\n2: CONTRADICTED"

        verdicts = verify_claims(["bon", "mauvais chiffre"], ["contexte"], fake_llm)
        assert verdicts[1].verdict == "contradicted"

    def test_empty_inputs(self):
        assert verify_claims([], ["ctx"], lambda m: "") == []
        assert verify_claims(["c"], [], lambda m: "") == []

    def test_llm_failure_returns_empty(self):
        def boom(messages):
            raise RuntimeError("model down")

        assert verify_claims(["c"], ["ctx"], boom) == []  # never breaks the chat


class TestBuildLLMWarning:
    def test_empty_when_no_contradiction(self):
        verdicts = [ClaimVerdict("a", "supported"), ClaimVerdict("b", "absent")]
        assert build_llm_warning(verdicts) == ""

    def test_lists_contradictions(self):
        verdicts = [ClaimVerdict("Le préavis est de trois mois.", "contradicted")]
        w = build_llm_warning(verdicts)
        assert "contredite" in w
        assert "Le préavis est de trois mois." in w

    def test_absent_is_not_repeated(self):
        # 'absent' is the deterministic pass's job; the LLM warning is contradictions.
        verdicts = [ClaimVerdict("x", "absent")]
        assert build_llm_warning(verdicts) == ""
