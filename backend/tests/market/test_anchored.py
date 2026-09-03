"""Tests for AnchoredSimulatorDataSource. No test hits the network."""

from unittest.mock import MagicMock, patch

import pytest

from app.market.anchored import AnchoredSimulatorDataSource
from app.market.cache import PriceCache
from app.market.models import SourceStatus


def _bar(ticker: str, close: float) -> MagicMock:
    bar = MagicMock()
    bar.ticker = ticker
    bar.close = close
    return bar


@pytest.mark.asyncio
class TestAnchoredSimulatorDataSource:
    async def test_start_anchors_to_grouped_daily_close(self):
        """A single get_grouped_daily_aggs call should price the whole watchlist."""
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [
            _bar("AAPL", 324.96),
            _bar("GOOGL", 337.12),
            _bar("OTHERTICKER", 12.34),
        ]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL", "GOOGL"])

        assert source._anchors == {"AAPL": 324.96, "GOOGL": 337.12}
        assert mock_client.get_grouped_daily_aggs.call_count == 1

        await source.stop()

    async def test_anchored_prices_seed_the_cache(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]
        cache = PriceCache()

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=cache, reanchor_interval=0
            )
            await source.start(["AAPL"])

        update = cache.get("AAPL")
        assert update.price == 324.96
        assert update.open_price == 324.96

        await source.stop()

    async def test_fetch_anchors_walks_back_over_a_weekend(self):
        """Saturday and Sunday return empty results; Friday's close should be used."""
        mock_client = MagicMock()
        # today() - 1 day = Saturday (empty), -2 = Friday (populated)
        mock_client.get_grouped_daily_aggs.side_effect = [
            [],  # Saturday
            [_bar("AAPL", 324.96)],  # Friday
        ]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache()
            )
            anchors, anchor_date = source._fetch_anchors(["AAPL"])

        assert anchors == {"AAPL": 324.96}
        assert anchor_date is not None
        assert mock_client.get_grouped_daily_aggs.call_count == 2

    async def test_fetch_anchors_falls_back_after_repeated_failures(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.side_effect = Exception("network error")

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache()
            )
            anchors, anchor_date = source._fetch_anchors(["AAPL"])

        assert anchors == {}
        assert anchor_date is None
        assert mock_client.get_grouped_daily_aggs.call_count == 7  # MAX_ANCHOR_LOOKBACK_DAYS

    async def test_start_falls_back_to_static_seeds_when_anchor_fetch_fails(self):
        """If Massive is unreachable at startup, the simulator must still start
        (with the static seed table) rather than dead-ending the app."""
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.side_effect = Exception("network error")

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])

        assert "AAPL" in source.get_tickers()
        assert source.describe().detail == "simulated from static seed prices (anchor fetch failed)"

        await source.stop()

    async def test_describe_reports_anchor_date_and_count(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])

        status = source.describe()
        assert isinstance(status, SourceStatus)
        assert status.name == "anchored-simulator"
        assert status.live is False  # simulated, never presented as live
        assert "1 anchored" in status.detail

        await source.stop()

    async def test_add_ticker_delegates_to_underlying_simulator(self):
        """Adding an unanchored symbol must never dead-end — the underlying
        simulator synthesizes a seed for it."""
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]
        cache = PriceCache()

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=cache, reanchor_interval=0
            )
            await source.start(["AAPL"])
            await source.add_ticker("ZZZZ")

        assert "ZZZZ" in source.get_tickers()
        assert cache.get("ZZZZ") is not None

        await source.stop()

    async def test_remove_ticker_delegates_to_underlying_simulator(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]
        cache = PriceCache()

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=cache, reanchor_interval=0
            )
            await source.start(["AAPL"])
            await source.remove_ticker("AAPL")

        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

        await source.stop()

    async def test_get_history_uses_real_minute_bars(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]
        minute_bar = MagicMock(timestamp=1757000000000, close=325.10)
        mock_client.get_aggs.return_value = [minute_bar]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])
            history = await source.get_history("AAPL")

        assert len(history) == 1
        assert history[0].price == 325.10

        await source.stop()

    async def test_get_history_empty_when_no_anchor_date(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.side_effect = Exception("network error")

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])
            history = await source.get_history("AAPL")

        assert history == []

        await source.stop()

    async def test_stop_is_idempotent(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])
            await source.stop()
            await source.stop()  # should not raise

    async def test_reanchor_task_started_when_interval_positive(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=3600.0
            )
            await source.start(["AAPL"])

        assert source._reanchor_task is not None
        assert not source._reanchor_task.done()

        await source.stop()
        assert source._reanchor_task is None

    async def test_no_reanchor_task_when_interval_zero(self):
        mock_client = MagicMock()
        mock_client.get_grouped_daily_aggs.return_value = [_bar("AAPL", 324.96)]

        with patch("app.market.anchored.RESTClient", return_value=mock_client):
            source = AnchoredSimulatorDataSource(
                api_key="basic-key", price_cache=PriceCache(), reanchor_interval=0
            )
            await source.start(["AAPL"])

        assert source._reanchor_task is None

        await source.stop()
