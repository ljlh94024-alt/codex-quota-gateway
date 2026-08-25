from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .config import settings
from .db import Database


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def week_bounds(now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    now = now or utc_now()
    start = now - dt.timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + dt.timedelta(days=7)


async def ensure_user_quota(db: Database, user_id: int, default_limit: float = 25.0, enabled: int = 1) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO user_quota(user_id,weekly_limit,enabled) VALUES(?,?,?)",
        (user_id, float(default_limit), int(enabled)),
    )


async def get_quota_limit(db: Database, user_id: int, default_limit: float = 25.0) -> float:
    row = await db.fetchone("SELECT weekly_limit FROM user_quota WHERE user_id=?", (user_id,))
    return float(row["weekly_limit"]) if row else float(default_limit)


async def update_weekly_usage(db: Database, user_id: int, tokens: int, request_time: str | None = None) -> None:
    start, end = week_bounds()
    week_start, week_end = iso(start), iso(end)
    when = request_time or iso(utc_now())
    await db.execute(
        """INSERT INTO weekly_usage(user_id,week_start,week_end,total_tokens,request_count,estimated_usage_percent)
           VALUES(?,?,?,?,1,?)
           ON CONFLICT(user_id,week_start) DO UPDATE SET
             total_tokens=weekly_usage.total_tokens+excluded.total_tokens,
             request_count=weekly_usage.request_count+1,
             estimated_usage_percent=excluded.estimated_usage_percent""",
        (user_id, week_start, week_end, max(0, int(tokens)), 0.0),
    )
    await refresh_week_percent(db, user_id, week_start)


async def refresh_week_percent(db: Database, user_id: int, week_start: str | None = None) -> None:
    start, _ = week_bounds()
    week_start = week_start or iso(start)
    usage = await db.fetchone("SELECT total_tokens FROM weekly_usage WHERE user_id=? AND week_start=?", (user_id, week_start))
    if not usage:
        return
    state = await db.fetchone("SELECT total_quota,updated_at,source FROM quota_state WHERE id=1")
    limit_percent = await get_quota_limit(db, user_id)
    total_quota = int(state["total_quota"]) if state else settings.default_total_quota
    token_limit = max(1, math.floor(total_quota * limit_percent / 100))
    percent = min(100.0, float(usage["total_tokens"]) / token_limit * 100)
    await db.execute("UPDATE weekly_usage SET estimated_usage_percent=? WHERE user_id=? AND week_start=?", (percent, user_id, week_start))


async def user_usage(db: Database, user: Any) -> dict[str, Any]:
    start, end = week_bounds()
    week_start, week_end = iso(start), iso(end)
    await ensure_user_quota(db, user["id"], user["weekly_limit"], user["enabled"])
    row = await db.fetchone("SELECT * FROM weekly_usage WHERE user_id=? AND week_start=?", (user["id"], week_start))
    quota = await db.fetchone("SELECT weekly_limit,enabled FROM user_quota WHERE user_id=?", (user["id"],))
    state = await db.fetchone("SELECT total_quota,updated_at,source FROM quota_state WHERE id=1")
    total_quota = int(state["total_quota"]) if state else settings.default_total_quota
    weekly_limit = float(quota["weekly_limit"]) if quota else float(user["weekly_limit"])
    token_limit = max(1, math.floor(total_quota * weekly_limit / 100))
    tokens = int(row["total_tokens"]) if row else 0
    requests = int(row["request_count"]) if row else 0
    today = await db.fetchone(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(total_tokens),0) AS tokens FROM usage_logs WHERE user_id=? AND created_at >= date('now')",
        (user["id"],),
    )
    percent = min(100.0, tokens / token_limit * 100)
    return {
        "user": user["username"],
        "api_status": "active" if int(user["enabled"]) else "disabled",
        "week_usage": {"tokens": tokens, "percent": round(percent, 2), "remaining": max(0, token_limit - tokens)},
        "quota": {
            "basis": "estimated",
            "estimated_total_tokens": total_quota,
            "weekly_limit_percent": weekly_limit,
            "estimated_token_limit": token_limit,
            "reset_time": week_end,
            "last_sync_time": state["updated_at"] if state else None,
        },
        "requests": requests,
        "today": {"tokens": int(today["tokens"]), "requests": int(today["requests"])},
    }


async def user_history(db: Database, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    limit = min(max(1, int(limit)), 100)
    rows = await db.fetchall(
        """SELECT request_time, response_time, model, input_tokens, output_tokens, total_tokens,
                  duration_ms AS duration, status, error_type
           FROM usage_logs WHERE user_id=? ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    )
    return [dict(row) for row in rows]


async def admin_usage(db: Database) -> dict[str, Any]:
    start, end = week_bounds()
    week_start, week_end = iso(start), iso(end)
    total = await db.fetchone("SELECT total_quota,updated_at,source FROM quota_state WHERE id=1")
    users = await db.fetchall(
        """SELECT u.id,u.username,COALESCE(q.weekly_limit,u.weekly_limit) AS weekly_limit,
                  COALESCE(w.total_tokens,0) AS total_tokens,COALESCE(w.request_count,0) AS request_count,
                  COALESCE(w.estimated_usage_percent,0) AS estimated_usage_percent,
                  u.enabled,u.status
           FROM users u LEFT JOIN user_quota q ON q.user_id=u.id
           LEFT JOIN weekly_usage w ON w.user_id=u.id AND w.week_start=?
           ORDER BY total_tokens DESC""",
        (week_start,),
    )
    total_quota = int(total["total_quota"]) if total else settings.default_total_quota
    result = []
    total_tokens = 0
    total_requests = 0
    for row in users:
        item = dict(row)
        limit = max(1, math.floor(total_quota * float(item["weekly_limit"]) / 100))
        item["estimated_usage_percent"] = round(min(100.0, int(item["total_tokens"]) / limit * 100), 2)
        total_tokens += int(item["total_tokens"])
        total_requests += int(item["request_count"])
        result.append(item)
    return {
        "week_start": week_start,
        "week_end": week_end,
        "quota_basis": "estimated",
        "estimated_total_quota": total_quota,
        "last_sync_time": total["updated_at"] if total else None,
        "total_tokens": total_tokens,
        "total_requests": total_requests,
        "users": result,
    }


async def public_dashboard_usage(db: Database) -> dict[str, Any]:
    """Return non-secret pool and user usage fields for the read-only page.

    User names are intentionally shown because the owner requested an
    operational sharing dashboard. Credentials, hashes, tokens, and OAuth
    fields remain excluded from this projection.
    """
    start, end = week_bounds()
    week_start, week_end = iso(start), iso(end)
    state = await db.fetchone("SELECT total_quota,updated_at FROM quota_state WHERE id=1")
    estimated_total = int(state["total_quota"]) if state else settings.default_total_quota
    rows = await db.fetchall(
        """SELECT u.id,u.username,COALESCE(q.weekly_limit,u.weekly_limit) AS weekly_limit,
                  COALESCE(w.total_tokens,0) AS total_tokens,
                  COALESCE(w.request_count,0) AS request_count
           FROM users u LEFT JOIN user_quota q ON q.user_id=u.id
           LEFT JOIN weekly_usage w ON w.user_id=u.id AND w.week_start=?
           WHERE u.enabled=1 ORDER BY u.id""",
        (week_start,),
    )
    total_tokens = sum(int(row["total_tokens"]) for row in rows)
    users = []
    total_requests = 0
    for row in rows:
        token_limit = max(1, math.floor(estimated_total * float(row["weekly_limit"]) / 100))
        tokens = int(row["total_tokens"])
        requests = int(row["request_count"])
        total_requests += requests
        users.append({
            "name": row["username"],
            "weekly_limit_percent": float(row["weekly_limit"]),
            "tokens": tokens,
            "token_limit": token_limit,
            "remaining_tokens": max(0, token_limit - tokens),
            "request_count": requests,
            "weekly_usage_percent": round(min(100.0, tokens / token_limit * 100), 2),
        })
    used_percent = round(min(100.0, total_tokens / max(1, estimated_total) * 100), 2)
    return {
        "pool_name": "Pro",
        "pool_count": 1,
        "quota_basis": "estimated",
        "estimated_total_tokens": estimated_total,
        "total_tokens": total_tokens,
        "total_requests": total_requests,
        "pool_usage_percent": used_percent,
        "estimated_used_percent": used_percent,
        "estimated_remaining_percent": round(max(0.0, 100.0 - used_percent), 2),
        "last_sync_time": state["updated_at"] if state else None,
        "week_start": week_start,
        "week_end": week_end,
        "users": users,
    }
