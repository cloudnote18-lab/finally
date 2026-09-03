"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    PricePoint          - Single point in a historical price series
    SourceStatus        - Introspection payload for GET /api/health
    PriceCache          - Thread-safe in-memory price store
    MarketDataSource    - Abstract interface for data providers
    create_market_data_source - Async factory; probes Massive entitlement and
                                 selects simulator / anchored-simulator / massive
    create_stream_router - FastAPI router factory for SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PricePoint, PriceUpdate, SourceStatus
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PricePoint",
    "SourceStatus",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
]
