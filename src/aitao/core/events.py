# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
In-process event bus for AiTao (US-26).

A tiny publish/subscribe hub so optional behaviours (stats, future automations,
webhooks, …) can react to pipeline events without the pipeline knowing about
them. Synchronous and in-process — no external broker.

Each handler is isolated: a handler that raises is logged and skipped, never
breaking the publisher or the other handlers. Handlers run inline, so a handler
with heavy work must offload it itself (the bus does not spawn threads).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, List

from aitao.core.logger import get_logger

logger = get_logger("core.events")

# Event names — the shared vocabulary publishers and subscribers agree on.
DOCUMENT_INDEXED = "document.indexed"
DOCUMENT_UPDATED = "document.updated"
DOCUMENT_DELETED = "document.deleted"
SEARCH_EXECUTED = "search.executed"

Handler = Callable[..., None]


class EventBus:
    """Minimal synchronous publish/subscribe bus with per-handler isolation."""

    def __init__(self) -> None:
        self._handlers: DefaultDict[str, List[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        """Register ``handler`` to be called on every ``publish(event, ...)``."""
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        """Remove a previously-subscribed handler (no-op if not subscribed)."""
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def publish(self, event: str, **payload: Any) -> None:
        """Call every handler subscribed to ``event`` with ``payload``.

        Never propagates a handler error to the caller: an exception in one
        handler is logged and the remaining handlers still run. This keeps a
        misbehaving subscriber from breaking the pipeline that published.
        """
        for handler in list(self._handlers.get(event, ())):
            try:
                handler(**payload)
            except Exception:
                logger.exception("Event handler for '%s' failed", event)

    def handlers(self, event: str) -> List[Handler]:
        """Return the handlers currently subscribed to ``event`` (for tests)."""
        return list(self._handlers.get(event, ()))


# Global bus shared across the app.
event_bus = EventBus()
