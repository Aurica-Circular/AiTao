# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the in-process EventBus (US-26).

Covers subscribe/publish with payload, multiple handlers, unsubscribe, the
unknown-event no-op, and — the key guarantee — that a handler raising an
exception is isolated: it does not break publish() or the other handlers.
"""

from aitao.core.events import (
    DOCUMENT_INDEXED,
    EventBus,
)


def test_publish_calls_handler_with_payload():
    bus = EventBus()
    received = {}

    def handler(**payload):
        received.update(payload)

    bus.subscribe(DOCUMENT_INDEXED, handler)
    bus.publish(DOCUMENT_INDEXED, doc_id="abc", path="/x/f.txt")

    assert received == {"doc_id": "abc", "path": "/x/f.txt"}


def test_multiple_handlers_all_run():
    bus = EventBus()
    calls = []

    bus.subscribe("evt", lambda **_: calls.append("a"))
    bus.subscribe("evt", lambda **_: calls.append("b"))
    bus.publish("evt")

    assert calls == ["a", "b"]


def test_failing_handler_is_isolated():
    bus = EventBus()
    calls = []

    def boom(**_):
        raise RuntimeError("handler blew up")

    bus.subscribe("evt", boom)
    bus.subscribe("evt", lambda **_: calls.append("after"))

    # publish must not raise, and the later handler must still run.
    bus.publish("evt")

    assert calls == ["after"]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    calls = []

    def handler(**_):
        calls.append(1)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    bus.publish("evt")

    assert calls == []
    # Unsubscribing an unknown handler is a no-op (must not raise).
    bus.unsubscribe("evt", handler)


def test_publish_unknown_event_is_noop():
    bus = EventBus()
    bus.publish("nobody.listening", foo=1)  # must not raise


def test_handlers_inspection():
    bus = EventBus()

    def handler(**_):
        pass

    assert bus.handlers("evt") == []
    bus.subscribe("evt", handler)
    assert bus.handlers("evt") == [handler]


def test_event_stats_counts_subscribed_events():
    from aitao.core.event_stats import EventStats
    from aitao.core.events import DOCUMENT_DELETED, DOCUMENT_INDEXED, SEARCH_EXECUTED

    bus = EventBus()
    stats = EventStats()
    stats.subscribe_to(bus)

    bus.publish(DOCUMENT_INDEXED, doc_id="a")
    bus.publish(DOCUMENT_INDEXED, doc_id="b")
    bus.publish(SEARCH_EXECUTED, query="x")

    assert stats.counts[DOCUMENT_INDEXED] == 2
    assert stats.counts[SEARCH_EXECUTED] == 1
    assert stats.counts[DOCUMENT_DELETED] == 0
