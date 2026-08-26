#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required' >&2; exit 1; }

if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
fi
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
"$python_bin" scripts/prepare_runtime_env.py --env-file .env
docker compose config >/dev/null
docker compose up -d --build gateway
for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 http://127.0.0.1:8080/healthz | grep -q '"status":"ok"'; then
        docker compose exec -T gateway getent hosts host.docker.internal >/dev/null
        echo NETWORK_SMOKE_OK
        exit 0
    fi
    sleep 1
done
docker compose logs --tail=120 gateway >&2 || true
echo 'Gateway did not become healthy' >&2
exit 1
