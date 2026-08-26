from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import hashlib
import json
import math
from typing import Any

import aiosqlite

from .config import settings
from .db import Database
from .usage import ensure_user_quota, get_quota_limit, week_bounds


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

    async def release_reservation(self, user_id: int, reserved: int) -> None:
        """Release an admission reservation without charging weekly usage."""
        if reserved <= 0:
            return
        async with self.lock:
            await self.db.execute(
                "UPDATE users SET weekly_reserved=MAX(0, weekly_reserved-?) WHERE id=?",
                (int(reserved), user_id),
            )

    async def finalize_task(
        self,
        task_id: str,
        *,
        status: str,
        error_text: str | None,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        request_time: str,
        model: str | None,
        request_id: str,
        error_type: str | None,
        charge: bool,
    ) -> bool:
        """Finalize one admitted task exactly once.

        Reservation ownership lives on the task row.  The state transition,
        user quota update, usage log and weekly rollup are committed in one
        SQLite transaction, so a duplicate cancellation/finally path cannot
        release another task's reservation or double-count usage.
        """
        now = iso(utc_now())
        observed_input = max(0, int(input_tokens or 0))
        observed_output = max(0, int(output_tokens or 0))
        observed_total = observed_input + observed_output
        final_phase = "completed" if status == "success" else status
        async with self.lock:
            async with aiosqlite.connect(self.db.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                task = await (
                    await db.execute(
                        "SELECT user_id,reserved_tokens,quota_state FROM tasks WHERE id=?",
                        (task_id,),
                    )
                ).fetchone()
                if not task or str(task["quota_state"] or "none") != "reserved":
                    await db.rollback()
                    return False

                user_id = int(task["user_id"])
                reserved = max(0, int(task["reserved_tokens"] or 0))
                charged = max(1, observed_total or reserved) if charge else 0
                new_quota_state = "settled" if charge else "released"

                await db.execute(
                    "UPDATE users SET weekly_reserved=MAX(0,weekly_reserved-?), weekly_used=weekly_used+? WHERE id=?",
                    (reserved, charged, user_id),
                )
                await db.execute(
                    """UPDATE tasks
                       SET status=?, phase=?, error=?, actual_tokens=?,
                           quota_state=?, quota_finalized_at=?, finished_at=?,
                           last_activity_at=?
                       WHERE id=? AND quota_state='reserved'""",
                    (status, final_phase, error_text, charged, new_quota_state, now, now, now, task_id),
                )
                usage_cursor = await db.execute(
                    """INSERT OR IGNORE INTO usage_logs(
                           user_id,request_id,model,input_tokens,output_tokens,
                           total_tokens,duration_ms,request_time,response_time,
                           status,error_type,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id,
                        request_id,
                        model,
                        observed_input,
                        observed_output,
                        observed_total,
                        max(0, int(duration_ms)),
                        request_time,
                        now,
                        status,
                        error_type,
                        now,
                    ),
                )

                # Keep dashboard request counts and token observations in sync
                # with the usage log; the INSERT is request-id unique.
                if usage_cursor.rowcount == 1:
                    start, end = week_bounds()
                    week_start, week_end = iso(start), iso(end)
                    await db.execute(
                        """INSERT INTO weekly_usage(
                               user_id,week_start,week_end,total_tokens,
                               request_count,estimated_usage_percent)
                           VALUES(?,?,?,?,1,0)
                           ON CONFLICT(user_id,week_start) DO UPDATE SET
                             total_tokens=weekly_usage.total_tokens+excluded.total_tokens,
                             request_count=weekly_usage.request_count+1""",
                        (user_id, week_start, week_end, observed_total),
                    )
                    usage_row = await (
                        await db.execute(
                            "SELECT total_tokens FROM weekly_usage WHERE user_id=? AND week_start=?",
                            (user_id, week_start),
                        )
                    ).fetchone()
                    quota_row = await (
                        await db.execute(
                            "SELECT total_quota FROM quota_state WHERE id=1"
                        )
                    ).fetchone()
                    user_quota = await (
                        await db.execute(
                            "SELECT weekly_limit FROM user_quota WHERE user_id=?",
                            (user_id,),
                        )
                    ).fetchone()
                    total_quota = int(quota_row["total_quota"]) if quota_row else settings.default_total_quota
                    limit_percent = float(user_quota["weekly_limit"]) if user_quota else 25.0
                    token_limit = max(1, math.floor(total_quota * limit_percent / 100))
                    percent = min(100.0, int(usage_row["total_tokens"]) / token_limit * 100) if usage_row else 0.0
                    await db.execute(
                        "UPDATE weekly_usage SET estimated_usage_percent=? WHERE user_id=? AND week_start=?",
                        (percent, user_id, week_start),
                    )
                await db.commit()
                return True

    async def recover_inflight(self, recovered_at: str) -> int:
        """Release reservations for tasks interrupted by a process restart."""
        async with self.lock:
            async with aiosqlite.connect(self.db.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                rows = await (
                    await db.execute(
                        """SELECT id,user_id,reserved_tokens,quota_state
                           FROM tasks WHERE status IN ('pending','running')"""
                    )
                ).fetchall()
                recovered = 0
                for row in rows:
                    reserved = max(0, int(row["reserved_tokens"] or 0))
                    if str(row["quota_state"] or "none") == "reserved" and reserved:
                        await db.execute(
                            "UPDATE users SET weekly_reserved=MAX(0,weekly_reserved-?) WHERE id=?",
                            (reserved, int(row["user_id"])),
                        )
                    await db.execute(
                        """UPDATE tasks SET status='failed',phase='failed',
                           error='gateway restarted before completion',
                           actual_tokens=0,quota_state=CASE WHEN quota_state='reserved' THEN 'released' ELSE quota_state END,
                           quota_finalized_at=?,finished_at=?,last_activity_at=?
                           WHERE id=?""",
                        (recovered_at, recovered_at, recovered_at, row["id"]),
                    )
                    recovered += 1
                # A legacy task created before reservation ownership was added
                # cannot be mapped to a reservation. It is safe to clear any
                # remaining aggregate only during startup, before traffic is
                # accepted, after all active task rows were reconciled.
                await db.execute("UPDATE users SET weekly_reserved=0 WHERE weekly_reserved<>0")
                await db.commit()
                return recovered

    async def summary(self, user) -> dict:
        state = await self.db.fetchone("SELECT * FROM quota_state WHERE id=1")
        total = int(state["total_quota"]) if state else settings.default_total_quota
        weekly_limit = await get_quota_limit(self.db, user["id"], user["weekly_limit"])
        limit = max(1, math.floor(total * weekly_limit / 100))
        remaining = max(0, limit - int(user["weekly_used"]) - int(user["weekly_reserved"]))
        return {"weekly_limit_percent": weekly_limit, "quota_total": total, "quota_limit": limit, "used": user["weekly_used"], "reserved": user["weekly_reserved"], "remaining": remaining, "reset_time": user["reset_time"]}
