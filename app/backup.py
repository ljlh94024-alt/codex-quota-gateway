from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .config import settings


def _external_upload(path: Path) -> None:
    target = settings.backup_target.strip()
    if not target:
        return
    parsed = urlparse(target)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("BACKUP_TARGET must be an s3://bucket/prefix URL")
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required when BACKUP_TARGET is configured") from exc
    prefix = parsed.path.strip("/")
    key = f"{prefix}/{path.name}" if prefix else path.name
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region or None,
    )
    client.upload_file(str(path), parsed.netloc, key, ExtraArgs={"ContentType": "application/gzip"})


def backup_database() -> Path:
    source = Path(settings.db_path)
    target_dir = Path(settings.backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except OSError:
        pass
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    target = target_dir / f"usage_{stamp}.db.gz"
    temp_db = target_dir / f".usage_{stamp}.db.tmp"
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(temp_db)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    try:
        os.chmod(temp_db, 0o600)
        with temp_db.open("rb") as raw, gzip.open(target, "wb", compresslevel=6) as compressed:
            shutil.copyfileobj(raw, compressed)
        os.chmod(target, 0o600)
    finally:
        temp_db.unlink(missing_ok=True)
    try:
        _external_upload(target)
    except Exception:
        # Local recovery must continue even when an optional remote target is
        # unavailable. Operators can inspect the local file and retry later.
        pass
    files = sorted(target_dir.glob("usage_*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[max(1, settings.backup_retention):]:
        old.unlink(missing_ok=True)
    return target


async def daily_backup_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=86400)
        except asyncio.TimeoutError:
            try:
                await asyncio.to_thread(backup_database)
            except (OSError, sqlite3.Error):
                pass
