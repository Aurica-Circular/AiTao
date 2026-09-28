# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# test_golden_live.py — US-101 live golden bench (thin, opt-in field check).
#
# Replays ``corpus: live`` scenarios (tests/fixtures/golden_scenarios/) against
# the REAL running stack (API on :8200, real Meilisearch index, real LLM) —
# mirrors tests/e2e/test_behavior_eval.py (US-17d). Unlike the deterministic
# runner (tests/integration/test_golden_conversations.py — the actual merge
# gate), this one hits a real, non-reproducible model; assertions therefore
# stay on AiTao's deterministic invariants exposed by the API response
# (rag_context paths, the fixed notice/banner markers), never on the model's
# free prose.
#
# Run manually before a release, against Phil's real stack:
#
#   ~/.local/share/venvs/aitao/bin/python -m pytest \
#       tests/e2e/test_golden_live.py -v -m golden_live
#
# Skips cleanly (collection-time skipif) when the API is not reachable — never
# part of the fast release gate or the deterministic golden CI job.

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))  # tests/ root -> `golden` package

from golden.phase import DELIVERED_PHASE  # noqa: E402
from golden.scenario_loader import Scenario, load_all_scenarios  # noqa: E402

API_URL = os.environ.get("AITAO_EVAL_API", "http://localhost:8200")
# LLM calls through a real local model can take minutes (mirrors test_behavior_eval.py)
REQUEST_TIMEOUT = 360.0
_SCENARIOS_DIR = Path(__file__).parent.parent / "fixtures" / "golden_scenarios"


def _api_available() -> bool:
    try:
        return httpx.get(f"{API_URL}/api/health", timeout=3.0).status_code == 200
    except Exception:
        return False


def _eval_model() -> str:
    if os.environ.get("AITAO_EVAL_MODEL"):
        return os.environ["AITAO_EVAL_MODEL"]
    from aitao.core.config import get_config

    return get_config().llm.default_model


pytestmark = [
    pytest.mark.golden_live,
    pytest.mark.skipif(
        not _api_available(),
        reason=f"AiTao API not running at {API_URL} — start with ./aitao.sh start",
    ),
]


def _live_scenarios() -> List[Scenario]:
    return load_all_scenarios(_SCENARIOS_DIR, corpus="live")


def _scenario_params():
    params = []
    for sc in _live_scenarios():
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


def _chat(history: List[Dict[str, str]]) -> Dict[str, Any]:
    """One accumulated-history call to the OpenAI-compatible endpoint, RAG on."""
    response = httpx.post(
        f"{API_URL}/v1/chat/completions",
        json={
            "model": _eval_model(),
            "messages": history,
            "stream": False,
            "aitao": {"rag": True},
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    return {
        "content": choice.get("message", {}).get("content", ""),
        "rag_context": data.get("rag_context") or [],
    }


def _paths(rag_context: List[Dict[str, Any]]) -> List[str]:
    return [str(d.get("path", "")) for d in rag_context]


@pytest.mark.parametrize("scenario", _scenario_params())
def test_live_scenario(scenario: Scenario):
    from aitao.llm.context_adequacy import REFUSAL_MESSAGE

    history: List[Dict[str, str]] = []
    for i, turn in enumerate(scenario.turns):
        history.append({"role": "user", "content": turn.user})
        result = _chat(history)
        content = result["content"]
        paths = _paths(result["rag_context"])
        expect = turn.expect
        label = f"{scenario.name} turn {i + 1} ({turn.user!r})"

        if "context_contains" in expect:
            for needle in expect["context_contains"]:
                assert any(needle in p for p in paths), (
                    f"{label}: expected a retrieved path containing {needle!r}, "
                    f"got {paths}"
                )
        if "context_excludes" in expect:
            for needle in expect["context_excludes"]:
                assert not any(needle in p for p in paths), (
                    f"{label}: expected no retrieved path containing {needle!r}, "
                    f"got {paths}"
                )
        if "referent_kept" in expect:
            needle = expect["referent_kept"]
            assert paths and needle in paths[0], (
                f"{label}: expected referent {needle!r} anchored first, got {paths}"
            )
        if "refusal" in expect:
            assert (REFUSAL_MESSAGE in content) == expect["refusal"], (
                f"{label}: expected refusal={expect['refusal']}, content={content!r}"
            )
        if "banner" in expect:
            has_banner = "⚠️ Fiabilité" in content
            assert has_banner == (expect["banner"] == "present"), (
                f"{label}: expected banner={expect['banner']!r}, content={content!r}"
            )
        if "multi_source_note" in expect:
            assert ("📎" in content) == expect["multi_source_note"], (
                f"{label}: expected multi_source_note={expect['multi_source_note']}, "
                f"content={content!r}"
            )
        if "cited_source" in expect:
            assert expect["cited_source"] in content, (
                f"{label}: expected citation of {expect['cited_source']!r}, "
                f"content={content!r}"
            )
        # US-103 — a clarification question is a fixed AiTao message starting
        # with the stable CLARIFICATION_PREFIX marker (never model prose).
        if "clarification" in expect:
            from aitao.llm.context_gate import CLARIFICATION_PREFIX

            asked = content.startswith(CLARIFICATION_PREFIX)
            assert asked == expect["clarification"], (
                f"{label}: expected clarification={expect['clarification']}, "
                f"content={content!r}"
            )
        if "clarification_names" in expect:
            for needle in expect["clarification_names"]:
                assert needle in content, (
                    f"{label}: expected the clarification to name {needle!r}, "
                    f"content={content!r}"
                )
        if "refusal_contains" in expect:
            for needle in expect["refusal_contains"]:
                assert needle in content, (
                    f"{label}: expected the refusal to name {needle!r}, "
                    f"content={content!r}"
                )
        # US-105 — intent router "general" route: no document context at all,
        # and the honest banner is present in the rendered answer.
        if "context_empty" in expect:
            assert (len(paths) == 0) == expect["context_empty"], (
                f"{label}: expected context_empty={expect['context_empty']}, "
                f"got {len(paths)} retrieved path(s): {paths}"
            )
        if "info_banner" in expect:
            from aitao.llm.intent_router import GENERAL_ANSWER_BANNER

            has_info = content.startswith(GENERAL_ANSWER_BANNER)
            assert has_info == expect["info_banner"], (
                f"{label}: expected info_banner={expect['info_banner']}, "
                f"content={content!r}"
            )

        history.append({"role": "assistant", "content": content})
