# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the conversation dossier (US-102, ÉPIC-30 brique 1).

Covers the pure-logic surface of llm/conversation_dossier.py: turn
classification (anaphor / ordinal / short-question / new), the sequential
session replay (``build_dossier``), the live decision (``decide``), and the
combined entry point (``resolve_dossier``) — all with fake resolver callbacks,
no real search engine involved (module has zero I/O by design).
"""

from typing import List, Optional

from aitao.llm.conversation_dossier import (
    ACTION_HOLD,
    ACTION_NONE,
    ACTION_RECALL,
    FOLLOWUP,
    NEW,
    ORDINAL_FIRST,
    ORDINAL_LAST,
    DossierEntry,
    build_dossier,
    classify_turn,
    decide,
    resolve_dossier,
)

ZH_GLASS = DossierEntry(path="zh_glass.md", title="百年淬鍊：範例玻璃股份有限公司")
FR_BAIL = DossierEntry(path="fr_bail.md", title="Contrat de location (bail)")
FR_BAROMETRE = DossierEntry(path="fr_barometre.md", title="Baromètre annuel de satisfaction")


class _FakeLogger:
    """Records calls instead of writing anywhere — for traceability assertions."""

    def __init__(self):
        self.info_calls: List[tuple] = []
        self.debug_calls: List[tuple] = []

    def info(self, message, metadata=None):
        self.info_calls.append((message, metadata))

    def debug(self, message, metadata=None):
        self.debug_calls.append((message, metadata))


# ---------------------------------------------------------------------------
# classify_turn — cheap text-only rule table
# ---------------------------------------------------------------------------

class TestClassifyTurn:
    def test_anaphor_il(self):
        assert classify_turn("Combien de pages contient-il ?") == FOLLOWUP

    def test_anaphor_ce_document(self):
        assert classify_turn("Traduis ce document en français.") == FOLLOWUP

    def test_anaphor_chinese_simplified(self):
        assert classify_turn("这个文件有多少页？") == FOLLOWUP

    def test_anaphor_chinese_traditional(self):
        assert classify_turn("這個文件有多少頁？") == FOLLOWUP

    def test_ordinal_first_french(self):
        assert classify_turn("Reviens au premier document, quel est son titre ?") == ORDINAL_FIRST

    def test_ordinal_last_french(self):
        assert classify_turn("Et le dernier document, il parle de quoi ?") == ORDINAL_LAST

    def test_ordinal_first_chinese(self):
        assert classify_turn("回到第一個文件") == ORDINAL_FIRST

    def test_ordinal_checked_before_anaphor(self):
        # Contains both an ordinal cue and a short question — ordinal wins.
        assert classify_turn("Le premier document, il dit quoi ?") == ORDINAL_FIRST

    def test_short_question_without_anaphor(self):
        assert classify_turn("Combien coûte-t-elle ?") == FOLLOWUP  # "elle" is also an anaphor
        assert classify_turn("Et le prix ?") == FOLLOWUP  # short + "?"

    def test_word_boundary_guards_against_false_positive(self):
        # "il" must not fire inside another word (e.g. a country name).
        assert classify_turn("Ce fournisseur est basé au Brésil.") == NEW

    def test_plain_new_topic_question(self):
        text = "Quel est le loyer mensuel du bail de Jean Dupont ?"
        assert classify_turn(text) == NEW

    def test_statement_no_question_mark_no_cue(self):
        assert classify_turn("Le loyer mensuel est de 1250 euros.") == NEW

    def test_empty_text(self):
        assert classify_turn("") == NEW

    # Impersonal French "il" (orchestrator review fix): existential forms are
    # NOT anaphors — only true verb-subject inversion holds the referent.
    def test_impersonal_y_a_t_il_is_new_topic(self):
        text = "Y a-t-il un document qui parle du loyer mensuel ?"
        assert classify_turn(text) == NEW

    def test_inversion_contient_il_stays_followup(self):
        # I-11 itself — must never be caught by the impersonal-il stripping.
        assert classify_turn("Combien de pages contient-il ?") == FOLLOWUP

    def test_short_impersonal_question_stays_contextual_followup(self):
        # "il y a" is stripped for the anaphor match, but the SHORT-question
        # rule still sees the original text: a brief contextual question keeps
        # behaving as a follow-up (documented interplay, not an accident).
        assert classify_turn("Il y a combien d'employés ?") == FOLLOWUP

    def test_sil_te_plait_still_followup_via_ce_document(self):
        text = "S'il te plaît, résume ce document"
        assert classify_turn(text) == FOLLOWUP


# ---------------------------------------------------------------------------
# build_dossier — sequential full-session replay
# ---------------------------------------------------------------------------

def _resolvers(named_map, fallback_map, fallback_calls=None):
    """Fake resolver pair: exact-substring lookup tables keyed by marker word."""

    def resolve_named(text: str) -> Optional[DossierEntry]:
        for marker, entry in named_map.items():
            if marker in text:
                return entry
        return None

    def resolve_fallback(text: str) -> Optional[DossierEntry]:
        if fallback_calls is not None:
            fallback_calls.append(text)
        for marker, entry in fallback_map.items():
            if marker in text:
                return entry
        return None

    return resolve_named, resolve_fallback


class TestBuildDossier:
    def test_empty_history_yields_empty_stack(self):
        resolve_named, resolve_fallback = _resolvers({}, {})
        assert build_dossier([], resolve_named, resolve_fallback) == []

    def test_named_turn_becomes_referent(self):
        resolve_named, resolve_fallback = _resolvers({"範例玻璃": ZH_GLASS}, {})
        stack = build_dossier(
            ["Quel document parle de 範例玻璃 ?"], resolve_named, resolve_fallback
        )
        assert stack == [ZH_GLASS]

    def test_anaphoric_turn_never_calls_retrieval_fallback(self):
        # The whole point of I-11: an anaphoric turn must NOT be re-run
        # through retrieval (a "poisoned" fallback would return the wrong
        # doc if it were ever invoked on this turn's own words).
        calls: List[str] = []
        resolve_named, resolve_fallback = _resolvers(
            {"範例玻璃": ZH_GLASS}, {"pages": FR_BAROMETRE}, fallback_calls=calls
        )
        stack = build_dossier(
            ["Quel document parle de 範例玻璃 ?", "Combien de pages contient-il ?"],
            resolve_named,
            resolve_fallback,
        )
        assert stack == [ZH_GLASS]  # the follow-up contributed nothing new
        assert calls == []  # fallback never ran on the anaphoric turn

    def test_new_topic_uses_retrieval_fallback(self):
        resolve_named, resolve_fallback = _resolvers({}, {"bail": FR_BAIL})
        stack = build_dossier(
            ["Quel est le loyer mensuel du bail de Jean Dupont ?"],
            resolve_named,
            resolve_fallback,
        )
        assert stack == [FR_BAIL]

    def test_aside_then_return_full_session(self):
        """Mirrors the 'fil_de_session' golden scenario shape (étude §6.6)."""
        resolve_named, resolve_fallback = _resolvers(
            {"範例玻璃": ZH_GLASS}, {"bail": FR_BAIL}
        )
        past_turns = [
            "Quel document parle de 範例玻璃 ?",  # named -> zh_glass
            "Combien de pages contient-il ?",  # follow-up -> held, no new entry
            "Quel est le loyer mensuel du bail de Jean Dupont ?",  # aside -> fr_bail
        ]
        stack = build_dossier(past_turns, resolve_named, resolve_fallback)
        # Most recent first: the aside is on top, but the original referent
        # survives further down the stack (available to an ordinal recall).
        assert stack == [FR_BAIL, ZH_GLASS]

    def test_ordinal_turn_reorders_without_new_entry(self):
        resolve_named, resolve_fallback = _resolvers(
            {"範例玻璃": ZH_GLASS}, {"bail": FR_BAIL}
        )
        past_turns = [
            "Quel document parle de 範例玻璃 ?",
            "Quel est le loyer mensuel du bail de Jean Dupont ?",
            "Reviens au premier document.",
        ]
        stack = build_dossier(past_turns, resolve_named, resolve_fallback)
        # The ordinal turn recalled zh_glass to the front; nothing new added.
        assert stack == [ZH_GLASS, FR_BAIL]

    def test_fallback_returning_none_leaves_stack_unchanged(self):
        resolve_named, resolve_fallback = _resolvers({"範例玻璃": ZH_GLASS}, {})
        stack = build_dossier(
            [
                "Quel document parle de 範例玻璃 ?",
                "Quelle est la météo aujourd'hui ?",  # resolves nothing
            ],
            resolve_named,
            resolve_fallback,
        )
        assert stack == [ZH_GLASS]


# ---------------------------------------------------------------------------
# decide — the live turn's action
# ---------------------------------------------------------------------------

class TestDecide:
    def test_new_name_releases_referent(self):
        decision = decide("Traduis maintenant X.pdf", True, [ZH_GLASS])
        assert decision.action == ACTION_NONE
        assert decision.referent is None
        assert "released" in decision.reason

    def test_followup_holds_top_of_stack(self):
        decision = decide("Combien de pages contient-il ?", False, [ZH_GLASS])
        assert decision.action == ACTION_HOLD
        assert decision.referent == ZH_GLASS
        assert "held" in decision.reason

    def test_followup_with_empty_stack_is_a_noop(self):
        """No-referent session: nothing to hold, must not fabricate one."""
        decision = decide("Combien de pages contient-il ?", False, [])
        assert decision.action == ACTION_NONE
        assert decision.referent is None

    def test_ordinal_first_recalls_oldest(self):
        decision = decide("Reviens au premier document.", False, [FR_BAIL, ZH_GLASS])
        assert decision.action == ACTION_RECALL
        assert decision.referent == ZH_GLASS

    def test_ordinal_last_recalls_most_recent(self):
        decision = decide("Et le dernier document ?", False, [FR_BAIL, ZH_GLASS])
        assert decision.action == ACTION_RECALL
        assert decision.referent == FR_BAIL

    def test_ambiguous_ordinal_with_empty_stack_is_a_noop(self):
        decision = decide("Reviens au premier document.", False, [])
        assert decision.action == ACTION_NONE
        assert decision.referent is None
        assert "no referent yet" in decision.reason

    def test_new_topic_with_no_signal_is_a_noop(self):
        decision = decide(
            "Quel est le loyer mensuel du bail de Jean Dupont ?", False, [ZH_GLASS]
        )
        assert decision.action == ACTION_NONE
        assert decision.referent is None
        assert "new topic" in decision.reason


# ---------------------------------------------------------------------------
# resolve_dossier — the combined entry point + traceability (5th invariant)
# ---------------------------------------------------------------------------

class TestResolveDossier:
    def test_full_i11_scenario(self):
        resolve_named, resolve_fallback = _resolvers({"範例玻璃": ZH_GLASS}, {})
        decision = resolve_dossier(
            ["Quel document parle de 範例玻璃 ?"],
            "Combien de pages contient-il ?",
            False,
            resolve_named,
            resolve_fallback,
        )
        assert decision.action == ACTION_HOLD
        assert decision.referent == ZH_GLASS

    def test_full_fil_de_session_scenario(self):
        resolve_named, resolve_fallback = _resolvers(
            {"範例玻璃": ZH_GLASS}, {"bail": FR_BAIL}
        )
        past_turns = [
            "Quel document parle de 範例玻璃 ?",
            "Combien de pages contient-il ?",
            "Quel est le loyer mensuel du bail de Jean Dupont ?",
        ]
        decision = resolve_dossier(
            past_turns,
            "Reviens au premier document, quel est son titre ?",
            False,
            resolve_named,
            resolve_fallback,
        )
        assert decision.action == ACTION_RECALL
        assert decision.referent == ZH_GLASS  # the FIRST referent, not the aside

    def test_logs_at_info_when_debug_true(self):
        logger = _FakeLogger()
        resolve_named, resolve_fallback = _resolvers({"範例玻璃": ZH_GLASS}, {})
        resolve_dossier(
            ["Quel document parle de 範例玻璃 ?"],
            "Combien de pages contient-il ?",
            False,
            resolve_named,
            resolve_fallback,
            logger=logger,
            debug=True,
        )
        assert logger.info_calls  # traceable at INFO during rollout
        assert not logger.debug_calls

    def test_logs_at_debug_when_debug_false(self):
        logger = _FakeLogger()
        resolve_named, resolve_fallback = _resolvers({"範例玻璃": ZH_GLASS}, {})
        resolve_dossier(
            ["Quel document parle de 範例玻璃 ?"],
            "Combien de pages contient-il ?",
            False,
            resolve_named,
            resolve_fallback,
            logger=logger,
            debug=False,
        )
        # Never fully silenced (5th invariant) — just demoted to DEBUG.
        assert logger.debug_calls
        assert not logger.info_calls

    def test_works_without_a_logger(self):
        resolve_named, resolve_fallback = _resolvers({}, {})
        decision = resolve_dossier([], "Bonjour", False, resolve_named, resolve_fallback)
        assert decision.action == ACTION_NONE
