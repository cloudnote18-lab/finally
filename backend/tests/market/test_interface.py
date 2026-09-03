"""Tests for the MarketDataSource abstract contract."""

import pytest

from app.market.interface import MarketDataSource


class _MinimalSource(MarketDataSource):
    """Implements only the abstract members, to exercise the default get_history()."""

    async def start(self, tickers):
        pass

    async def stop(self):
        pass

    async def add_ticker(self, ticker):
        pass

    async def remove_ticker(self, ticker):
        pass

    def get_tickers(self):
        return []

    def describe(self):
        from app.market.models import SourceStatus

        return SourceStatus(name="minimal", live=False, detail="", tickers=0, cache_populated=False)


class TestMarketDataSource:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            MarketDataSource()

    def test_subclass_missing_describe_cannot_instantiate(self):
        class _Incomplete(MarketDataSource):
            async def start(self, tickers):
                pass

            async def stop(self):
                pass

            async def add_ticker(self, ticker):
                pass

            async def remove_ticker(self, ticker):
                pass

            def get_tickers(self):
                return []

        with pytest.raises(TypeError):
            _Incomplete()

    @pytest.mark.asyncio
    async def test_default_get_history_returns_empty_list(self):
        source = _MinimalSource()
        assert await source.get_history("AAPL") == []
