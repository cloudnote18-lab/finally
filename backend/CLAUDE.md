# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, PricePoint, SourceStatus, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price` (previous tick), `open_price` (session baseline), `timestamp`, plus properties `change`/`change_percent` (tick-to-tick), `tick_direction` ("up"/"down"/"flat", drives the flash animation), `change_today`/`change_percent_today` (vs. `open_price`, drives the daily % column), and `to_dict()` for JSON serialization (timestamp is ISO 8601 UTC on the wire).

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, open_price=None) -> PriceUpdate` — `open_price` sticks for the session once set; omit it on subsequent calls to keep the existing baseline.
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource`, `AnchoredSimulatorDataSource`, and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`. Also: `describe() -> SourceStatus` (for `GET /api/health`) and `get_history(ticker, points=120) -> list[PricePoint]` (for chart first-paint; default `[]`).

- **`create_market_data_source(cache)`** — **Async** factory; must be awaited from the FastAPI lifespan handler before `start()`. Selection is driven by a one-time Massive entitlement probe (`capabilities.py`), not just key presence — a key that authenticates but isn't entitled to live prices (free/Basic tier) routes to `AnchoredSimulatorDataSource` (real closing prices, synthetic motion) rather than a `MassiveDataSource` that would poll forever and never populate the cache. See `planning/MARKET_INTERFACE.md` for the full decision table.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
