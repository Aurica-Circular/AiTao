# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Example event subscriber (US-26): in-process counters for pipeline events.

A minimal, dependency-free handler that demonstrates the EventBus: subscribe an
``EventStats`` to a bus and it tallies how many documents were indexed/deleted
and how many searches ran — without the pipeline knowing it exists.

Real subscribers (future automations, webhooks) follow the same shape:
``bus.subscribe(event_name, handler)``.
"""

from __future__ import annotations

from collections import Counter

from aitao.core.events import (
    DOCUMENT_DELETED,
    DOCUMENT_INDEXED,
    DOCUMENT_UPDATED,
    SEARCH_EXECUTED,
    EventBus,
)

_TRACKED = (DOCUMENT_INDEXED, DOCUMENT_UPDATED, DOCUMENT_DELETED, SEARCH_EXECUTED)


class EventStats:
    """Counts pipeline events it is subscribed to (per event name)."""

    def __init__(self) -> None:
        self.counts: Counter = Counter()

    def subscribe_to(self, bus: EventBus) -> None:
        """Attach a counter for each tracked event to ``bus``."""
        for event in _TRACKED:
            bus.subscribe(event, self._counter_for(event))

    def _counter_for(self, event: str):
        def handler(**_payload) -> None:
            self.counts[event] += 1
        return handler
