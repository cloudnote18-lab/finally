"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat tick_direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.tick_direction == "flat"
        assert update.previous_price == 190.50

    def test_first_update_open_price_defaults_to_price(self):
        """On first write, open_price defaults to price when not given."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.open_price == 190.50

    def test_direction_up(self):
        """Test price update with upward tick_direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.tick_direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward tick_direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.tick_direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12

    def test_explicit_open_price(self):
        """Test that an explicit open_price is stored as given."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, open_price=180.00)
        assert update.open_price == 180.00

    def test_open_price_persists_across_updates(self):
        """open_price should stay fixed for the session unless explicitly changed."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, open_price=180.00)
        update = cache.update("AAPL", 195.00)  # no open_price given
        assert update.open_price == 180.00

    def test_open_price_can_be_updated_explicitly(self):
        """A source may explicitly move the baseline (e.g. Massive's prev_day.close)."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, open_price=180.00)
        update = cache.update("AAPL", 195.00, open_price=185.00)
        assert update.open_price == 185.00

    def test_open_price_rounded(self):
        """Test that open_price is rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, open_price=180.126)
        assert update.open_price == 180.13
