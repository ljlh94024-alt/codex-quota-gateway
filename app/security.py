from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import settings
from .db import Database
from .quota import iso, utc_now


@dataclass(frozen=True)
class Rule:
    name: str
    level: str
    enabled: bool = True


@dataclass(frozen=True)
class SecretMatch:
    rule: str
    match_type: str


class SecurityGuard:
    """Deterministic, low-false-positive security controls for the Gateway."""

    SECRET_PATTERNS = (
        ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{20,}\b")),
        ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
        ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
        ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
        ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
        ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
        ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    )

    def __init__(self, db: Database):
        self.db = db
        self.rules = self._load_rules()
        self._lock = asyncio.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._violations: dict[int, deque[float]] = defaultdict(deque)
        self._suspended_until: dict[int, float] = {}

    def _load_rules(self) -> dict[str, Rule]:
        defaults = {
            "suspicious_keyword": Rule("suspicious_keyword", "warning", False),
            "leaked_secret": Rule("leaked_secret", "block", True),
            "abnormal_usage": Rule("abnormal_usage", "review", True),
        }
        try:
            raw = yaml.safe_load(Path(settings.security_rules_path).read_text(encoding="utf-8")) or {}
            for item in raw.get("rules", []):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"])
                defaults[name] = Rule(name, str(item.get("level", "warning")), bool(item.get("enabled", True)))
        except (OSError, yaml.YAMLError, TypeError):
            pass
        return defaults

    async def audit(self, user_id: int | None, event_type: str, risk_level: str, ip: str | None, action: str, details: dict[str, Any] | None = None) -> None:
        if not settings.audit_log:
            return
        safe_details = details or {}
        await self.db.execute(
            "INSERT INTO security_events(user_id,event_type,risk_level,ip,timestamp,action,details) VALUES(?,?,?,?,?,?,?)",
            (user_id, event_type, risk_level, ip, iso(utc_now()), action, json.dumps(safe_details, ensure_ascii=False, separators=(",", ":"))),
        )

    def _trim(self, bucket: deque[float], now: float) -> None:
        cutoff = now - 60.0
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    async def check_rate(self, user_id: int, key_fingerprint: str, ip: str | None) -> tuple[bool, int, str, int | None]:
        now = time.monotonic()
        async with self._lock:
            suspended_until = self._suspended_until.get(user_id, 0.0)
            if suspended_until > now:
                return False, 429, "temporarily_suspended", max(1, int(suspended_until - now))
            if suspended_until:
                self._suspended_until.pop(user_id, None)

            dimensions = (f"user:{user_id}", f"key:{key_fingerprint}", f"ip:{ip or 'unknown'}")
            for dimension in dimensions:
                bucket = self._requests[dimension]
                self._trim(bucket, now)
                if len(bucket) >= max(1, settings.rate_limit_per_minute):
                    violations = self._violations[user_id]
                    self._trim(violations, now)
                    violations.append(now)
                    if len(violations) >= max(1, settings.rate_limit_violation_threshold):
                        self._suspended_until[user_id] = now + max(1, settings.rate_limit_suspend_seconds)
                        return False, 429, "temporarily_suspended", settings.rate_limit_suspend_seconds
                    return False, 429, "rate_limit_exceeded", 60
            for dimension in dimensions:
                self._requests[dimension].append(now)
        return True, 200, "ok", None

    def scan_payload(self, payload: Any) -> SecretMatch | None:
        rule = self.rules.get("leaked_secret")
        if not settings.secret_scan or not rule or not rule.enabled:
            return None
        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        for match_type, pattern in self.SECRET_PATTERNS:
            if pattern.search(text):
                return SecretMatch(rule.name, match_type)
        return None

    async def observe_usage(self, user_id: int, ip: str | None) -> None:
        rule = self.rules.get("abnormal_usage")
        if not rule or not rule.enabled:
            return
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(total_tokens),0) AS tokens FROM usage_logs WHERE user_id=? AND created_at >= datetime('now', ?)",
            (user_id, f"-{max(1, settings.abnormal_window_minutes)} minutes"),
        )
        if not row:
            return
        requests = int(row["requests"])
        tokens = int(row["tokens"])
        if requests >= settings.abnormal_requests_10m or tokens >= settings.abnormal_tokens_10m:
            recent = await self.db.fetchone(
                "SELECT id FROM security_events WHERE user_id=? AND event_type='abnormal_usage' AND timestamp >= datetime('now', ?)",
                (user_id, f"-{max(1, settings.abnormal_window_minutes)} minutes"),
            )
            if recent:
                return
            await self.audit(user_id, "abnormal_usage", rule.level, ip, "review", {"window_minutes": settings.abnormal_window_minutes, "request_count": requests, "total_tokens": tokens})
