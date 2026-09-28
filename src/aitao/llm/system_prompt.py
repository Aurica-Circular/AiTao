# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
System prompt construction for AiTao's context-grounded chat (Tier 1).

Builds the system prompt injected on every conversation. It deliberately keeps
two concerns separate:

  - IDENTITY (editable): who AiTao is and who the user is, read from config
    (`identity.who_is_aitao`, `identity.who_are_you`, `indexing.include_paths`).
  - BEHAVIOURAL CONTRACT (fixed): the non-negotiable anti-hallucination rules
    that make AiTao a "notaire, pas oracle" — answer only from context, cite
    sources, refuse when the context is silent, and never confuse the assistant
    with the user.

Separating the two is what fixes the observed failures: an assistant that
answered "Je suis Phil" (user profile mistaken for its own identity) and that
invented facts when no context was present.

This is a Core feature — no license gating.
"""

from typing import Any, List, Optional

from aitao.core.config import get_config


class SystemPromptBuilder:
    """Assemble AiTao's Tier 1 system prompt from config + the fixed contract.

    The behavioural contract is a class constant — it is AiTao's runtime
    guarantee and must never be sourced from (user-editable) config.
    """

    # ------------------------------------------------------------------
    # Non-negotiable behavioural contract (NOT user-editable)
    # ------------------------------------------------------------------
    BEHAVIOR_CONTRACT: str = (
        "# YOUR OPERATING RULES (non-negotiable)\n"
        "- You are AiTao, the assistant. You are NOT the user: never speak as "
        "the user, and never adopt the user's name, background, or identity.\n"
        "- Answer ONLY from the CONTEXT available in this conversation (AiTao's "
        "configuration, live system facts, the user's indexed documents, and "
        "files they shared).\n"
        "- If the answer is not in that context, say so plainly in the user's "
        'language (e.g. "I could not find this in your documents"). Never '
        "invent facts, dates, names, numbers, or sources.\n"
        "- TRANSFORMING content that IS in the context is part of your job and "
        "is NOT inventing: when the user asks you to translate, summarize, or "
        "restructure a retrieved document, do it faithfully and completely from "
        "the context. Never refuse a translation of context content by claiming "
        "you lack a translation tool — you are the translator.\n"
        "- When you rely on a retrieved document, cite its source (file path or "
        "title).\n"
        "- If a question could mean either AiTao's own setup or general "
        "knowledge, prefer AiTao's configuration, or ask the user to clarify.\n"
        "- Follow the REPLY LANGUAGE rules below."
    )

    _DEFAULT_IDENTITY: str = (
        "You are AiTao, a local-first assistant that helps the user work with "
        "their own documents."
    )

    def __init__(self, config: Optional[Any] = None) -> None:
        self._config = config if config is not None else get_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build(self) -> str:
        """Return the full system prompt (identity + contract + user + paths)."""
        sections: List[str] = [
            self._identity_section(),
            self.BEHAVIOR_CONTRACT,
        ]

        facts_section = self._facts_section()
        if facts_section:
            sections.append(facts_section)

        language_section = self._language_section()
        if language_section:
            sections.append(language_section)

        user_section = self._user_section()
        if user_section:
            sections.append(user_section)

        paths_section = self._paths_section()
        if paths_section:
            sections.append(paths_section)

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    def _identity_section(self) -> str:
        """AiTao's own identity (config `who_is_aitao`, with a safe default)."""
        who_is_aitao = self._get("identity.who_is_aitao") or self._DEFAULT_IDENTITY
        return f"# WHO YOU ARE (AiTao — the assistant)\n{who_is_aitao}"

    def _facts_section(self) -> str:
        """Tier 0 live system facts (date/time/timezone/version) — US-17a."""
        try:
            from aitao.llm.system_facts import build_system_facts_section

            return build_system_facts_section()
        except Exception:
            return ""

    def _language_section(self) -> str:
        """Reply-language rules in priority order (US-20, US-128).

        Three layers, highest priority first:
          1. an explicit language request in the user's message — always wins;
          2. else the configured ``identity.response_language``, if it forces a
             specific language;
          3. else the language of the user's latest message ("mirror" mode).

        ``response_language`` is treated as mirror mode when empty or set to the
        literal ``"mirror"`` (the default) — layer 3 applies. Any other value
        forces that language (layer 2). Always rendered, so layer 1 (explicit
        request) applies in every case.
        """
        lang = self._get("identity.response_language").strip()
        # "mirror" (or empty) means: reply in the language of the user's message.
        if lang.lower() == "mirror":
            lang = ""
        lines = [
            "# REPLY LANGUAGE (in priority order)",
            "1. If the user's latest message explicitly asks for a reply "
            "language, answer in that language — this overrides the rules below.",
        ]
        if lang:
            lines.append(
                f"2. Otherwise, always answer in {lang}, regardless of the "
                "language of the retrieved documents or the question."
            )
        else:
            lines.append(
                "2. Otherwise, reply in the same language as the user's "
                "latest message."
            )
        return "\n".join(lines)

    def _user_section(self) -> str:
        """The user's profile, explicitly flagged as *not* the assistant."""
        who_are_you = self._get("identity.who_are_you")
        if not who_are_you:
            return ""
        return (
            "# ABOUT THE USER (the person you assist — this is NOT you)\n"
            f"{who_are_you}"
        )

    def _paths_section(self) -> str:
        """The indexed locations AiTao may reference (config `include_paths`)."""
        include_paths = self._include_paths()
        if not include_paths:
            return ""
        listed = "\n".join(f"  - {path}" for path in include_paths)
        return (
            "# INDEXED LOCATIONS (the only folders/volumes AiTao can access)\n"
            f"{listed}\n"
            "When asked which folders, volumes, or documents you can access or "
            "index, list exactly these paths — do not generalize or invent others."
        )

    # ------------------------------------------------------------------
    # Config helpers (defensive: config issues must never break chat)
    # ------------------------------------------------------------------
    def _get(self, key: str) -> str:
        # key format: "section.field" — resolved via typed settings
        try:
            section_name, _, field = key.partition(".")
            section = getattr(self._config, section_name, None)
            return str(getattr(section, field, "") or "").strip() if section else ""
        except Exception:
            return ""

    def _include_paths(self) -> List[str]:
        try:
            # List from this builder's config; PathManager resolves it (US-098 A4).
            from aitao.core.pathmanager import path_manager
            raw = self._config.indexing.include_paths or []
            return path_manager.resolve_include_paths(raw, existing_only=False)
        except Exception:
            return []


def build_system_prompt(config: Optional[Any] = None) -> str:
    """Convenience wrapper returning the assembled Tier 1 system prompt."""
    return SystemPromptBuilder(config).build()
