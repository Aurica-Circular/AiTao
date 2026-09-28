# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/golden/scenario_loader.py — YAML scenario schema + loader (US-101).
#
# Parses and validates the multi-turn golden conversation scenarios consumed by
# tests/integration/test_golden_conversations.py (deterministic, committed
# corpus) and tests/e2e/test_golden_live.py (live, real stack). Fails loudly
# (ScenarioError) on any unknown key or malformed value so a typo in a
# scenario file is caught at collection time, not silently ignored.
#
# Schema (see ETUDE-FIABILITE.md §8 for the rationale):
#   name, description, incident, phase (0-3), corpus ("committed" | "live")
#   turns: [{ user, scripted_answer?, intent_route?, expect? }]
#   intent_route: "documentary" | "general" (US-105) — stands in for the
#     intent router's verdict on this turn, exactly like scripted_answer
#     stands in for the model: the committed bench has no LLM, so the
#     interpreter's decision is scripted per turn. Omitted -> "documentary"
#     (the pre-US-105 behaviour), so every existing scenario loads and behaves
#     unchanged with zero edits.
#   reader_appeal: raw text (US-104) standing in for the reader-appeal LLM's
#     response when the deterministic grounding check is about to flag a
#     sentence this turn (e.g. "1: HABILLAGE"), exactly like scripted_answer
#     stands in for the model. Omitted (the default for every pre-US-104
#     scenario) -> the appeal's llm_call is None, so the real fail-open path
#     runs and the deterministic flags are never touched.
#   expect keys (all optional — assert only what is present):
#     context_contains, context_excludes: [file stem, ...]
#     cited_source: file stem
#     refusal: bool
#     refusal_contains: [substring, ...]  (requires refusal: true — a US-103
#                       targeted refusal must name what is missing)
#     banner: "absent" | "present"
#     multi_source_note: bool
#     referent_kept: file stem
#     clarification: bool                 (US-103 gate outcome 3; the runner
#                       ALSO asserts no clarification on every turn that does
#                       not set it — zero superfluous questions, both ways)
#     clarification_names: [file stem, ...] (requires clarification: true —
#                       the question must name these candidates)
#     route: "documentary" | "general"    (US-105 — the route actually
#                       applied this turn; the runner ALSO asserts
#                       route == "documentary" on every turn that does not
#                       set intent_route: "general" — zero wrongful "general"
#                       routing, both ways, mirroring the clarification guard)
#     context_empty: bool                 (US-105 — no document context at all
#                       was retrieved/injected this turn)
#     info_banner: bool                   (US-105 — the "ℹ️ Réponse générale"
#                       banner is present/absent in the rendered answer)
#     banner_contains: [substring, ...]   (US-104 — requires 'banner: present'
#                       — the reliability banner (grounding + attribution) must
#                       name these substrings, e.g. the probable source of a
#                       wrong-attribution corrective banner)

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_SCENARIO_KEYS = {"name", "description", "incident", "phase", "corpus", "turns"}
_TURN_KEYS = {"user", "scripted_answer", "intent_route", "reader_appeal", "expect"}
_EXPECT_KEYS = {
    "context_contains",
    "context_excludes",
    "cited_source",
    "refusal",
    "refusal_contains",
    "banner",
    "banner_contains",
    "multi_source_note",
    "referent_kept",
    "clarification",
    "clarification_names",
    "route",
    "context_empty",
    "info_banner",
}
_VALID_CORPUS = {"committed", "live"}
_VALID_BANNER = {"absent", "present"}
_VALID_ROUTE = {"documentary", "general"}
# expect keys that only make sense when the turn actually generates/grades an
# answer — a scenario asking for these without a scripted_answer is malformed
# (there is nothing to grade), not merely under-specified.
_ANSWER_EXPECT_KEYS = {"banner", "banner_contains", "cited_source"}


class ScenarioError(ValueError):
    """A scenario YAML file violates the schema above."""


@dataclass(frozen=True)
class ScenarioTurn:
    user: str
    scripted_answer: Optional[str] = None
    intent_route: Optional[str] = None  # US-105: scripted router verdict
    reader_appeal: Optional[str] = None  # US-104: scripted appeal raw text
    expect: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    incident: str
    phase: int
    corpus: str
    turns: List[ScenarioTurn]
    source_path: Path


def _fail(path: Path, msg: str) -> None:
    raise ScenarioError(f"{path.name}: {msg}")


def _validate_expect(path: Path, turn_idx: int, expect: Any, has_answer: bool) -> Dict[str, Any]:
    if expect is None:
        return {}
    if not isinstance(expect, dict):
        _fail(path, f"turn {turn_idx}: 'expect' must be a mapping")
    unknown = set(expect) - _EXPECT_KEYS
    if unknown:
        _fail(path, f"turn {turn_idx}: unknown expect key(s) {sorted(unknown)}")
    if "banner" in expect and expect["banner"] not in _VALID_BANNER:
        _fail(path, f"turn {turn_idx}: banner must be one of {sorted(_VALID_BANNER)}")
    if "route" in expect and expect["route"] not in _VALID_ROUTE:
        _fail(path, f"turn {turn_idx}: route must be one of {sorted(_VALID_ROUTE)}")
    if "context_empty" in expect and not isinstance(expect["context_empty"], bool):
        _fail(path, f"turn {turn_idx}: 'context_empty' must be a bool")
    if "info_banner" in expect and not isinstance(expect["info_banner"], bool):
        _fail(path, f"turn {turn_idx}: 'info_banner' must be a bool")
    if "clarification" in expect and not isinstance(expect["clarification"], bool):
        _fail(path, f"turn {turn_idx}: 'clarification' must be a bool")
    if "clarification_names" in expect:
        names = expect["clarification_names"]
        if not isinstance(names, list) or not names or not all(
            isinstance(n, str) and n for n in names
        ):
            _fail(
                path,
                f"turn {turn_idx}: 'clarification_names' must be a non-empty "
                "list of strings",
            )
        if expect.get("clarification") is not True:
            _fail(
                path,
                f"turn {turn_idx}: 'clarification_names' requires "
                "'clarification: true' (there is no question to name otherwise)",
            )
    if "banner_contains" in expect:
        subs = expect["banner_contains"]
        if not isinstance(subs, list) or not subs or not all(
            isinstance(s, str) and s for s in subs
        ):
            _fail(
                path,
                f"turn {turn_idx}: 'banner_contains' must be a non-empty "
                "list of strings",
            )
        if expect.get("banner") != "present":
            _fail(
                path,
                f"turn {turn_idx}: 'banner_contains' requires 'banner: "
                "present' (there is no banner to check otherwise)",
            )
    if "refusal_contains" in expect:
        subs = expect["refusal_contains"]
        if not isinstance(subs, list) or not subs or not all(
            isinstance(s, str) and s for s in subs
        ):
            _fail(
                path,
                f"turn {turn_idx}: 'refusal_contains' must be a non-empty "
                "list of strings",
            )
        if expect.get("refusal") is not True:
            _fail(
                path,
                f"turn {turn_idx}: 'refusal_contains' requires 'refusal: true' "
                "(there is no refusal message to check otherwise)",
            )
    if not has_answer:
        needing_answer = _ANSWER_EXPECT_KEYS & set(expect)
        if needing_answer:
            _fail(
                path,
                f"turn {turn_idx}: expect key(s) {sorted(needing_answer)} require a "
                f"'scripted_answer' to grade (there is nothing to check without one)",
            )
    return expect


def load_scenario(path: Path) -> Scenario:
    """Parse one scenario YAML file. Raises ScenarioError on any schema violation."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        _fail(path, "top-level YAML must be a mapping")
    unknown = set(raw) - _SCENARIO_KEYS
    if unknown:
        _fail(path, f"unknown top-level key(s) {sorted(unknown)}")
    for required in ("name", "phase", "corpus", "turns"):
        if required not in raw:
            _fail(path, f"missing required key '{required}'")

    corpus = raw["corpus"]
    if corpus not in _VALID_CORPUS:
        _fail(path, f"corpus must be one of {sorted(_VALID_CORPUS)}, got {corpus!r}")

    phase = raw["phase"]
    if not isinstance(phase, int) or isinstance(phase, bool) or not (0 <= phase <= 3):
        _fail(path, f"phase must be an int 0-3, got {phase!r}")

    raw_turns = raw["turns"]
    if not isinstance(raw_turns, list) or not raw_turns:
        _fail(path, "'turns' must be a non-empty list")

    turns: List[ScenarioTurn] = []
    for i, rt in enumerate(raw_turns):
        if not isinstance(rt, dict):
            _fail(path, f"turn {i}: must be a mapping")
        unknown_turn = set(rt) - _TURN_KEYS
        if unknown_turn:
            _fail(path, f"turn {i}: unknown key(s) {sorted(unknown_turn)}")
        if not rt.get("user"):
            _fail(path, f"turn {i}: missing required 'user'")
        scripted_answer = rt.get("scripted_answer")
        intent_route = rt.get("intent_route")
        if intent_route is not None and intent_route not in _VALID_ROUTE:
            _fail(
                path,
                f"turn {i}: 'intent_route' must be one of {sorted(_VALID_ROUTE)}, "
                f"got {intent_route!r}",
            )
        reader_appeal = rt.get("reader_appeal")
        if reader_appeal is not None and not isinstance(reader_appeal, str):
            _fail(path, f"turn {i}: 'reader_appeal' must be a string")
        expect = _validate_expect(path, i, rt.get("expect"), has_answer=bool(scripted_answer))
        turns.append(
            ScenarioTurn(
                user=rt["user"], scripted_answer=scripted_answer,
                intent_route=intent_route, reader_appeal=reader_appeal,
                expect=expect,
            )
        )

    return Scenario(
        name=raw["name"],
        description=str(raw.get("description", "")),
        incident=str(raw.get("incident", "")),
        phase=phase,
        corpus=corpus,
        turns=turns,
        source_path=path,
    )


def load_all_scenarios(directory: Path, corpus: Optional[str] = None) -> List[Scenario]:
    """Load every ``*.yaml`` scenario in ``directory``, sorted by filename.

    ``corpus`` optionally filters to "committed" or "live" scenarios only.
    """
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]
    if corpus is not None:
        scenarios = [s for s in scenarios if s.corpus == corpus]
    return scenarios
