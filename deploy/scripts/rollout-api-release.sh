#!/usr/bin/env bash
set -Eeuo pipefail

umask 022

app_root="${APP_ROOT:-/opt/stock-assistant}"
release_root="${API_RELEASE_ROOT:-/opt/stock-assistant-releases}"
slot_root="${API_SLOT_ROOT:-/opt/stock-assistant-api}"
static_link="${STATIC_CURRENT_LINK:-/var/www/stock-assistant-current}"
state_root="${DEPLOYMENT_STATE_ROOT:-/var/lib/stock-assistant/deployments}"
bootstrap_python="${PYTHON_BOOTSTRAP:-/opt/stock-assistant/venv/bin/python}"
public_health_url="${PUBLIC_HEALTH_URL:-http://127.0.0.1/health/ready}"
health_timeout="${API_ROLLOUT_HEALTH_TIMEOUT_SECONDS:-45}"
drain_seconds="${API_ROLLOUT_DRAIN_SECONDS:-3}"
nginx_upstream_file="${NGINX_API_UPSTREAM_FILE:-/etc/nginx/stock-assistant-api-upstreams.conf}"
target_ref="${1:-HEAD}"
read -r -a replica_ports <<<"${API_REPLICA_PORTS:-8001 8002}"

if (( EUID != 0 )); then
  echo "rollout failed: run as root" >&2
  exit 1
fi
[[ -d "$app_root/.git" ]] || {
  echo "rollout failed: APP_ROOT is not a Git worktree" >&2
  exit 1
}
[[ "$health_timeout" =~ ^[0-9]+$ ]] && (( health_timeout >= 5 && health_timeout <= 300 )) || {
  echo "rollout failed: invalid API_ROLLOUT_HEALTH_TIMEOUT_SECONDS" >&2
  exit 1
}
[[ "$drain_seconds" =~ ^[0-9]+$ ]] && (( drain_seconds >= 1 && drain_seconds <= 30 )) || {
  echo "rollout failed: invalid API_ROLLOUT_DRAIN_SECONDS" >&2
  exit 1
}
(( ${#replica_ports[@]} >= 2 && ${#replica_ports[@]} <= 4 )) || {
  echo "rollout failed: configure between two and four API replica ports" >&2
  exit 1
}
[[ "$target_ref" != -* ]] || {
  echo "rollout failed: target Git ref cannot start with a dash" >&2
  exit 1
}
declare -A validated_ports=()
for port in "${replica_ports[@]}"; do
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1024 && port <= 65535 )) || {
    echo "rollout failed: invalid API replica port" >&2
    exit 1
  }
  [[ -z "${validated_ports[$port]:-}" ]] || {
    echo "rollout failed: duplicate API replica port" >&2
    exit 1
  }
  validated_ports["$port"]=1
done
[[ -z "$(git -C "$app_root" status --porcelain)" ]] || {
  echo "rollout failed: application worktree is dirty" >&2
  exit 1
}

release_id="$(git -C "$app_root" rev-parse --verify "${target_ref}^{commit}")"
[[ "$release_id" =~ ^[0-9a-f]{40}$ ]] || {
  echo "rollout failed: target release is not a full Git commit" >&2
  exit 1
}

install -d -m 0755 "$release_root" "$slot_root"
install -d -o stockassistant -g stockassistant -m 0700 "$state_root"
exec 9>"/run/stock-assistant-api-rollout.lock"
flock -n 9 || {
  echo "rollout failed: another API rollout is active" >&2
  exit 1
}

target_dir="$release_root/$release_id"
staging=""
cleanup_prepare() {
  local original_status="$?"
  trap - EXIT
  if [[ -n "$staging" && -d "$staging" && "$staging" == "$release_root"/.* ]]; then
    rm -rf -- "$staging"
  fi
  exit "$original_status"
}
trap cleanup_prepare EXIT
if [[ ! -d "$target_dir" ]]; then
  staging="$(mktemp -d "$release_root/.${release_id}.XXXXXX")"
  chmod 0755 "$staging"
  git -C "$app_root" archive --format=tar "$release_id" | tar -xf - -C "$staging"
  printf '%s\n' "$release_id" >"$staging/RELEASE_ID"
  "$bootstrap_python" -m venv "$staging/.venv"
  "$staging/.venv/bin/pip" install --disable-pip-version-check \
    -r "$staging/backend/requirements.txt"
  npm --prefix "$staging/frontend" ci --no-audit --no-fund
  npm --prefix "$staging/frontend" run build
  [[ -s "$staging/frontend/dist/index.html" ]] || {
    echo "rollout failed: frontend release artifact missing" >&2
    exit 1
  }
  [[ "$staging" == "$release_root"/.* ]] || {
    echo "rollout failed: unsafe staging path" >&2
    exit 1
  }
  rm -rf -- "$staging/frontend/node_modules"
  chmod -R go-w "$staging"
  mv "$staging" "$target_dir"
  staging=""
fi
[[ "$(<"$target_dir/RELEASE_ID")" == "$release_id" ]] || {
  echo "rollout failed: release identity mismatch" >&2
  exit 1
}
[[ -s "$target_dir/frontend/dist/index.html" ]] || {
  echo "rollout failed: prepared release has no frontend build" >&2
  exit 1
}
[[ -x "$target_dir/.venv/bin/python" ]] || {
  echo "rollout failed: prepared release has no Python runtime" >&2
  exit 1
}
trap - EXIT

declare -A previous_targets=()
changed_ports=()
previous_static=""
static_changed=0
previous_upstream=""
upstream_changed=0
rollback_required=1

atomic_link() {
  local target="$1"
  local link="$2"
  local temporary="${link}.next.$$"
  rm -f -- "$temporary"
  ln -s "$target" "$temporary"
  mv -Tf "$temporary" "$link"
}

write_upstream_config() {
  local drained_port="${1:-}"
  local temporary="${nginx_upstream_file}.next.$$"
  : >"$temporary"
  local candidate
  for candidate in "${replica_ports[@]}"; do
    if [[ "$candidate" == "$drained_port" ]]; then
      printf 'server 127.0.0.1:%s down;\n' "$candidate" >>"$temporary"
    else
      printf 'server 127.0.0.1:%s max_fails=1 fail_timeout=10s;\n' "$candidate" >>"$temporary"
    fi
  done
  chmod 0644 "$temporary"
  mv -f "$temporary" "$nginx_upstream_file"
  upstream_changed=1
  nginx -t
  systemctl reload nginx
}

wait_for_replica() {
  local port="$1"
  local expected_release="$2"
  local expected_replica="api-${port}"
  local response=""
  local attempt
  for attempt in $(seq 1 "$health_timeout"); do
    response="$(curl --fail --silent --show-error --max-time 3 \
      "http://127.0.0.1:${port}/health/ready" 2>/dev/null || true)"
    if grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' <<<"$response" \
      && grep -Eq "\"replica_id\"[[:space:]]*:[[:space:]]*\"${expected_replica}\"" <<<"$response" \
      && grep -Eq "\"release_id\"[[:space:]]*:[[:space:]]*\"${expected_release}\"" <<<"$response"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

rollback() {
  local original_status="$?"
  if (( rollback_required == 0 )); then
    [[ -n "$staging" && -d "$staging" ]] && rm -rf -- "$staging"
    return
  fi
  trap - EXIT
  set +e
  echo "rollout failed: restoring previous API slots" >&2
  if (( static_changed == 1 )); then
    if [[ -n "$previous_static" ]]; then
      atomic_link "$previous_static" "$static_link"
    else
      rm -f -- "$static_link"
    fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
  fi
  local index port old_target old_release
  for (( index=${#changed_ports[@]}-1; index>=0; index-- )); do
    port="${changed_ports[$index]}"
    old_target="${previous_targets[$port]:-}"
    if (( upstream_changed == 1 )); then
      write_upstream_config "$port" >/dev/null 2>&1
      sleep "$drain_seconds"
    fi
    if [[ -n "$old_target" && -d "$old_target/backend" ]]; then
      atomic_link "$old_target" "$slot_root/$port"
      systemctl restart "stock-assistant-api@${port}.service"
      old_release="$(<"$old_target/RELEASE_ID")"
      wait_for_replica "$port" "$old_release" >/dev/null 2>&1
    else
      systemctl disable --now "stock-assistant-api@${port}.service" >/dev/null 2>&1
      rm -f -- "$slot_root/$port"
    fi
    (( upstream_changed == 1 )) && write_upstream_config >/dev/null 2>&1
  done
  if (( upstream_changed == 1 )); then
    if [[ -n "$previous_upstream" ]]; then
      printf '%s' "$previous_upstream" >"$nginx_upstream_file"
      chmod 0644 "$nginx_upstream_file"
      nginx -t >/dev/null 2>&1 && systemctl reload nginx
    else
      rm -f -- "$nginx_upstream_file"
    fi
  fi
  [[ -n "$staging" && -d "$staging" ]] && rm -rf -- "$staging"
  exit "$original_status"
}
trap rollback EXIT

if [[ -f "$nginx_upstream_file" ]]; then
  previous_upstream="$(<"$nginx_upstream_file")"$'\n'
fi

for port in "${replica_ports[@]}"; do
  slot="$slot_root/$port"
  if [[ -e "$slot" && ! -L "$slot" ]]; then
    echo "rollout failed: API slot is not a symbolic link: $slot" >&2
    exit 1
  fi
  previous_targets["$port"]="$(readlink -f "$slot" 2>/dev/null || true)"
  changed_ports+=("$port")
  if [[ -f "$nginx_upstream_file" ]] && nginx -t >/dev/null 2>&1; then
    write_upstream_config "$port"
    sleep "$drain_seconds"
  fi
  atomic_link "$target_dir" "$slot"
  systemctl enable "stock-assistant-api@${port}.service" >/dev/null
  systemctl restart "stock-assistant-api@${port}.service"
  if ! wait_for_replica "$port" "$release_id"; then
    echo "rollout failed: replica api-${port} did not become ready" >&2
    exit 1
  fi
  if (( upstream_changed == 1 )); then
    write_upstream_config
  fi
done

if [[ -L "$static_link" ]]; then
  previous_static="$(readlink -f "$static_link")"
elif [[ -e "$static_link" ]]; then
  echo "rollout failed: STATIC_CURRENT_LINK exists and is not a symbolic link" >&2
  exit 1
fi
atomic_link "$target_dir/frontend/dist" "$static_link"
static_changed=1
nginx -t
systemctl reload nginx
public_response=""
for attempt in $(seq 1 15); do
  public_response="$(curl --fail --silent --show-error --max-time 8 \
    "$public_health_url" 2>/dev/null || true)"
  if grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' <<<"$public_response" \
    && grep -Eq "\"release_id\"[[:space:]]*:[[:space:]]*\"${release_id}\"" \
      <<<"$public_response"; then
    break
  fi
  sleep 1
done
grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' <<<"$public_response" \
  && grep -Eq "\"release_id\"[[:space:]]*:[[:space:]]*\"${release_id}\"" <<<"$public_response" || {
  echo "rollout failed: public readiness or release identity is invalid" >&2
  exit 1
}

state_file="$state_root/current-release.json"
state_tmp="${state_file}.next.$$"
printf '{"schema_version":"api_rollout.v1","release_id":"%s","replicas":%s,"activated_at":"%s"}\n' \
  "$release_id" "${#replica_ports[@]}" "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" >"$state_tmp"
chown stockassistant:stockassistant "$state_tmp"
chmod 0600 "$state_tmp"
mv -f "$state_tmp" "$state_file"

rollback_required=0
echo "API rollout verified: release=${release_id} replicas=${#replica_ports[@]}"
