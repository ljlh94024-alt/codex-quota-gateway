from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.backup import backup_database
from app.config import settings


def backup_files() -> list[Path]:
    return sorted(Path(settings.backup_dir).glob("usage_*.db.gz"), key=lambda p: p.name, reverse=True)


def list_backups() -> None:
    for path in backup_files():
        print(f"{path.name}\t{path.stat().st_size} bytes")


def restore(name: str) -> None:
    target = Path(settings.backup_dir, name).resolve()
    backup_root = Path(settings.backup_dir).resolve()
    if target.parent != backup_root or target.name != name or target.suffix != ".gz":
        raise SystemExit("invalid backup filename")
    if not target.is_file():
        raise SystemExit("backup file not found")
    backup_database()
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="restore-", suffix=".db", dir=db_path.parent)
    os.close(fd)
    temp = Path(tmp_name)
    try:
        with gzip.open(target, "rb") as source, temp.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        check = sqlite3.connect(temp)
        result = check.execute("PRAGMA quick_check").fetchone()[0]
        check.close()
        if result != "ok":
            raise SystemExit("backup quick_check failed")
        os.chmod(temp, 0o600)
        os.replace(temp, db_path)
        os.chmod(db_path, 0o600)
    finally:
        temp.unlink(missing_ok=True)
    print(f"restored={target.name}")


parser = argparse.ArgumentParser(description="List or restore Codex Gateway SQLite backups")
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("list")
restore_parser = sub.add_parser("restore")
restore_parser.add_argument("filename")
args = parser.parse_args()
if args.command == "list":
    list_backups()
else:
    restore(args.filename)
