from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from .config import settings
from .db import Database
from .quota import hash_api_key, iso, next_reset, utc_now


async def main() -> None:
    db = Database()
    await db.init()
    secret_dir = Path(settings.secrets_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    users = await db.fetchall("SELECT username FROM users ORDER BY id")
    records = []
    if not users:
        for index in range(1, 5):
            username = f"user{index}"
            key = "cg_" + secrets.token_urlsafe(32)
            await db.execute(
                "INSERT INTO users(username, api_key_hash, weekly_limit, reset_time, created_at) VALUES(?,?,?,?,?)",
                (username, hash_api_key(key), 25.0, iso(next_reset()), iso(utc_now())),
            )
            records.append({"username": username, "api_key": key})
    else:
        for row in users:
            records.append({"username": row["username"], "api_key": "(existing-key-not-redisplayed)"})
    await db.execute(
        "INSERT INTO quota_state(id,total_quota,source,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO NOTHING",
        (settings.default_total_quota, "local-default", iso(utc_now())),
    )
    key_file = secret_dir / "gateway_keys.json"
    if records and records[0]["api_key"].startswith("cg_"):
        key_file.write_text(json.dumps({"users": records, "generated_at": iso(utc_now())}, indent=2), encoding="utf-8")
        os.chmod(key_file, 0o600)
    admin_file = secret_dir / "admin_key.txt"
    if not settings.admin_key and not admin_file.exists():
        admin_file.write_text("adm_" + secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(admin_file, 0o600)
    print(f"users={len(records)}")
    print(f"keys_file={key_file}")
    print(f"admin_file={admin_file}")


if __name__ == "__main__":
    asyncio.run(main())
