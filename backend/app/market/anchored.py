"""GBM simulation seeded from real Massive closing prices.

Bridges the gap for Basic-tier (free) Massive keys, which authenticate fine but
are not entitled to any live price. One free-tier API call
(`get_grouped_daily_aggs`) prices the whole market at once, so the simulator
starts from real closing levels — e.g. AAPL at its genuine close instead of a
hard-coded, rapidly stale seed — while GBM supplies the tick-to-tick motion so
the terminal still looks alive. See planning/MARKET_INTERFACE.md §6.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from massive import RESTClient

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PricePoint, SourceStatus, epoch_to_iso
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

MAX_ANCHOR_LOOKBACK_DAYS = 7  # walk back at most a week to skip weekends/holidays
DEFAULT_REANCHOR_INTERVAL_SECONDS = 3600.0  # re-anchor hourly so a long-running container
# tracks the next session's close instead of drifting from reality


class AnchoredSimulatorDataSource(MarketDataSource):
    """GBM simulation seeded from real Massive closing prices.

    Bridges the gap for Basic-tier keys: real price *levels* from one
    free-tier API call, plus synthetic price *motion* so the terminal is alive.
    Displayed prices are simulated, not live — describe().live is always False.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        reanchor_interval: float = DEFAULT_REANCHOR_INTERVAL_SECONDS,
    ) -> None:
        self._client = RESTClient(api_key=api_key, retries=0)
        self._cache = price_cache
        self._update_interval = update_interval
        self._reanchor_interval = reanchor_interval
        self._sim: SimulatorDataSource | None = None
        self._anchors: dict[str, float] = {}
        self._anchor_date: str | None = None
        self._reanchor_task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._anchors, self._anchor_date = await asyncio.to_thread(
            self._fetch_anchors, tickers
        )
        # Real closes where we have them; the static seed table covers the rest.
        self._sim = SimulatorDataSource(
            self._cache,
            update_interval=self._update_interval,
            seed_overrides=self._anchors,
            status_detail=self._status_detail(),
        )
        await self._sim.start(tickers)

        if self._reanchor_interval > 0:
            self._reanchor_task = asyncio.create_task(
                self._reanchor_loop(), name="anchored-reanchor"
            )

    async def stop(self) -> None:
        if self._reanchor_task and not self._reanchor_task.done():
            self._reanchor_task.cancel()
            try:
                await self._reanchor_task
            except asyncio.CancelledError:
                pass
        self._reanchor_task = None
        if self._sim:
            await self._sim.stop()

    async def add_ticker(self, ticker: str) -> None:
        # Costs nothing: the underlying simulator synthesizes a seed for any
        # symbol not in the anchor set, so the demo never dead-ends on an
        # unknown ticker.
        if self._sim:
            await self._sim.add_ticker(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            await self._sim.remove_ticker(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def describe(self) -> SourceStatus:
        return SourceStatus(
            name="anchored-simulator",
            live=False,
            detail=self._status_detail(),
            tickers=len(self.get_tickers()),
            cache_populated=len(self._cache) > 0,
        )

    async def get_history(self, ticker: str, points: int = 120) -> list[PricePoint]:
        """Real minute bars from the last completed session (free tier allows this)."""
        if not self._anchor_date:
            return []
        try:
            bars = await asyncio.to_thread(
                self._client.get_aggs,
                ticker,
                1,
                "minute",
                self._anchor_date,
                self._anchor_date,
                limit=50_000,
            )
        except Exception as e:
            logger.warning("Anchored get_history failed for %s: %s", ticker, e)
            return []
        return [PricePoint(epoch_to_iso(bar.timestamp / 1000), bar.close) for bar in bars[-points:]]

    # --- Internal ---

    def _fetch_anchors(self, tickers: list[str]) -> tuple[dict[str, float], str | None]:
        """ONE API call prices every ticker. Walks back over weekends/holidays."""
        wanted = {t.upper() for t in tickers}
        day = date.today()
        for _ in range(MAX_ANCHOR_LOOKBACK_DAYS):
            day -= timedelta(days=1)
            iso = day.isoformat()
            try:
                bars = self._client.get_grouped_daily_aggs(iso, adjusted=True)
            except Exception as e:
                logger.warning("Anchor fetch failed for %s: %s", iso, e)
                continue
            if not bars:
                continue  # weekend or holiday
            found = {bar.ticker: bar.close for bar in bars if bar.ticker in wanted}
            logger.info(
                "Anchored %d/%d tickers to %s closes", len(found), len(wanted), iso
            )
            return found, iso
        logger.warning("No anchors available — falling back to the static seed table")
        return {}, None

    def _status_detail(self) -> str:
        if self._anchor_date:
            return (
                f"simulated from real {self._anchor_date} closes "
                f"({len(self._anchors)} anchored)"
            )
        return "simulated from static seed prices (anchor fetch failed)"

    async def _reanchor_loop(self) -> None:
        """Periodically re-fetch anchors so a long-running container tracks the
        next session's close instead of drifting from reality."""
        while True:
            await asyncio.sleep(self._reanchor_interval)
            try:
                tickers = self.get_tickers()
                anchors, anchor_date = await asyncio.to_thread(
                    self._fetch_anchors, tickers
                )
                if anchors:
                    self._anchors, self._anchor_date = anchors, anchor_date
                    if self._sim:
                        self._sim.set_status_detail(self._status_detail())
            except Exception:
                logger.exception("Re-anchor failed")
