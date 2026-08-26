#!/usr/bin/env bash
set -euo pipefail

command -v docker >/dev/null 2>&1
docker compose version >/dev/null 2>&1
command -v curl >/dev/null 2>&1

created_env=0
if [[ ! -f .env ]]; then cp .env.example .env; created_env=1; fi
cleanup() { if [[ "$created_env" == 1 ]]; then rm -f .env; fi; }
trap cleanup EXIT

docker compose config >/dev/null
docker compose up -d --build gateway
body=''
for _ in $(seq 1 60); do
    if body="$(curl -fsS --max-time 3 http://127.0.0.1:8080/healthz 2>/dev/null)" && printf '%s' "$body" | grep -q '"status":"ok"'; then break; fi
    sleep 1
done
printf '%s' "$body" | grep -q '"status":"ok"'
docker compose exec -T gateway getent hosts host.docker.internal >/dev/null
docker compose --profile https run --rm --no-deps --entrypoint sh caddy -c 'wget -qO- http://gateway:8080/healthz' | grep -q '"status":"ok"'
echo NETWORK_SMOKE_OK
