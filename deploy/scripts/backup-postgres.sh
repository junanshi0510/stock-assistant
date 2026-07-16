#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIR:=/var/backups/stock-assistant/postgresql}"
: "${BACKUP_RETENTION_DAYS:=14}"
: "${BACKUP_UPLOAD_ENABLED:=1}"
: "${APP_VENV:=/opt/stock-assistant/venv}"

install -d -m 0700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || { echo "backup already running" >&2; exit 75; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
hostname_safe="$(hostname | tr -cd 'A-Za-z0-9_.-')"
backup="$BACKUP_DIR/stock-assistant-${hostname_safe}-${timestamp}.dump"
partial="${backup}.partial"
checksum="${backup}.sha256"

cleanup() {
  rm -f -- "$partial"
}
trap cleanup EXIT

pg_dump \
  --dbname="$DATABASE_URL" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file="$partial"

pg_restore --list "$partial" >/dev/null
mv -- "$partial" "$backup"
sha256sum "$backup" >"$checksum"

if [[ "$BACKUP_UPLOAD_ENABLED" == "1" ]]; then
  digest="$(cut -d' ' -f1 "$checksum")"
  "$APP_VENV/bin/python" -m backup_to_oss "$backup" --sha256 "$digest"
fi

find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'stock-assistant-*.dump' -o -name 'stock-assistant-*.dump.sha256' \) \
  -mtime "+$BACKUP_RETENTION_DAYS" -delete

echo "backup verified: $backup"

