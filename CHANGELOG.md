# Changelog

## Unreleased

- Replaced the hard-coded Gateway `/v1/models` response with authenticated,
  upstream model discovery.
- Preserved upstream model IDs and metadata without aliases or renaming.
- Added a short in-memory cache to avoid repeated discovery calls while still
  refreshing the catalog after a service restart or cache expiry.
- Kept `/v1/responses`, `/v1/chat/completions`, streaming, and quota accounting
  on the existing pass-through path.
- Added SQLite usage history, per-user weekly rollups and quota metadata.
- Added `/api/me/usage`, `/api/me/history`, `/admin/usage`, and lightweight
  user/admin dashboards.
- Added startup/daily SQLite backups with configurable seven-copy retention.
- Added PBKDF2 admin password hashes, expiring HttpOnly/Secure/SameSite session
  cookies, `/admin/login`, logout, and `/status`.
- Added gzip SQLite backups, optional S3-compatible upload, restore/list CLI,
  database permission hardening, Docker healthchecks, and log rotation.
- Added an optional Caddy HTTPS profile with automatic certificate renewal.
- Added opt-in anonymous read-only Dashboard mode with `/api/public/dashboard`,
  anonymized user labels, and dashboard access audit records. `/v1/*` and
  `/admin/*` authentication boundaries remain unchanged.
- Added an opt-in HTTP `public-test` profile on configurable `PUBLIC_PORT`
  with path allowlisting, optional dashboard password, and rollback by stopping
  the profile and disabling `PUBLIC_TEST_MODE`.
- Temporarily allowlisted `/v1/*` in the HTTP test profile; Gateway Bearer
  authentication remains mandatory and Admin paths remain blocked.
- Added DuckDNS update, Caddy generation, one-command HTTPS deployment, and
  public deployment reporting scripts.
