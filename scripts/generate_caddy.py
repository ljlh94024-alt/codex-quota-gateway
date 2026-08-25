#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


domain = os.environ.get("DOMAIN", "").strip()
if not domain:
    subdomain = os.environ.get("DUCKDNS_SUBDOMAIN", "").strip().removesuffix(".duckdns.org")
    domain = f"{subdomain}.duckdns.org" if subdomain else ""
if not domain or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in domain):
    raise SystemExit("DOMAIN is required")

target = Path(__file__).resolve().parents[1] / "caddy" / "Caddyfile"
target.parent.mkdir(parents=True, exist_ok=True)
content = (
    domain + " {\n"
    "    encode gzip\n"
    "    redir / /dashboard 302\n"
    "    reverse_proxy gateway:8080\n"
    "    header {\n"
    '        Strict-Transport-Security "max-age=31536000; includeSubDomains"\n'
    '        X-Content-Type-Options "nosniff"\n'
    '        X-Frame-Options "DENY"\n'
    '        Referrer-Policy "same-origin"\n'
    "    }\n"
    "}\n"
)
target.write_text(content, encoding="utf-8")
print("CADDYFILE_GENERATED")
