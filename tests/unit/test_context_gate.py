# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the upstream context gate (US-103, ÉPIC-30 brique 2).

Covers the three outcomes (proceed / targeted refusal / clarification) and
every guardrail: refusal only on verbatim proof (never on a score), no
token-absence refusal on a held follow-up (the "quel est son titre ?" case),
clarification only on deterministic proof of ambiguity (two candidates in
conflict, or a follow-up with nothing to hold), never two clarifications in
a row (stateless, from history), candidates always named. Pure logic — the
dossier state dict is hand-built exactly as conversation_dossier_hook fills
it; no search engine, no LLM.
"""

from types import SimpleNamespace
from typing import List

from aitao.llm.context_adequacy import REFUSAL_MESSAGE
from aitao.llm.context_gate import (
    CLARIFICATION,
    CLARIFICATION_PREFIX,
    PROCEED,
    REFUSAL,
    evaluate_gate,
    previous_turn_was_clarification,
)
from aitao.llm.conversation_dossier import (
    ACTION_HOLD,
    ACTION_NONE,
    ACTION_RECALL,
    FOLLOWUP,
    NEW,
)

BAIL = {"path": "/docs/fr_bail.md", "title": "Contrat de location (bail)"}
GLASS = {"path": "/docs/zh_glass.md", "title": "百年淬鍊：範例玻璃股份有限公司"}


def _doc(path="/docs/fr_bail.md", content="Le bail fixe le loyer mensuel à 1250 EUR.",
         title="Contrat de location", score=1.0):
    return SimpleNamespace(path=path, title=title, content=content, score=score)


def _hold_state(referent=BAIL, stack=None, anchored=None, turn_kind=FOLLOWUP):
    return {
        "action": ACTION_HOLD,
        "turn_kind": turn_kind,
        "referent": referent,
        "stack": stack if stack is not None else [BAIL, GLASS],
        "anchored_paths": anchored,
    }


class _FakeLogger:
    def __init__(self):
        self.info_calls: List[tuple] = []
        self.debug_calls: List[tuple] = []

    def info(self, message, metadata=None):
        self.info_calls.append((message, metadata))

    def debug(self, message, metadata=None):
        self.debug_calls.append((message, metadata))


# ---------------------------------------------------------------------------
# previous_turn_was_clarification — the stateless "never two in a row" probe
# ---------------------------------------------------------------------------

class TestPreviousTurnWasClarification:
    def test_last_assistant_turn_is_clarification(self):
        messages = [
            {"role": "user", "content": "Combien de pages contient-il ?"},
            {"role": "assistant", "content": f"{CLARIFICATION_PREFIX}Tu parles de X ou Y ?"},
            {"role": "user", "content": "它的營業額是多少？"},
        ]
        assert previous_turn_was_clarification(messages) is True

    def test_clarification_two_turns_ago_does_not_suppress(self):
        # A normal answer in between resets the guardrail: only the LAST
        # assistant turn counts ("never two IN A ROW", not "once per session").
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": f"{CLARIFICATION_PREFIX}X ou Y ?"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "Réponse normale."},
            {"role": "user", "content": "q3"},
        ]
        assert previous_turn_was_clarification(messages) is False

    def test_no_assistant_turn(self):
        assert previous_turn_was_clarification([{"role": "user", "content": "q"}]) is False

    def test_none_messages(self):
        assert previous_turn_was_clarification(None) is False


# ---------------------------------------------------------------------------
# Outcome 1 — proceed
# ---------------------------------------------------------------------------

class TestProceed:
    def test_ordinary_question_with_matching_context(self):
        verdict = evaluate_gate(
            "Quel est le loyer mensuel du bail ?",
            [_doc()],  # contains "loyer", "mensuel", "bail"
        )
        assert verdict.outcome == PROCEED
        assert verdict.proceed is True
        assert verdict.message is None

    def test_config_question_never_gated(self):
        verdict = evaluate_gate("qui es-tu ?", [])
        assert verdict.outcome == PROCEED

    def test_small_talk_never_gated(self):
        verdict = evaluate_gate("Bonjour, ça va ?", [])
        assert verdict.outcome == PROCEED

    def test_empty_dossier_state_degrades_to_adequacy_semantics(self):
        # No dossier info at all (e.g. a mocked engine): gate behaves like
        # the pre-US-103 evaluate_refusal on a good context.
        verdict = evaluate_gate("Quel est le loyer ?", [_doc()], dossier_state=None)
        assert verdict.outcome == PROCEED


# ---------------------------------------------------------------------------
# Outcome 2 — refusal (verbatim proof only, never a score)
# ---------------------------------------------------------------------------

class TestRefusal:
    def test_no_context_delegates_to_adequacy_refusal(self):
        verdict = evaluate_gate("Quelles sont les charges de copropriété 2025 ?", [])
        assert verdict.outcome == REFUSAL
        assert verdict.message == REFUSAL_MESSAGE

    def test_held_referent_absent_from_context_is_refused_with_its_name(self):
        # Defense in depth for I-11: the dossier holds fr_bail but the
        # winning context does not contain it (e.g. the pin fetch failed).
        state = _hold_state(referent=BAIL)
        other = _doc(path="/docs/fr_barometre.md", content="plus de 120 pages")
        verdict = evaluate_gate("Combien de pages contient-il ?", [other], state)
        assert verdict.outcome == REFUSAL
        assert "fr_bail.md" in verdict.message
        assert "documents récupérés" in verdict.message

    def test_recalled_referent_absent_is_refused_too(self):
        state = _hold_state(referent=GLASS)
        state["action"] = ACTION_RECALL
        verdict = evaluate_gate(
            "Reviens au premier document, quel est son titre ?", [_doc()], state
        )
        assert verdict.outcome == REFUSAL
        assert "zh_glass.md" in verdict.message

    def test_held_referent_present_is_not_refused(self):
        state = _hold_state(referent=BAIL, stack=[BAIL])
        verdict = evaluate_gate("Combien de pages contient-il ?", [_doc()], state)
        assert verdict.outcome == PROCEED

    def test_tokens_provably_absent_refused_even_with_strong_scores(self):
        # The I-11 aggravation ("garbage in, validated garbage out"): the
        # widened pass anchored a PAST topic, scores look strong (1.0), yet
        # none of THIS question's tokens is anywhere in the context. Refusal
        # fires on the verbatim proof — the scores are never consulted.
        state = {"action": ACTION_NONE, "turn_kind": NEW,
                 "referent": None, "stack": None, "anchored_paths": None}
        docs = [_doc(content="範例玻璃股份有限公司創立於一九二三年", score=1.0)]
        verdict = evaluate_gate(
            "Quelle est la politique de télétravail chez Microsoft ?", docs, state
        )
        assert verdict.outcome == REFUSAL
        assert "télétravail" in verdict.message
        assert "Microsoft" in verdict.message

    def test_partial_token_presence_is_no_proof(self):
        # One term present somewhere = no verbatim proof of absence: the
        # grounded model answers (and says what is missing) — never refuse.
        state = {"action": ACTION_NONE, "turn_kind": NEW,
                 "referent": None, "stack": None, "anchored_paths": None}
        docs = [_doc(content="Le contrat Microsoft est signé.", score=1.0)]
        verdict = evaluate_gate(
            "Quelle est la politique de télétravail chez Microsoft ?", docs, state
        )
        assert verdict.outcome == PROCEED

    def test_no_token_absence_refusal_without_dossier_classification(self):
        # Empty dossier state (mocked engine / degraded path): without the
        # dossier's proof that the turn is a NEW topic, the token-absence
        # guard must not fire on a strong context — degrade to the
        # pre-US-103 behaviour rather than risk a wrongful refusal (I-12).
        docs = [_doc(content="contenu sans aucun rapport", score=1.0)]
        verdict = evaluate_gate(
            "Quelle est la politique de télétravail chez Microsoft ?", docs, {}
        )
        assert verdict.outcome == PROCEED

    def test_no_token_absence_refusal_on_a_held_followup(self):
        # "Quel est son titre ?" — "titre" appears nowhere in a CJK document,
        # yet the question is perfectly answerable from the title metadata. A
        # held follow-up must NEVER be refused on its incidental words
        # (wrongful refusal = the I-12/I-09 trust killer).
        state = _hold_state(referent=GLASS, stack=[GLASS])
        doc = _doc(path=GLASS["path"], title=GLASS["title"],
                   content="範例玻璃股份有限公司創立於一九二三年", score=1.0)
        verdict = evaluate_gate("Quel est son titre ?", [doc], state)
        assert verdict.outcome == PROCEED

    def test_weak_context_refusal_upgraded_to_targeted_message(self):
        # The US-17c reformulation message is replaced by the targeted one
        # when the absence is verbatim-provable — same outcome, honest detail.
        docs = [
            _doc(title="Rapport A", content="ventes annuelles", score=0.4),
            _doc(path="/docs/b.md", title="Note B", content="réunion", score=0.3),
        ]
        verdict = evaluate_gate("Quel est le budget du projet Zeta ?", docs)
        assert verdict.outcome == REFUSAL
        assert "Zeta" in verdict.message

    def test_score_zero_context_with_token_present_keeps_generic_message(self):
        # Anchoring scored the docs 0 but a term IS present verbatim: no
        # proof of absence, so the historic reformulation message stays.
        docs = [_doc(title="Rapport A", content="le budget 2024", score=0.0)]
        verdict = evaluate_gate("Quel est le budget du projet Zeta ?", docs)
        assert verdict.outcome == REFUSAL
        assert "Zeta" not in (verdict.message or "")
        assert "reformuler" in verdict.message.lower()


# ---------------------------------------------------------------------------
# Outcome 3a — clarification on a proven referent conflict
# ---------------------------------------------------------------------------

class TestClarificationConflict:
    QUESTION = "它的員工人數是多少？"  # anaphoric follow-up (它的), anchors GLASS

    def _context(self):
        return [_doc(path=BAIL["path"], title=BAIL["title"], score=1.0)]

    def test_conflict_asks_one_question_naming_both_candidates(self):
        state = _hold_state(anchored={GLASS["path"]})
        verdict = evaluate_gate(self.QUESTION, self._context(), state)
        assert verdict.outcome == CLARIFICATION
        assert verdict.message.startswith(CLARIFICATION_PREFIX)
        assert "fr_bail.md" in verdict.message
        assert "zh_glass.md" in verdict.message

    def test_no_conflict_when_held_referent_also_anchored(self):
        state = _hold_state(anchored={GLASS["path"], BAIL["path"]})
        verdict = evaluate_gate(self.QUESTION, self._context(), state)
        assert verdict.outcome == PROCEED

    def test_no_question_when_probe_unusable(self):
        # anchored_paths None = the probe failed — never guess, never ask.
        state = _hold_state(anchored=None)
        verdict = evaluate_gate(self.QUESTION, self._context(), state)
        assert verdict.outcome == PROCEED

    def test_no_question_when_nothing_anchored(self):
        state = _hold_state(anchored=set())
        verdict = evaluate_gate(self.QUESTION, self._context(), state)
        assert verdict.outcome == PROCEED

    def test_single_candidate_never_asks(self):
        state = _hold_state(stack=[BAIL], anchored={GLASS["path"]})
        verdict = evaluate_gate(self.QUESTION, self._context(), state)
        assert verdict.outcome == PROCEED

    def test_never_two_clarifications_in_a_row(self):
        state = _hold_state(anchored={GLASS["path"]})
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": f"{CLARIFICATION_PREFIX}X ou Y ?"},
            {"role": "user", "content": self.QUESTION},
        ]
        verdict = evaluate_gate(self.QUESTION, self._context(), state, messages=messages)
        assert verdict.outcome == PROCEED  # held referent in context → answer

    def test_clarification_allowed_again_after_a_normal_answer(self):
        state = _hold_state(anchored={GLASS["path"]})
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": f"{CLARIFICATION_PREFIX}X ou Y ?"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "Réponse normale."},
            {"role": "user", "content": self.QUESTION},
        ]
        verdict = evaluate_gate(self.QUESTION, self._context(), state, messages=messages)
        assert verdict.outcome == CLARIFICATION


# ---------------------------------------------------------------------------
# Outcome 3b — clarification on a follow-up with nothing to hold
# ---------------------------------------------------------------------------

class TestClarificationNoReferent:
    QUESTION = "Combien de pages contient-il ?"

    def _state(self):
        return {"action": ACTION_NONE, "turn_kind": FOLLOWUP,
                "referent": None, "stack": [], "anchored_paths": None}

    def test_followup_with_empty_stack_and_nothing_anchored_asks(self):
        docs = [_doc(content="plus de 120 pages", score=0.0)]  # anchoring noise
        verdict = evaluate_gate(self.QUESTION, docs, self._state())
        assert verdict.outcome == CLARIFICATION
        assert verdict.message.startswith(CLARIFICATION_PREFIX)
        assert "quel document" in verdict.message.lower()

    def test_no_question_when_a_document_is_anchored(self):
        docs = [_doc(content="le document contient 12 pages", score=1.0)]
        verdict = evaluate_gate(self.QUESTION, docs, self._state())
        assert verdict.outcome == PROCEED

    def test_no_question_with_session_attachments(self):
        # "Résume ce document" with a file shared in-session: the anaphor
        # refers to the attachment — never ask, never refuse (US-17 rules).
        verdict = evaluate_gate(self.QUESTION, [], self._state(), has_session=True)
        assert verdict.outcome == PROCEED

    def test_no_question_when_stack_was_not_computed(self):
        # stack=None (replay skipped: new-topic/named short-circuit) is not
        # the same proof as stack=[] — no question without the replay.
        state = {"action": ACTION_NONE, "turn_kind": NEW,
                 "referent": None, "stack": None, "anchored_paths": None}
        verdict = evaluate_gate("Question factuelle sans réponse locale", [], state)
        assert verdict.outcome == REFUSAL  # ordinary adequacy refusal instead

    def test_suppressed_after_a_clarification_falls_back_to_refusal(self):
        messages = [
            {"role": "user", "content": self.QUESTION},
            {"role": "assistant", "content": f"{CLARIFICATION_PREFIX}De quel document parles-tu ?"},
            {"role": "user", "content": self.QUESTION},
        ]
        verdict = evaluate_gate(self.QUESTION, [], self._state(), messages=messages)
        assert verdict.outcome == REFUSAL
        assert verdict.message == REFUSAL_MESSAGE


# ---------------------------------------------------------------------------
# Traceability (étude §6.4, 5th invariant)
# ---------------------------------------------------------------------------

class TestLogging:
    def test_verdict_logged_at_info_when_debug(self):
        logger = _FakeLogger()
        evaluate_gate("Quel est le loyer ?", [_doc()], logger=logger, debug=True)
        assert any(m == "context gate verdict" for m, _ in logger.info_calls)
        assert not logger.debug_calls

    def test_verdict_logged_at_debug_when_not_debug(self):
        logger = _FakeLogger()
        evaluate_gate("Quel est le loyer ?", [_doc()], logger=logger, debug=False)
        assert any(m == "context gate verdict" for m, _ in logger.debug_calls)
        assert not logger.info_calls

    def test_clarification_counter_line_names_alias_and_candidates(self):
        # Feeds the "lexique utilisateur" return-to-backlog counters (étude
        # §6.5): every question asked is logged with the alias + candidates.
        logger = _FakeLogger()
        state = _hold_state(anchored={GLASS["path"]})
        context = [_doc(path=BAIL["path"], title=BAIL["title"], score=1.0)]
        evaluate_gate("它的員工人數是多少？", context, state, logger=logger, debug=True)
        counter = [
            (m, meta) for m, meta in logger.info_calls
            if m == "clarification question asked"
        ]
        assert counter, f"no counter line in {logger.info_calls}"
        meta = counter[0][1]
        assert meta["alias"].startswith("它的")
        assert "fr_bail.md" in meta["candidates"]
        assert "zh_glass.md" in meta["candidates"]

    def test_works_without_a_logger(self):
        verdict = evaluate_gate("Quel est le loyer ?", [_doc()])
        assert verdict.outcome == PROCEED
