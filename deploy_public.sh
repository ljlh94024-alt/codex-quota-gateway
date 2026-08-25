#!/usr/bin/env bash
set -eu

: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN is required}"
: "${DUCKDNS_SUBDOMAIN:?DUCKDNS_SUBDOMAIN is required}"
export SERVER_IP="${SERVER_IP:?SERVER_IP is required}"
export DOMAIN="${DOMAIN:-${DUCKDNS_SUBDOMAIN%.duckdns.org}.duckdns.org}"

command -v docker >/dev/null
docker compose version >/dev/null
command -v python3 >/dev/null
command -v curl >/dev/null

python3 scripts/setup_duckdns.py
python3 scripts/generate_caddy.py

set_env() {
    key="$1"
    value="$2"
    if grep -q "^${key}=" .env; then
        sed -i "s/^${key}=.*/${key}=${value}/" .env
    else
        printf '%s\n' "${key}=${value}" >> .env
    fi
}
set_env PUBLIC_TEST_MODE false
set_env COOKIE_SECURE true
set_env SESSION_COOKIE_SECURE true
set_env BIND_HOST 0.0.0.0

docker compose --profile public-test stop public_test >/dev/null 2>&1 || true
docker compose --profile public-test rm -f public_test >/dev/null 2>&1 || true
docker compose --profile https up -d

https_code=""
for _ in $(seq 1 30); do
    https_code="$(curl -k -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${DOMAIN}/healthz" || true)"
    [ "$https_code" = "200" ] && break
    sleep 5
done
[ "$https_code" = "200" ]
dashboard_code="$(curl -k -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${DOMAIN}/dashboard")"
api_code="$(curl -k -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${DOMAIN}/v1/models")"
admin_code="$(curl -k -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${DOMAIN}/admin/dashboard")"
redirect_code="$(curl -k -sS -o /dev/null -w '%{http_code}' --max-time 15 "http://${DOMAIN}/dashboard" || true)"
[ "$dashboard_code" = "200" ]
[ "$api_code" = "401" ]
[ "$admin_code" = "303" ] || [ "$admin_code" = "307" ]

cat > PUBLIC_DEPLOY_REPORT.md <<EOF
# Public Deployment Report

Domain: ${DOMAIN}
DNS: DNS_READY (${SERVER_IP})
HTTPS: HTTP ${https_code}
Dashboard: HTTP ${dashboard_code}
API without key: HTTP ${api_code}
Admin without session: HTTP ${admin_code}
HTTP redirect: HTTP ${redirect_code}
Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
echo "PUBLIC_DEPLOYMENT_OK"
