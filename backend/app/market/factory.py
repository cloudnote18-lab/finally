"""Factory for creating market data sources."""

from __future__ import annotations

import asyncio
import logging
import os

from .anchored import AnchoredSimulatorDataSource
from .cache import PriceCache
from .capabilities import probe_capabilities
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


async def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Select a market data source. Never raises; always returns a working source.

    Async because, when MASSIVE_API_KEY is set, this makes real network calls to
    probe entitlement. Call once, from the FastAPI lifespan handler, before
    start() is invoked.

    A Massive key can be valid but unable to return live prices (Basic/free
    tier is end-of-day only). Selection is therefore driven by a one-time
    capability probe rather than by key presence alone:

    - No key                    -> SimulatorDataSource (synthetic seeds)
    - Key, real-time entitled   -> MassiveDataSource (live snapshots)
    - Key, end-of-day only      -> AnchoredSimulatorDataSource (real closes, synthetic motion)
    - Key, invalid/rejected     -> SimulatorDataSource (never boot into a broken state)
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if not api_key:
        logger.info("No MASSIVE_API_KEY — using GBM simulator")
        return SimulatorDataSource(price_cache)

    caps = await asyncio.to_thread(probe_capabilities, api_key)

    if caps.realtime:
        logger.info("Massive: real-time entitled — using live snapshots")
        return MassiveDataSource(api_key, price_cache, poll_interval=5.0)

    if caps.end_of_day:
        logger.warning(
            "Massive key is end-of-day only (Basic tier). Anchoring the simulator "
            "to real closing prices — displayed prices are simulated, not live."
        )
        return AnchoredSimulatorDataSource(api_key, price_cache)

    logger.error("MASSIVE_API_KEY rejected (%s) — falling back to simulator", caps.detail)
    return SimulatorDataSource(price_cache, status_detail=f"key rejected: {caps.detail}")
