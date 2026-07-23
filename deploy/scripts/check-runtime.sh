#!/usr/bin/env bash
set -Eeuo pipefail

endpoint="${HEALTHCHECK_URL:-http://127.0.0.1:8000/health/full}"
services=(
  stock-assistant-api.service
  stock-assistant-agent-worker.service
  stock-assistant-market-worker.service
  stock-assistant-llm-worker.service
  stock-assistant-ocr-worker.service
  stock-assistant-scheduler-worker.service
  stock-assistant-celery-beat.service
)

for service in "${services[@]}"; do
  systemctl is-active --quiet "$service" || {
    echo "runtime health failed: inactive service=$service" >&2
    exit 1
  }
done

response="$(curl --fail --silent --show-error --max-time 12 "$endpoint")"
grep -Eq '"full_service_ready"[[:space:]]*:[[:space:]]*true' <<<"$response" || {
  echo "runtime health failed: full service readiness is false" >&2
  exit 1
}

echo "runtime health verified"
