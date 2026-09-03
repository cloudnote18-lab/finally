"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.models import SourceStatus
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)  # Should get ~5 updates

        # Should have multiple updates with fast interval
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.1, event_probability=1.0
        )
        await source.start(["AAPL"])

        # Just verify it starts and stops cleanly
        await asyncio.sleep(0.2)
        await source.stop()

    async def test_open_price_seeded_from_start_price(self):
        """The first cache write's open_price should be the session seed."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        update = cache.get("AAPL")
        assert update.open_price == update.price  # nothing has moved yet

        await source.stop()

    async def test_open_price_stable_across_ticks(self):
        """open_price must stay fixed for the session even as price ticks."""
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.02, event_probability=0.0
        )
        await source.start(["AAPL"])
        first_open = cache.get("AAPL").open_price

        await asyncio.sleep(0.15)

        assert cache.get("AAPL").open_price == first_open
        await source.stop()

    async def test_seed_overrides_used_for_open_price(self):
        """Anchored (real-close) seeds should be used verbatim as the session open."""
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.1, seed_overrides={"AAPL": 500.00}
        )
        await source.start(["AAPL"])

        update = cache.get("AAPL")
        assert update.price == 500.00
        assert update.open_price == 500.00

        await source.stop()

    async def test_describe_returns_source_status(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        status = source.describe()
        assert isinstance(status, SourceStatus)
        assert status.name == "simulator"
        assert status.live is False
        assert status.tickers == 2
        assert status.cache_populated is True

        await source.stop()

    async def test_describe_before_start_reports_empty(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        status = source.describe()
        assert status.tickers == 0
        assert status.cache_populated is False

    async def test_describe_uses_custom_status_detail(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, status_detail="key rejected: bad key")
        status = source.describe()
        assert status.detail == "key rejected: bad key"

    async def test_set_status_detail_updates_describe(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        source.set_status_detail("re-anchored")
        assert source.describe().detail == "re-anchored"

    async def test_get_history_empty_before_start(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        history = await source.get_history("AAPL")
        assert history == []

    async def test_get_history_prefilled_on_start(self):
        """The chart must not be empty on first paint — start() prefills a
        ring buffer of history rather than waiting for live ticks."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        history = await source.get_history("AAPL")
        assert len(history) > 1
        # Timestamps should be non-decreasing and end at/near "now".
        assert all(a.timestamp <= b.timestamp for a, b in zip(history, history[1:]))

        await source.stop()

    async def test_get_history_respects_points_limit(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        history = await source.get_history("AAPL", points=5)
        assert len(history) == 5

        await source.stop()

    async def test_prefill_resets_live_price_to_seed(self):
        """The prefill run must not leave the simulator's live price at wherever
        the prefill's random walk happened to end up."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        update = cache.get("AAPL")
        assert update.price == update.open_price

        await source.stop()

    async def test_get_history_unknown_ticker_is_empty(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        assert await source.get_history("NOPE") == []

        await source.stop()

    async def test_add_ticker_history_starts_tracking(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        await asyncio.sleep(0.2)

        history = await source.get_history("TSLA")
        assert len(history) >= 1

        await source.stop()

    async def test_remove_ticker_clears_history(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.remove_ticker("AAPL")

        assert await source.get_history("AAPL") == []

        await source.stop()
