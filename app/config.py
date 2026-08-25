from dataclasses import dataclass
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("DB_PATH", "/app/data/gateway.db")
    upstream_base_url: str = os.getenv("UPSTREAM_BASE_URL", "").rstrip("/")
    upstream_api_key: str = os.getenv("UPSTREAM_API_KEY", "")
    admin_key: str = os.getenv("ADMIN_KEY", "")
    global_concurrency: int = _int("MAX_GLOBAL_CONCURRENCY", 6)
    user_concurrency: int = _int("MAX_USER_CONCURRENCY", 2)
    max_retries: int = _int("MAX_RETRIES", 3)
    default_total_quota: int = _int("DEFAULT_TOTAL_QUOTA", 1_000_000_000)
    max_context_tokens: int = _int("MAX_CONTEXT_TOKENS", 270_000)
    request_timeout: float = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "600"))
    secrets_dir: str = os.getenv("SECRETS_DIR", "/app/secrets")
    rate_limit_per_minute: int = _int("RATE_LIMIT_PER_MINUTE", 300)
    rate_limit_suspend_seconds: int = _int("RATE_LIMIT_SUSPEND_SECONDS", 300)
    rate_limit_violation_threshold: int = _int("RATE_LIMIT_VIOLATION_THRESHOLD", 5)
    secret_scan: bool = _bool("SECRET_SCAN", True)
    audit_log: bool = _bool("AUDIT_LOG", True)
    security_rules_path: str = os.getenv("SECURITY_RULES_PATH", "/app/security_rules.yaml")
    abnormal_window_minutes: int = _int("ABNORMAL_WINDOW_MINUTES", 10)
    abnormal_requests_10m: int = _int("ABNORMAL_REQUESTS_10M", 30)
    abnormal_tokens_10m: int = _int("ABNORMAL_TOKENS_10M", 100_000)
    backup_dir: str = os.getenv("BACKUP_DIR", "/app/data/backup")
    backup_retention: int = _int("BACKUP_RETENTION", 7)
    backup_target: str = os.getenv("BACKUP_TARGET", "")
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "")
    s3_access_key_id: str = os.getenv("S3_ACCESS_KEY_ID", "")
    s3_secret_access_key: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
    s3_region: str = os.getenv("S3_REGION", "auto")
    secret_key: str = os.getenv("SECRET_KEY", "")
    admin_password_hash: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    admin_session_ttl_seconds: int = _int("ADMIN_SESSION_TTL_SECONDS", 28800)
    session_cookie_secure: bool = _bool("SESSION_COOKIE_SECURE", True)
    domain: str = os.getenv("DOMAIN", "")
    public_dashboard_mode: bool = _bool("PUBLIC_DASHBOARD_MODE", False)
    public_test_mode: bool = _bool("PUBLIC_TEST_MODE", False)
    public_port: int = _int("PUBLIC_PORT", 18080)
    bind_host: str = os.getenv("BIND_HOST", "127.0.0.1")
    public_dashboard_password: str = os.getenv("PUBLIC_DASHBOARD_PASSWORD", "")
    cookie_secure: bool = _bool("COOKIE_SECURE", True)

    @property
    def db_parent(self) -> Path:
        return Path(self.db_path).parent


settings = Settings()
