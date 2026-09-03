"""Entitlement probing for the Massive API.

A Massive API key can be valid but still unable to return live prices — a free
Basic-tier key authenticates successfully and then returns NOT_AUTHORIZED for
every snapshot/last-trade endpoint, while end-of-day aggregate endpoints work
fine. This module answers "what can this key actually do?" once at startup so
the factory can route to a source that will actually produce prices, instead
of a source that quietly fails on every poll.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import urllib3.exceptions
from massive import RESTClient
from massive.exceptions import AuthError, BadResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MassiveCapabilities:
    """What a given API key is actually allowed to do."""

    valid: bool  # key authenticates at all
    realtime: bool  # snapshot / last-trade endpoints entitled
    end_of_day: bool  # aggregate endpoints entitled
    detail: str


def probe_capabilities(api_key: str) -> MassiveCapabilities:
    """Two cheap calls, run once at startup. Costs 2 of the free tier's 5/min budget.

    Never raises — every failure mode is captured in the returned MassiveCapabilities.
    """
    try:
        client = RESTClient(api_key=api_key, retries=0, read_timeout=5.0)
    except AuthError:
        return MassiveCapabilities(False, False, False, "no API key configured")

    # 1. Cheapest possible entitlement test for real-time.
    try:
        client.get_snapshot_all(market_type="stocks", tickers=["AAPL"])
        return MassiveCapabilities(True, True, True, "real-time snapshots entitled")
    except BadResponse as e:
        if "NOT_AUTHORIZED" not in str(e):
            return MassiveCapabilities(False, False, False, f"unexpected: {e}")
    except urllib3.exceptions.MaxRetryError as e:
        return MassiveCapabilities(False, False, False, f"unreachable/rate-limited: {e}")
    except Exception as e:  # pragma: no cover - defensive catch-all, never raise
        return MassiveCapabilities(False, False, False, f"unexpected: {e}")

    # 2. Snapshots refused — is this a valid key on a lower plan, or a bad key?
    try:
        client.get_previous_close_agg("AAPL")
        return MassiveCapabilities(True, False, True, "end-of-day only (Basic tier)")
    except Exception as e:
        return MassiveCapabilities(False, False, False, f"key rejected: {e}")
