"""Unit tests for :mod:`kompany.core.event_hub`."""

from __future__ import annotations

import asyncio

import pytest

from kompany.core.event_hub import EventHub


async def _drain_one(hub: EventHub) -> dict:
    """Subscribe and pull exactly one event, then exit."""
    async for evt in hub.subscribe():
        return evt
    raise AssertionError("subscription closed without yielding")


@pytest.mark.asyncio
async def test_publish_subscribe_single():
    hub = EventHub()

    async def subscriber() -> dict:
        gen = hub.subscribe()
        # Pull the first event; close the generator afterwards.
        result = await gen.__anext__()
        await gen.aclose()
        return result

    sub_task = asyncio.create_task(subscriber())
    # Give the subscriber a tick to enter ``await queue.get()``.
    await asyncio.sleep(0)
    hub.publish("audit.test", {"a": 1})

    received = await asyncio.wait_for(sub_task, timeout=1.0)
    assert received["type"] == "audit.test"
    assert received["data"] == {"a": 1}
    assert received["id"] == 1


@pytest.mark.asyncio
async def test_multiple_subscribers_each_get_event():
    hub = EventHub()

    async def subscriber():
        gen = hub.subscribe()
        result = await gen.__anext__()
        await gen.aclose()
        return result

    t1 = asyncio.create_task(subscriber())
    t2 = asyncio.create_task(subscriber())
    await asyncio.sleep(0)
    hub.publish("inbox.updated", {"reason": "created"})

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1["type"] == "inbox.updated"
    assert r2["type"] == "inbox.updated"


@pytest.mark.asyncio
async def test_backpressure_drops_oldest():
    # Manually register a queue exactly like ``subscribe`` does so we can
    # control consumption order: publish three events while no consumer
    # is reading, then drain the queue and assert the oldest was evicted.
    hub = EventHub(capacity=2)
    queue: asyncio.Queue = asyncio.Queue(maxsize=hub._capacity)
    hub._subscribers.add(queue)
    try:
        hub.publish("audit.a", {"v": 1})
        hub.publish("audit.b", {"v": 2})
        hub.publish("audit.c", {"v": 3})  # evicts ``audit.a``

        first = await asyncio.wait_for(queue.get(), timeout=0.5)
        second = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert first["data"]["v"] == 2
        assert second["data"]["v"] == 3
        assert queue.empty()
    finally:
        hub._subscribers.discard(queue)


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_close():
    hub = EventHub()
    assert hub.subscriber_count == 0
    gen = hub.subscribe()
    # Kick off ``__anext__`` so the generator body registers the queue.
    consumer = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    assert hub.subscriber_count == 1
    hub.publish("real", {"x": 1})
    evt = await asyncio.wait_for(consumer, timeout=0.5)
    assert evt["type"] == "real"
    await gen.aclose()
    assert hub.subscriber_count == 0


def test_publish_with_no_loop_is_noop():
    hub = EventHub()
    # No subscribers + no loop -> silent drop, no exception.
    hub.publish("audit.x", {"y": 2})
    assert hub.subscriber_count == 0
