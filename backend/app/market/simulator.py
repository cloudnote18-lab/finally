"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PricePoint, SourceStatus, epoch_to_iso
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)

logger = logging.getLogger(__name__)

# Deterministic synthetic seed range for unknown tickers, e.g. $20.00-$500.00.
SYNTHETIC_SEED_MIN = 20.0
SYNTHETIC_SEED_SPAN_CENTS = 48_000  # (SEED_MAX - SEED_MIN) * 100


def synthesize_seed(ticker: str) -> float:
    """Stable pseudo-price for an unknown symbol. Same ticker, same price, always.

    Unlike a random draw, this survives container restarts — a held position's
    cost basis and the P&L chart stay consistent instead of jumping around.
    """
    digest = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)
    return round(SYNTHETIC_SEED_MIN + (digest % SYNTHETIC_SEED_SPAN_CENTS) / 100.0, 2)


@dataclass
class _Shock:
    """A transient, decaying price overlay simulating a sudden intraday move.

    Modeled as an additive overlay on top of the GBM path (not a permanent
    level shift), so a shock is visible as a spike that bleeds off over
    roughly a minute without permanently distorting the calibrated volatility.
    """

    magnitude: float  # signed fraction, e.g. -0.03
    decay: float = 0.985  # per-tick multiplier; ~half-life 46 ticks (~23s at 500ms)


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Where:
        S(t)   = current price
        mu     = annualized drift (expected return)
        sigma  = annualized volatility
        dt     = time step as fraction of a trading year
        Z      = correlated standard normal random variable

    The tiny dt (~8.5e-8 for 500ms ticks over 252 trading days * 6.5h/day)
    produces sub-cent moves per tick that accumulate naturally over time.
    """

    # 500ms expressed as a fraction of a trading year
    # 252 trading days * 6.5 hours/day * 3600 seconds/hour = 5,896,800 seconds
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    # Default event probability, calibrated so shocks add drama without
    # swamping the calibrated sigma: ~1.2 events per 10-minute demo across a
    # 10-ticker watchlist. See planning/MARKET_SIMULATOR.md §6.
    DEFAULT_EVENT_PROBABILITY = 1e-4

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = DEFAULT_EVENT_PROBABILITY,
        seed_overrides: dict[str, float] | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._seed_overrides = dict(seed_overrides or {})

        # Per-ticker state
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._shocks: dict[str, _Shock] = {}

        # Cholesky decomposition of the correlation matrix (for correlated moves)
        self._cholesky: np.ndarray | None = None

        # Initialize all starting tickers
        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance all tickers by one time step. Returns {ticker: new_price}.

        This is the hot path — called every 500ms. Keep it fast.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        # Generate n independent standard normal draws
        z_independent = np.random.standard_normal(n)

        # Apply Cholesky to get correlated draws
        if self._cholesky is not None:
            z_correlated = self._cholesky @ z_independent
        else:
            z_correlated = z_independent

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            params = self._params[ticker]
            mu = params["mu"]
            sigma = params["sigma"]

            # GBM: S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
            # This is the underlying, correctly-calibrated price. Shocks (below)
            # are a separate overlay and never touch this value directly.
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: a transient, decaying overlay — not a permanent
            # level shift. See _Shock docstring.
            if random.random() < self._event_prob:
                magnitude = random.uniform(0.015, 0.04) * random.choice([-1, 1])
                self._shocks[ticker] = _Shock(magnitude=magnitude)
                logger.debug("Shock event on %s: %.2f%%", ticker, magnitude * 100)

            shock = self._shocks.get(ticker)
            if shock is not None:
                displayed = self._prices[ticker] * (1 + shock.magnitude)
                shock.magnitude *= shock.decay
                if abs(shock.magnitude) < 1e-4:
                    del self._shocks[ticker]
            else:
                displayed = self._prices[ticker]

            result[ticker] = round(displayed, 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds the correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds the correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._shocks.pop(ticker, None)
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price for a ticker, or None if not tracked."""
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        return list(self._tickers)

    def reset_price(self, ticker: str, price: float) -> None:
        """Reset a ticker's price and clear any active shock, without touching correlation.

        Used to rewind the simulator after running it forward to prefill history,
        so live trading starts from the true seed rather than the prefill's endpoint.
        """
        if ticker in self._prices:
            self._prices[ticker] = price
            self._shocks.pop(ticker, None)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add a ticker without rebuilding Cholesky (for batch initialization)."""
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        seed = self._seed_overrides.get(ticker)
        if seed is None:
            seed = SEED_PRICES.get(ticker)
        if seed is None:
            seed = synthesize_seed(ticker)
        self._prices[ticker] = seed
        self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild the Cholesky decomposition of the ticker correlation matrix.

        Called whenever tickers are added or removed. O(n^2) but n < 100.
        """
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        # Build the correlation matrix
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho

        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # The shipped correlation structure is verified positive-definite at
            # any size (block-equicorrelated, min eigenvalue = 1 - rho_max), but a
            # future change to the structure could break that. Degrade to
            # independent moves rather than 500ing a watchlist add.
            logger.error(
                "Correlation matrix not positive-definite for %d tickers; "
                "falling back to uncorrelated moves",
                n,
            )
            self._cholesky = np.eye(n)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Determine correlation between two tickers based on sector grouping.

        Correlation structure:
          - Same tech sector:   0.6
          - Same finance sector: 0.5
          - TSLA with anything: 0.3 (it does its own thing)
          - Cross-sector:       0.3
          - Unknown tickers:    0.3
        """
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]

        # TSLA is in tech set but behaves independently
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR

        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR

        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that calls GBMSimulator.step() every
    `update_interval` seconds and writes results to the PriceCache.
    """

    # 600 ticks x 500ms = 5 minutes of history per ticker. ~480KB for 50 tickers.
    HISTORY_POINTS = 600

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = GBMSimulator.DEFAULT_EVENT_PROBABILITY,
        seed_overrides: dict[str, float] | None = None,
        status_detail: str | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed_overrides = dict(seed_overrides or {})
        self._status_detail = status_detail
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._open_prices: dict[str, float] = {}

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers=tickers,
            event_probability=self._event_prob,
            seed_overrides=self._seed_overrides,
        )
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._open_prices[ticker] = price

        self._prefill_history(tickers)

        # Seed the cache with initial prices so SSE has data immediately
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(
                    ticker=ticker, price=price, open_price=self._open_prices.get(ticker, price)
                )
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.add_ticker(ticker)
            price = self._sim.get_price(ticker)
            if price is not None:
                self._open_prices.setdefault(ticker, price)
                self._history.setdefault(ticker, deque(maxlen=self.HISTORY_POINTS))
                self._cache.update(
                    ticker=ticker, price=price, open_price=self._open_prices[ticker]
                )
            logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        self._history.pop(ticker, None)
        self._open_prices.pop(ticker, None)
        logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def describe(self) -> SourceStatus:
        return SourceStatus(
            name="simulator",
            live=False,
            detail=self._status_detail or "synthetic GBM simulation",
            tickers=len(self.get_tickers()),
            cache_populated=len(self._cache) > 0,
        )

    def set_status_detail(self, detail: str | None) -> None:
        """Allow a wrapping source (e.g. AnchoredSimulatorDataSource) to update the
        health detail after re-anchoring, without exposing the private attribute."""
        self._status_detail = detail

    async def get_history(self, ticker: str, points: int = 120) -> list[PricePoint]:
        series = self._history.get(ticker, ())
        return [PricePoint(epoch_to_iso(t), p) for t, p in list(series)[-points:]]

    async def _run_loop(self) -> None:
        """Core loop: step the simulation, write to cache, sleep."""
        while True:
            try:
                if self._sim:
                    prices = self._sim.step()
                    now = time.time()
                    for ticker, price in prices.items():
                        self._cache.update(
                            ticker=ticker,
                            price=price,
                            open_price=self._open_prices.get(ticker, price),
                        )
                        hist = self._history.setdefault(
                            ticker, deque(maxlen=self.HISTORY_POINTS)
                        )
                        hist.append((now, price))
            except Exception:
                logger.exception("Simulator step failed")  # never let one bad tick kill the loop
            await asyncio.sleep(self._interval)

    def _prefill_history(self, tickers: list[str]) -> None:
        """Run the simulator forward HISTORY_POINTS steps with no sleeping, recording
        the path, then reset every price to its seed. So the very first chart paint has
        five minutes of plausible history instead of a single dot.
        """
        if self._sim is None or not tickers:
            return

        now = time.time()
        paths: dict[str, deque[tuple[float, float]]] = {
            t: deque(maxlen=self.HISTORY_POINTS) for t in tickers
        }
        for i in range(self.HISTORY_POINTS):
            prices = self._sim.step()
            ts = now - (self.HISTORY_POINTS - i) * self._interval
            for ticker in tickers:
                if ticker in prices:
                    paths[ticker].append((ts, prices[ticker]))

        for ticker in tickers:
            self._history[ticker] = paths[ticker]
            seed = self._open_prices.get(ticker)
            if seed is not None:
                self._sim.reset_price(ticker, seed)
