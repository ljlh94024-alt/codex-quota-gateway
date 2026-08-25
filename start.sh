#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required' >&2; exit 1; }

created_env=0
if [[ ! -f .env ]]; then cp .env.example .env; created_env=1; fi
trap 'if [[ "$created_env" == 1 ]]; then rm -f .env; fi' EXIT
python3 - <<'PY'
from pathlib import Path
import secrets
p=Path('.env'); lines=p.read_text(encoding='utf-8').splitlines(); out=[]; seen=set()
for line in lines:
    if line and not line.lstrip().startswith('#') and '=' in line:
        key,val=line.split('=',1); seen.add(key)
        if key=='BIND_HOST': line='BIND_HOST=0.0.0.0'
        elif key=='SECRET_KEY' and val in {'','replace-with-32-byte-random-secret'}: line='SECRET_KEY='+secrets.token_urlsafe(48)
    out.append(line)
if 'BIND_HOST' not in seen: out.append('BIND_HOST=0.0.0.0')
p.write_text('\n'.join(out)+'\n',encoding='utf-8')
PY
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
