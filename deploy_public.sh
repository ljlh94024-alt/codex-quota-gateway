#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN is required}"
: "${DUCKDNS_SUBDOMAIN:?DUCKDNS_SUBDOMAIN is required}"
: "${SERVER_IP:?SERVER_IP is required}"
DOMAIN="${DOMAIN:-${DUCKDNS_SUBDOMAIN%.duckdns.org}.duckdns.org}"
export SERVER_IP DOMAIN
export MSYS_NO_PATHCONV="${MSYS_NO_PATHCONV:-1}"

command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required' >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo 'curl is required' >&2; exit 1; }
[[ -f .env.example ]] || { echo '.env.example is required' >&2; exit 1; }
[[ -f caddy/Caddyfile ]] || { echo 'caddy/Caddyfile is required' >&2; exit 1; }

python_bin="${PYTHON_BIN:-python3}"
python_fallback="${PYTHON_FALLBACK:-python}"
if command -v "$python_bin" >/dev/null 2>&1 && "$python_bin" -c 'import sys' >/dev/null 2>&1; then
    :
elif command -v "$python_fallback" >/dev/null 2>&1 && "$python_fallback" -c 'import sys' >/dev/null 2>&1; then
    python_bin="$python_fallback"
else
    echo 'No usable Python interpreter found; checked PYTHON_BIN and PYTHON_FALLBACK.' >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    cp .env.example .env
fi
"$python_bin" scripts/prepare_runtime_env.py --env-file .env \
    --set PUBLIC_TEST_MODE=false \
    --set COOKIE_SECURE=true \
    --set SESSION_COOKIE_SECURE=true \
    --set DOMAIN="$DOMAIN"

"$python_bin" scripts/setup_duckdns.py

docker compose --profile https config >/dev/null
docker compose --profile https run --rm --no-deps \
    --entrypoint caddy caddy \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile

docker compose --profile public-test stop public_test >/dev/null 2>&1 || true
docker compose --profile public-test rm -f public_test >/dev/null 2>&1 || true
docker compose --profile https up -d --build gateway caddy

local_body=""
local_code=""
for _ in $(seq 1 60); do
    local_code="$(curl -sS -o "/tmp/codex-gateway-health.$$" -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/healthz || true)"
    local_body="$(cat "/tmp/codex-gateway-health.$$" 2>/dev/null || true)"
    if [[ "$local_code" == "200" ]] && grep -q '"status":"ok"' <<<"$local_body"; then
        break
    fi
    sleep 1
done
rm -f "/tmp/codex-gateway-health.$$"
if [[ "$local_code" != "200" ]] || ! grep -q '"status":"ok"' <<<"$local_body"; then
    echo 'Gateway local health check failed' >&2
    docker compose logs --tail=120 gateway >&2 || true
    exit 1
fi

https_code=""
for _ in $(seq 1 30); do
    https_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 20 "https://${DOMAIN}/healthz" || true)"
    [[ "$https_code" == "200" ]] && break
    sleep 5
done
[[ "$https_code" == "200" ]] || { echo 'Public HTTPS health check failed' >&2; exit 1; }

dashboard_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 20 "https://${DOMAIN}/dashboard")"
api_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 20 "https://${DOMAIN}/v1/models")"
admin_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 20 "https://${DOMAIN}/admin/dashboard")"
redirect_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 20 "http://${DOMAIN}/dashboard" || true)"
[[ "$dashboard_code" == "200" ]] || exit 1
[[ "$api_code" == "401" ]] || exit 1
[[ "$admin_code" == "303" || "$admin_code" == "307" ]] || exit 1
[[ "$redirect_code" == "308" ]] || exit 1

umask 077
mkdir -p reports
chmod 700 reports
report_path="reports/public-deploy-$(date -u +%Y%m%dT%H%M%SZ).md"
{
    echo '# Public Deployment Report'
    echo
    echo 'Domain: configured (redacted)'
    echo 'DNS: ready'
    echo "Local Gateway: HTTP ${local_code}"
    echo "HTTPS: HTTP ${https_code}"
    echo "Dashboard: HTTP ${dashboard_code}"
    echo "API without key: HTTP ${api_code}"
    echo "Admin without session: HTTP ${admin_code}"
    echo "HTTP redirect: HTTP ${redirect_code}"
    echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$report_path"
chmod 600 "$report_path"
echo "PUBLIC_DEPLOYMENT_OK report=$report_path"
