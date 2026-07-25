from __future__ import annotations

import datetime as dt
import email.utils
import unittest
from unittest.mock import Mock, call, patch

import provider_transport


class ProviderTransportTests(unittest.TestCase):
    def test_massive_moves_api_key_out_of_query_string(self):
        expected = object()
        with patch.object(
            provider_transport,
            "_rate_limited_get",
            return_value=expected,
        ) as request:
            response = provider_transport.massive_get(
                "https://api.massive.com/v2/aggs/test",
                params={"adjusted": "true", "apiKey": "secret-value"},
                headers={"X-Request-Origin": "test"},
                timeout=5,
            )

        self.assertIs(response, expected)
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["params"], {"adjusted": "true"})
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer secret-value",
        )
        self.assertEqual(kwargs["headers"]["X-Request-Origin"], "test")

    def test_429_honors_retry_after_without_exposing_request_params(self):
        limited = Mock(status_code=429, headers={"Retry-After": "7"})
        success = Mock(status_code=200, headers={})
        with (
            patch.object(
                provider_transport,
                "_claim_rate_slot",
            ) as claim_slot,
            patch.object(
                provider_transport.requests,
                "get",
                side_effect=[limited, success],
            ) as request,
            patch.object(provider_transport.time, "sleep") as sleep,
        ):
            response = provider_transport._rate_limited_get(
                "massive",
                "https://provider.invalid/history",
                interval_seconds=2,
                max_attempts=3,
                max_retry_after_seconds=30,
                params={"apiKey": "must-not-be-logged"},
                timeout=5,
            )

        self.assertIs(response, success)
        self.assertEqual(claim_slot.call_args_list, [call("massive", 2.0)] * 2)
        sleep.assert_called_once_with(7.0)
        self.assertEqual(request.call_count, 2)

    def test_final_429_is_returned_for_caller_raise_for_status(self):
        limited = Mock(status_code=429, headers={})
        with (
            patch.object(provider_transport, "_claim_rate_slot"),
            patch.object(
                provider_transport.requests,
                "get",
                return_value=limited,
            ) as request,
            patch.object(provider_transport.time, "sleep") as sleep,
        ):
            response = provider_transport._rate_limited_get(
                "massive",
                "https://provider.invalid/history",
                interval_seconds=0,
                max_attempts=2,
                max_retry_after_seconds=30,
            )

        self.assertIs(response, limited)
        self.assertEqual(request.call_count, 2)
        sleep.assert_not_called()

    def test_retry_after_http_date_is_supported(self):
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
        parsed = provider_transport._retry_after_seconds(
            email.utils.format_datetime(future)
        )
        self.assertIsNotNone(parsed)
        self.assertGreater(parsed, 20)
        self.assertLessEqual(parsed, 30)


if __name__ == "__main__":
    unittest.main()
