# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Lightweight, LLM-free intent classification for AiTao (US-DEMO-9).

Detects "configuration questions" — where the user asks about AiTao's own
identity, about themselves, or about the folders/volumes AiTao can index — so
the chat layer can inject a precise, config-grounded directive instead of
letting the model guess (and answer generically or as the wrong persona).

This addresses two observed failures:
  - "Qui suis-je ?" → the model described itself instead of the user.
  - "Quels volumes peux-tu indexer ?" → the model invented generic categories
    instead of listing the configured include_paths.

Pure regex, no LLM call. Matching is accent- and case-insensitive and tolerant
of punctuation (FR + EN). This is the focused precursor to the full four-class
intent router (US-DEMO-11).

Core feature — no license gating.
"""

import re
import unicodedata
from enum import Enum
from typing import Any, List, Optional

from aitao.core.config import get_config


class ConfigIntent(str, Enum):
    """Config-question intents handled by the Tier 1 directive layer."""

    IDENTITY_SELF = "identity_self"   # "qui es-tu", "what is AiTao"
    IDENTITY_USER = "identity_user"   # "qui suis-je", "what do you know about me"
    INDEX_SCOPE = "index_scope"       # "quels volumes peux-tu indexer"
    TEMPORAL = "temporal"             # "quel jour sommes-nous", "what time is it"
    NONE = "none"


def _normalize(text: str) -> str:
    """Lowercase, strip accents, and reduce punctuation to single spaces.

    Produces an ASCII, space-delimited form so the patterns below stay simple
    and robust to accents, apostrophes, and hyphenation ("présente-toi",
    "qu'est-ce que" …).
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accents.lower())
    return cleaned.strip()


# Patterns are matched against the normalized (accent-free, space-delimited) form.
_IDENTITY_USER_PATTERNS: List[str] = [
    r"\bqui suis je\b",
    r"\bqui je suis\b",
    r"\bje suis qui\b",
    r"\bque sais tu (de|sur) moi\b",
    r"\bque connais tu (de|sur) moi\b",
    r"\bparle moi de moi\b",
    r"\bmon profil\b",
    r"\bmon nom\b",
    r"\b(quel est|c est quoi) mon nom\b",
    r"\bcomment je m appelle\b",
    r"\btu (connais|sais) mon nom\b",
    r"\bwho am i\b",
    r"\bwhat do you know about me\b",
    r"\btell me about myself\b",
    r"\bmy profile\b",
    r"\bmy name\b",
    r"\b(what is|what s) my name\b",
    r"\bdo you know my name\b",
]

_IDENTITY_SELF_PATTERNS: List[str] = [
    r"\bqui es tu\b",
    r"\bqui etes vous\b",
    r"\btu es qui\b",
    r"\bpresente[sz]? toi\b",
    r"\bqui est aitao\b",
    r"\bc est quoi aitao\b",
    r"\bqu est ce qu[e]? aitao\b",
    r"\bque peux tu faire\b",
    r"\bwho are you\b",
    r"\bwhat are you\b",
    r"\bwhat is aitao\b",
    r"\bintroduce yourself\b",
    r"\btell me about yourself\b",
    r"\bwhat can you do\b",
]

_INDEX_SCOPE_PATTERNS: List[str] = [
    r"\bquel(s|le|les)? (volumes?|dossiers?|repertoires?|chemins?)\b",
    r"\b(volumes?|dossiers?|repertoires?)\b.{0,30}\b(index|acces|acceder|cherch|scan)",
    r"\bque (peux|peut) tu index",
    r"\bqu est ce que tu peux index",
    r"\bou (cherch|index|regard|scan)",
    r"\bta config(uration)?\b",
    r"\binclude paths\b",
    r"\bwhich (folders?|volumes?|directories?|paths?)\b",
    r"\bwhat (folders?|volumes?) can you (index|access)\b",
    r"\bwhat can you index\b",
    r"\bwhere do you (search|look|index|scan)\b",
    r"\byour configuration\b",
]


# Temporal questions about the *current* date/time (US-17a). Patterns are kept
# narrow on purpose: "quelle date figure dans le contrat" must NOT match —
# only questions about today/now, answered from Tier 0 system facts.
_TEMPORAL_PATTERNS: List[str] = [
    r"\bquel jour (de la semaine )?(sommes nous|on est|est on|est ce|c est)\b",
    r"\b(nous sommes|on est) quel jour( de la semaine)?\b",
    r"\bquelle (est la )?date (sommes nous|aujourd hui|d aujourd hui|du jour)\b",
    r"\b(la )?date (d )?aujourd hui\b",
    # End-anchored: "quelle est la date du contrat" asks about a DOCUMENT date
    # and must go through RAG — only the bare question is temporal.
    r"\bquelle est la date( stp| svp)?$",
    r"\bquelle heure (est il|il est)\b",
    r"\bil est quelle heure\b",
    r"\bwhat day (is it|is today|are we)\b",
    r"\bwhat (is today s|s today s) date\b",
    r"\bwhat is the date( today)?( please)?$",
    r"\btoday s date\b",
    r"\bwhat time is it\b",
    r"\bquelle version (de toi|d aitao|utilises tu|es tu)\b",
    r"\bwhat version (of aitao|are you)\b",
]


def _matches_any(text: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def classify_config_intent(message: str) -> ConfigIntent:
    """Classify a user message as a config question (or NONE). Regex only.

    User-identity ("qui suis-je") is checked before self-identity ("qui es-tu")
    because it is the more specific case.
    """
    norm = _normalize(message)
    if not norm:
        return ConfigIntent.NONE
    if _matches_any(norm, _TEMPORAL_PATTERNS):
        return ConfigIntent.TEMPORAL
    if _matches_any(norm, _IDENTITY_USER_PATTERNS):
        return ConfigIntent.IDENTITY_USER
    if _matches_any(norm, _IDENTITY_SELF_PATTERNS):
        return ConfigIntent.IDENTITY_SELF
    if _matches_any(norm, _INDEX_SCOPE_PATTERNS):
        return ConfigIntent.INDEX_SCOPE
    return ConfigIntent.NONE


_DIRECTIVE_HEADER = "# DIRECT INSTRUCTION FOR THIS QUESTION"


def build_config_directive(
    message: str, config: Optional[Any] = None
) -> Optional[str]:
    """Return a config-grounded directive for a detected config question.

    The directive is meant to be appended to the system prompt so the model
    answers the current question from the exact configuration values instead of
    guessing. Returns None when the message is not a config question (or the
    relevant config value is missing).
    """
    intent = classify_config_intent(message)
    if intent == ConfigIntent.NONE:
        return None

    if intent == ConfigIntent.TEMPORAL:
        from aitao.llm.system_facts import build_system_facts_section

        return (
            f"{_DIRECTIVE_HEADER}\n"
            "The user is asking about the current date, time, or your version. "
            "Answer DIRECTLY from the live system facts below, in the user's "
            "language. Do NOT search documents, do NOT cite any source, and "
            "never use a date found in a document as today's date.\n"
            f"{build_system_facts_section()}"
        )

    config = config if config is not None else get_config()

    if intent == ConfigIntent.IDENTITY_SELF:
        return (
            f"{_DIRECTIVE_HEADER}\n"
            "The user is asking who YOU (AiTao) are. Answer from the 'WHO YOU "
            "ARE' section — describe yourself, the assistant, not the user."
        )

    if intent == ConfigIntent.IDENTITY_USER:
        who_are_you = _get(config, "identity.who_are_you")
        if not who_are_you:
            return None
        return (
            f"{_DIRECTIVE_HEADER}\n"
            "The user is asking about THEMSELVES. Answer with this exact "
            "information about the user — do not describe yourself:\n"
            f"{who_are_you}"
        )

    # INDEX_SCOPE
    paths = _include_paths(config)
    if not paths:
        return None
    listed = "\n".join(f"  - {path}" for path in paths)
    return (
        f"{_DIRECTIVE_HEADER}\n"
        "The user is asking which folders/volumes you can index or access. "
        "Reply with EXACTLY this list, one path per line, and nothing else — "
        f"do not generalize or invent categories:\n{listed}"
    )


def _get(config: Any, key: str) -> str:
    # key format: "section.field" — resolved via typed settings
    try:
        section_name, _, field = key.partition(".")
        section = getattr(config, section_name, None)
        return str(getattr(section, field, "") or "").strip() if section else ""
    except Exception:
        return ""


def _include_paths(config: Any) -> List[str]:
    try:
        # List from the passed config; PathManager resolves it (US-098 A4).
        from aitao.core.pathmanager import path_manager
        raw = config.indexing.include_paths or []
        return path_manager.resolve_include_paths(raw, existing_only=False)
    except Exception:
        return []
