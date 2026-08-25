# Public Deployment Report

Domain: gateway.example.com (template; configure your own DNS)
DNS: not included in public source
HTTPS: HTTP 200
Dashboard: HTTP 200
Domain root: HTTP 302 → /dashboard (public read-only data page)
API without key: HTTP 401
Admin without session: HTTP 303
HTTP redirect: HTTP 308
SSE: HTTP 200, data chunks received through Caddy
Temporary data page: not included in public source
Temporary public API: not included in public source
Temporary API without key: HTTP 401
Temporary admin path: HTTP 404
Time: redacted
