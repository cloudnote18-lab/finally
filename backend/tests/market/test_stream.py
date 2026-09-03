"""Tests for the SSE streaming generator.

planning/MARKET_INTERFACE.md §8 (PLAN.md §13.4 item 31) drops the Playwright
"disconnect and verify reconnection" E2E case in favor of covering the server
side here — an EventSource's auto-reconnect is a browser behavior we can't
usefully re-test, but the heartbeat and disconnect-detection logic in
`_generate_events` are ours to verify directly.
"""

from unittest.mock import MagicMock

import pytest

from app.market.cache import PriceCache
from app.market.stream import _generate_events


def _make_request(disconnected_after: int | None = None) -> MagicMock:
    """A fake Request whose is_disconnected() returns True after N calls."""
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")

    calls = {"n": 0}

    async def is_disconnected():
        calls["n"] += 1
        if disconnected_after is None:
            return False
        return calls["n"] > disconnected_after

    request.is_disconnected = is_disconnected
    return request


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_event_is_retry_directive(self):
        cache = PriceCache()
        request = _make_request(disconnected_after=0)

        events = [e async for e in _generate_events(cache, request, interval=0.01)]
        assert events[0] == "retry: 1000\n\n"

    async def test_sends_data_when_cache_populated(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        request = _make_request(disconnected_after=1)

        events = [e async for e in _generate_events(cache, request, interval=0.01)]
        data_events = [e for e in events if e.startswith("data:")]
        assert len(data_events) == 1
        assert "AAPL" in data_events[0]

    async def test_no_data_event_when_cache_empty(self):
        cache = PriceCache()
        request = _make_request(disconnected_after=1)

        events = [e async for e in _generate_events(cache, request, interval=0.01)]
        data_events = [e for e in events if e.startswith("data:")]
        assert data_events == []

    async def test_stops_on_disconnect(self):
        cache = PriceCache()
        request = _make_request(disconnected_after=0)

        events = [e async for e in _generate_events(cache, request, interval=0.01)]
        # Only the initial retry directive — the loop must exit before sleeping/looping.
        assert events == ["retry: 1000\n\n"]

    async def test_only_sends_once_per_version_change(self):
        """Re-fetching an unchanged cache must not re-emit the same data event."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        request = _make_request(disconnected_after=3)

        events = [e async for e in _generate_events(cache, request, interval=0.01)]
        data_events = [e for e in events if e.startswith("data:")]
        assert len(data_events) == 1

    async def test_heartbeat_sent_after_silence(self):
        """No price change for longer than heartbeat_interval → an SSE comment ping,
        so a proxy or idle Massive poll doesn't silently drop the connection."""
        cache = PriceCache()
        request = _make_request(disconnected_after=2)

        events = [
            e
            async for e in _generate_events(
                cache, request, interval=0.01, heartbeat_interval=0.0
            )
        ]
        assert any(e == ": ping\n\n" for e in events)

    async def test_no_heartbeat_before_interval_elapses(self):
        cache = PriceCache()
        request = _make_request(disconnected_after=2)

        events = [
            e
            async for e in _generate_events(
                cache, request, interval=0.01, heartbeat_interval=1000.0
            )
        ]
        assert not any(e == ": ping\n\n" for e in events)

    async def test_new_data_resets_heartbeat_clock(self):
        """A version bump should count as activity — no ping should immediately
        follow a fresh data event."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        request = _make_request(disconnected_after=1)

        events = [
            e
            async for e in _generate_events(
                cache, request, interval=0.01, heartbeat_interval=0.0
            )
        ]
        # First event after retry is the data event, not a ping, even though
        # heartbeat_interval is 0 — a version change takes priority.
        assert events[1].startswith("data:")
