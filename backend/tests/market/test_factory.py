"""Tests for the market data source factory."""

import os
from unittest.mock import patch

import pytest

from app.market.anchored import AnchoredSimulatorDataSource
from app.market.cache import PriceCache
from app.market.capabilities import MassiveCapabilities
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestFactory:
    """Tests for create_market_data_source. Never hits the network — probe_capabilities
    is always mocked."""

    async def test_creates_simulator_when_no_api_key(self):
        """No key → simulator, and the (network-calling) probe must not even run."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            with patch("app.market.factory.probe_capabilities") as mock_probe:
                source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        mock_probe.assert_not_called()

    async def test_creates_simulator_when_api_key_empty(self):
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            with patch("app.market.factory.probe_capabilities"):
                source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    async def test_creates_simulator_when_api_key_whitespace(self):
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            with patch("app.market.factory.probe_capabilities"):
                source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    async def test_creates_massive_when_realtime_entitled(self):
        cache = PriceCache()
        caps = MassiveCapabilities(True, True, True, "real-time snapshots entitled")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            with patch("app.market.factory.probe_capabilities", return_value=caps):
                source = await create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key"

    async def test_creates_anchored_simulator_when_end_of_day_only(self):
        """The free-tier case: a valid key that cannot return live prices must
        route to the anchored simulator, not a MassiveDataSource that would
        poll forever and never populate the cache."""
        cache = PriceCache()
        caps = MassiveCapabilities(True, False, True, "end-of-day only (Basic tier)")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            with patch("app.market.factory.probe_capabilities", return_value=caps):
                with patch("app.market.anchored.RESTClient"):
                    source = await create_market_data_source(cache)

        assert isinstance(source, AnchoredSimulatorDataSource)

    async def test_falls_back_to_simulator_when_key_rejected(self):
        """An invalid/revoked key must never boot into a broken state — it
        degrades to the simulator instead of a source that produces nothing."""
        cache = PriceCache()
        caps = MassiveCapabilities(False, False, False, "key rejected: 401")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            with patch("app.market.factory.probe_capabilities", return_value=caps):
                source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert "key rejected" in source.describe().detail

    async def test_probe_called_with_the_configured_key(self):
        cache = PriceCache()
        caps = MassiveCapabilities(True, True, True, "real-time snapshots entitled")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "specific-key-123"}, clear=True):
            with patch(
                "app.market.factory.probe_capabilities", return_value=caps
            ) as mock_probe:
                await create_market_data_source(cache)

        mock_probe.assert_called_once_with("specific-key-123")

    async def test_simulator_receives_cache(self):
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    async def test_massive_receives_cache(self):
        cache = PriceCache()
        caps = MassiveCapabilities(True, True, True, "real-time snapshots entitled")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            with patch("app.market.factory.probe_capabilities", return_value=caps):
                source = await create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache

    async def test_never_raises_on_probe_exception_paths(self):
        """probe_capabilities itself never raises (it catches everything), but
        the factory must still resolve to a usable source for every capability
        combination it can return."""
        cache = PriceCache()
        caps = MassiveCapabilities(False, False, False, "unreachable/rate-limited: timeout")

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            with patch("app.market.factory.probe_capabilities", return_value=caps):
                source = await create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
