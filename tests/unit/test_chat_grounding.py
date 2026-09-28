# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the chat grounding helper (US-076).

Exercise run_grounding_check's gating — toggle off, no context, embedding
failure — with a fake config and a deterministic fake embedder (no bge-m3, no
real config file). The deterministic check is Core (no licence gate).
monkeypatch keeps every override sandboxed to its test.
"""

from types import SimpleNamespace
from typing import List, Sequence

import aitao.api.routes.chat_grounding as grounding_mod
from aitao.api.routes.chat_grounding import (
    _resolve_verify_model,
    grounding_trailer,
    make_llm_verify_call,
    run_attribution_check,
    run_deep_verification,
    run_grounding_check,
    run_reader_appeal,
    run_stale_citation_check,
)

_VOCAB = ["bail", "préavis", "mois", "loyer", "chat", "lune"]


def _fake_embed(texts: Sequence[str]) -> List[List[float]]:
    return [[1.0 if w in t.lower() else 0.0 for w in _VOCAB] for t in texts]


class _Doc:
    def __init__(self, content: str, path: str = "", title: str = ""):
        self.content = content
        self.path = path
        self.title = title


class _FakeLogger:
    """Records calls instead of writing — mirrors test_intent_router.py /
    test_context_gate.py's logger double, adapted to chat_grounding's
    module-level ``logger`` (used directly, not injected as a parameter)."""

    def __init__(self):
        self.info_calls: List[tuple] = []
        self.debug_calls: List[tuple] = []
        self.warning_calls: List[tuple] = []

    def info(self, message, metadata=None):
        self.info_calls.append((message, metadata))

    def debug(self, message, metadata=None):
        self.debug_calls.append((message, metadata))

    def warning(self, message, metadata=None):
        self.warning_calls.append((message, metadata))


def _set_level(monkeypatch, level: str, reader_llm: bool = True):
    cfg = SimpleNamespace(
        rag=SimpleNamespace(verify_answer=level, reader_llm=reader_llm)
    )
    monkeypatch.setattr(grounding_mod, "get_config", lambda: cfg)


def test_disabled_returns_none(monkeypatch):
    _set_level(monkeypatch, "off")
    assert run_grounding_check("Le bail court.", [_Doc("Le bail.")], _fake_embed) is None


def test_no_context_returns_none(monkeypatch):
    _set_level(monkeypatch, "fast")
    assert run_grounding_check("Le bail court.", [], _fake_embed) is None


def test_enabled_returns_report(monkeypatch):
    _set_level(monkeypatch, "fast")
    report = run_grounding_check(
        "Le préavis du bail est de trois mois.",
        [_Doc("Le bail prévoit un préavis de trois mois.")],
        _fake_embed,
    )
    assert report is not None
    assert report.checked == 1
    assert report.grounding_score > 0.5


def test_embedding_failure_never_breaks_chat(monkeypatch):
    _set_level(monkeypatch, "fast")

    def boom(texts):
        raise RuntimeError("embedding model down")

    out = run_grounding_check(
        "Le préavis du bail est de trois mois.", [_Doc("Le bail.")], boom
    )
    assert out is None  # guarded — the chat response must still go out


class TestVerifyAnswerLevel:
    """verify_answer is a level (off/fast/deep), back-compatible with the
    legacy boolean (true->fast, false->off)."""

    def test_legacy_boolean_is_migrated(self):
        from aitao.core.config_schema import RAGConfig

        assert RAGConfig(verify_answer=True).verify_answer == "fast"
        assert RAGConfig(verify_answer=False).verify_answer == "off"

    def test_levels_accepted_case_insensitive(self):
        from aitao.core.config_schema import RAGConfig

        assert RAGConfig(verify_answer="deep").verify_answer == "deep"
        assert RAGConfig(verify_answer="FAST").verify_answer == "fast"

    def test_default_is_fast(self):
        # US-092: the deterministic net is on by default — reliability is
        # acquired, not optional.
        from aitao.core.config_schema import RAGConfig

        assert RAGConfig().verify_answer == "fast"

    def test_unknown_value_falls_back_to_fast(self):
        # A typo must never silently disable the reliability net (US-076 B
        # principle applied to the level since the US-092 default-on).
        from aitao.core.config_schema import RAGConfig

        assert RAGConfig(verify_answer="banana").verify_answer == "fast"

    def test_explicit_off_is_respected(self):
        from aitao.core.config_schema import RAGConfig

        assert RAGConfig(verify_answer="off").verify_answer == "off"

    def test_off_skips_check(self, monkeypatch):
        _set_level(monkeypatch, "off")
        assert run_grounding_check(
            "Le préavis du bail est de trois mois.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
        ) is None

    def test_deep_runs_deterministic_for_now(self, monkeypatch):
        # run_grounding_check itself is always deterministic; the LLM pass lives
        # in run_deep_verification / grounding_trailer.
        _set_level(monkeypatch, "deep")
        report = run_grounding_check(
            "Le préavis du bail est de trois mois.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
        )
        assert report is not None
        assert report.checked == 1


def _report(sentence, score=0.8):
    from aitao.llm.answer_validator import GroundingReport, SentenceGrounding

    return GroundingReport(
        grounding_score=score,
        weakest_score=score,
        sentences=[SentenceGrounding(sentence, score)],
        checked=1,
    )


def _premium(monkeypatch, ok=True):
    monkeypatch.setattr(
        "aitao.core.license.LicenseManager.is_premium", lambda self: ok
    )


class TestDeepVerification:
    def test_no_llm_call_returns_empty(self, monkeypatch):
        _set_level(monkeypatch, "deep")
        assert run_deep_verification(_report("Trois mois."), [_Doc("ctx")], None) == []

    def test_fast_level_skips_llm(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        out = run_deep_verification(
            _report("Trois mois."), [_Doc("ctx")], lambda m: "1: CONTRADICTED"
        )
        assert out == []

    def test_deep_flags_contradiction(self, monkeypatch):
        _set_level(monkeypatch, "deep")
        verdicts = run_deep_verification(
            _report("Le préavis est de trois mois."),
            [_Doc("Le préavis est d'un mois.")],
            lambda m: "1: CONTRADICTED",
        )
        assert verdicts[0].verdict == "contradicted"

    def test_deep_skips_non_fact_claims(self, monkeypatch):
        _set_level(monkeypatch, "deep")
        # no number -> nothing selected -> no LLM verdict
        assert run_deep_verification(
            _report("Le logement est décent."), [_Doc("ctx")], lambda m: "1: CONTRADICTED"
        ) == []

    def test_deep_works_without_premium(self, monkeypatch):
        _set_level(monkeypatch, "deep")
        # Deep verification is now free and works without premium licensing.
        # With is_premium patched to return False, a deep verification call
        # should still process claims with numbers and return verdicts.
        monkeypatch.setattr(
            "aitao.core.license.LicenseManager.is_premium", lambda self: False
        )
        verdicts = run_deep_verification(
            _report("Le préavis est de trois mois."),
            [_Doc("Le préavis est d'un mois.")],
            lambda m: "1: CONTRADICTED",
        )
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "contradicted"


class TestGroundingTrailer:
    def test_off_returns_none(self, monkeypatch):
        _set_level(monkeypatch, "off")
        assert grounding_trailer("Trois mois.", [_Doc("ctx")], _fake_embed) is None

    def test_fast_is_deterministic_only(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        out = grounding_trailer(
            "Le chat dort sur la lune avec 3 pizzas.",
            [_Doc("Le bail prévoit un préavis.")],
            _fake_embed,
        )
        assert out is not None
        text, _ = out
        assert "Fiabilité" in text
        assert "approfondie" not in text  # no LLM section at fast

    def test_deep_adds_llm_contradiction(self, monkeypatch):
        _set_level(monkeypatch, "deep")
        out = grounding_trailer(
            "Le préavis du bail est de trois mois.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
            lambda m: "1: CONTRADICTED",
        )
        assert out is not None
        text, _ = out
        assert "contredite" in text

    def test_attribution_runs_even_when_verify_answer_off(self, monkeypatch):
        # US-104 part B — the attribution check is Core and ALWAYS active,
        # independent of verify_answer.
        _set_level(monkeypatch, "off")
        receipt = _Doc(
            "Receipt RCP-2026-0099 confirms your payment was received in full.",
            path="/x/en_receipt.md", title="en_receipt",
        )
        invoice = _Doc(
            "Reference: INV-2026-0042. Total amount due: 4 200 USD.",
            path="/x/en_invoice.md", title="en_invoice",
        )
        out = grounding_trailer(
            "D'après en_receipt.md, le montant reçu s'élève à 4200 USD.",
            [receipt, invoice],
            _fake_embed,
        )
        assert out is not None
        text, report = out
        assert "en_invoice.md" in text
        assert "en_invoice.md" in report.attribution_notes[0]

    def test_role_counts_and_attribution_notes_on_report(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        out = grounding_trailer(
            "C'est le premier document du contexte.",
            [_Doc("Le bail prévoit un préavis.")],
            _fake_embed,
        )
        assert out is not None
        _, report = out
        assert report.role_counts.get("habillage") == 1

    def test_reader_appeal_unflags_scripted_habillage(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        # "47" is a genuine digit not present anywhere in the context, so the
        # deterministic pass flags it; rules alone classify this AFFIRMATION
        # (no ordinal word), so only the appeal can unflag it.
        out = grounding_trailer(
            "Il s'agit du document numéro 47 dans la liste.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
            reader_llm_call=lambda m: "1: HABILLAGE",
        )
        assert out is None or "Fiabilité" not in out[0]

    def test_reader_llm_disabled_keeps_flag(self, monkeypatch):
        _set_level(monkeypatch, "fast", reader_llm=False)
        out = grounding_trailer(
            "Il s'agit du document numéro 47 dans la liste.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
            reader_llm_call=lambda m: "1: HABILLAGE",
        )
        assert out is not None
        assert "Fiabilité" in out[0]

    def test_stale_citation_merges_with_in_context_flags(self, monkeypatch):
        # I-15 — a subject change left the answer citing a document (zh_glass)
        # absent from this turn's context; the corrective banner must name the
        # probable real source (the ONLY context doc, fr_bail).
        _set_level(monkeypatch, "off")
        doc = _Doc(
            "Le présent bail fixe le loyer mensuel à 1250 EUR.",
            path="/x/fr_bail.md", title="fr_bail",
        )
        out = grounding_trailer(
            "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR.",
            [doc],
            _fake_embed,
        )
        assert out is not None
        text, report = out
        assert "introuvable" in text
        assert "fr_bail.md" in text
        assert "fr_bail.md" in report.attribution_notes[0]

    def test_empty_fabricated_list_is_zero_behaviour_change(self, monkeypatch):
        # I-15 — a CORRECT citation of the only context doc: no fabricated
        # name (G2 finds nothing), no I-05 flag (needs >= 2 context docs) ->
        # nothing to report, exactly like before this extension existed.
        _set_level(monkeypatch, "off")
        doc = _Doc(
            "Le présent bail fixe le loyer mensuel à 1250 EUR.",
            path="/x/fr_bail.md", title="fr_bail",
        )
        out = grounding_trailer(
            "D'après fr_bail.md, le loyer mensuel s'élève à 1250 EUR.",
            [doc],
            _fake_embed,
        )
        assert out is None


class TestSentenceRoleDebugLogging:
    """US-104 étude §6.4, 5th invariant: role of each sentence logged with its
    reason, on by default (``[rag] reliability_debug``), débrayable via
    config — mirrors intent_router.py / context_gate.py's INFO/DEBUG gating."""

    def test_per_sentence_role_and_summary_are_logged(self, monkeypatch):
        _set_level(monkeypatch, "off")  # verify_answer off — roles are still classified
        fake_logger = _FakeLogger()
        monkeypatch.setattr(grounding_mod, "logger", fake_logger)

        grounding_trailer(
            "C'est le premier document du contexte.",
            [_Doc("Le bail prévoit un préavis.")],
            _fake_embed,
        )

        role_lines = [c for c in fake_logger.info_calls if c[0] == "sentence role classified"]
        assert len(role_lines) == 1
        _, metadata = role_lines[0]
        assert metadata["role"] == "habillage"
        assert "ordinal" in metadata["reason"]
        assert metadata["sentence"] == "C'est le premier document du contexte."

        summary_lines = [c for c in fake_logger.info_calls if c[0] == "sentence roles summary"]
        assert len(summary_lines) == 1
        assert summary_lines[0][1]["counts"]["habillage"] == 1

    def test_sentence_is_clipped_to_80_chars(self, monkeypatch):
        _set_level(monkeypatch, "off")
        fake_logger = _FakeLogger()
        monkeypatch.setattr(grounding_mod, "logger", fake_logger)
        long_sentence = "Le bail prévoit un préavis de trois mois " + "x" * 100 + "."

        grounding_trailer(long_sentence, [_Doc("Le bail.")], _fake_embed)

        role_lines = [c for c in fake_logger.info_calls if c[0] == "sentence role classified"]
        assert len(role_lines[0][1]["sentence"]) <= 80

    def test_reliability_debug_off_logs_at_debug_level(self, monkeypatch):
        # débrayable via config (US-104 5th invariant) — off means DEBUG, not silent.
        cfg = SimpleNamespace(
            rag=SimpleNamespace(verify_answer="off", reliability_debug=False)
        )
        monkeypatch.setattr(grounding_mod, "get_config", lambda: cfg)
        fake_logger = _FakeLogger()
        monkeypatch.setattr(grounding_mod, "logger", fake_logger)

        grounding_trailer(
            "C'est le premier document du contexte.",
            [_Doc("Le bail prévoit un préavis.")],
            _fake_embed,
        )

        assert not any(c[0] == "sentence role classified" for c in fake_logger.info_calls)
        assert any(c[0] == "sentence role classified" for c in fake_logger.debug_calls)

    def test_reader_appeal_verdict_is_logged_per_sentence(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        fake_logger = _FakeLogger()
        monkeypatch.setattr(grounding_mod, "logger", fake_logger)

        grounding_trailer(
            "Il s'agit du document numéro 47 dans la liste.",
            [_Doc("Le bail prévoit un préavis de trois mois.")],
            _fake_embed,
            reader_llm_call=lambda m: "1: HABILLAGE",
        )

        appeal_lines = [c for c in fake_logger.info_calls if c[0] == "reader appeal verdict"]
        assert len(appeal_lines) == 1
        _, metadata = appeal_lines[0]
        assert metadata["verdict"] == "habillage_echo"
        assert "47" in metadata["sentence"]


class TestRunAttributionCheck:
    def test_no_roles_returns_empty(self):
        assert run_attribution_check(None, [_Doc("ctx")], _fake_embed) == []

    def test_no_context_docs_returns_empty(self):
        assert run_attribution_check([], [], _fake_embed) == []

    def test_never_raises_on_broken_check(self, monkeypatch):
        monkeypatch.setattr(
            grounding_mod, "check_attribution",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        from aitao.llm.response_reader import classify_sentences

        roles = classify_sentences(["Une phrase."], [])
        assert run_attribution_check(roles, [_Doc("a"), _Doc("b")], _fake_embed) == []


class TestRunStaleCitationCheck:
    def test_no_roles_returns_empty(self):
        assert run_stale_citation_check("ans", None, [_Doc("ctx")], _fake_embed) == []

    def test_no_context_docs_returns_empty(self):
        assert run_stale_citation_check("ans", [], [], _fake_embed) == []

    def test_no_fabricated_citation_returns_empty(self):
        from aitao.llm.response_reader import classify_sentences

        doc = _Doc(
            "Le bail prévoit un préavis de trois mois.",
            path="/x/fr_bail.md", title="fr_bail",
        )
        answer = "D'après fr_bail.md, le préavis est de trois mois."
        roles = classify_sentences([answer], [doc])
        assert run_stale_citation_check(answer, roles, [doc], _fake_embed) == []

    def test_stale_citation_is_flagged(self):
        from aitao.llm.response_reader import classify_sentences

        doc = _Doc(
            "Le présent bail fixe le loyer mensuel à 1250 EUR.",
            path="/x/fr_bail.md", title="fr_bail",
        )
        answer = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        roles = classify_sentences([answer], [doc])
        flags = run_stale_citation_check(answer, roles, [doc], _fake_embed)
        assert len(flags) == 1
        assert flags[0].rule == "stale_digit"
        assert flags[0].probable_source == "fr_bail.md"

    def test_never_raises_on_broken_check(self, monkeypatch):
        monkeypatch.setattr(
            grounding_mod, "check_stale_citations",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        from aitao.llm.response_reader import classify_sentences

        doc = _Doc(
            "Le présent bail fixe le loyer mensuel à 1250 EUR.",
            path="/x/fr_bail.md", title="fr_bail",
        )
        answer = "D'après zh_glass.md, le loyer mensuel s'élève à 1250 EUR."
        roles = classify_sentences([answer], [doc])
        assert run_stale_citation_check(answer, roles, [doc], _fake_embed) == []


class TestRunReaderAppeal:
    def _report_with_unsupported(self, sentence):
        from aitao.llm.answer_validator import GroundingReport, SentenceGrounding

        return GroundingReport(
            grounding_score=0.1, weakest_score=0.1,
            sentences=[SentenceGrounding(sentence, 0.1)],
            unsupported=[sentence], checked=1,
        )

    def test_nothing_unsupported_is_a_no_op(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        from aitao.llm.answer_validator import GroundingReport

        report = GroundingReport(grounding_score=1.0, weakest_score=1.0)
        assert run_reader_appeal(report, lambda m: "1: HABILLAGE") is report

    def test_no_llm_call_keeps_flag(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        report = self._report_with_unsupported("Phrase signalée.")
        out = run_reader_appeal(report, None)
        assert out.unsupported == ["Phrase signalée."]

    def test_disabled_config_keeps_flag(self, monkeypatch):
        _set_level(monkeypatch, "fast", reader_llm=False)
        report = self._report_with_unsupported("Phrase signalée.")
        out = run_reader_appeal(report, lambda m: "1: HABILLAGE")
        assert out.unsupported == ["Phrase signalée."]

    def test_habillage_verdict_unflags(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        report = self._report_with_unsupported("Phrase signalée.")
        out = run_reader_appeal(report, lambda m: "1: HABILLAGE")
        assert out.unsupported == []

    def test_never_raises_on_broken_llm_call(self, monkeypatch):
        _set_level(monkeypatch, "fast")
        report = self._report_with_unsupported("Phrase signalée.")

        def boom(m):
            raise RuntimeError("model unavailable")

        out = run_reader_appeal(report, boom)
        assert out.unsupported == ["Phrase signalée."]


def _set_verify_model(monkeypatch, verify_model: str):
    cfg = SimpleNamespace(rag=SimpleNamespace(verify_answer="deep", verify_model=verify_model))
    monkeypatch.setattr(grounding_mod, "get_config", lambda: cfg)


class _FakeClient:
    """Ollama-shaped stub: records the model used and exposes list_models."""

    def __init__(self, available, raises=False):
        self._available = [SimpleNamespace(name=n) for n in available]
        self._raises = raises
        self.used_model = None
        self.list_calls = 0

    def list_models(self):
        self.list_calls += 1
        if self._raises:
            raise RuntimeError("tags unreachable")
        return self._available

    def chat(self, messages, model, stream=False, options=None):
        self.used_model = model
        return {"message": {"content": "1: SUPPORTED"}}


class TestResolveVerifyModel:
    def test_empty_uses_chat_model(self, monkeypatch):
        _set_verify_model(monkeypatch, "")
        client = _FakeClient(["granite4:latest"])
        assert _resolve_verify_model(client, "qwen3.5:latest") == "qwen3.5:latest"
        assert client.list_calls == 0  # no lookup when not configured

    def test_sentinel_defaut_uses_chat_model(self, monkeypatch):
        # The template ships "defaut" (model unknown at install) — it must mean
        # "reuse the chat model", not trigger a lookup for a model named "defaut".
        for sentinel in ("defaut", "Defaut", "default", "auto"):
            _set_verify_model(monkeypatch, sentinel)
            client = _FakeClient(["granite4:latest"])
            assert _resolve_verify_model(client, "qwen3.5:latest") == "qwen3.5:latest"
            assert client.list_calls == 0

    def test_same_as_chat_model_short_circuits(self, monkeypatch):
        _set_verify_model(monkeypatch, "qwen3.5:latest")
        client = _FakeClient(["qwen3.5:latest"])
        assert _resolve_verify_model(client, "qwen3.5:latest") == "qwen3.5:latest"
        assert client.list_calls == 0

    def test_available_model_is_used(self, monkeypatch):
        _set_verify_model(monkeypatch, "granite4:latest")
        client = _FakeClient(["granite4:latest", "qwen3.5:latest"])
        assert _resolve_verify_model(client, "qwen3.5:latest") == "granite4:latest"

    def test_missing_model_falls_back_to_chat(self, monkeypatch):
        _set_verify_model(monkeypatch, "granite4:latest")
        client = _FakeClient(["qwen3.5:latest"])  # granite4 not pulled
        assert _resolve_verify_model(client, "qwen3.5:latest") == "qwen3.5:latest"

    def test_unreachable_list_falls_back_to_chat(self, monkeypatch):
        _set_verify_model(monkeypatch, "granite4:latest")
        client = _FakeClient([], raises=True)
        assert _resolve_verify_model(client, "qwen3.5:latest") == "qwen3.5:latest"


class TestMakeLLMVerifyCall:
    def test_uses_dedicated_model_and_is_lazy(self, monkeypatch):
        _set_verify_model(monkeypatch, "granite4:latest")
        client = _FakeClient(["granite4:latest", "qwen3.5:latest"])
        call = make_llm_verify_call(client, "qwen3.5:latest")
        assert client.list_calls == 0  # not resolved until actually invoked
        out = call([{"role": "user", "content": "x"}])
        assert out == "1: SUPPORTED"
        assert client.used_model == "granite4:latest"

    def test_resolution_is_memoised(self, monkeypatch):
        _set_verify_model(monkeypatch, "granite4:latest")
        client = _FakeClient(["granite4:latest"])
        call = make_llm_verify_call(client, "qwen3.5:latest")
        call([{"role": "user", "content": "a"}])
        call([{"role": "user", "content": "b"}])
        assert client.list_calls == 1  # resolved once, reused
