# Market Data — Summary

**Status:** Complete. Implements the design in `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`,
and `MASSIVE_API.md`. Lives entirely in `backend/app/market/`, with tests in
`backend/tests/market/`.

## What's There

- **`PriceCache`** (`cache.py`) — thread-safe in-memory store, one `PriceUpdate` per ticker,
  with a monotonic `version` counter for SSE change detection.
- **`PriceUpdate` / `PricePoint` / `SourceStatus`** (`models.py`) — `PriceUpdate` carries both
  a tick-to-tick delta (`previous_price`, `change`, `change_percent`, `tick_direction` — drives
  the flash animation) and a session-baseline delta (`open_price`, `change_today`,
  `change_percent_today` — drives the daily % column). Wire format is ISO 8601 UTC.
- **`MarketDataSource`** (`interface.py`) — the shared abstract contract: `start`/`stop`/
  `add_ticker`/`remove_ticker`/`get_tickers`, plus `describe()` (health introspection) and
  `get_history()` (chart first-paint backfill).
- **Three implementations**, selected by `create_market_data_source()` (`factory.py`, async)
  based on a one-time Massive entitlement probe (`capabilities.py`) rather than key presence
  alone — a free/Basic-tier key authenticates but can't return a live price, so it is routed
  away from `MassiveDataSource` instead of silently producing an empty watchlist:
  - **`SimulatorDataSource`** (`simulator.py`) — no key. GBM price motion, correlated across a
    sector structure, with a deterministic (hash-based) seed for unknown tickers and a
    decaying (not permanent) shock overlay for occasional drama. Keeps a 5-minute ring buffer
    per ticker, prefilled at startup so charts aren't empty on first paint.
  - **`AnchoredSimulatorDataSource`** (`anchored.py`) — key present but end-of-day only (the
    free tier). One API call (`get_grouped_daily_aggs`) anchors the whole watchlist to real
    closing prices; the GBM simulator then supplies tick-to-tick motion on top. Re-anchors
    hourly. Badged as simulated (`SourceStatus.live == False`).
  - **`MassiveDataSource`** (`massive_client.py`) — key entitled to real-time snapshots (paid
    tiers). Polls `get_snapshot_all` on an interval; corrected nanosecond `sip_timestamp`
    handling, captures `prev_day.close` as `open_price`, classifies rate-limit errors
    distinctly from bad responses, and surfaces tickers missing from a poll via `describe()`.
  - Any key state that can't be classified falls back to `SimulatorDataSource` — the app never
    boots into a broken, empty-watchlist state.
- **SSE streaming** (`stream.py`) — `GET /api/stream/prices`, full price map on every cache
  version change, with an SSE comment heartbeat (`: ping`) when the stream would otherwise sit
  silent (matters most against a 15s Massive poll interval or a closed market).
- **`seed_prices.py`** — static fallback seeds/volatility/correlation params for the no-key
  case, kept current against real closing prices.

## Testing

`backend/tests/market/` covers all of the above with mocked Massive calls (no test hits the
network) plus statistical smoke tests for the GBM calibration, the shock decay behavior, and
correlation. Run with `uv run --extra dev pytest -v` from `backend/`.

## Details

See `MARKET_INTERFACE.md` (source selection, `PriceUpdate`/`SourceStatus` design, SSE wire
format), `MARKET_SIMULATOR.md` (GBM math, calibration, shock design, seeding), and
`MASSIVE_API.md` (entitlement research, endpoint reference, rate-limit gotchas) for the full
design rationale.
