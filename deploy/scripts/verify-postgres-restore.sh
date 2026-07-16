#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
: "${POSTGRES_ADMIN_URL:?POSTGRES_ADMIN_URL is required for restore verification}"
: "${BACKUP_DIR:=/var/backups/stock-assistant/postgresql}"

latest="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'stock-assistant-*.dump' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "$latest" && -f "$latest" ]] || { echo "no backup found" >&2; exit 1; }
sha256sum --check "${latest}.sha256"
pg_restore --list "$latest" >/dev/null

database="stock_assistant_restore_verify_$(date -u +%Y%m%d%H%M%S)_$$"
cleanup() {
  dropdb --if-exists --force --maintenance-db="$POSTGRES_ADMIN_URL" "$database" >/dev/null 2>&1 || true
}
trap cleanup EXIT

createdb --maintenance-db="$POSTGRES_ADMIN_URL" "$database"
verify_url="${POSTGRES_ADMIN_URL%/*}/$database"
pg_restore \
  --dbname="$verify_url" \
  --no-owner \
  --no-acl \
  --exit-on-error \
  "$latest"

table_count="$(psql "$verify_url" -Atqc "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'")"
migration_count="$(psql "$verify_url" -Atqc "SELECT count(*) FROM platform_schema_migrations")"
[[ "$table_count" -ge 48 ]] || { echo "restored table count too small: $table_count" >&2; exit 1; }
[[ "$migration_count" -ge 1 ]] || { echo "platform migration marker missing" >&2; exit 1; }

echo "restore verified: backup=$latest tables=$table_count migrations=$migration_count"

