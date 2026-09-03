"""Seed prices and per-ticker parameters for the market simulator.

These are the no-key fallback only. When a Massive API key is available,
AnchoredSimulatorDataSource replaces these levels with real closing prices —
see planning/MARKET_INTERFACE.md. Refreshed to real 2026-09-02 closes so the
no-key demo does not show implausible levels (e.g. NVDA and NFLX are badly
stale after their 2024 splits).
"""

# Starting prices for the default watchlist, sourced from real 2026-09-02 closes.
SEED_PRICES: dict[str, float] = {
    "AAPL": 324.96,
    "GOOGL": 337.12,
    "MSFT": 496.82,
    "AMZN": 254.98,
    "TSLA": 357.01,
    "NVDA": 224.41,
    "META": 592.85,
    "JPM": 356.22,
    "V": 378.40,
    "NFLX": 82.73,
}

# Per-ticker GBM parameters
# sigma: annualized volatility (higher = more price movement)
# mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # High volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # Low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # Low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

# Default parameters for tickers not in the list above (dynamically added)
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Correlation groups for the simulator's Cholesky decomposition
# Tickers in the same group have higher intra-group correlation
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

# Correlation coefficients
INTRA_TECH_CORR = 0.6  # Tech stocks move together
INTRA_FINANCE_CORR = 0.5  # Finance stocks move together
CROSS_GROUP_CORR = 0.3  # Between sectors / unknown tickers
TSLA_CORR = 0.3  # TSLA does its own thing
