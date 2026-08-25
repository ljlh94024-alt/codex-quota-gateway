from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from .config import settings
from .db import Database
from .quota import iso, utc_now


PBKDF2_ITERATIONS = 310_000


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password or len(password) < 12:
        raise ValueError("admin password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(iterations, base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode())


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(iterations)
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(actual).decode(), expected)
    except (ValueError, TypeError, base64.binascii.Error):
        return False


def password_hash() -> str:
    if settings.admin_password_hash:
        return settings.admin_password_hash
    try:
        return Path(settings.secrets_dir, "admin_password_hash.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    key = settings.secret_key.encode("utf-8") if settings.secret_key else b"codex-gateway-session-fallback"
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


async def create_session(db: Database) -> tuple[str, str]:
    token = new_session_token()
    now = utc_now()
    expires = now.timestamp() + settings.admin_session_ttl_seconds
    expires_at = dt_from_timestamp(expires)
    await db.execute(
        "INSERT INTO admin_sessions(token_hash,created_at,expires_at) VALUES(?,?,?)",
        (token_hash(token), iso(now), iso(expires_at)),
    )
    return token, iso(expires_at)


def dt_from_timestamp(value: float):
    import datetime as dt

    return dt.datetime.fromtimestamp(value, dt.timezone.utc)


async def valid_session(db: Database, token: str | None) -> bool:
    if not token:
        return False
    row = await db.fetchone(
        "SELECT id FROM admin_sessions WHERE token_hash=? AND expires_at>?",
        (token_hash(token), iso(utc_now())),
    )
    return row is not None


async def delete_session(db: Database, token: str | None) -> None:
    if token:
        await db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (token_hash(token),))
