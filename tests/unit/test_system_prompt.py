# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for SystemPromptBuilder (US-DEMO-8).

Verifies the hardened Tier 1 system prompt:
- AiTao's identity and the behavioural contract are always present
- the user's profile is clearly separated from the assistant's identity
  (the fix for the "Je suis Phil" confusion)
- indexed paths are listed with a "do not generalize" instruction
- config problems degrade gracefully instead of breaking chat
"""

from types import SimpleNamespace

from aitao.llm.system_prompt import SystemPromptBuilder, build_system_prompt


class _FakeConfig:
    """Minimal ConfigManager stand-in for deterministic tests.

    Exposes both the legacy .get()/.get_section() API and the typed
    attribute properties introduced in US-22.
    """

    def __init__(self, values=None, sections=None):
        self._values = values or {}
        self._sections = sections or {}
        # Build typed attributes from values/sections
        self.identity = SimpleNamespace(
            who_is_aitao=self._values.get("identity.who_is_aitao", ""),
            who_are_you=self._values.get("identity.who_are_you", ""),
            response_language=self._values.get("identity.response_language", ""),
        )
        indexing_section = (self._sections.get("indexing") or {})
        self.indexing = SimpleNamespace(
            include_paths=indexing_section.get("include_paths", []),
        )

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_section(self, name):
        return self._sections.get(name)


class _BoomConfig:
    """Config whose accessors raise — used to prove graceful degradation."""

    def get(self, key, default=None):
        raise RuntimeError("config boom")

    def get_section(self, name):
        raise RuntimeError("config boom")


def _full_config():
    return _FakeConfig(
        values={
            "identity.who_is_aitao": "Je suis AiTao, une IA pour vos documents personnels.",
            "identity.who_are_you": "Phil, un Français vivant à Taiwan.",
        },
        sections={
            "indexing": {
                "include_paths": ["/Users/phil/Documents", "/Users/alice/Projects"]
            }
        },
    )


class TestSystemPromptBuilder:
    def test_includes_identity(self):
        prompt = SystemPromptBuilder(_full_config()).build()
        assert "# WHO YOU ARE" in prompt
        assert "Je suis AiTao" in prompt

    def test_includes_behaviour_contract(self):
        prompt = SystemPromptBuilder(_full_config()).build()
        assert "non-negotiable" in prompt
        assert "ONLY from the CONTEXT" in prompt
        assert "Never invent" in prompt
        assert "cite its source" in prompt

    def test_contract_allows_transforming_context(self):
        """Translating/summarizing context content must be explicitly allowed —
        a strict model read 'answer ONLY from the context' as forbidding
        translation and refused ('I lack a translation tool')."""
        prompt = SystemPromptBuilder(_full_config()).build()
        assert "TRANSFORMING" in prompt
        assert "translate" in prompt
        assert "NOT inventing" in prompt

    def test_separates_user_from_identity(self):
        """The user profile must be flagged as NOT the assistant, and must come
        after the identity section — the 'Je suis Phil' guard."""
        prompt = SystemPromptBuilder(_full_config()).build()
        assert "# ABOUT THE USER" in prompt
        assert "this is NOT you" in prompt
        assert "Phil" in prompt
        assert prompt.index("# WHO YOU ARE") < prompt.index("# ABOUT THE USER")

    def test_lists_paths_without_generalizing(self):
        prompt = SystemPromptBuilder(_full_config()).build()
        assert "# INDEXED LOCATIONS" in prompt
        assert "/Users/phil/Documents" in prompt
        assert "/Users/alice/Projects" in prompt
        assert "do not generalize" in prompt

    def test_empty_config_still_has_identity_and_contract(self):
        prompt = SystemPromptBuilder(_FakeConfig()).build()
        assert "AiTao" in prompt              # default identity
        assert "non-negotiable" in prompt     # contract always present
        assert "# ABOUT THE USER" not in prompt
        assert "# INDEXED LOCATIONS" not in prompt

    def test_missing_user_profile_omits_section(self):
        cfg = _FakeConfig(values={"identity.who_is_aitao": "Je suis AiTao."})
        prompt = SystemPromptBuilder(cfg).build()
        assert "# ABOUT THE USER" not in prompt

    def test_config_errors_do_not_crash(self):
        prompt = SystemPromptBuilder(_BoomConfig()).build()
        assert "AiTao" in prompt
        assert "non-negotiable" in prompt

    def test_response_language_directive_present_when_set(self):
        cfg = _FakeConfig(values={"identity.response_language": "français"})
        prompt = SystemPromptBuilder(cfg).build()
        assert "# REPLY LANGUAGE (in priority order)" in prompt
        assert "always answer in français" in prompt
        # layer 1 (explicit request) is always present and on top
        assert "explicitly asks for a reply" in prompt

    def test_language_section_always_present_with_explicit_override(self):
        # Even without config, layer 1 (explicit request) and layer 3
        # (same-language fallback) must be present.
        prompt = SystemPromptBuilder(_FakeConfig()).build()
        assert "# REPLY LANGUAGE (in priority order)" in prompt
        assert "explicitly asks for a reply" in prompt
        assert "same language as the user" in prompt
        assert "Otherwise, always answer in" not in prompt  # no forced lang

    def test_mirror_value_falls_back_to_message_language(self):
        # US-128 — "mirror" is the default and means: reply in the language of
        # the user's message (layer 3), never a forced language (layer 2).
        cfg = _FakeConfig(values={"identity.response_language": "mirror"})
        prompt = SystemPromptBuilder(cfg).build()
        assert "# REPLY LANGUAGE (in priority order)" in prompt
        assert "same language as the user" in prompt
        assert "Otherwise, always answer in" not in prompt  # not a forced lang

    def test_mirror_value_is_case_insensitive(self):
        # US-128 — "Mirror"/" MIRROR " must be recognised, not forced verbatim.
        cfg = _FakeConfig(values={"identity.response_language": " Mirror "})
        prompt = SystemPromptBuilder(cfg).build()
        assert "same language as the user" in prompt
        assert "always answer in" not in prompt

    def test_build_system_prompt_wrapper_matches_class(self):
        cfg = _full_config()
        assert build_system_prompt(cfg) == SystemPromptBuilder(cfg).build()
