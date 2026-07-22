from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import market_data_gateway
import market_data_operations
from task_queue import TaskQueueUnavailableError


class MarketDataGatewayTests(unittest.TestCase):
    def test_provider_status_is_an_allowlisted_read_operation(self):
        expected = {"policy_version": "test", "secrets_exposed": False, "markets": []}
        with patch.object(
            market_data_operations.hot_stocks,
            "get_provider_status",
            return_value=expected,
        ) as provider:
            result = market_data_operations.execute_operation("market.providers", {})

        self.assertEqual(result, expected)
        provider.assert_called_once_with()
        self.assertIn("market.providers", market_data_operations.allowed_operations())

    def test_provider_probe_is_an_allowlisted_diagnostic_operation(self):
        expected = {"market": "美股", "available": True, "provider": "massive_eod_us"}
        with patch.object(
            market_data_operations.hot_stocks,
            "probe_provider",
            return_value=expected,
        ) as provider:
            result = market_data_operations.execute_operation(
                "market.providers_probe", {"market": "美股"}
            )

        self.assertEqual(result, expected)
        provider.assert_called_once_with("美股")
        self.assertIn("market.providers_probe", market_data_operations.allowed_operations())

    def test_embedded_mode_executes_allowlisted_operation(self):
        expected = {"code": "013403", "estimate": 1.2345}
        with (
            patch.object(market_data_gateway, "uses_celery_queue", return_value=False),
            patch.object(
                market_data_operations.funds_mod,
                "get_fund_estimate",
                return_value=expected,
            ) as operation,
        ):
            result = market_data_gateway.execute_market_operation(
                "fund.estimate", {"code": "013403"}
            )
        self.assertEqual(result, expected)
        operation.assert_called_once_with(code="013403")

    def test_celery_mode_polls_verified_database_result(self):
        repository = Mock()
        repository.create_job.return_value = (
            {
                "id": "job-1",
                "job_type": "market_data_operation",
                "queue_name": "market-data",
            },
            True,
        )
        repository.get_job.return_value = {
            "id": "job-1",
            "status": "succeeded",
            "result": {"code": "013403", "source": "real-provider"},
            "result_verified": True,
        }
        with (
            patch.object(market_data_gateway, "uses_celery_queue", return_value=True),
            patch.object(
                market_data_gateway,
                "BackgroundJobRepository",
                return_value=repository,
            ),
            patch.object(market_data_gateway, "enqueue_background_job") as enqueue,
        ):
            result = market_data_gateway.execute_market_operation(
                "fund.estimate",
                {"code": "013403"},
                timeout_seconds=1,
                tenant_id="tenant-7",
                user_id="user-42",
            )
        self.assertEqual(result["source"], "real-provider")
        enqueue.assert_called_once()
        queued_job = repository.create_job.call_args.kwargs
        self.assertEqual(queued_job["payload"]["input"], {"code": "013403"})
        self.assertEqual(queued_job["tenant_id"], "tenant-7")
        self.assertEqual(queued_job["user_id"], "user-42")

    def test_worker_client_error_preserves_supported_http_status(self):
        repository = Mock()
        repository.create_job.return_value = (
            {
                "id": "job-not-found",
                "job_type": "market_data_operation",
                "queue_name": "market-data",
            },
            True,
        )
        repository.get_job.return_value = {
            "id": "job-not-found",
            "status": "failed",
            "error_code": "MARKET_CLIENT_404",
            "error_message": "portfolio holding was not found",
        }
        with (
            patch.object(market_data_gateway, "uses_celery_queue", return_value=True),
            patch.object(
                market_data_gateway,
                "BackgroundJobRepository",
                return_value=repository,
            ),
            patch.object(market_data_gateway, "enqueue_background_job"),
        ):
            with self.assertRaises(market_data_gateway.MarketDataGatewayError) as raised:
                market_data_gateway.execute_market_operation(
                    "portfolio.insights",
                    {"user_id": "user-42"},
                    timeout_seconds=1,
                    tenant_id="tenant-7",
                    user_id="user-42",
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.error_code, "MARKET_CLIENT_404")
        self.assertEqual(raised.exception.job_id, "job-not-found")

    def test_production_queue_failure_never_executes_operation_inline(self):
        repository = Mock()
        repository.create_job.return_value = (
            {
                "id": "job-queue-failed",
                "job_type": "market_data_operation",
                "queue_name": "market-data",
            },
            True,
        )
        with (
            patch.object(market_data_gateway, "uses_celery_queue", return_value=True),
            patch.object(
                market_data_gateway,
                "BackgroundJobRepository",
                return_value=repository,
            ),
            patch.object(
                market_data_gateway,
                "enqueue_background_job",
                side_effect=TaskQueueUnavailableError("redis down"),
            ),
            patch.object(
                market_data_operations,
                "execute_operation",
            ) as inline_operation,
        ):
            with self.assertRaises(market_data_gateway.MarketDataGatewayError) as raised:
                market_data_gateway.execute_market_operation(
                    "fund.estimate", {"code": "013403"}
                )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.job_id, "job-queue-failed")
        inline_operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
