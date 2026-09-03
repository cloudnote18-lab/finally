"""Massive (Polygon.io) API client for real market data.

Only usable by keys entitled to real-time snapshots (Advanced tier or above,
$199/mo). Lower tiers authenticate but get NOT_AUTHORIZED on every snapshot
call — factory.py probes entitlement once at startup and routes those keys to
AnchoredSimulatorDataSource instead. See planning/MASSIVE_API.md.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

import urllib3.exceptions
from massive import RESTClient
from massive.exceptions import AuthError, BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PricePoint, SourceStatus, epoch_to_iso

logger = logging.getLogger(__name__)

NANOSECONDS_PER_SECOND = 1e9


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    `retries=0` on the client deliberately: the SDK's default retry/backoff is
    entirely inside the same rate-limit window, so a retry on 429 is guaranteed
    to fail too — it just burns budget. Poll-level backoff (the interval itself)
    is what matters.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._unknown_tickers: set[str] = set()
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._live = False
        self._last_error: str | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key, retries=0)
        self._tickers = [t.upper().strip() for t in tickers]

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._unknown_tickers.discard(ticker)
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def describe(self) -> SourceStatus:
        detail = self._last_error or "real-time snapshots"
        if self._unknown_tickers:
            detail += f"; not returned by last poll: {', '.join(sorted(self._unknown_tickers))}"
        return SourceStatus(
            name="massive",
            live=self._live,
            detail=detail,
            tickers=len(self._tickers),
            cache_populated=len(self._cache) > 0,
        )

    async def get_history(self, ticker: str, points: int = 120) -> list[PricePoint]:
        """Real intraday minute bars for the current session (paid-tier only)."""
        if not self._client:
            return []
        try:
            bars = await asyncio.to_thread(
                self._fetch_today_minute_bars, ticker, points
            )
        except Exception as e:
            logger.warning("Massive get_history failed for %s: %s", ticker, e)
            return []
        return [PricePoint(epoch_to_iso(bar.timestamp / 1000), bar.close) for bar in bars[-points:]]

    def _fetch_today_minute_bars(self, ticker: str, points: int) -> list:
        today = datetime.date.today().isoformat()
        return self._client.get_aggs(ticker, 1, "minute", today, today, limit=max(points, 1))

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        if not self._tickers or not self._client:
            return

        try:
            # The Massive RESTClient is synchronous — run in a thread to
            # avoid blocking the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
        except AuthError as e:
            self._live = False
            self._last_error = f"auth error: {e}"
            logger.error("Massive poll failed (auth): %s", e)
            return
        except urllib3.exceptions.MaxRetryError as e:
            # Rate limited or unreachable. Do not retry within this cycle — the
            # next scheduled poll is the backoff.
            self._live = False
            self._last_error = "rate limited or unreachable"
            logger.warning("Massive poll rate-limited/unreachable: %s", e)
            return
        except BadResponse as e:
            self._live = False
            self._last_error = str(e)
            logger.error("Massive poll failed (bad response): %s", e)
            return
        except Exception as e:
            self._live = False
            self._last_error = str(e)
            logger.error("Massive poll failed: %s", e)
            return

        processed = 0
        seen: set[str] = set()
        for snap in snapshots:
            ticker = getattr(snap, "ticker", None)
            if ticker:
                seen.add(ticker)
            try:
                last_trade = snap.last_trade
                if last_trade is None:
                    raise AttributeError("snapshot has no last_trade")
                price = last_trade.price
                # Massive snapshot timestamps are Unix NANOSECONDS on sip_timestamp
                # (not `timestamp`, and not milliseconds — see planning/MASSIVE_API.md §6).
                timestamp = last_trade.sip_timestamp / NANOSECONDS_PER_SECOND
                open_price = snap.prev_day.close if snap.prev_day else None
                self._cache.update(
                    ticker=ticker,
                    price=price,
                    timestamp=timestamp,
                    open_price=open_price,
                )
                processed += 1
            except (AttributeError, TypeError) as e:
                logger.warning(
                    "Skipping snapshot for %s: %s",
                    ticker or "???",
                    e,
                )

        # Tickers we asked for but that never appeared in the response —
        # surfaced via describe() rather than vanishing silently.
        self._unknown_tickers = {t for t in self._tickers if t not in seen}
        self._live = processed > 0
        if processed:
            self._last_error = None
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))

    def _fetch_snapshots(self) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
