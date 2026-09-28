# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_golden_conversations.py — Golden multi-turn conversation bench (US-101,
# ÉPIC-30 phase 0).
#
# Extends the single-turn golden suite (test_golden_retrieval.py, US-89) to
# whole CONVERSATIONS: each scenario in tests/fixtures/golden_scenarios/ is a
# sequence of turns replayed through the real retrieval + gate pipeline
# (RAGEngine.enrich_messages with the conversation dossier, the US-103
# context gate — proceed / targeted refusal / clarification —, the
# citation_guard notices, the US-104 response reader + reader appeal +
# source attribution, the answer_validator "fast" grounding check) — NO LLM.
# A scripted answer stands in for the model so the answer-side invariants
# (banner absent/present, cited source) are gradeable deterministically.
#
# History is threaded exactly as the API route does (src/api/routes/chat.py):
# clean_history_messages() strips AiTao's own reliability notices from past
# assistant turns before the next retrieval pass sees them, and the assistant
# turn stored for the next iteration carries the SAME notices/banner a real
# response would (multi-source note, grounding banner) — a refusal
# short-circuits exactly like the route (no notices, no banner).
#
# xfail mechanism (see tests/golden/phase.py): a scenario whose `phase`
# exceeds DELIVERED_PHASE encodes a TARGET behaviour not yet built. It still
# runs (nothing is skipped) but is wrapped `xfail(strict=False)`, so the suite
# stays green while encoding what the next US must achieve; it flips to a
# real, unmarked pass the day that phase ships (bump DELIVERED_PHASE then).
# All four planned phases (US-102/103/104) are delivered as of DELIVERED_PHASE
# = 3 — every committed scenario now runs as a real, unmarked assertion.
#
# Requires Meilisearch + the bge-m3 embedding model -> slow + requires_meilisearch,
# run by the dedicated golden CI job, not the fast release gate.

import sys
from pathlib import Path
from typing import Dict, List

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.requires_meilisearch]

# `golden` is a plain (non-test) package under tests/ — not on pythonpath
# (pyproject only adds "src"), so add the tests/ root explicitly, mirroring
# the sys.path.insert pattern already used by tests/e2e/test_startup_chain.py.
sys.path.insert(0, str(Path(__file__).parent.parent))

from golden.phase import DELIVERED_PHASE  # noqa: E402
from golden.scenario_loader import Scenario, load_all_scenarios  # noqa: E402

_CORPUS = Path(__file__).parent.parent / "fixtures" / "golden_corpus"
_SCENARIOS_DIR = Path(__file__).parent.parent / "fixtures" / "golden_scenarios"
_MEILI_INDEX = "test_golden_conversations"


@pytest.fixture(scope="module")
def golden_rag(meilisearch_test_available):
    """Index the golden corpus (+ US-101 additions) into isolated stores."""
    if not meilisearch_test_available:
        pytest.skip("Meilisearch not available")

    from aitao.core.config import get_config
    from aitao.core.logger import get_logger
    from golden.corpus_fixture import index_corpus
    from aitao.llm.rag_engine import RAGEngine
    from aitao.search.hybrid_engine import HybridSearchEngine

    meili, chunks, _files = index_corpus(_CORPUS, _MEILI_INDEX)

    engine = HybridSearchEngine(meilisearch_repo=meili)
    engine._chunk_store = chunks

    rag = RAGEngine(get_config(), get_logger("golden_conversations"))
    rag._search_engine = engine
    # Same rationale as test_golden_retrieval.py: IDF distillation is
    # meaningless on a handful of docs — this suite guards the gate + the
    # multi-turn pinning/anchoring, not US-89-1.
    rag.distill_query = False

    yield rag


def _committed_scenarios() -> List[Scenario]:
    return load_all_scenarios(_SCENARIOS_DIR, corpus="committed")


def _scenario_params():
    params = []
    for sc in _committed_scenarios():
        marks = []
        if sc.phase > DELIVERED_PHASE:
            marks.append(
                pytest.mark.xfail(
                    reason=(
                        f"{sc.incident or sc.name}: ÉPIC-30 phase {sc.phase} not yet "
                        f"delivered (DELIVERED_PHASE={DELIVERED_PHASE})"
                    ),
                    strict=False,
                )
            )
        params.append(pytest.param(sc, id=sc.name, marks=marks))
    return params


def _anchored_stems(context) -> List[str]:
    """Stems of context docs the gate actually anchors (score > 0), in order."""
    return [
        Path(str(getattr(d, "path", ""))).stem
        for d in context
        if float(getattr(d, "score", 0) or 0) > 0
    ]


def _run_scenario(rag, scenario: Scenario) -> None:
    import dataclasses

    from aitao.llm.answer_validator import build_grounding_warning, evaluate_grounding
    from aitao.llm.citation_guard import build_multi_source_notice, find_fabricated_citations
    from aitao.llm.context_gate import CLARIFICATION, REFUSAL, evaluate_gate
    from aitao.llm.history_hygiene import clean_history_messages
    from aitao.llm.intent_router import GENERAL, GENERAL_ANSWER_BANNER, classify_intent
    from aitao.llm.reader_appeal import apply_appeal, run_appeal
    from aitao.llm.response_reader import classify_answer
    from aitao.llm.source_attribution import (
        build_attribution_warning,
        check_attribution,
        check_stale_citations,
    )

    raw_history: List[Dict[str, str]] = []

    for i, turn in enumerate(scenario.turns):
        label = f"{scenario.name} turn {i + 1} ({turn.user!r})"
        raw_history.append({"role": "user", "content": turn.user})

        # Mirrors src/api/routes/chat.py: strip AiTao's own notices from past
        # assistant turns before retrieval sees the history, thread the
        # dossier state into the US-103 context gate, and let the gate decide
        # (proceed / targeted refusal / ONE clarification question).
        cleaned = clean_history_messages(raw_history)
        expect = turn.expect

        # US-105 — the committed bench has no LLM, so the interpreter's raw
        # answer is scripted (turn.intent_route), exactly like scripted_answer
        # stands in for the model. The REAL classify_intent still runs on top
        # of that scripted text, so its parsing/fail-open/logging is genuinely
        # exercised, not just echoed. Unset -> "DOCUMENTARY" (the pre-US-105
        # behaviour), so every existing scenario is unaffected.
        raw_token = "GENERAL" if turn.intent_route == "general" else "DOCUMENTARY"
        recent_turns = cleaned[:-1]  # everything before this turn's question
        intent_verdict = classify_intent(
            turn.user, recent_turns=recent_turns, llm_call=lambda _m: raw_token,
            logger=rag.logger, debug=rag.reliability_debug,
        )
        route = intent_verdict.route

        if route == GENERAL:
            # Mirrors src/api/routes/chat.py: Tier 2 retrieval and the context
            # gate are skipped entirely — no document context, no gate verdict.
            context: List = []
            verdict = None
            refused = False
            clarified = False
        else:
            gate_state: Dict = {}
            _, context, _ = rag.enrich_messages(cleaned, dossier_state=gate_state)

            verdict = evaluate_gate(
                turn.user, context, gate_state, messages=cleaned,
                has_session=False, logger=rag.logger, debug=rag.reliability_debug,
            )
            refused = verdict.outcome == REFUSAL
            clarified = verdict.outcome == CLARIFICATION
        stems = _anchored_stems(context)
        top_stem = stems[0] if stems else ""

        if "context_contains" in expect:
            for stem in expect["context_contains"]:
                assert stem in stems, (
                    f"{label}: expected {stem!r} in retrieved context, got {stems}"
                )
        if "context_excludes" in expect:
            for stem in expect["context_excludes"]:
                assert stem not in stems, (
                    f"{label}: expected {stem!r} NOT in retrieved context, got {stems}"
                )
        if "referent_kept" in expect:
            assert top_stem == expect["referent_kept"], (
                f"{label}: expected referent {expect['referent_kept']!r} anchored on "
                f"top, got {top_stem!r} (all anchored: {stems})"
            )
        verdict_outcome = verdict.outcome if verdict is not None else None
        verdict_message = verdict.message if verdict is not None else None
        if "refusal" in expect:
            assert refused == expect["refusal"], (
                f"{label}: expected refusal={expect['refusal']}, "
                f"got {refused} (verdict: {verdict_outcome} {verdict_message!r})"
            )
        if "refusal_contains" in expect:
            for needle in expect["refusal_contains"]:
                assert needle in (verdict_message or ""), (
                    f"{label}: expected the refusal to name {needle!r}, "
                    f"got: {verdict_message!r}"
                )
        # US-103 — zero superfluous questions, asserted BOTH ways on EVERY
        # turn: a scenario that does not opt in with `clarification: true`
        # must never see one (the bench counts both directions, étude §6.5).
        assert clarified == bool(expect.get("clarification", False)), (
            f"{label}: expected clarification={expect.get('clarification', False)}, "
            f"got {clarified} (verdict: {verdict_outcome} {verdict_message!r})"
        )
        if "clarification_names" in expect:
            for stem in expect["clarification_names"]:
                assert stem in (verdict_message or ""), (
                    f"{label}: expected the clarification question to name "
                    f"{stem!r}, got: {verdict_message!r}"
                )
        if "multi_source_note" in expect:
            notice = build_multi_source_notice(
                turn.user, context, bearer_counter=rag.count_token_bearers
            )
            assert bool(notice) == expect["multi_source_note"], (
                f"{label}: expected multi_source_note={expect['multi_source_note']}, "
                f"got notice={notice!r}"
            )
        # US-105 — zero wrongful "general" routing, asserted BOTH ways on
        # EVERY turn (mirrors the clarification guard above): a scenario that
        # does not opt in with `intent_route: general` must always route
        # documentary — the real classify_intent ran on the scripted raw
        # token above, this checks it parsed/decided correctly.
        assert (route == GENERAL) == (turn.intent_route == "general"), (
            f"{label}: expected route={'general' if turn.intent_route == 'general' else 'documentary'}, "
            f"got {route} (reason: {intent_verdict.reason})"
        )
        if "route" in expect:
            assert route == expect["route"], (
                f"{label}: expected route={expect['route']!r}, got {route!r}"
            )
        if "context_empty" in expect:
            assert (len(context) == 0) == expect["context_empty"], (
                f"{label}: expected context_empty={expect['context_empty']}, "
                f"got {len(context)} context doc(s)"
            )

        # Build the assistant turn exactly as the route would: a "general"
        # route or a refusal/clarification short-circuits before generation
        # (no citation/grounding notices) — the scripted answer stands in for
        # the model in every case, prefixed with the US-105 banner when
        # general — so history threading (never-two-in-a-row, banner
        # stripping) is exercised exactly as the API does it.
        if route == GENERAL:
            assistant_content = GENERAL_ANSWER_BANNER + (turn.scripted_answer or "")
        elif not verdict.proceed:
            assistant_content = verdict.message or ""
        else:
            answer = turn.scripted_answer or ""
            notice = ""
            banner = ""
            if answer:
                notice = build_multi_source_notice(
                    turn.user, context, bearer_counter=rag.count_token_bearers
                )

                # US-104 — roles computed once (rules only), reused by both
                # the grounding check and the attribution check, exactly like
                # chat_grounding.grounding_trailer.
                roles = classify_answer(answer, context)
                report = evaluate_grounding(answer, context, rag.embed_texts, roles=roles)

                # US-104 part C — reader appeal, scripted per turn exactly
                # like scripted_answer/intent_route: the REAL parse/decide
                # code (run_appeal/apply_appeal) runs on the scripted raw
                # text. Unset -> llm_call is None, the real fail-open path
                # runs, and the flagged sentences are asserted UNCHANGED —
                # zero wrongful unflagging, mirrors the clarification/route
                # guards above, asserted on every turn.
                pre_appeal_unsupported = list(report.unsupported)
                reader_llm_call = (
                    (lambda _m, _raw=turn.reader_appeal: _raw)
                    if turn.reader_appeal else None
                )
                appeal_verdicts = run_appeal(report.unsupported, reader_llm_call)
                if appeal_verdicts:
                    kept = apply_appeal(report.unsupported, appeal_verdicts)
                    report = dataclasses.replace(report, unsupported=kept)
                if turn.reader_appeal is None:
                    assert report.unsupported == pre_appeal_unsupported, (
                        f"{label}: reader appeal changed the flagged sentences "
                        "with no scripted verdict (llm_call=None must never "
                        "unflag)"
                    )

                grounding_banner = build_grounding_warning(report)

                # US-104 part B — attribution check, ALWAYS active
                # (independent of verify_answer / the grounding banner).
                attribution_flags = check_attribution(roles, context, rag.embed_texts)

                # I-15 — stale-citation liaison, exactly like the trailer's
                # run_stale_citation_check: only runs when G2 (citation_guard)
                # actually found a citation absent from this turn's context;
                # an empty fabricated list is a strict no-op (sain_*/i104
                # scenarios must see zero behaviour change).
                fabricated = find_fabricated_citations(answer, context)
                if fabricated:
                    attribution_flags = attribution_flags + check_stale_citations(
                        roles, fabricated, context, rag.embed_texts
                    )
                attribution_banner = build_attribution_warning(attribution_flags)

                banner = grounding_banner + attribution_banner

                if "banner" in expect:
                    banner_present = bool(banner)
                    expected_present = expect["banner"] == "present"
                    assert banner_present == expected_present, (
                        f"{label}: expected banner={expect['banner']!r}, got "
                        f"{'present' if banner_present else 'absent'} ({banner!r})"
                    )
                if "banner_contains" in expect:
                    for needle in expect["banner_contains"]:
                        assert needle in banner, (
                            f"{label}: expected the banner to contain "
                            f"{needle!r}, got: {banner!r}"
                        )
                if "cited_source" in expect:
                    stem = expect["cited_source"]
                    assert stem in stems, (
                        f"{label}: cited source {stem!r} was not retrieved "
                        f"(anchored: {stems})"
                    )
                    assert stem.lower() in answer.lower(), (
                        f"{label}: expected the scripted answer to cite {stem!r}, "
                        f"got: {answer!r}"
                    )
            assistant_content = answer + notice + banner

        if "info_banner" in expect:
            has_info = assistant_content.startswith(GENERAL_ANSWER_BANNER)
            assert has_info == expect["info_banner"], (
                f"{label}: expected info_banner={expect['info_banner']}, "
                f"got {'present' if has_info else 'absent'}"
            )

        raw_history.append({"role": "assistant", "content": assistant_content})


class TestGoldenConversations:
    @pytest.mark.parametrize("scenario", _scenario_params())
    def test_scenario(self, golden_rag, scenario: Scenario):
        _run_scenario(golden_rag, scenario)
