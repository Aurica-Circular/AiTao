# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for Tier 0 system facts (US-17a).

Covers:
- build_system_facts_section: date/time/timezone/version content, injectable
  clock for determinism
- get_aitao_version: returns the installed version
- SystemPromptBuilder: the SYSTEM FACTS section is present in every prompt
"""

from datetime import datetime, timezone, timedelta

from aitao.llm.system_facts import build_system_facts_section, get_aitao_version
from aitao.llm.system_prompt import build_system_prompt


class TestBuildSystemFactsSection:
    def test_contains_injected_date_and_time(self):
        fixed = datetime(2026, 6, 11, 14, 30, tzinfo=timezone(timedelta(hours=8)))
        section = build_system_facts_section(now=fixed)
        assert "2026-06-11" in section
        assert "Thursday 11 June 2026" in section
        assert "14:30" in section
        assert "UTC+08:00" in section

    def test_contains_version(self):
        section = build_system_facts_section()
        assert f"AiTao version: {get_aitao_version()}" in section

    def test_no_citation_instruction(self):
        section = build_system_facts_section()
        assert "NEVER cite" in section

    def test_defaults_to_now(self):
        section = build_system_facts_section()
        assert datetime.now().strftime("%Y-%m-%d") in section


class TestGetAitaoVersion:
    def test_returns_non_empty_string(self):
        assert get_aitao_version()


class TestSystemPromptIncludesFacts:
    def test_facts_section_present_in_full_prompt(self):
        prompt = build_system_prompt()
        assert "SYSTEM FACTS" in prompt
        assert datetime.now().strftime("%Y-%m-%d") in prompt
