#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v docker >/dev/null 2>&1
docker compose version >/dev/null 2>&1

export DOMAIN=example.invalid
export MSYS_NO_PATHCONV=1
docker compose --profile https config >/dev/null
docker compose --profile https run --rm --no-deps \
    --entrypoint caddy caddy \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile
echo DEPLOYMENT_CONFIG_SMOKE_OK
