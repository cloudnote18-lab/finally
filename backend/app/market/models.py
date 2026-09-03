"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def epoch_to_iso(timestamp: float) -> str:
    """Convert a Unix epoch (seconds) to an ISO 8601 UTC string, e.g. '...T17:42:11.413700Z'.

    Always includes microseconds (unlike `datetime.isoformat()`, which omits them
    when exactly zero) so every timestamp has a fixed-width format — plain string
    comparison then agrees with chronological order.
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time.

    `previous_price` is the previous *tick* (drives the flash animation only).
    `open_price` is fixed for the session (drives the daily change column) —
    the seed price for the simulator, the anchor close for the anchored
    simulator, or the previous day's close for Massive.
    """

    ticker: str
    price: float
    previous_price: float
    open_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        """Absolute price change from the previous tick."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous tick."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def tick_direction(self) -> str:
        """'up', 'down', or 'flat' — drives the CSS flash class."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def change_today(self) -> float:
        """Absolute change since the session open/anchor."""
        return round(self.price - self.open_price, 4)

    @property
    def change_percent_today(self) -> float:
        """Percentage change since the session open/anchor — drives the daily % column."""
        if self.open_price == 0:
            return 0.0
        return round((self.price - self.open_price) / self.open_price * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission. Timestamp is ISO 8601 UTC on the wire."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "open_price": self.open_price,
            "timestamp": epoch_to_iso(self.timestamp),
            "tick_direction": self.tick_direction,
            "change": self.change,
            "change_percent": self.change_percent,
            "change_today": self.change_today,
            "change_percent_today": self.change_percent_today,
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single point in a historical price series."""

    timestamp: str  # ISO 8601 UTC
    price: float

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "price": self.price}


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """Introspection for a MarketDataSource, surfaced via GET /api/health."""

    name: str  # "simulator" | "massive" | "anchored-simulator"
    live: bool  # True only when prices reflect the real current market
    detail: str  # human-readable, surfaced verbatim in /api/health
    tickers: int
    cache_populated: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "live": self.live,
            "detail": self.detail,
            "tickers": self.tickers,
            "cache_populated": self.cache_populated,
        }
