#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v docker >/dev/null 2>&1
docker compose version >/dev/null 2>&1
[[ -f .env.example ]]

created_env=0
if [[ ! -f .env ]]; then
    install -m 600 .env.example .env
    created_env=1
fi
cleanup() {
    if [[ "$created_env" == 1 ]]; then
        rm -f .env
    fi
}
trap cleanup EXIT

export DOMAIN=example.invalid
export MSYS_NO_PATHCONV=1
docker compose --profile https config >/dev/null
docker compose --profile https run --rm --no-deps \
    --entrypoint caddy caddy \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile
echo DEPLOYMENT_CONFIG_SMOKE_OK
