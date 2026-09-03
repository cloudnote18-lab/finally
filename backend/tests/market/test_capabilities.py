"""Tests for probe_capabilities — the Massive entitlement probe.

No test hits the network: the RESTClient constructor and its methods are
always mocked.
"""

from unittest.mock import MagicMock, patch

import urllib3.exceptions
from massive.exceptions import AuthError, BadResponse

from app.market.capabilities import probe_capabilities


class TestProbeCapabilities:
    def test_no_key_is_invalid(self):
        with patch("app.market.capabilities.RESTClient", side_effect=AuthError("no key")):
            caps = probe_capabilities("")

        assert caps.valid is False
        assert caps.realtime is False
        assert caps.end_of_day is False

    def test_realtime_entitled_key(self):
        """Advanced tier: snapshot call succeeds outright."""
        mock_client = MagicMock()
        mock_client.get_snapshot_all.return_value = []

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("advanced-key")

        assert caps.valid is True
        assert caps.realtime is True
        assert caps.end_of_day is True

    def test_end_of_day_only_key(self):
        """Basic/free tier: snapshot NOT_AUTHORIZED, but aggregate call succeeds.
        This is the tier nearly every student will have — the whole point of the
        capability probe is to detect it accurately."""
        mock_client = MagicMock()
        mock_client.get_snapshot_all.side_effect = BadResponse(
            "NOT_AUTHORIZED: Please upgrade your plan"
        )
        mock_client.get_previous_close_agg.return_value = [MagicMock(close=324.96)]

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("basic-key")

        assert caps.valid is True
        assert caps.realtime is False
        assert caps.end_of_day is True
        assert "end-of-day" in caps.detail.lower()

    def test_invalid_key_rejected_by_both_calls(self):
        mock_client = MagicMock()
        mock_client.get_snapshot_all.side_effect = BadResponse("NOT_AUTHORIZED")
        mock_client.get_previous_close_agg.side_effect = AuthError("invalid key")

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("bad-key")

        assert caps.valid is False
        assert caps.realtime is False
        assert caps.end_of_day is False
        assert "rejected" in caps.detail.lower()

    def test_snapshot_error_not_not_authorized_is_treated_as_unexpected(self):
        """A BadResponse that isn't NOT_AUTHORIZED (e.g. a genuine server error)
        should not be silently reinterpreted as an entitlement gap."""
        mock_client = MagicMock()
        mock_client.get_snapshot_all.side_effect = BadResponse("INTERNAL_SERVER_ERROR")

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("some-key")

        assert caps.valid is False
        assert caps.realtime is False
        assert caps.end_of_day is False

    def test_rate_limited_on_first_call(self):
        """MaxRetryError (rate limiting), not BadResponse — planning/MASSIVE_API.md §7."""
        mock_client = MagicMock()
        error = urllib3.exceptions.MaxRetryError(pool=MagicMock(), url="/x", reason="429")
        mock_client.get_snapshot_all.side_effect = error

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("some-key")

        assert caps.valid is False
        assert "rate" in caps.detail.lower() or "unreachable" in caps.detail.lower()

    def test_never_raises(self):
        """probe_capabilities must never raise — the factory awaits it directly
        with no try/except of its own."""
        mock_client = MagicMock()
        mock_client.get_snapshot_all.side_effect = RuntimeError("something exploded")

        with patch("app.market.capabilities.RESTClient", return_value=mock_client):
            caps = probe_capabilities("some-key")  # must not raise

        assert caps.valid is False

    def test_probe_uses_no_retries(self):
        mock_client = MagicMock()
        mock_client.get_snapshot_all.return_value = []

        with patch("app.market.capabilities.RESTClient", return_value=mock_client) as mock_cls:
            probe_capabilities("some-key")

        _, kwargs = mock_cls.call_args
        assert kwargs.get("retries") == 0
