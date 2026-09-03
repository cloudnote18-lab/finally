# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Structure

- `app/` - Application code
  - `market/` - Market data subsystem
    - `models.py` - PriceUpdate/PricePoint/SourceStatus dataclasses
    - `cache.py` - Thread-safe price cache
    - `interface.py` - MarketDataSource abstract interface
    - `capabilities.py` - Massive API entitlement probe
    - `simulator.py` - GBM-based market simulator
    - `anchored.py` - Simulator seeded from real Massive closing prices (free-tier keys)
    - `massive_client.py` - Massive/Polygon.io API client (real-time tiers)
    - `factory.py` - Async, capability-driven data source factory
    - `stream.py` - SSE streaming endpoint (with heartbeat)
    - `seed_prices.py` - Default ticker prices and parameters

- `tests/` - Unit and integration tests
  - `market/` - Market data tests

## Running Tests

```bash
# Install dependencies
uv sync --dev

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/market/test_simulator.py

# Run with verbose output
uv run pytest -v
```

## Environment Variables

- `MASSIVE_API_KEY` - Optional. If set, use real market data from Massive API. If not set, use the built-in simulator.

## Development

```bash
# Install dependencies
uv sync --dev

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```
