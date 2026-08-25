#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import time
import urllib.parse
import urllib.request


TOKEN = os.environ.get("DUCKDNS_TOKEN", "").strip()
SUBDOMAIN = os.environ.get("DUCKDNS_SUBDOMAIN", "").strip().removesuffix(".duckdns.org")
SERVER_IP = os.environ.get("SERVER_IP", "").strip()

if not TOKEN or not SUBDOMAIN or not SERVER_IP:
    raise SystemExit("DUCKDNS_TOKEN, DUCKDNS_SUBDOMAIN and SERVER_IP are required")
if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in SUBDOMAIN):
    raise SystemExit("invalid DuckDNS subdomain")

domain = f"{SUBDOMAIN}.duckdns.org"
query = urllib.parse.urlencode({"domains": SUBDOMAIN, "token": TOKEN, "ip": SERVER_IP, "verbose": "true"})
with urllib.request.urlopen(
    urllib.request.Request(
        "https://www.duckdns.org/update?" + query,
        headers={"User-Agent": "codex-gateway-deployer/1.0"},
    ),
    timeout=20,
) as response:
    result = response.read().decode("utf-8", "replace")
if not result.startswith("OK"):
    raise SystemExit("DuckDNS update failed")
print("DNS_UPDATE_OK")

for _ in range(30):
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)}
        if SERVER_IP in addresses:
            print("DNS_READY")
            raise SystemExit(0)
    except socket.gaierror:
        pass
    time.sleep(2)
raise SystemExit("DNS did not resolve to SERVER_IP")
