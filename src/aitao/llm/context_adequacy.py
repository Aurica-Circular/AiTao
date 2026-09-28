# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Context adequacy gate (US-DEMO-10).

Before calling the LLM, decide whether the available context is sufficient to
answer the user's question. For a *factual* question with no relevant local
document, AiTao refuses ("Je n'ai pas trouvé…") instead of letting the model
invent an answer or borrow an irrelevant document — the "notaire, pas oracle"
contract. Config questions (identity / scope) and small talk are never refused.

Pure logic, no LLM call. Core feature — no license gating.
"""

import re
from typing import List, Optional

from aitao.llm.intent_classifier import (
    ConfigIntent,
    _normalize,
    classify_config_intent,
)

REFUSAL_MESSAGE = (
    "Je n'ai pas trouvé cette information dans vos documents indexés. "
    "Reformulez votre question, ou vérifiez que le document concerné est bien indexé."
)

# Below this score, retrieved documents are considered too weakly relevant to
# ground an answer (US-17c). Hybrid RRF scores ~1.0 when both engines agree,
# ~0.45-0.5 when only one engine matches loosely — 0.55 separates the two.
WEAK_CONTEXT_THRESHOLD = 0.55

# Context-source labels exposed to API consumers
SOURCE_CONFIG = "config"
SOURCE_DOCS = "docs"
SOURCE_SESSION = "session"
SOURCE_NONE = "none"

_CONVERSATIONAL_PATTERNS: List[str] = [
    r"\b(bonjour|bonsoir|salut|coucou|hello|hi|hey|yo)\b",
    r"\bca va\b",
    r"\bcomment (ca va|vas tu|allez vous)\b",
    r"\b(merci|thanks|thank you)\b",
    r"\b(au revoir|bye|a bientot|a plus)\b",
    r"\b(ok|okay|super|genial|parfait|cool|bravo|d accord)\b",
]

# Leading politeness tokens stripped before classification (US-17b): a greeting
# prefix must not mask the real question that follows ("Bonjour, quelles sont
# les échéances ?" is factual, not small talk). Matched on the normalized form.
_GREETING_PREFIX = re.compile(
    r"^(?:(?:bonjour|bonsoir|salut|coucou|hello|hi|hey|yo)\b\s*)+"
)

# Interrogative / imperative cues: a short remainder without any of these is
# still small talk ("Bonjour AiTao !", "salut mon ami"), not a factual question.
_QUESTION_CUES = re.compile(
    r"\b(qui|que|quoi|quel(le)?s?|comment|pourquoi|quand|combien|ou|"
    r"what|who|where|when|why|how|which|can|do(es)?|is|are|"
    r"peux|montre|trouve|cherche|resume|liste|donne|explique)\b"
)


def is_conversational(message: str) -> bool:
    """True for greetings / small talk that must never trigger a refusal.

    Leading greetings are stripped first so only the remainder is classified:
    a pure greeting stays conversational, but a greeting followed by a real
    question is handled as that question (US-17b).
    """
    # CJK has no word boundaries and _normalize() (ASCII-oriented) strips it, so a
    # real Chinese question normalises to "" and would be mistaken for small talk —
    # bypassing the refusal gate entirely. Check the ORIGINAL message first: a run
    # of several ideographs is a real query, never a greeting. US-89-4.
    if len(re.findall(r"[㐀-鿿豈-﫿]", message)) >= 4:
        return False
    norm = _normalize(message)
    if not norm:
        return True
    remainder = _GREETING_PREFIX.sub("", norm).strip()
    if not remainder:
        return True
    had_greeting = remainder != norm
    if (
        had_greeting
        and len(remainder.split()) <= 2
        and not _QUESTION_CUES.search(remainder)
    ):
        # "Bonjour AiTao !", "salut mon ami" — still just a greeting
        return True
    if len(remainder.split()) > 8:
        return False
    return any(re.search(pattern, remainder) for pattern in _CONVERSATIONAL_PATTERNS)


def needs_document_retrieval(message: str) -> bool:
    """True for factual questions that warrant a Tier 2 document search.

    Config questions are answered from config and small talk needs no
    documents, so the (slower) hybrid search is skipped for them — faster
    responses and no irrelevant chunks padding the prompt.
    """
    if classify_config_intent(message) != ConfigIntent.NONE:
        return False
    if is_conversational(message):
        return False
    return True


def _all_weak(context_docs: List) -> bool:
    """True when every retrieved doc scores below the relevance threshold.

    A document without a score is conservatively treated as relevant — better
    a model answer grounded on it than a wrong reformulation prompt.
    """
    scores = [getattr(doc, "score", None) for doc in context_docs]
    if any(score is None for score in scores):
        return False
    return all(float(score) < WEAK_CONTEXT_THRESHOLD for score in scores)


def build_weak_context_message(context_docs: List) -> str:
    """Reformulation prompt listing the closest (but weak) matches (US-17c)."""
    titles: List[str] = []
    for doc in context_docs[:3]:
        title = str(getattr(doc, "title", "") or getattr(doc, "path", "") or "")
        if title and title not in titles:
            titles.append(title)
    listed = "\n".join(f"  • {title}" for title in titles)
    closest = (
        f"Les documents qui s'en rapprochent le plus :\n{listed}\n"
        if titles
        else ""
    )
    return (
        "Je n'ai trouvé aucun document suffisamment pertinent pour répondre "
        "précisément à votre question.\n"
        f"{closest}"
        "Pouvez-vous reformuler ou préciser (mots-clés exacts, période, nom "
        "du document) ?"
    )


def context_source(
    message: str, context_docs: List, has_session: bool = False
) -> str:
    """Best-effort label of where the answer's grounding comes from."""
    if classify_config_intent(message) != ConfigIntent.NONE:
        return SOURCE_CONFIG
    if context_docs:
        return SOURCE_DOCS
    if has_session:
        return SOURCE_SESSION
    return SOURCE_NONE


def evaluate_refusal(
    message: str, context_docs: List, has_session: bool = False
) -> Optional[str]:
    """Return the refusal message if a factual question lacks relevant context.

    Returns None (i.e. proceed to the LLM) when the question is a config
    question, greeting / small talk, when relevant documents were retrieved, or
    when files were shared earlier in the session. Otherwise (a factual question
    with no usable local context) it returns ``REFUSAL_MESSAGE``.
    """
    if classify_config_intent(message) != ConfigIntent.NONE:
        return None
    if is_conversational(message):
        return None
    if context_docs:
        # US-17c — uniformly weak retrieval = effectively no usable context:
        # ask the user to refine instead of letting the model improvise.
        if _all_weak(context_docs):
            return build_weak_context_message(context_docs)
        return None
    if has_session:
        return None
    return REFUSAL_MESSAGE
