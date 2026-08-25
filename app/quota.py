from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import hashlib
import json
import math
from typing import Any

from .config import settings
from .db import Database
from .usage import ensure_user_quota, get_quota_limit


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def next_reset(now: dt.datetime | None = None) -> dt.datetime:
    now = now or utc_now()
    days = 7 - now.weekday()
    candidate = (now + dt.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=7)
    return candidate


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(payload: Any) -> int:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, min(settings.max_context_tokens, math.ceil(len(raw) / 4)))


class QuotaManager:
    def __init__(self, db: Database):
        self.db = db
        self.lock = asyncio.Lock()

    async def _roll_week(self, user) -> None:
        await ensure_user_quota(self.db, user["id"], user["weekly_limit"], user["enabled"])
        try:
            reset = dt.datetime.fromisoformat(user["reset_time"])
        except (TypeError, ValueError):
            reset = utc_now()
        if utc_now() >= reset:
            await self.db.execute(
                "UPDATE users SET weekly_used=0, weekly_reserved=0, reset_time=? WHERE id=?",
                (iso(next_reset()), user["id"]),
            )

    async def reserve(self, user_id: int, estimate: int) -> tuple[bool, dict]:
        async with self.lock:
            user = await self.db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
            if not user:
                return False, {"reason": "user_not_found"}
            await self._roll_week(user)
            user = await self.db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
            state = await self.db.fetchone("SELECT * FROM quota_state WHERE id=1")
            total = int(state["total_quota"]) if state else settings.default_total_quota
            weekly_limit = await get_quota_limit(self.db, user_id, user["weekly_limit"])
            limit = max(1, math.floor(total * weekly_limit / 100))
            used = int(user["weekly_used"])
            reserved = int(user["weekly_reserved"])
            remaining = max(0, limit - used - reserved)
            if remaining <= 0:
                return False, {"reason": "weekly_quota_exceeded", "limit": limit, "used": used, "remaining": max(0, limit - used - reserved)}
            # Do not reject a request merely because its conservative estimate
            # crosses the boundary. Admit while actual usage is below the
            # user's share, reserve at most what remains, then settle against
            # the upstream-reported usage. The next request is blocked once
            # actual usage reaches the limit.
            reservation = min(max(1, int(estimate)), remaining)
            await self.db.execute("UPDATE users SET weekly_reserved=weekly_reserved+? WHERE id=?", (reservation, user_id))
            return True, {"limit": limit, "reserved": reservation, "remaining": max(0, remaining - reservation)}

    async def settle(self, user_id: int, reserved: int, actual: int) -> None:
        charged = max(1, int(actual or reserved))
        async with self.lock:
            await self.db.execute(
                "UPDATE users SET weekly_reserved=MAX(0, weekly_reserved-?), weekly_used=weekly_used+? WHERE id=?",
                (reserved, charged, user_id),
            )

    async def summary(self, user) -> dict:
        state = await self.db.fetchone("SELECT * FROM quota_state WHERE id=1")
        total = int(state["total_quota"]) if state else settings.default_total_quota
        weekly_limit = await get_quota_limit(self.db, user["id"], user["weekly_limit"])
        limit = max(1, math.floor(total * weekly_limit / 100))
        remaining = max(0, limit - int(user["weekly_used"]) - int(user["weekly_reserved"]))
        return {"weekly_limit_percent": weekly_limit, "quota_total": total, "quota_limit": limit, "used": user["weekly_used"], "reserved": user["weekly_reserved"], "remaining": remaining, "reset_time": user["reset_time"]}
