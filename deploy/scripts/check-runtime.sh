#!/usr/bin/env bash
set -Eeuo pipefail

services=(
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

read -r -a replica_ports <<<"${API_REPLICA_PORTS:-8001 8002}"
required_replicas="${API_REPLICA_REQUIRED:-${#replica_ports[@]}}"
[[ "$required_replicas" =~ ^[0-9]+$ ]] || {
  echo "runtime health failed: API_REPLICA_REQUIRED must be numeric" >&2
  exit 1
}
(( required_replicas >= 1 && required_replicas <= ${#replica_ports[@]} )) || {
  echo "runtime health failed: API_REPLICA_REQUIRED is outside configured replica count" >&2
  exit 1
}

ready_replicas=0
full_replicas=0
declare -A seen_ports=()
declare -A ready_releases=()
for port in "${replica_ports[@]}"; do
  [[ "$port" =~ ^[0-9]+$ ]] || {
    echo "runtime health failed: invalid replica port" >&2
    exit 1
  }
  [[ -z "${seen_ports[$port]:-}" ]] || {
    echo "runtime health failed: duplicate replica port" >&2
    exit 1
  }
  seen_ports["$port"]=1
  service="stock-assistant-api@${port}.service"
  if ! systemctl is-active --quiet "$service"; then
    echo "runtime health warning: inactive API replica=$service" >&2
    continue
  fi
  ready_response="$(curl --fail --silent --show-error --max-time 4 \
    "http://127.0.0.1:${port}/health/ready" 2>/dev/null || true)"
  if grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' <<<"$ready_response" \
    && grep -Eq "\"replica_id\"[[:space:]]*:[[:space:]]*\"api-${port}\"" <<<"$ready_response"; then
    ready_replicas=$((ready_replicas + 1))
  else
    echo "runtime health warning: unready API replica=$service" >&2
    continue
  fi
  release_id="$(sed -n 's/.*"release_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9._:-]*\)".*/\1/p' <<<"$ready_response" | head -n 1)"
  [[ -n "$release_id" ]] || {
    echo "runtime health failed: API replica has no release identity=$service" >&2
    exit 1
  }
  ready_releases["$release_id"]=1
  full_response="$(curl --fail --silent --show-error --max-time 6 \
    "http://127.0.0.1:${port}/health/full" 2>/dev/null || true)"
  if grep -Eq '"full_service_ready"[[:space:]]*:[[:space:]]*true' <<<"$full_response"; then
    full_replicas=$((full_replicas + 1))
  fi
done

(( ready_replicas >= required_replicas )) || {
  echo "runtime health failed: ready API replicas=${ready_replicas} required=${required_replicas}" >&2
  exit 1
}
(( full_replicas >= 1 )) || {
  echo "runtime health failed: no API replica reports full service readiness" >&2
  exit 1
}
(( ${#ready_releases[@]} == 1 )) || {
  echo "runtime health failed: ready API replicas run different releases" >&2
  exit 1
}

echo "runtime health verified: ready_replicas=${ready_replicas} full_replicas=${full_replicas}"
