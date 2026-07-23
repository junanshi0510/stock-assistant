from __future__ import annotations

import datetime as dt
import inspect
import tempfile
import unittest
from pathlib import Path

import availability_service
from availability_repository import AvailabilityRepository
from migrations import availability_control_v1


UTC = dt.timezone.utc


def observation(state: str, *, component_id: str = "database", label: str = "权威数据库") -> dict:
    return {
        "component_id": component_id,
        "label": label,
        "category": "core",
        "observed_state": state,
        "message": f"state={state}",
        "details": {},
    }


class AvailabilityControlTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "availability.db")
        self.repository = AvailabilityRepository(self.database_path)
        self.base = dt.datetime(2026, 7, 23, 0, 0, tzinfo=UTC)

    def tearDown(self):
        self.tempdir.cleanup()

    def record(
        self,
        minute: int,
        state: str,
        *,
        probe_id: str | None = None,
        trigger_type: str = "manual",
    ):
        current = self.base + dt.timedelta(minutes=minute)
        return self.repository.record_probe(
            trigger_type=trigger_type,
            actor_id="admin-test",
            observations=[observation(state)],
            started_at=current,
            completed_at=current,
            probe_id=probe_id,
            failure_threshold=2,
            recovery_threshold=2,
        )

    def test_failure_and_recovery_are_debounced_and_audited(self):
        healthy = self.record(0, "operational")
        first_failure = self.record(5, "outage")
        confirmed_failure = self.record(10, "outage")
        first_recovery = self.record(15, "operational")
        confirmed_recovery = self.record(20, "operational")

        self.assertEqual(healthy["payload"]["effective_status"], "operational")
        pending = first_failure["payload"]["components"][0]
        self.assertEqual(pending["observed_state"], "outage")
        self.assertEqual(pending["effective_state"], "operational")
        self.assertEqual(pending["pending_transition"], "failure_confirmation")

        failed = confirmed_failure["payload"]["components"][0]
        self.assertEqual(failed["effective_state"], "outage")
        self.assertTrue(failed["incident_id"])
        self.assertEqual(
            confirmed_failure["payload"]["transitions"][0]["event_type"],
            "incident_opened",
        )

        recovering = first_recovery["payload"]["components"][0]
        self.assertEqual(recovering["effective_state"], "outage")
        self.assertEqual(recovering["pending_transition"], "recovery_confirmation")
        self.assertEqual(
            confirmed_recovery["payload"]["transitions"][0]["event_type"],
            "incident_resolved",
        )
        self.assertEqual(
            confirmed_recovery["payload"]["components"][0]["effective_state"],
            "operational",
        )

        incidents = self.repository.list_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["status"], "resolved")
        self.assertEqual(incidents[0]["event_count"], 2)
        self.assertTrue(self.repository.verify_incident_events()["verified"])

    def test_probes_and_incident_events_are_immutable(self):
        self.record(0, "operational")
        self.record(5, "outage")
        confirmed = self.record(10, "outage")
        probe_id = confirmed["id"]
        event_id = self.repository.list_incident_events()[0]["id"]

        with self.repository._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    "UPDATE availability_probe_runs SET overall_status='operational' WHERE id=?",
                    (probe_id,),
                )
        with self.repository._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    "DELETE FROM availability_incident_events WHERE id=?", (event_id,)
                )
        self.assertTrue(self.repository.verify_probe(probe_id)["verified"])

    def test_probe_id_is_idempotent(self):
        first = self.record(0, "operational", probe_id="availability_probe_fixed")
        duplicate = self.record(5, "outage", probe_id="availability_probe_fixed")
        self.assertEqual(first["payload_sha256"], duplicate["payload_sha256"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(len(self.repository.list_probes()), 1)

    def test_components_build_safe_capabilities_and_redact_secrets(self):
        health_result = {
            "ready": True,
            "full_service_ready": True,
            "database": {
                "ready": True,
                "dialect": "postgresql",
                "platform_schema": True,
                "opportunity_schema": True,
                "portfolio_twin_schema": True,
                "portfolio_valuation_schema": True,
                "availability_schema": True,
            },
            "redis": {
                "ready": True,
                "mode": "celery",
                "queue_depths": {queue: 0 for queue in availability_service._QUEUES},
            },
            "workers": {
                "ready": True,
                "mode": "celery",
                "workers": {
                    f"{queue}@host": [queue] for queue in availability_service._QUEUES
                },
            },
            "object_storage": {
                "ready": True,
                "required": True,
                "provider": "aliyun_oss",
                "error": "apiKey=should-not-survive",
            },
        }
        provider_result = {
            "policy_version": "professional_hot_stock_router.v2",
            "markets": [
                {
                    "market": market,
                    "state": "ready",
                    "provider": f"provider-{market}",
                    "provider_label": "专业源",
                    "configured": True,
                    "available_provider_count": 1,
                    "provider_count": 2,
                }
                for market in availability_service._MARKETS
            ],
        }
        components, metadata = availability_service.collect_components(
            health_result=health_result,
            provider_result=provider_result,
            deep=True,
            deep_results={
                "A股": {
                    "available": True,
                    "provider": "tushare",
                    "api_key": "nested-secret-must-not-survive",
                }
            },
        )
        self.assertEqual(len(components), 16)
        self.assertTrue(metadata["full_service_ready"])
        saved = self.repository.record_probe(
            trigger_type="manual",
            actor_id="admin-test",
            observations=components,
            started_at=self.base,
            completed_at=self.base,
        )
        serialized = str(saved["payload"])
        self.assertNotIn("should-not-survive", serialized)
        self.assertNotIn("nested-secret-must-not-survive", serialized)
        capabilities = availability_service.build_capabilities(saved["payload"])
        self.assertEqual(capabilities["decision_mode"]["mode"], "normal")
        self.assertTrue(capabilities["portfolio_valuation_refresh"]["available"])

    def test_api_replica_quorum_preserves_traffic_and_exposes_reduced_redundancy(self):
        health_result = {
            "ready": True,
            "full_service_ready": True,
            "database": {"ready": True, "dialect": "postgresql"},
            "redis": {
                "ready": True,
                "mode": "celery",
                "queue_depths": {queue: 0 for queue in availability_service._QUEUES},
            },
            "workers": {
                "ready": True,
                "mode": "celery",
                "workers": {f"{queue}@host": [queue] for queue in availability_service._QUEUES},
            },
            "object_storage": {"ready": True, "required": True},
        }
        provider_result = {
            "markets": [
                {"market": market, "state": "ready", "configured": True}
                for market in availability_service._MARKETS
            ]
        }
        components, metadata = availability_service.collect_components(
            health_result=health_result,
            provider_result=provider_result,
            api_replica_results=[
                {
                    "name": "api-8001",
                    "component_id": "api_replica:api-8001",
                    "ready": True,
                    "replica_id": "api-8001",
                    "release_id": "release-a",
                    "latency_ms": 4,
                },
                {
                    "name": "api-8002",
                    "component_id": "api_replica:api-8002",
                    "ready": False,
                    "error_type": "ConnectionRefusedError",
                },
            ],
        )
        replicas = {
            item["component_id"]: item
            for item in components
            if item["category"] == "api_replica"
        }
        self.assertEqual(len(components), 18)
        self.assertEqual(replicas["api_replica:api-8001"]["observed_state"], "operational")
        self.assertEqual(replicas["api_replica:api-8002"]["observed_state"], "degraded")
        self.assertEqual(metadata["api_replicas"]["ready_count"], 1)
        self.assertTrue(metadata["api_replicas"]["traffic_ready"])
        capabilities = availability_service.build_capabilities({"components": components})
        self.assertTrue(capabilities["api_traffic"]["available"])
        self.assertEqual(capabilities["api_traffic"]["mode"], "reduced_redundancy")

    def test_api_traffic_slo_uses_any_replica_but_redundancy_requires_all(self):
        for index in range(12):
            current = self.base + dt.timedelta(minutes=index * 5)
            second_state = "degraded" if index == 11 else "operational"
            self.repository.record_probe(
                trigger_type="scheduled",
                actor_id="scheduler",
                observations=[
                    {
                        **observation("operational", component_id="api_replica:api-8001"),
                        "category": "api_replica",
                    },
                    {
                        **observation(second_state, component_id="api_replica:api-8002"),
                        "category": "api_replica",
                    },
                ],
                started_at=current,
                completed_at=current,
            )
        runs = self.repository.list_probes(limit=100)
        slos = availability_service.calculate_slos(
            runs,
            now=self.base + dt.timedelta(minutes=60),
        )
        traffic = slos["groups"]["api_traffic"]["windows"]["24h"]
        redundancy = slos["groups"]["api_redundancy"]["windows"]["24h"]
        self.assertEqual(traffic["availability_pct"], 100.0)
        self.assertEqual(traffic["bad_count"], 0)
        self.assertEqual(redundancy["bad_count"], 1)
        self.assertAlmostEqual(redundancy["availability_pct"], 91.6667)

    def test_public_summary_becomes_unknown_when_monitor_is_stale(self):
        self.record(0, "operational")
        fresh = availability_service.public_summary(
            repository_instance=self.repository,
            now=self.base + dt.timedelta(minutes=1),
        )
        stale = availability_service.public_summary(
            repository_instance=self.repository,
            now=self.base + dt.timedelta(seconds=availability_service.STALE_AFTER_SECONDS + 1),
        )
        self.assertEqual(fresh["status"], "operational")
        self.assertFalse(fresh["monitoring_stale"])
        self.assertEqual(stale["status"], "unknown")
        self.assertTrue(stale["monitoring_stale"])

    def test_public_status_does_not_hide_confirmed_incident_behind_unknown_observation(self):
        self.record(0, "operational")
        self.record(5, "degraded")
        self.record(10, "degraded")
        self.record(15, "unknown")

        summary = availability_service.public_summary(
            repository_instance=self.repository,
            now=self.base + dt.timedelta(minutes=16),
        )

        self.assertEqual(summary["observed_status"], "unknown")
        self.assertEqual(summary["effective_status"], "degraded")
        self.assertEqual(summary["status"], "degraded")
        self.assertEqual(summary["open_incident_count"], 1)

    def test_slo_uses_known_probe_windows_and_reports_error_budget(self):
        for index in range(12):
            self.record(
                index * 5,
                "operational" if index < 11 else "outage",
                trigger_type="scheduled",
            )
        self.record(59, "operational", trigger_type="manual")
        runs = self.repository.list_probes(limit=100)
        slos = availability_service.calculate_slos(
            runs,
            now=self.base + dt.timedelta(minutes=60),
        )
        window = slos["groups"]["core_access"]["windows"]["24h"]
        self.assertEqual(window["sample_count"], 12)
        self.assertEqual(window["good_count"], 11)
        self.assertEqual(window["bad_count"], 1)
        self.assertTrue(window["enough_samples"])
        self.assertAlmostEqual(window["availability_pct"], 91.6667)
        self.assertGreater(window["burn_rate"], 1)
        self.assertEqual(slos["eligible_trigger_types"], ["scheduled"])

    def test_unknown_component_prevents_false_operational_summary(self):
        current = self.base
        result = self.repository.record_probe(
            trigger_type="manual",
            actor_id="admin-test",
            observations=[
                observation("operational", component_id="database"),
                observation("unknown", component_id="market:A股"),
            ],
            started_at=current,
            completed_at=current,
        )
        self.assertEqual(result["payload"]["overall_status"], "unknown")

    def test_queue_outage_closes_only_affected_capabilities(self):
        observations = []
        for component_id in (
            "database",
            "redis",
            "object_storage",
            *(f"worker:{queue}" for queue in availability_service._QUEUES),
            *(f"queue:{queue}" for queue in availability_service._QUEUES),
            *(f"market:{market}" for market in availability_service._MARKETS),
        ):
            state = "outage" if component_id == "queue:market-data" else "operational"
            observations.append(observation(state, component_id=component_id))
        run = self.repository.record_probe(
            trigger_type="manual",
            actor_id="admin-test",
            observations=observations,
            started_at=self.base,
            completed_at=self.base,
        )
        capabilities = availability_service.build_capabilities(run["payload"])
        self.assertFalse(capabilities["market_refresh"]["available"])
        self.assertFalse(capabilities["portfolio_valuation_refresh"]["available"])
        self.assertFalse(capabilities["agent_research"]["available"])
        self.assertTrue(capabilities["private_ocr_import"]["available"])

    def test_migration_contains_tables_guards_and_marker(self):
        self.assertIn("availability_probe_runs", availability_control_v1.POSTGRES_DDL)
        self.assertIn("availability_incident_events", availability_control_v1.POSTGRES_DDL)
        self.assertIn("RETURNS trigger", availability_control_v1.POSTGRES_GUARD)
        self.assertIn(
            "BEFORE UPDATE OR DELETE",
            inspect.getsource(availability_control_v1.install_availability_schema),
        )
        self.assertEqual(availability_control_v1.MIGRATION_ID, "availability-control.v1")


if __name__ == "__main__":
    unittest.main()
