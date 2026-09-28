# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for history hygiene (strip reliability notices from past turns).

The ⚠️ blocks the guard/validator append are for the user; fed back to the
model they entrench old refusals. Stripping must remove exactly those trailing
blocks and never touch user turns or clean answers.
"""

from aitao.llm.history_hygiene import clean_history_messages, strip_reliability_notices

ANSWER = "Voici la traduction du document demandé."
CITATION_NOTICE = (
    "\n\n⚠️ Avertissement : la réponse ci-dessus cite une source introuvable "
    "dans vos documents indexés : « taiwan.pdf ». Ne vous fiez pas à cette référence."
)
VALIDATOR_NOTICE = (
    "\n\n⚠️ Fiabilité : 8 affirmations ci-dessus ne s'appuient pas clairement "
    "sur vos documents — à vérifier dans les sources : • « … »"
)
# US-104 part B — the attribution corrective banner (source_attribution.
# build_attribution_warning) shares the exact same "\n\n⚠️ Fiabilité :"
# prefix as the grounding validator's own notice by design, precisely so
# history hygiene strips it with zero code change.
ATTRIBUTION_NOTICE = (
    "\n\n⚠️ Fiabilité : l'extrait attribué à « en_receipt.md » semble "
    "provenir de « en_invoice.md » — vérifiez la source."
)


class TestStripReliabilityNotices:
    def test_strips_citation_notice(self):
        assert strip_reliability_notices(ANSWER + CITATION_NOTICE) == ANSWER

    def test_strips_validator_notice(self):
        assert strip_reliability_notices(ANSWER + VALIDATOR_NOTICE) == ANSWER

    def test_strips_attribution_notice(self):
        # US-104 — proves the shared "\n\n⚠️ Fiabilité :" prefix means this
        # module needed zero changes to also strip the new attribution banner.
        assert strip_reliability_notices(ANSWER + ATTRIBUTION_NOTICE) == ANSWER

    def test_strips_both_blocks(self):
        text = ANSWER + CITATION_NOTICE + VALIDATOR_NOTICE
        assert strip_reliability_notices(text) == ANSWER

    def test_clean_answer_untouched(self):
        assert strip_reliability_notices(ANSWER) == ANSWER

    def test_warning_emoji_in_content_untouched(self):
        # A legitimate ⚠️ inside the answer body is not a notice block.
        text = "Attention ⚠️ ce contrat expire bientôt."
        assert strip_reliability_notices(text) == text

    def test_empty_text(self):
        assert strip_reliability_notices("") == ""


GENERAL_BANNER = "ℹ️ Réponse générale — pas issue de vos documents.\n\n"
CLARIFICATION_PREFIX = "❓ "


class TestStripGeneralBanner:
    """US-105 — the intent router's leading 'general answer' banner is
    stripped from history like the trailing ⚠️ blocks, but from the FRONT."""

    def test_strips_leading_banner(self):
        assert strip_reliability_notices(GENERAL_BANNER + ANSWER) == ANSWER

    def test_strips_banner_and_trailing_notice_together(self):
        text = GENERAL_BANNER + ANSWER + VALIDATOR_NOTICE
        assert strip_reliability_notices(text) == ANSWER

    def test_no_banner_untouched(self):
        assert strip_reliability_notices(ANSWER) == ANSWER

    def test_clarification_marker_is_not_touched(self):
        # US-103's "never two clarifications in a row" guardrail depends on
        # this marker surviving history hygiene — a different emoji from the
        # US-105 banner, so it must never be matched/stripped here.
        question = f"{CLARIFICATION_PREFIX}Tu parles toujours de « bail » ?"
        assert strip_reliability_notices(question) == question

    def test_clean_history_messages_strips_banner_from_assistant_turn(self):
        msgs = [
            {"role": "user", "content": "Combien font 15% de 240 ?"},
            {"role": "assistant", "content": GENERAL_BANNER + "36."},
        ]
        cleaned = clean_history_messages(msgs)
        assert cleaned[1]["content"] == "36."
        assert msgs[1]["content"].startswith("ℹ️")  # input list untouched

    def test_clean_history_messages_preserves_clarification_marker(self):
        question = f"{CLARIFICATION_PREFIX}Tu parles toujours de « bail » ?"
        msgs = [{"role": "assistant", "content": question}]
        cleaned = clean_history_messages(msgs)
        assert cleaned[0]["content"] == question


class TestCleanHistoryMessages:
    def test_assistant_turns_cleaned_user_untouched(self):
        user_text = "traduis le doc ⚠️ Fiabilité incluse dans MA question"
        msgs = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": ANSWER + VALIDATOR_NOTICE},
            {"role": "user", "content": "complète"},
        ]
        cleaned = clean_history_messages(msgs)
        assert cleaned[0]["content"] == user_text        # user never modified
        assert cleaned[1]["content"] == ANSWER           # notice stripped
        assert cleaned[2]["content"] == "complète"
        assert msgs[1]["content"].endswith("»")          # input list untouched

    def test_no_op_returns_equal_messages(self):
        msgs = [{"role": "assistant", "content": ANSWER}]
        assert clean_history_messages(msgs) == msgs
