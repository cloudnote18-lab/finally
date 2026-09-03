"""Tests for MassiveDataSource (mocked). No test hits the network."""

from unittest.mock import MagicMock, patch

import pytest
import urllib3.exceptions
from massive.exceptions import AuthError, BadResponse

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.models import SourceStatus


def _make_snapshot(
    ticker: str, price: float, sip_timestamp_ns: int, prev_close: float | None = None
) -> MagicMock:
    """Create a mock Massive snapshot object matching the real SDK's field names."""
    snap = MagicMock()
    snap.ticker = ticker
    snap.last_trade = MagicMock()
    snap.last_trade.price = price
    snap.last_trade.sip_timestamp = sip_timestamp_ns
    if prev_close is not None:
        snap.prev_day = MagicMock()
        snap.prev_day.close = prev_close
    else:
        snap.prev_day = None
    return snap


@pytest.mark.asyncio
class TestMassiveDataSource:
    """Unit tests for MassiveDataSource with mocked API."""

    async def test_poll_updates_cache(self):
        """Test that polling updates the cache."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,  # Long interval so the loop doesn't auto-poll
        )
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [
            _make_snapshot("AAPL", 190.50, 1707580800_000000000),
            _make_snapshot("GOOGL", 175.25, 1707580800_000000000),
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_nanosecond_timestamp_conversion(self):
        """Massive snapshot timestamps are sip_timestamp in NANOSECONDS, not
        `timestamp` in milliseconds. Regression guard for planning/MASSIVE_API.md
        §6: a naive /1000.0 on the wrong field is wrong by a factor of a million."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1605192894630916600)]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.timestamp == pytest.approx(1605192894.63, abs=0.01)

    async def test_open_price_captured_from_prev_day_close(self):
        """Regression guard: the daily change column has nowhere to get its
        baseline from unless prev_day.close is captured as open_price."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        mock_snapshots = [
            _make_snapshot("AAPL", 325.41, 1707580800_000000000, prev_close=324.96)
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.open_price == 324.96

    async def test_missing_prev_day_leaves_open_price_defaulted(self):
        """When prev_day is unavailable, open_price should fall back to the
        cache's own default (price on first write) rather than raising."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800_000000000)]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.open_price == 190.50

    async def test_malformed_snapshot_skipped(self):
        """Test that malformed snapshots are skipped gracefully."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        good_snap = _make_snapshot("AAPL", 190.50, 1707580800_000000000)
        bad_snap = MagicMock()
        bad_snap.ticker = "BAD"
        bad_snap.last_trade = None  # Will cause AttributeError

        with patch.object(source, "_fetch_snapshots", return_value=[good_snap, bad_snap]):
            await source._poll_once()

        # Good ticker processed, bad one skipped
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_auth_error_marks_not_live(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="bad-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", side_effect=AuthError("invalid key")):
            await source._poll_once()

        assert source.describe().live is False
        assert "auth" in source.describe().detail.lower()

    async def test_rate_limit_error_classified_distinctly(self):
        """Rate limiting arrives as urllib3.MaxRetryError, not BadResponse —
        planning/MASSIVE_API.md §7. A handler written as `except BadResponse`
        alone would miss it entirely."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        error = urllib3.exceptions.MaxRetryError(pool=MagicMock(), url="/x", reason="429")
        with patch.object(source, "_fetch_snapshots", side_effect=error):
            await source._poll_once()  # must not raise

        status = source.describe()
        assert status.live is False
        assert "rate limit" in status.detail.lower() or "unreachable" in status.detail.lower()

    async def test_bad_response_error_does_not_crash(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(
            source, "_fetch_snapshots", side_effect=BadResponse("NOT_AUTHORIZED")
        ):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None
        assert source.describe().live is False

    async def test_api_error_does_not_crash(self):
        """Test that unexpected errors don't crash the poller."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None  # No update happened

    async def test_successful_poll_marks_live(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(
            source, "_fetch_snapshots", return_value=[_make_snapshot("AAPL", 190.50, 1)]
        ):
            await source._poll_once()

        assert source.describe().live is True

    async def test_unknown_tickers_tracked(self):
        """Tickers requested but absent from the response should be surfaced,
        not silently dropped — planning/MARKET_INTERFACE.md §7."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL", "ZZZZ"]
        source._client = MagicMock()

        with patch.object(
            source, "_fetch_snapshots", return_value=[_make_snapshot("AAPL", 190.50, 1)]
        ):
            await source._poll_once()

        assert "ZZZZ" in source.describe().detail

    async def test_add_ticker(self):
        """Test adding a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_uppercase_normalization(self):
        """Test that tickers are normalized to uppercase."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("aapl")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_strips_whitespace(self):
        """Test that ticker whitespace is stripped."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("  AAPL  ")
        assert "AAPL" in source.get_tickers()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]

        tickers = source.get_tickers()
        assert tickers == ["AAPL", "GOOGL"]

    async def test_empty_tickers_skips_poll(self):
        """Test that polling is skipped when there are no tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = []

        # Should not call _fetch_snapshots
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_stop_is_idempotent(self):
        """Test that stop() can be called multiple times."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task(self):
        """Test that stop() cancels the polling task."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=10.0)

        # Mock the client and start
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        # Verify task is running
        assert source._task is not None
        assert not source._task.done()

        # Stop and verify task is cancelled
        await source.stop()
        assert source._task is None

    async def test_start_immediate_poll(self):
        """Test that start() does an immediate poll before starting the loop."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800_000000000)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
                await source.start(["AAPL"])

        # Cache should have data immediately from the first poll
        assert cache.get_price("AAPL") == 190.50

        await source.stop()

    async def test_start_constructs_client_with_no_retries(self):
        """retries=0 is deliberate: the SDK's default backoff is inside the
        same rate-limit window, so a retry on 429 is guaranteed to fail too —
        it only burns budget. planning/MASSIVE_API.md §7."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient") as mock_client_cls:
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("retries") == 0

        await source.stop()

    async def test_describe_before_start(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        status = source.describe()
        assert isinstance(status, SourceStatus)
        assert status.name == "massive"
        assert status.live is False
