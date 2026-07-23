# -*- coding: utf-8 -*-
"""Immutable availability probes and tamper-evident incident transitions."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from database import (
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)


PROBE_SCHEMA_VERSION = "availability_probe.v1"
EVENT_SCHEMA_VERSION = "availability_incident_event.v1"
METHOD_VERSION = "availability_state_machine.v1"
REQUIRED_TABLES = {"availability_probe_runs", "availability_incident_events"}
VALID_STATES = {"operational", "degraded", "outage", "unknown"}
VALID_TRIGGERS = {"scheduled", "manual", "manual_deep", "deployment"}
VALID_EVENTS = {"incident_opened", "severity_changed", "incident_resolved"}
_STATE_RANK = {"operational": 0, "unknown": 1, "degraded": 2, "outage": 3}
_PROBE_LOCK_ID = 5_203_114_729_447_823


class AvailabilityRepositoryError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _utc_now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _utc_now(value).isoformat(timespec="milliseconds")


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS availability_probe_runs (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    method_version  TEXT NOT NULL,
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN ('scheduled','manual','manual_deep','deployment')),
    actor_id        TEXT NOT NULL,
    overall_status  TEXT NOT NULL CHECK(overall_status IN ('operational','degraded','outage','unknown')),
    effective_status TEXT NOT NULL CHECK(effective_status IN ('operational','degraded','outage','unknown')),
    started_at      TEXT NOT NULL,
    completed_at    TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_availability_probe_recent
ON availability_probe_runs(created_at DESC, id DESC);
CREATE TRIGGER IF NOT EXISTS trg_availability_probe_no_update
BEFORE UPDATE ON availability_probe_runs BEGIN
    SELECT RAISE(ABORT, 'availability probe runs are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_availability_probe_no_delete
BEFORE DELETE ON availability_probe_runs BEGIN
    SELECT RAISE(ABORT, 'availability probe runs are immutable');
END;

CREATE TABLE IF NOT EXISTS availability_incident_events (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL,
    sequence_no     INTEGER NOT NULL,
    schema_version  TEXT NOT NULL,
    component_id    TEXT NOT NULL,
    category        TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK(event_type IN ('incident_opened','severity_changed','incident_resolved')),
    from_state      TEXT NOT NULL CHECK(from_state IN ('operational','degraded','outage','unknown')),
    to_state        TEXT NOT NULL CHECK(to_state IN ('operational','degraded','outage','unknown')),
    details_json    TEXT NOT NULL,
    previous_hash   TEXT,
    event_hash      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(incident_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_availability_incident_recent
ON availability_incident_events(created_at DESC, incident_id, sequence_no DESC);
CREATE TRIGGER IF NOT EXISTS trg_availability_incident_no_update
BEFORE UPDATE ON availability_incident_events BEGIN
    SELECT RAISE(ABORT, 'availability incident events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_availability_incident_no_delete
BEFORE DELETE ON availability_incident_events BEGIN
    SELECT RAISE(ABORT, 'availability incident events are immutable');
END;
"""


def _worst_state(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=lambda value: _STATE_RANK.get(value, 0))


def _incident_id(component_id: str, first_failure_at: str) -> str:
    identity = canonical_json({"component_id": component_id, "first_failure_at": first_failure_at})
    return f"availability_incident_{sha256_text(identity)[:28]}"


def _advance_component(
    observation: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    now_iso: str,
    failure_threshold: int,
    recovery_threshold: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    observed = str(observation.get("observed_state") or "unknown")
    if observed not in VALID_STATES:
        raise ValueError(f"invalid availability state: {observed}")
    prior = dict(previous or {})
    prior_effective = str(prior.get("effective_state") or "unknown")
    if prior_effective not in VALID_STATES:
        prior_effective = "unknown"
    incident_id = str(prior.get("incident_id") or "") or None
    failure_streak = int(prior.get("failure_streak") or 0)
    success_streak = int(prior.get("success_streak") or 0)
    first_failure_at = prior.get("first_failure_at")
    transition: dict[str, Any] | None = None
    pending_transition: str | None = None

    if observed == "unknown":
        effective = prior_effective
        if not previous:
            effective = "unknown"
        success_streak = 0
        pending_transition = "observation_unknown"
    elif observed == "operational":
        success_streak += 1
        failure_streak = 0
        if incident_id and success_streak < recovery_threshold:
            effective = prior_effective
            pending_transition = "recovery_confirmation"
        else:
            effective = "operational"
            if incident_id:
                transition = {
                    "incident_id": incident_id,
                    "event_type": "incident_resolved",
                    "from_state": prior_effective,
                    "to_state": "operational",
                }
                incident_id = None
                first_failure_at = None
        if not incident_id:
            first_failure_at = None
    else:
        failure_streak += 1
        success_streak = 0
        first_failure_at = first_failure_at or now_iso
        if prior_effective == "operational" and failure_streak < failure_threshold:
            effective = "operational"
            pending_transition = "failure_confirmation"
        else:
            effective = observed
        if failure_streak >= failure_threshold and not incident_id:
            incident_id = _incident_id(str(observation["component_id"]), str(first_failure_at))
            transition = {
                "incident_id": incident_id,
                "event_type": "incident_opened",
                "from_state": prior_effective,
                "to_state": observed,
            }
        elif incident_id and effective != prior_effective:
            transition = {
                "incident_id": incident_id,
                "event_type": "severity_changed",
                "from_state": prior_effective,
                "to_state": effective,
            }

    result = {
        "component_id": str(observation["component_id"]),
        "label": str(observation.get("label") or observation["component_id"]),
        "category": str(observation.get("category") or "runtime"),
        "observed_state": observed,
        "effective_state": effective,
        "message": str(observation.get("message") or "")[:300],
        "details": dict(observation.get("details") or {}),
        "failure_streak": failure_streak,
        "success_streak": success_streak,
        "first_failure_at": first_failure_at,
        "last_success_at": (
            now_iso if observed == "operational" else prior.get("last_success_at")
        ),
        "last_failure_at": (
            now_iso if observed in {"degraded", "outage"} else prior.get("last_failure_at")
        ),
        "incident_id": incident_id,
        "pending_transition": pending_transition,
        "observed_at": now_iso,
    }
    if transition:
        transition.update({
            "component_id": result["component_id"],
            "category": result["category"],
            "message": result["message"],
            "failure_streak": failure_streak,
            "success_streak": success_streak,
        })
    return result, transition


class AvailabilityRepository:
    def __init__(self, database_target: str | os.PathLike[str] | None = None) -> None:
        self.database_target = str(
            database_target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with connect_database(self.database_target, close_on_exit=True) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    @staticmethod
    def _run_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["payload"] = _load(item.pop("payload_json", None), {})
        item["integrity"] = {
            "verified": sha256_text(canonical_json(item["payload"]))
            == item.get("payload_sha256"),
            "schema_version": item.get("schema_version"),
        }
        return item

    @staticmethod
    def _event_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["details"] = _load(item.pop("details_json", None), {})
        return item

    def _append_event(self, connection, transition: dict[str, Any], *, probe_id: str, created_at: str) -> None:
        incident_id = str(transition["incident_id"])
        previous = connection.execute(
            """
            SELECT sequence_no, event_hash FROM availability_incident_events
            WHERE incident_id=? ORDER BY sequence_no DESC LIMIT 1
            """,
            (incident_id,),
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        event_id = f"availability_evt_{sha256_text(canonical_json({'probe_id': probe_id, 'component_id': transition['component_id'], 'event_type': transition['event_type']}))[:32]}"
        details = {
            "probe_id": probe_id,
            "message": transition.get("message"),
            "failure_streak": transition.get("failure_streak"),
            "success_streak": transition.get("success_streak"),
        }
        base = {
            "id": event_id,
            "incident_id": incident_id,
            "sequence_no": sequence_no,
            "schema_version": EVENT_SCHEMA_VERSION,
            "component_id": str(transition["component_id"]),
            "category": str(transition["category"]),
            "event_type": str(transition["event_type"]),
            "from_state": str(transition["from_state"]),
            "to_state": str(transition["to_state"]),
            "details": details,
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": created_at,
        }
        event_hash = sha256_text(canonical_json(base))
        connection.execute(
            """
            INSERT INTO availability_incident_events(
                id, incident_id, sequence_no, schema_version, component_id,
                category, event_type, from_state, to_state, details_json,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                event_id,
                incident_id,
                sequence_no,
                EVENT_SCHEMA_VERSION,
                base["component_id"],
                base["category"],
                base["event_type"],
                base["from_state"],
                base["to_state"],
                canonical_json(details),
                base["previous_hash"],
                event_hash,
                created_at,
            ),
        )

    def record_probe(
        self,
        *,
        trigger_type: str,
        actor_id: str,
        observations: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        started_at: dt.datetime | None = None,
        completed_at: dt.datetime | None = None,
        probe_id: str | None = None,
        failure_threshold: int = 2,
        recovery_threshold: int = 2,
    ) -> dict[str, Any]:
        if trigger_type not in VALID_TRIGGERS:
            raise ValueError("invalid availability probe trigger")
        ids = [str(item.get("component_id") or "") for item in observations]
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("availability components must be non-empty and unique")
        failure_threshold = max(1, int(failure_threshold))
        recovery_threshold = max(1, int(recovery_threshold))
        started = _utc_now(started_at)
        completed = _utc_now(completed_at or started)
        if completed < started:
            raise ValueError("availability probe completion precedes start")
        run_id = str(probe_id or f"availability_probe_{uuid.uuid4().hex}")
        completed_iso = _iso(completed)

        with self._connect() as connection:
            dialect = database_dialect(connection)
            if dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(?)", (_PROBE_LOCK_ID,)
                ).fetchone()
            existing = connection.execute(
                "SELECT * FROM availability_probe_runs WHERE id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                result = self._run_from_row(existing) or {}
                result["deduplicated"] = True
                return result
            previous_row = connection.execute(
                "SELECT * FROM availability_probe_runs ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            previous_payload = _load(previous_row["payload_json"], {}) if previous_row else {}
            previous_components = {
                str(item.get("component_id")): item
                for item in previous_payload.get("components") or []
            }
            components: list[dict[str, Any]] = []
            transitions: list[dict[str, Any]] = []
            for observation in sorted(observations, key=lambda item: str(item["component_id"])):
                component, transition = _advance_component(
                    observation,
                    previous_components.get(str(observation["component_id"])),
                    now_iso=completed_iso,
                    failure_threshold=failure_threshold,
                    recovery_threshold=recovery_threshold,
                )
                components.append(component)
                if transition:
                    transitions.append(transition)
            overall = _worst_state([item["observed_state"] for item in components])
            effective = _worst_state([item["effective_state"] for item in components])
            payload = {
                "schema_version": PROBE_SCHEMA_VERSION,
                "method_version": METHOD_VERSION,
                "probe_id": run_id,
                "trigger_type": trigger_type,
                "actor_id": str(actor_id),
                "started_at": _iso(started),
                "completed_at": completed_iso,
                "overall_status": overall,
                "effective_status": effective,
                "summary": {
                    state: sum(1 for item in components if item["observed_state"] == state)
                    for state in sorted(VALID_STATES)
                },
                "components": components,
                "transitions": transitions,
                "metadata": dict(metadata or {}),
            }
            payload_text = canonical_json(payload)
            connection.execute(
                """
                INSERT INTO availability_probe_runs(
                    id, schema_version, method_version, trigger_type, actor_id,
                    overall_status, effective_status, started_at, completed_at,
                    payload_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    PROBE_SCHEMA_VERSION,
                    METHOD_VERSION,
                    trigger_type,
                    str(actor_id),
                    overall,
                    effective,
                    _iso(started),
                    completed_iso,
                    payload_text,
                    sha256_text(payload_text),
                    completed_iso,
                ),
            )
            for transition in transitions:
                self._append_event(
                    connection, transition, probe_id=run_id, created_at=completed_iso
                )
        result = self.get_probe(run_id)
        if result is None:
            raise AvailabilityRepositoryError("availability probe disappeared")
        result["deduplicated"] = False
        return result

    def get_probe(self, probe_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM availability_probe_runs WHERE id=?", (str(probe_id),)
            ).fetchone()
        return self._run_from_row(row)

    def latest_probe(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM availability_probe_runs ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return self._run_from_row(row)

    def list_probes(self, limit: int = 288) -> list[dict[str, Any]]:
        bounded = max(1, min(10_000, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM availability_probe_runs
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def list_incident_events(self, limit: int = 1000) -> list[dict[str, Any]]:
        bounded = max(1, min(10_000, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM availability_incident_events
                ORDER BY created_at DESC, incident_id, sequence_no DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_incidents(self, limit: int = 30) -> list[dict[str, Any]]:
        events = self.list_incident_events(limit=max(200, int(limit) * 20))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            grouped.setdefault(str(event["incident_id"]), []).append(event)
        incidents = []
        for incident_id, values in grouped.items():
            ordered = sorted(values, key=lambda item: int(item["sequence_no"]))
            first, latest = ordered[0], ordered[-1]
            incidents.append({
                "incident_id": incident_id,
                "component_id": latest["component_id"],
                "category": latest["category"],
                "status": "resolved" if latest["event_type"] == "incident_resolved" else "open",
                "current_state": latest["to_state"],
                "opened_at": first["created_at"],
                "resolved_at": latest["created_at"] if latest["event_type"] == "incident_resolved" else None,
                "event_count": len(ordered),
                "latest_message": (latest.get("details") or {}).get("message"),
                "events": ordered,
            })
        incidents.sort(key=lambda item: str(item["opened_at"]), reverse=True)
        return incidents[: max(1, min(100, int(limit)))]

    def verify_probe(self, probe_id: str) -> dict[str, Any]:
        item = self.get_probe(probe_id)
        if item is None:
            raise AvailabilityRepositoryError("availability probe not found")
        return {"probe_id": probe_id, **item["integrity"]}

    def verify_incident_events(self) -> dict[str, Any]:
        events = list(reversed(self.list_incident_events(limit=10_000)))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            grouped.setdefault(str(event["incident_id"]), []).append(event)
        checked = 0
        for incident_id, values in grouped.items():
            previous_hash = None
            expected_sequence = 1
            for event in sorted(values, key=lambda item: int(item["sequence_no"])):
                base = {
                    "id": event["id"],
                    "incident_id": incident_id,
                    "sequence_no": int(event["sequence_no"]),
                    "schema_version": event["schema_version"],
                    "component_id": event["component_id"],
                    "category": event["category"],
                    "event_type": event["event_type"],
                    "from_state": event["from_state"],
                    "to_state": event["to_state"],
                    "details": event["details"],
                    "previous_hash": event["previous_hash"],
                    "created_at": event["created_at"],
                }
                if (
                    int(event["sequence_no"]) != expected_sequence
                    or event["previous_hash"] != previous_hash
                    or sha256_text(canonical_json(base)) != event["event_hash"]
                ):
                    return {
                        "verified": False,
                        "event_count": checked,
                        "incident_id": incident_id,
                        "failing_sequence": expected_sequence,
                    }
                checked += 1
                expected_sequence += 1
                previous_hash = event["event_hash"]
        return {
            "verified": True,
            "event_count": checked,
            "incident_count": len(grouped),
        }


__all__ = [
    "AvailabilityRepository",
    "AvailabilityRepositoryError",
    "EVENT_SCHEMA_VERSION",
    "METHOD_VERSION",
    "PROBE_SCHEMA_VERSION",
    "SQLITE_SCHEMA",
    "canonical_json",
    "sha256_text",
]
