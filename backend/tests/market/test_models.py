"""Tests for market data models: PriceUpdate, PricePoint, SourceStatus."""

import pytest

from app.market.models import PricePoint, PriceUpdate, SourceStatus, epoch_to_iso


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            open_price=189.00,
            timestamp=1234567890.0,
        )
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.open_price == 189.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test tick-to-tick price change calculation."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, open_price=190.00
        )
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative tick-to-tick price change."""
        update = PriceUpdate(
            ticker="AAPL", price=189.50, previous_price=190.00, open_price=190.00
        )
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test tick-to-tick percentage change calculation (up)."""
        update = PriceUpdate(
            ticker="AAPL", price=190.00, previous_price=100.00, open_price=100.00
        )
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test tick-to-tick percentage change calculation (down)."""
        update = PriceUpdate(
            ticker="AAPL", price=100.00, previous_price=200.00, open_price=200.00
        )
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(
            ticker="AAPL", price=100.00, previous_price=0.00, open_price=100.00
        )
        assert update.change_percent == 0.0

    def test_tick_direction_up(self):
        """Test tick_direction calculation (up)."""
        update = PriceUpdate(
            ticker="AAPL", price=191.00, previous_price=190.00, open_price=190.00
        )
        assert update.tick_direction == "up"

    def test_tick_direction_down(self):
        """Test tick_direction calculation (down)."""
        update = PriceUpdate(
            ticker="AAPL", price=189.00, previous_price=190.00, open_price=190.00
        )
        assert update.tick_direction == "down"

    def test_tick_direction_flat(self):
        """Test tick_direction calculation (flat)."""
        update = PriceUpdate(
            ticker="AAPL", price=190.00, previous_price=190.00, open_price=190.00
        )
        assert update.tick_direction == "flat"

    def test_change_today(self):
        """Test the daily change is measured against open_price, not previous tick."""
        update = PriceUpdate(
            ticker="AAPL", price=195.00, previous_price=194.99, open_price=190.00
        )
        assert update.change_today == 5.00

    def test_change_percent_today(self):
        """Test the daily percentage change is measured against open_price."""
        update = PriceUpdate(
            ticker="AAPL", price=209.00, previous_price=208.99, open_price=190.00
        )
        assert update.change_percent_today == pytest.approx(10.0, abs=0.001)

    def test_change_percent_today_zero_open(self):
        """Test daily percentage change with a zero open_price (guard against ZeroDivisionError)."""
        update = PriceUpdate(
            ticker="AAPL", price=100.00, previous_price=100.00, open_price=0.00
        )
        assert update.change_percent_today == 0.0

    def test_change_today_independent_of_tick_change(self):
        """A ticker can be flat tick-to-tick but still up/down on the day."""
        update = PriceUpdate(
            ticker="AAPL", price=200.00, previous_price=200.00, open_price=190.00
        )
        assert update.tick_direction == "flat"
        assert update.change_today == 10.00
        assert update.change_percent_today == pytest.approx(5.263, abs=0.001)

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            open_price=180.00,
            timestamp=1234567890.0,
        )
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["open_price"] == 180.00
        assert result["timestamp"] == epoch_to_iso(1234567890.0)
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["tick_direction"] == "up"
        assert result["change_today"] == 10.50
        assert "change_percent_today" in result

    def test_to_dict_timestamp_is_iso_string(self):
        """Wire format is ISO 8601 UTC, not a raw float, per PLAN.md §13.1 item 6."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            open_price=190.00,
            timestamp=1234567890.0,
        )
        result = update.to_dict()
        assert isinstance(result["timestamp"], str)
        assert result["timestamp"].endswith("Z")

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, open_price=190.00
        )

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error


class TestEpochToIso:
    """Unit tests for the epoch_to_iso wire-format helper."""

    def test_returns_utc_z_suffix(self):
        assert epoch_to_iso(0).startswith("1970-01-01T00:00:00")
        assert epoch_to_iso(0).endswith("Z")

    def test_preserves_subsecond_precision(self):
        result = epoch_to_iso(1234567890.4137)
        assert "413700" in result or "413" in result


class TestPricePoint:
    """Unit tests for the PricePoint model."""

    def test_creation(self):
        point = PricePoint(timestamp="2026-09-03T17:42:11Z", price=325.41)
        assert point.timestamp == "2026-09-03T17:42:11Z"
        assert point.price == 325.41

    def test_to_dict(self):
        point = PricePoint(timestamp="2026-09-03T17:42:11Z", price=325.41)
        assert point.to_dict() == {"timestamp": "2026-09-03T17:42:11Z", "price": 325.41}

    def test_immutability(self):
        point = PricePoint(timestamp="2026-09-03T17:42:11Z", price=325.41)
        with pytest.raises(AttributeError):
            point.price = 100.0


class TestSourceStatus:
    """Unit tests for the SourceStatus model."""

    def test_creation(self):
        status = SourceStatus(
            name="simulator", live=False, detail="synthetic", tickers=10, cache_populated=True
        )
        assert status.name == "simulator"
        assert status.live is False
        assert status.tickers == 10
        assert status.cache_populated is True

    def test_to_dict(self):
        status = SourceStatus(
            name="massive", live=True, detail="real-time", tickers=5, cache_populated=True
        )
        assert status.to_dict() == {
            "name": "massive",
            "live": True,
            "detail": "real-time",
            "tickers": 5,
            "cache_populated": True,
        }

    def test_immutability(self):
        status = SourceStatus(
            name="simulator", live=False, detail="x", tickers=0, cache_populated=False
        )
        with pytest.raises(AttributeError):
            status.live = True
