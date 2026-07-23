#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
: "${TARGET_DATABASE_URL:?TARGET_DATABASE_URL is required}"
: "${SOURCE_SQLITE:=/var/lib/stock-assistant/stock_assistant.db}"
: "${BACKUP_ROOT:=/opt/stock-assistant-backups}"
: "${APP_ROOT:=/opt/stock-assistant}"
: "${ENV_FILE:=/etc/stock-assistant/stock-assistant.env}"

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -f "$SOURCE_SQLITE" ]] || { echo "SQLite source not found: $SOURCE_SQLITE" >&2; exit 1; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP_ROOT"
install -d -o stockassistant -g stockassistant -m 0700 \
  /var/lib/stock-assistant/migration/snapshots \
  /var/lib/stock-assistant/migration/reports

backup_sqlite() {
  local label="$1"
  local target="$BACKUP_ROOT/stock-assistant-${label}-${timestamp}.db"
  sqlite3 "$SOURCE_SQLITE" ".timeout 30000" ".backup '$target'"
  local integrity
  integrity="$(sqlite3 "$target" 'PRAGMA integrity_check;')"
  [[ "$integrity" == "ok" ]] || {
    echo "SQLite backup integrity failed: $target" >&2
    exit 1
  }
  sha256sum "$target" >"${target}.sha256"
  echo "$target"
}

pre_backup="$(backup_sqlite pre-cutover)"
echo "pre-cutover SQLite backup verified: $pre_backup"

environment_backup=""
rollback_legacy_api() {
  local code=$?
  if [[ "$code" -ne 0 ]]; then
    echo "cutover failed; attempting to restart the SQLite API" >&2
    systemctl start stock-assistant-api.service 2>/dev/null || true
    for port in 8001 8002; do
      [[ -L "/opt/stock-assistant-api/$port" ]] \
        && systemctl start "stock-assistant-api@${port}.service" 2>/dev/null || true
    done
  fi
  exit "$code"
}
trap rollback_legacy_api EXIT

if [[ -f "$ENV_FILE" ]]; then
  environment_backup="${ENV_FILE}.pre-cutover-${timestamp}"
  install -o root -g root -m 0600 "$ENV_FILE" "$environment_backup"
  legacy_environment="$(mktemp)"
  awk \
    '!/^(DATABASE_URL|STOCK_ASSISTANT_DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|TASK_QUEUE_MODE)=/' \
    "$ENV_FILE" >"$legacy_environment"
  install -o root -g root -m 0600 "$legacy_environment" "$ENV_FILE"
  rm -f -- "$legacy_environment"
fi

worker_services=(
  stock-assistant-agent-worker.service
  stock-assistant-market-worker.service
  stock-assistant-llm-worker.service
  stock-assistant-ocr-worker.service
  stock-assistant-scheduler-worker.service
  stock-assistant-celery-beat.service
)
systemctl stop "${worker_services[@]}" 2>/dev/null || true
systemctl stop \
  stock-assistant-api.service \
  stock-assistant-api@8001.service \
  stock-assistant-api@8002.service \
  2>/dev/null || true

final_backup="$(backup_sqlite final-cutover)"
echo "final SQLite backup verified: $final_backup"

report="/var/lib/stock-assistant/migration/reports/sqlite-to-postgres-${timestamp}.json"
runuser -u stockassistant -- env \
  PYTHONPATH="$APP_ROOT/backend" \
  DATABASE_URL="$TARGET_DATABASE_URL" \
  "$APP_ROOT/venv/bin/python" -m migrations.migrate_sqlite_to_postgres \
  --sqlite "$SOURCE_SQLITE" \
  --snapshot-dir /var/lib/stock-assistant/migration/snapshots \
  --report "$report" \
  --batch-size 500

runuser -u stockassistant -- "$APP_ROOT/venv/bin/python" - "$report" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tables = list((report.get("tables") or {}).values())
if report.get("status") != "verified" or len(tables) < 45:
    raise SystemExit("migration report is not verified")
if not all(item.get("verified") for item in tables):
    raise SystemExit("one or more migrated tables failed verification")
print(f"migration report verified: tables={len(tables)}")
PY

if [[ -n "$environment_backup" ]]; then
  install -o root -g root -m 0600 "$environment_backup" "$ENV_FILE"
fi

trap - EXIT
echo "PostgreSQL cutover data verification completed; SQLite source retained"
echo "report: $report"
