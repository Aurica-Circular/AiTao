# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tier 0 system facts for AiTao's chat (US-17a).

Provides live, trusted facts about the runtime environment — current date,
time, timezone, and AiTao version — injected into every conversation's system
prompt. These facts come from the local system clock, never from indexed
documents, so the model can answer "what day is it?" correctly instead of
hallucinating a date from its training data (observed failure: demo of
June 5th — answered "October 21st, 2023" with a fabricated citation).

Pure logic, no LLM call. Core feature — no license gating.
"""

from datetime import datetime
from typing import Optional


def get_aitao_version() -> str:
    """Return the installed AiTao version, or 'unknown' if unavailable."""
    try:
        from importlib.metadata import version

        return version("aitao")
    except Exception:
        return "unknown"


def build_system_facts_section(now: Optional[datetime] = None) -> str:
    """Build the Tier 0 'SYSTEM FACTS' system-prompt section.

    Args:
        now: Injectable current datetime (for tests). Defaults to the local
            system clock with its timezone.

    Returns:
        A formatted prompt section stating date, time, timezone and version,
        with an instruction to answer such questions directly and without
        citing any document.
    """
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.astimezone()

    tz_name = now.tzname() or "local"
    offset = now.strftime("%z")
    offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"

    return (
        "# SYSTEM FACTS (live values from the local system — always trust these)\n"
        f"- Current date: {now.strftime('%A %d %B %Y')} ({now.strftime('%Y-%m-%d')})\n"
        f"- Current time: {now.strftime('%H:%M')} ({tz_name}, {offset_fmt})\n"
        f"- AiTao version: {get_aitao_version()}\n"
        "When asked about today's date, the current time, or your version, "
        "answer directly from these values, in the user's language. NEVER cite "
        "a document or source for these facts, and never use a date from any "
        "indexed document as today's date."
    )
