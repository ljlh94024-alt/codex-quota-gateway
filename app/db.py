from __future__ import annotations

import aiosqlite

from .config import settings


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    api_key_hash TEXT NOT NULL UNIQUE,
    weekly_limit REAL NOT NULL DEFAULT 25,
    weekly_used INTEGER NOT NULL DEFAULT 0,
    weekly_reserved INTEGER NOT NULL DEFAULT 0,
    reset_time TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT,
    request_id TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    request_time TEXT,
    response_time TEXT,
    status TEXT NOT NULL,
    error_type TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage_logs(user_id, created_at);
CREATE TABLE IF NOT EXISTS weekly_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    estimated_usage_percent REAL NOT NULL DEFAULT 0,
    UNIQUE(user_id, week_start),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_weekly_usage_user_week ON weekly_usage(user_id, week_start);
CREATE TABLE IF NOT EXISTS user_quota (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    weekly_limit REAL NOT NULL DEFAULT 25,
    enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    ip TEXT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_security_events_user_time ON security_events(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_security_events_level_time ON security_events(risk_level, timestamp);
CREATE TABLE IF NOT EXISTS quota_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_quota INTEGER NOT NULL,
    upstream_used INTEGER,
    upstream_remaining INTEGER,
    source TEXT NOT NULL DEFAULT 'local-default',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry ON admin_sessions(expires_at);
CREATE TABLE IF NOT EXISTS dashboard_access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accessed_at TEXT NOT NULL,
    ip TEXT NOT NULL,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dashboard_access_time ON dashboard_access_logs(accessed_at);
"""


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or settings.db_path

    async def init(self) -> None:
        import datetime as dt
        import os
        os.makedirs(str(__import__("pathlib").Path(self.path).parent), exist_ok=True)
        try:
            os.chmod(str(__import__("pathlib").Path(self.path).parent), 0o700)
        except OSError:
            pass
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(users)")}
            if "status" not in columns:
                await db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            usage_columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(usage_logs)")}
            for column, definition in {
                "request_time": "TEXT",
                "response_time": "TEXT",
                "error_type": "TEXT",
            }.items():
                if column not in usage_columns:
                    await db.execute(f"ALTER TABLE usage_logs ADD COLUMN {column} {definition}")
            await db.execute("INSERT OR IGNORE INTO user_quota(user_id,weekly_limit,enabled) SELECT id,weekly_limit,enabled FROM users")
            # Backfill weekly rollups once for usage rows created before the
            # statistics tables existed. INSERT OR IGNORE keeps later startups
            # idempotent and avoids counting the same historical rows twice.
            old_rows = await db.execute_fetchall(
                "SELECT user_id, COALESCE(request_time, created_at) AS event_time, total_tokens FROM usage_logs"
            )
            grouped: dict[tuple[int, str], list[int]] = {}
            for row in old_rows:
                try:
                    value = dt.datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if value.tzinfo is None:
                    value = value.replace(tzinfo=dt.timezone.utc)
                value = value.astimezone(dt.timezone.utc)
                start = (value - dt.timedelta(days=value.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                key = (int(row[0]), start.isoformat())
                bucket = grouped.setdefault(key, [0, 0])
                bucket[0] += max(0, int(row[2] or 0))
                bucket[1] += 1
            for (user_id, week_start), (tokens, requests) in grouped.items():
                end = (dt.datetime.fromisoformat(week_start) + dt.timedelta(days=7)).isoformat()
                await db.execute(
                    "INSERT OR IGNORE INTO weekly_usage(user_id,week_start,week_end,total_tokens,request_count,estimated_usage_percent) VALUES(?,?,?,?,?,0)",
                    (user_id, week_start, end, tokens, requests),
                )
            await db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            return await cur.fetchall()

    async def transaction(self, statements: list[tuple[str, tuple]]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            for sql, params in statements:
                await db.execute(sql, params)
            await db.commit()
