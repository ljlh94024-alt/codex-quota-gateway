#!/usr/bin/env python3
"""Prepare a local runtime .env without exposing or replacing secrets."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


PLACEHOLDER_SECRET = "replace-with-32-byte-random-secret"


def _parse_assignments(lines: list[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            positions.setdefault(key, []).append(index)
    return positions


def prepare_env(path: Path, updates: dict[str, str] | None = None) -> None:
    """Create/adjust known settings while preserving comments and unknown keys."""
    if not path.exists():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    updates = dict(updates or {})
    updates["BIND_HOST"] = "0.0.0.0"
    positions = _parse_assignments(lines)

    secret_indexes = positions.get("SECRET_KEY", [])
    if not secret_indexes:
        secret_value = secrets.token_urlsafe(48)
    else:
        current = lines[secret_indexes[-1]].split("=", 1)[1].strip()
        secret_value = current if current and current != PLACEHOLDER_SECRET else secrets.token_urlsafe(48)
    if secret_indexes:
        for index in secret_indexes:
            lines[index] = "SECRET_KEY=" + secret_value
    else:
        lines.append("SECRET_KEY=" + secret_value)

    positions = _parse_assignments(lines)
    for key, value in updates.items():
        assignment = f"{key}={value}"
        indexes = positions.get(key, [])
        if not indexes:
            lines.append(assignment)
            positions[key] = [len(lines) - 1]
        else:
            for index in indexes:
                lines[index] = assignment

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args()
    updates: dict[str, str] = {}
    for assignment in args.set:
        if "=" not in assignment:
            parser.error("--set requires KEY=VALUE")
        key, value = assignment.split("=", 1)
        if not key or "\n" in key or "\n" in value:
            parser.error("invalid --set assignment")
        updates[key] = value
    prepare_env(args.env_file, updates)
    print("ENV_PREPARED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
