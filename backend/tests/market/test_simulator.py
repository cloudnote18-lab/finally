"""Tests for GBMSimulator."""

import math
import random

import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator, synthesize_seed


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_cholesky_survives_many_unknown_tickers(self):
        """The correlation matrix must stay positive-definite as tickers are added
        one at a time (the incremental path, not the batch constructor) — a failure
        here would 500 a watchlist add."""
        sim = GBMSimulator(tickers=["AAPL"])
        for i in range(100):
            sim.add_ticker(f"ZZ{i:03d}")
        assert sim._cholesky is not None
        result = sim.step()
        assert len(result) == 101

    def test_cholesky_linalg_error_falls_back_to_identity(self, monkeypatch):
        """A non-positive-definite matrix must degrade to uncorrelated moves,
        not raise and crash the caller (e.g. a watchlist add)."""

        def raise_linalg_error(_matrix):
            raise np.linalg.LinAlgError("not positive definite")

        sim = GBMSimulator(tickers=["AAPL"])
        monkeypatch.setattr(np.linalg, "cholesky", raise_linalg_error)
        sim.add_ticker("GOOGL")  # triggers _rebuild_cholesky with 2 tickers

        assert sim._cholesky is not None
        np.testing.assert_array_equal(sim._cholesky, np.eye(2))
        # Simulator must still be usable.
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_default_event_probability_is_calibrated(self):
        """Regression guard: event_probability must stay at the calibrated 1e-4,
        not the old 0.001 which inflated realized volatility ~20x
        (planning/MARKET_SIMULATOR.md §6)."""
        assert GBMSimulator.DEFAULT_EVENT_PROBABILITY == 1e-4

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if "." in price_str:
            decimal_part = price_str.split(".")[1]
            assert len(decimal_part) <= 2

    def test_reset_price(self):
        """reset_price rewinds the price and clears any active shock."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.step()
        sim.reset_price("AAPL", 999.99)
        assert sim.get_price("AAPL") == 999.99
        assert "AAPL" not in sim._shocks

    def test_reset_price_unknown_ticker_is_noop(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.reset_price("NOPE", 1.0)  # should not raise


class TestSynthesizeSeed:
    """Unit tests for deterministic unknown-ticker seed synthesis.

    Regression guard for planning/MARKET_SIMULATOR.md §3: unknown tickers used
    to get `random.uniform(50, 300)`, a different price every restart, which
    made a held position's cost basis jump and lied to the P&L chart.
    """

    def test_deterministic_across_calls(self):
        assert synthesize_seed("ZZZZ") == synthesize_seed("ZZZZ")

    def test_deterministic_across_fresh_instances(self):
        """Same ticker, same price, across two independently constructed simulators."""
        sim1 = GBMSimulator(tickers=["ZZZZ"])
        sim2 = GBMSimulator(tickers=["ZZZZ"])
        assert sim1.get_price("ZZZZ") == sim2.get_price("ZZZZ")

    def test_different_tickers_differ(self):
        # Not a hard guarantee for arbitrary hashes, but true for these symbols
        # and a good smoke test that the hash actually depends on the ticker.
        assert synthesize_seed("ZZZZ") != synthesize_seed("QQQQ")

    def test_in_expected_range(self):
        for ticker in ["ZZZZ", "QQQQ", "FOOBAR", "X"]:
            price = synthesize_seed(ticker)
            assert 20.0 <= price <= 500.0

    def test_unknown_ticker_uses_synthesized_seed(self):
        sim = GBMSimulator(tickers=["ZZZZ"])
        assert sim.get_price("ZZZZ") == synthesize_seed("ZZZZ")

    def test_unknown_ticker_uses_default_params(self):
        sim = GBMSimulator(tickers=["ZZZZ"])
        assert sim._params["ZZZZ"]["sigma"] == 0.25
        assert sim._params["ZZZZ"]["mu"] == 0.05


class TestSeedOverrides:
    """Unit tests for anchoring the simulator to real (Massive-sourced) prices."""

    def test_seed_override_takes_priority_over_static_table(self):
        sim = GBMSimulator(tickers=["AAPL"], seed_overrides={"AAPL": 999.99})
        assert sim.get_price("AAPL") == 999.99

    def test_seed_override_used_for_unknown_ticker(self):
        sim = GBMSimulator(tickers=["ZZZZ"], seed_overrides={"ZZZZ": 42.42})
        assert sim.get_price("ZZZZ") == 42.42

    def test_missing_override_falls_back_to_static_table(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"], seed_overrides={"AAPL": 999.99})
        assert sim.get_price("GOOGL") == SEED_PRICES["GOOGL"]

    def test_missing_override_and_missing_table_entry_synthesizes(self):
        sim = GBMSimulator(tickers=["ZZZZ"], seed_overrides={"AAPL": 999.99})
        assert sim.get_price("ZZZZ") == synthesize_seed("ZZZZ")


class TestShockDynamics:
    """Unit tests for the decaying shock overlay.

    Regression guard for the historical bug: shocks used to be a *permanent*
    multiplicative level shift, which — at the old event_probability — inflated
    realized volatility roughly 20x and made every per-ticker sigma decorative
    (planning/MARKET_SIMULATOR.md §6). Shocks must now decay back toward the
    underlying (still-calibrated) GBM path.
    """

    def test_shock_is_applied_on_trigger(self, monkeypatch):
        monkeypatch.setattr(random, "random", lambda: 0.0)  # always triggers
        monkeypatch.setattr(random, "uniform", lambda a, b: 0.04)
        monkeypatch.setattr(random, "choice", lambda seq: 1)

        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0)
        seed = sim.get_price("AAPL")
        result = sim.step()

        assert "AAPL" in sim._shocks
        # Displayed price reflects the +4% shock (approximately, modulo the
        # underlying GBM drift/diffusion for this one tick).
        assert result["AAPL"] > seed

    def test_underlying_price_is_never_permanently_shifted_by_a_shock(self, monkeypatch):
        """The GBM path (`_prices`) must stay independent of the shock overlay —
        only the *displayed* price reflects the shock."""
        monkeypatch.setattr(random, "random", lambda: 0.0)
        monkeypatch.setattr(random, "uniform", lambda a, b: 0.04)
        monkeypatch.setattr(random, "choice", lambda seq: 1)

        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0)
        sim.step()
        underlying = sim._prices["AAPL"]
        displayed = sim.step()["AAPL"]

        # The shock inflates the displayed price above the underlying GBM price.
        assert displayed != round(underlying, 2)

    def test_shock_decays_and_does_not_grow(self, monkeypatch):
        monkeypatch.setattr(random, "random", lambda: 0.0)
        monkeypatch.setattr(random, "uniform", lambda a, b: 0.04)
        monkeypatch.setattr(random, "choice", lambda seq: 1)

        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0)
        sim.step()
        initial_magnitude = abs(sim._shocks["AAPL"].magnitude)

        # Stop triggering new shocks; let the existing one decay.
        sim._event_prob = 0.0
        for _ in range(50):
            sim.step()

        remaining = sim._shocks.get("AAPL")
        if remaining is not None:
            assert abs(remaining.magnitude) < initial_magnitude

    def test_shock_eventually_expires(self, monkeypatch):
        monkeypatch.setattr(random, "random", lambda: 0.0)
        monkeypatch.setattr(random, "uniform", lambda a, b: 0.03)
        monkeypatch.setattr(random, "choice", lambda seq: 1)

        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0)
        sim.step()
        sim._event_prob = 0.0  # no further triggers
        for _ in range(500):
            sim.step()

        assert "AAPL" not in sim._shocks


class TestStatisticalCalibration:
    """Lighter-weight statistical regression tests.

    These are smoke tests, not the full empirical study in
    planning/MARKET_SIMULATOR.md §2/§6 (200 one-day runs is too slow for a
    unit test) — but they use generous, one-sided bounds specifically chosen
    to catch a regression back to the old, uncalibrated event process
    (0.001 probability, permanent shifts), which produced roughly a
    10%-per-hour realized sd against a ~0.5% theoretical target.
    """

    def test_pure_gbm_daily_volatility_is_within_theory(self):
        """With shocks disabled, realized log-return sd over many short runs
        should track sigma * sqrt(steps * dt)."""
        np.random.seed(42)
        sigma = TICKER_PARAMS["AAPL"]["sigma"]
        steps = 2000
        runs = 60

        log_returns = []
        for _ in range(runs):
            sim = GBMSimulator(tickers=["AAPL"], event_probability=0.0)
            start = sim.get_price("AAPL")
            for _ in range(steps):
                sim.step()
            end = sim.get_price("AAPL")
            log_returns.append(math.log(end / start))

        realized_sd = float(np.std(log_returns))
        theoretical_sd = sigma * math.sqrt(steps * GBMSimulator.DEFAULT_DT)

        # Generous tolerance band (statistical test, not an exact bound).
        assert theoretical_sd * 0.4 < realized_sd < theoretical_sd * 2.5

    def test_default_event_probability_does_not_blow_up_volatility(self):
        """The historical bug inflated realized volatility ~20x. At the
        calibrated default, realized sd should stay within a small multiple of
        the pure-GBM theoretical value — nowhere near the old ~20x blowup."""
        np.random.seed(7)
        random.seed(7)
        sigma = TICKER_PARAMS["AAPL"]["sigma"]
        steps = 3600  # ~30 minutes of trading time
        runs = 40

        log_returns = []
        for _ in range(runs):
            sim = GBMSimulator(tickers=["AAPL"])  # production default event_probability
            start = sim.get_price("AAPL")
            for _ in range(steps):
                sim.step()
            end = sim.get_price("AAPL")
            log_returns.append(math.log(end / start))

        realized_sd = float(np.std(log_returns))
        theoretical_sd = sigma * math.sqrt(steps * GBMSimulator.DEFAULT_DT)

        # The old bug produced ~20x theory; the fix should stay under ~6x even
        # with sampling noise from a modest run count.
        assert realized_sd < theoretical_sd * 6

    def test_correlation_is_applied_not_bypassed(self):
        """Realized correlation between two tech tickers should be
        substantially higher than between a tech and a finance ticker —
        confirms the Cholesky decomposition is actually wired in."""
        np.random.seed(11)
        steps = 3000

        sim = GBMSimulator(tickers=["AAPL", "GOOGL", "JPM"], event_probability=0.0)
        paths = {"AAPL": [], "GOOGL": [], "JPM": []}
        for _ in range(steps):
            prices = sim.step()
            for t in paths:
                paths[t].append(prices[t])

        def log_returns(series):
            arr = np.array(series, dtype=float)
            return np.diff(np.log(arr))

        tech_corr = float(np.corrcoef(log_returns(paths["AAPL"]), log_returns(paths["GOOGL"]))[0, 1])
        cross_corr = float(np.corrcoef(log_returns(paths["AAPL"]), log_returns(paths["JPM"]))[0, 1])

        assert tech_corr > cross_corr
        assert tech_corr > 0.3


class TestVisibleTickRate:
    """Regression guard for planning/MARKET_SIMULATOR.md §2: at real anchored
    price levels, the 2-decimal displayed price must actually change on most
    ticks, or the flash animation and sparklines look dead."""

    @pytest.mark.parametrize("ticker", list(SEED_PRICES.keys()))
    def test_visible_tick_rate_exceeds_fifty_percent(self, ticker):
        np.random.seed(3)
        sim = GBMSimulator(tickers=[ticker], event_probability=0.0)
        prices = [sim.get_price(ticker)]
        for _ in range(1500):
            prices.append(sim.step()[ticker])

        visible = sum(1 for a, b in zip(prices, prices[1:]) if a != b)
        rate = visible / (len(prices) - 1)
        assert rate > 0.5, f"{ticker} visible-tick rate {rate:.2%} too low"
