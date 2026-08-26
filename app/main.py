from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from .config import settings
from .db import Database
from .backup import backup_database, daily_backup_loop
from .dashboard import render
from .admin_auth import create_session, delete_session, password_hash, valid_session, verify_password
from .quota import QuotaManager, estimate_tokens, hash_api_key, iso, utc_now
from .security import SecurityGuard
from .usage import admin_usage, public_dashboard_usage, user_history, user_usage, update_weekly_usage


RETRY_STATUS = {429, 500, 502, 503}
MODEL_CACHE_TTL_SECONDS = 60.0


class State:
    def __init__(self):
        self.db = Database()
        self.quota: QuotaManager | None = None
        self.global_sem = asyncio.Semaphore(max(1, settings.global_concurrency))
        self.user_sems = defaultdict(lambda: asyncio.Semaphore(max(1, settings.user_concurrency)))
        self.model_cache: tuple[float, Any] | None = None
        self.model_cache_lock = asyncio.Lock()
        self.security: SecurityGuard | None = None


state = State()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await state.db.init()
    # This gateway is intentionally single-instance. Any pending/running task
    # left in SQLite at process startup cannot still own an in-memory
    # semaphore. Reconcile each task-owned reservation before accepting traffic.
    recovered_at = iso(utc_now())
    state.quota = QuotaManager(state.db)
    await state.quota.recover_inflight(recovered_at)
    state.security = SecurityGuard(state.db)
    await asyncio.to_thread(backup_database)
    backup_stop = asyncio.Event()
    backup_task = asyncio.create_task(daily_backup_loop(backup_stop))
    try:
        yield
    finally:
        backup_stop.set()
        backup_task.cancel()
        await asyncio.gather(backup_task, return_exceptions=True)


app = FastAPI(title="Codex Quota Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["Content-Type", "X-Request-ID"],
    max_age=600,
)


@app.middleware("http")
async def normalize_duplicate_v1_prefix(request: Request, call_next):
    """Tolerate clients that append /v1 to an already versioned base URL."""
    path = request.scope.get("path", "")
    if path == "/v1/v1" or path.startswith("/v1/v1/"):
        normalized = path[3:]
        request.scope["path"] = normalized
        request.scope["raw_path"] = normalized.encode("utf-8")
    return await call_next(request)


def error(message: str, status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "gateway_error", "code": code}}, status_code=status)


async def current_user(authorization: str | None) -> Any:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    raw = authorization.split(None, 1)[1].strip()
    if not raw or len(raw) > 256:
        raise HTTPException(401, "invalid bearer token")
    user = await state.db.fetchone("SELECT * FROM users WHERE api_key_hash=? AND enabled=1", (hash_api_key(raw),))
    if not user:
        raise HTTPException(401, "invalid API key")
    if str(user["status"] or "active") == "disabled":
        raise HTTPException(403, "API key disabled")
    return user


def request_ip(request: Request) -> str:
    """Use the socket peer; do not trust spoofable forwarding headers by default."""
    return request.client.host if request.client else "unknown"


def upstream_url(endpoint: str) -> str:
    if not settings.upstream_base_url:
        return ""
    return f"{settings.upstream_base_url}/{endpoint.lstrip('/')}"


def upstream_headers(stream: bool) -> dict[str, str]:
    headers = {"content-type": "application/json", "accept": "text/event-stream" if stream else "application/json"}
    if settings.upstream_api_key:
        headers["authorization"] = f"Bearer {settings.upstream_api_key}"
    return headers


def parse_usage(data: Any) -> tuple[int, int]:
    if not isinstance(data, dict):
        return 0, 0
    response = data.get("response")
    usage = data.get("usage") or (response.get("usage") if isinstance(response, dict) else None)
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("inputTokens", 0))) or 0
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", usage.get("outputTokens", 0))) or 0
    try:
        return int(input_tokens), int(output_tokens)
    except (TypeError, ValueError):
        return 0, 0


async def update_upstream_quota(headers: dict, data: Any) -> None:
    """Accept quota metadata when an authorized upstream exposes it."""
    total = remaining = used = None
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    for key in ("x-quota-total", "x-ratelimit-limit-tokens", "x-ratelimit-limit"):
        if lower.get(key, "").isdigit():
            total = int(lower[key])
            break
    for key in ("x-quota-remaining", "x-ratelimit-remaining-tokens", "x-ratelimit-remaining"):
        if lower.get(key, "").isdigit():
            remaining = int(lower[key])
            break
    if isinstance(data, dict):
        quota = data.get("quota") or data.get("limits")
        if isinstance(quota, dict):
            total = int(quota.get("total", total)) if str(quota.get("total", total)).isdigit() else total
            remaining = int(quota.get("remaining", remaining)) if str(quota.get("remaining", remaining)).isdigit() else remaining
            used = int(quota.get("used", used)) if str(quota.get("used", used)).isdigit() else used
    if total is None and remaining is None and used is None:
        return
    if total is None and remaining is not None and used is not None:
        total = remaining + used
    if total is None:
        return
    if used is None and remaining is not None:
        used = max(0, total - remaining)
    await state.db.execute(
        "INSERT INTO quota_state(id,total_quota,upstream_used,upstream_remaining,source,updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET total_quota=excluded.total_quota, upstream_used=excluded.upstream_used, upstream_remaining=excluded.upstream_remaining, source=excluded.source, updated_at=excluded.updated_at",
        (int(total), used, remaining, "upstream", iso(utc_now())),
    )
async def update_task_phase(task_id: str, phase: str, *, status: str | None = None) -> None:
    now = iso(utc_now())
    if status is None:
        await state.db.execute("UPDATE tasks SET phase=?, last_activity_at=? WHERE id=?", (phase, now, task_id))
    else:
        started = ", started_at=COALESCE(started_at, ?)" if status == "running" else ""
        params = (status, phase, now, now, task_id) if started else (status, phase, now, task_id)
        await state.db.execute(f"UPDATE tasks SET status=?, phase=?, last_activity_at=?{started} WHERE id=?", params)


async def mark_task_reserved(task_id: str, reserved_tokens: int) -> None:
    await state.db.execute(
        "UPDATE tasks SET reserved_tokens=?, quota_state='reserved', last_activity_at=? WHERE id=?",
        (max(0, int(reserved_tokens)), iso(utc_now()), task_id),
    )


async def record_task(task_id: str, status: str, error_text: str | None = None, *, phase: str | None = None) -> None:
    final_phase = phase or {"success": "completed", "cancelled": "cancelled"}.get(status, status)
    await state.db.execute("UPDATE tasks SET status=?, phase=?, error=?, finished_at=?, last_activity_at=? WHERE id=?", (status, final_phase, error_text, iso(utc_now()), iso(utc_now()), task_id))


async def shielded(coro) -> None:
    """Run best-effort async cleanup even while the request is cancelling."""
    task = asyncio.create_task(coro)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # The inner task remains protected and will finish independently.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    except Exception:
        if not task.done():
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)


async def record_usage(
    user_id: int,
    request_id: str,
    model: str | None,
    inp: int,
    out: int,
    duration_ms: int,
    status: str,
    request_time: str | None = None,
    error_type: str | None = None,
) -> None:
    response_time = iso(utc_now())
    request_time = request_time or response_time
    inserted = await state.db.execute(
        "INSERT OR IGNORE INTO usage_logs(user_id,request_id,model,input_tokens,output_tokens,total_tokens,duration_ms,request_time,response_time,status,error_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, request_id, model, inp, out, inp + out, duration_ms, request_time, response_time, status, error_type, response_time),
    )
    if inserted == 1:
        await update_weekly_usage(state.db, user_id, inp + out, request_time)


async def nonstream_upstream(endpoint: str, payload: dict) -> tuple[int, dict, Any, str]:
    url = upstream_url(endpoint)
    if not url:
        return 503, {}, {"error": {"message": "upstream is not configured", "type": "gateway_error", "code": "upstream_not_configured"}}, "upstream_not_configured"
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        for attempt in range(max(1, settings.max_retries)):
            try:
                response = await client.post(url, headers=upstream_headers(False), json=payload)
            except httpx.HTTPError:
                if attempt + 1 >= settings.max_retries:
                    return 502, {}, {"error": {"message": "upstream unavailable", "type": "gateway_error", "code": "upstream_unavailable"}}, "upstream_unavailable"
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUS and attempt + 1 < settings.max_retries:
                await asyncio.sleep(2**attempt)
                continue
            try:
                data = response.json()
            except ValueError:
                data = {"error": {"message": "invalid upstream response", "type": "gateway_error", "code": "upstream_invalid_response"}}
            if response.status_code in {401, 403}:
                return response.status_code, {}, {"error": {"message": "upstream authentication failed", "type": "gateway_error", "code": "upstream_auth_failed"}}, "upstream_auth_failed"
            if response.status_code >= 400:
                return response.status_code, {}, {"error": {"message": "upstream request failed", "type": "gateway_error", "code": "upstream_error"}}, "upstream_error"
            return response.status_code, dict(response.headers), data, "success"
    return 502, {}, {"error": {"message": "upstream unavailable", "type": "gateway_error", "code": "upstream_unavailable"}}, "upstream_unavailable"


async def open_stream(endpoint: str, payload: dict):
    url = upstream_url(endpoint)
    if not url:
        return None, None, error("upstream is not configured", 503, "upstream_not_configured")
    client = httpx.AsyncClient(timeout=settings.request_timeout)
    for attempt in range(max(1, settings.max_retries)):
        try:
            request = client.build_request("POST", url, headers=upstream_headers(True), json=payload)
            response = await client.send(request, stream=True)
        except httpx.HTTPError:
            if attempt + 1 >= settings.max_retries:
                await client.aclose()
                return None, None, error("upstream unavailable", 502, "upstream_unavailable")
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code in RETRY_STATUS and attempt + 1 < settings.max_retries:
            await response.aclose()
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code >= 400:
            await response.aread()
            await response.aclose()
            await client.aclose()
            if response.status_code in {401, 403}:
                return None, None, error("upstream authentication failed", response.status_code, "upstream_auth_failed")
            return None, None, error("upstream request failed", response.status_code, "upstream_error")
        return client, response, None
    await client.aclose()
    return None, None, error("upstream unavailable", 502, "upstream_unavailable")


async def fetch_upstream_models() -> tuple[int, Any, str]:
    """Fetch the upstream model catalog without rewriting model IDs.

    The catalog is shared by all authenticated Gateway users and cached briefly
    to avoid a fan-out of identical discovery requests. A successful upstream
    response replaces the cache; failures never poison an existing cache.
    """
    if not settings.upstream_base_url:
        return 503, {"error": {"message": "upstream is not configured", "type": "gateway_error", "code": "upstream_not_configured"}}, "upstream_not_configured"

    now = time.monotonic()
    if state.model_cache and now - state.model_cache[0] < MODEL_CACHE_TTL_SECONDS:
        return 200, state.model_cache[1], "success"

    async with state.model_cache_lock:
        now = time.monotonic()
        if state.model_cache and now - state.model_cache[0] < MODEL_CACHE_TTL_SECONDS:
            return 200, state.model_cache[1], "success"

        headers = {"accept": "application/json"}
        if settings.upstream_api_key:
            headers["authorization"] = f"Bearer {settings.upstream_api_key}"
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            for attempt in range(max(1, settings.max_retries)):
                try:
                    response = await client.get(upstream_url("models"), headers=headers)
                except httpx.HTTPError:
                    if attempt + 1 >= settings.max_retries:
                        return 502, {"error": {"message": "upstream unavailable", "type": "gateway_error", "code": "upstream_unavailable"}}, "upstream_unavailable"
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code in RETRY_STATUS and attempt + 1 < settings.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                try:
                    data = response.json()
                except ValueError:
                    return 502, {"error": {"message": "invalid upstream model response", "type": "gateway_error", "code": "upstream_invalid_response"}}, "upstream_invalid_response"
                if response.status_code in {401, 403}:
                    return response.status_code, {"error": {"message": "upstream authentication failed", "type": "gateway_error", "code": "upstream_auth_failed"}}, "upstream_auth_failed"
                if response.status_code >= 400:
                    return response.status_code, {"error": {"message": "upstream model discovery failed", "type": "gateway_error", "code": "upstream_error"}}, "upstream_error"
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    return 502, {"error": {"message": "invalid upstream model catalog", "type": "gateway_error", "code": "upstream_invalid_response"}}, "upstream_invalid_response"
                state.model_cache = (time.monotonic(), data)
                return response.status_code, data, "success"
    return 502, {"error": {"message": "upstream unavailable", "type": "gateway_error", "code": "upstream_unavailable"}}, "upstream_unavailable"


async def handle_proxy(request: Request, endpoint: str, authorization: str | None):
    try:
        user = await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    ip = request_ip(request)
    request_id = str(uuid.uuid4())
    if state.security is not None:
        allowed, rate_status, rate_code, retry_after = await state.security.check_rate(user["id"], user["api_key_hash"], ip)
        if not allowed:
            await state.security.audit(user["id"], "rate_limit", "warning", ip, "suspend" if rate_code == "temporarily_suspended" else "rate_limit", {"request_id": request_id, "reason": rate_code})
            response = error("temporarily suspended" if rate_code == "temporarily_suspended" else "rate limit exceeded", rate_status, rate_code)
            if retry_after:
                response.headers["Retry-After"] = str(retry_after)
            return response
    try:
        payload = await request.json()
    except Exception:
        return error("request body must be JSON", 400, "invalid_json")
    if state.security is not None:
        secret_match = state.security.scan_payload(payload)
        if secret_match is not None:
            await state.security.audit(user["id"], "secret_detected", "block", ip, "block", {"request_id": request_id, "rule": secret_match.rule, "match_type": secret_match.match_type})
            return error("request blocked: potential secret detected", 400, "secret_detected")
    task_id = request_id
    model = payload.get("model") if isinstance(payload, dict) else None
    now = iso(utc_now())
    await state.db.execute("INSERT INTO tasks(id,user_id,status,endpoint,model,request_id,phase,last_activity_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (task_id, user["id"], "pending", endpoint, model, request_id, "pending", now, now))
    estimate = estimate_tokens(payload)
    ok, quota_info = await state.quota.reserve(user["id"], estimate)
    if not ok:
        await state.db.execute(
            "UPDATE tasks SET status='cancelled',phase='cancelled',error=?,finished_at=?,last_activity_at=?,quota_state='released',quota_finalized_at=? WHERE id=?",
            ("weekly quota exceeded", iso(utc_now()), iso(utc_now()), iso(utc_now()), task_id),
        )
        return error("weekly quota exceeded", 429, "weekly_quota_exceeded")
    reserved_estimate = int(quota_info.get("reserved", estimate))
    await mark_task_reserved(task_id, reserved_estimate)
    user_sem = state.user_sems[user["id"]]
    started = time.monotonic()
    acquired_user = False
    acquired_global = False
    handed_off = False
    upstream_started = False
    cleanup_done = False
    upstream_client = None
    upstream_response = None

    def release_slots() -> None:
        nonlocal acquired_user, acquired_global
        # This is deliberately synchronous and first in cleanup. It must not
        # be skipped by a cancelled/failed database await.
        if acquired_global:
            state.global_sem.release()
            acquired_global = False
        if acquired_user:
            user_sem.release()
            acquired_user = False

    async def finalize(status: str, error_text: str | None, inp: int, out: int, *, charge: bool, error_type: str | None = None) -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        release_slots()
        if upstream_response is not None:
            await shielded(upstream_response.aclose())
        if upstream_client is not None:
            await shielded(upstream_client.aclose())
        await shielded(
            state.quota.finalize_task(
                task_id,
                status=status,
                error_text=error_text,
                input_tokens=inp,
                output_tokens=out,
                duration_ms=int((time.monotonic() - started) * 1000),
                request_time=now,
                model=model,
                request_id=request_id,
                error_type=error_type,
                charge=charge,
            )
        )
        if state.security is not None:
            await shielded(state.security.observe_usage(user["id"], ip))

    try:
        await update_task_phase(task_id, "waiting_user_slot")
        try:
            await asyncio.wait_for(user_sem.acquire(), timeout=settings.queue_timeout_seconds)
        except asyncio.TimeoutError:
            await finalize("timed_out", "queue timeout while waiting for user slot", 0, 0, charge=False, error_type="queue_timeout")
            return error("request queue timeout", 429, "queue_timeout")
        acquired_user = True

        await update_task_phase(task_id, "waiting_global_slot")
        try:
            await asyncio.wait_for(state.global_sem.acquire(), timeout=settings.queue_timeout_seconds)
        except asyncio.TimeoutError:
            await finalize("timed_out", "queue timeout while waiting for global slot", 0, 0, charge=False, error_type="queue_timeout")
            return error("request queue timeout", 429, "queue_timeout")
        acquired_global = True
        await update_task_phase(task_id, "opening_upstream", status="running")

        if payload.get("stream"):
            # open_stream has sent the request once it begins; settle the
            # reservation conservatively if the upstream never returns headers.
            upstream_started = True
            upstream_client, upstream_response, early = await open_stream(endpoint, payload)
            if early is not None:
                await finalize("failed", early.body.decode("utf-8", "ignore")[:500], 0, 0, charge=True, error_type="upstream_error")
                return early
            await update_task_phase(task_id, "waiting_first_event")

            async def generate():
                inp = out = 0
                status = "success"
                error_text = None
                error_type = None
                seen_event = False
                iterator = upstream_response.aiter_bytes().__aiter__()
                stream_deadline = time.monotonic() + settings.stream_max_seconds
                sse_buffer = b""
                try:
                    while True:
                        if await request.is_disconnected():
                            status = "cancelled"
                            error_text = "client disconnected"
                            error_type = "client_disconnected"
                            break
                        if time.monotonic() >= stream_deadline:
                            status = "timed_out"
                            error_text = "upstream stream maximum duration exceeded"
                            error_type = "upstream_stream_timeout"
                            timeout_event = {"error": {"message": error_text, "type": "gateway_error", "code": error_type}}
                            if endpoint != "chat/completions":
                                timeout_event = {"type": "error", **timeout_event}
                            yield f"data: {json.dumps(timeout_event)}\n\ndata: [DONE]\n\n".encode()
                            break
                        try:
                            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=settings.stream_idle_timeout)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            status = "timed_out"
                            error_text = "upstream stream idle timeout"
                            error_type = "upstream_stream_timeout"
                            timeout_event = {"error": {"message": error_text, "type": "gateway_error", "code": error_type}}
                            if endpoint != "chat/completions":
                                timeout_event = {"type": "error", **timeout_event}
                            yield f"data: {json.dumps(timeout_event)}\n\ndata: [DONE]\n\n".encode()
                            break

                        sse_buffer += chunk
                        complete_event = False
                        lines = sse_buffer.split(b"\n")
                        sse_buffer = lines.pop()
                        for line in lines:
                            line = line.rstrip(b"\r")
                            if not line.startswith(b"data:"):
                                continue
                            data_bytes = line[5:].strip()
                            if not data_bytes:
                                continue
                            seen_event = True
                            if data_bytes == b"[DONE]":
                                complete_event = True
                                continue
                            try:
                                data = json.loads(data_bytes)
                                a, b = parse_usage(data)
                                inp, out = max(inp, a), max(out, b)
                                event_type = str(data.get("type", "")) if isinstance(data, dict) else ""
                                if event_type in {"response.completed", "response.failed", "response.incomplete"}:
                                    complete_event = True
                            except (ValueError, json.JSONDecodeError):
                                pass
                        if seen_event:
                            await update_task_phase(task_id, "streaming")
                        yield chunk
                        if complete_event:
                            break
                except asyncio.CancelledError:
                    status = "cancelled"
                    error_text = "client disconnected"
                    error_type = "client_disconnected"
                    raise
                except Exception as exc:
                    status = "failed"
                    error_text = str(exc)[:500]
                    error_type = "stream_error"
                    raise
                finally:
                    await finalize(status, error_text, inp, out, charge=True, error_type=error_type)

            await update_upstream_quota(dict(upstream_response.headers), {})
            handed_off = True
            return StreamingResponse(generate(), media_type="text/event-stream", headers={"x-request-id": request_id, "cache-control": "no-cache"})

        upstream_started = True
        status_code, headers, data, status = await nonstream_upstream(endpoint, payload)
        await update_upstream_quota(headers, data)
        inp, out = parse_usage(data)
        error_type = None
        if status != "success" and isinstance(data, dict):
            error_type = str((data.get("error") or {}).get("code") or "upstream_error")
        await finalize("success" if status == "success" else "failed", None if status == "success" else status, inp, out, charge=True, error_type=error_type)
        return JSONResponse(data, status_code=status_code, headers={"x-request-id": request_id})
    except asyncio.CancelledError:
        await finalize("cancelled", "client disconnected", 0, 0, charge=upstream_started, error_type="client_disconnected")
        raise
    except Exception as exc:
        await finalize("failed", str(exc)[:500], 0, 0, charge=upstream_started, error_type="gateway_error")
        return error("gateway request failed", 502, "gateway_error")
    finally:
        if not handed_off:
            release_slots()


@app.get("/healthz")
async def healthz():
    try:
        active = max(0, settings.global_concurrency - state.global_sem._value)
        rows = await state.db.fetchall(
            "SELECT user_id,status,phase,created_at,last_activity_at FROM tasks WHERE status IN ('pending','running')"
        )
        users: dict[str, dict[str, int]] = {}
        for row in rows:
            key = str(row["user_id"])
            bucket = users.setdefault(key, {"active": 0, "waiting": 0})
            if row["status"] == "running":
                bucket["active"] += 1
            else:
                bucket["waiting"] += 1
        oldest = min((str(row["last_activity_at"] or row["created_at"]) for row in rows), default=None)
        concurrency = {
            "global": {"active": active, "available": max(0, state.global_sem._value), "limit": settings.global_concurrency},
            "users": users,
            "pending": sum(1 for row in rows if row["status"] == "pending"),
            "running": sum(1 for row in rows if row["status"] == "running"),
            "oldest_activity_at": oldest,
        }
    except Exception:
        concurrency = {"global": {"active": None, "available": None, "limit": settings.global_concurrency}, "users": {}, "pending": None, "running": None, "oldest_activity_at": None}
    return {"status": "ok", "upstream_configured": bool(settings.upstream_base_url), "global_concurrency": settings.global_concurrency, "concurrency": concurrency}


@app.get("/status")
async def status():
    try:
        row = await state.db.fetchone("SELECT MAX(created_at) AS last_request FROM usage_logs")
        db_status = "ok"
        last_request = row["last_request"] if row else None
    except Exception:
        db_status, last_request = "error", None
    proxy_status = "not_configured"
    if settings.upstream_base_url:
        try:
            headers = {"accept": "application/json"}
            if settings.upstream_api_key:
                headers["authorization"] = f"Bearer {settings.upstream_api_key}"
            async with httpx.AsyncClient(timeout=min(settings.request_timeout, 5.0)) as client:
                probe = await client.get(upstream_url("models"), headers=headers)
            proxy_status = "ok" if probe.status_code < 400 else ("auth_failed" if probe.status_code in {401, 403} else "unavailable")
        except httpx.HTTPError:
            proxy_status = "unavailable"
    return {
        "gateway": "ok",
        "codex_proxy": proxy_status,
        "database": db_status,
        "last_request": last_request,
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    if settings.public_dashboard_mode or settings.public_test_mode:
        await record_dashboard_access(request, "/dashboard")
        if not public_dashboard_access_allowed(request):
            return render("public_dashboard_login.html")
        return render("public_dashboard.html")
    return render("user_dashboard.html")


async def record_dashboard_access(request: Request, path: str) -> None:
    try:
        await state.db.execute(
            "INSERT INTO dashboard_access_logs(accessed_at,ip,path) VALUES(?,?,?)",
            (iso(utc_now()), request_ip(request), path),
        )
    except Exception:
        pass


@app.get("/api/public/dashboard")
async def public_dashboard_api(request: Request):
    if not (settings.public_dashboard_mode or settings.public_test_mode):
        return error("public dashboard is disabled", 404, "public_dashboard_disabled")
    await record_dashboard_access(request, "/api/public/dashboard")
    if not public_dashboard_access_allowed(request):
        return error("public dashboard password required", 401, "public_dashboard_authentication_required")
    snapshot = await status()
    usage = await public_dashboard_usage(state.db)
    return {
        "gateway": "ONLINE" if snapshot["gateway"] == "ok" else "OFFLINE",
        "codex_proxy": "ONLINE" if snapshot["codex_proxy"] == "ok" else "OFFLINE",
        "last_request": snapshot["last_request"],
        **usage,
    }


def public_dashboard_access_allowed(request: Request) -> bool:
    password = settings.public_dashboard_password
    if not password:
        return True
    supplied = request.cookies.get("public_dashboard_access", "")
    key = (settings.secret_key or "temporary-public-dashboard").encode("utf-8")
    expected = hmac.new(key, ("dashboard:" + password).encode("utf-8"), hashlib.sha256).hexdigest()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


@app.post("/dashboard/access")
async def public_dashboard_access(request: Request):
    if not (settings.public_dashboard_mode or settings.public_test_mode):
        return error("public dashboard is disabled", 404, "public_dashboard_disabled")
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        supplied = str(payload.get("password", ""))
    else:
        supplied = parse_qs(body.decode("utf-8", "ignore")).get("password", [""])[0]
    if settings.public_dashboard_password and not hmac.compare_digest(supplied, settings.public_dashboard_password):
        return error("invalid dashboard password", 401, "public_dashboard_authentication_failed")
    key = (settings.secret_key or "temporary-public-dashboard").encode("utf-8")
    value = hmac.new(key, ("dashboard:" + settings.public_dashboard_password).encode("utf-8"), hashlib.sha256).hexdigest()
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        "public_dashboard_access", value, max_age=86400, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return response


@app.get("/api/me/usage")
async def me_usage(authorization: str | None = Header(default=None)):
    try:
        user = await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    return await user_usage(state.db, user)


@app.get("/api/me/history")
async def me_history(limit: int = 100, authorization: str | None = Header(default=None)):
    try:
        user = await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    return {"user": user["username"], "history": await user_history(state.db, user["id"], limit)}


@app.get("/v1/usage")
async def compatible_usage(authorization: str | None = Header(default=None)):
    try:
        user = await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    return await user_usage(state.db, user)


@app.get("/v1/user/balance")
async def compatible_balance(authorization: str | None = Header(default=None)):
    try:
        user = await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    usage = await user_usage(state.db, user)
    limit = int(usage["quota"]["estimated_token_limit"])
    used = int(usage["week_usage"]["tokens"])
    return {
        "object": "balance",
        "data": {
            "available": max(0, limit - used),
            "total": limit,
            "used": used,
            "unit": "estimated_tokens",
            "weekly_limit_percent": usage["quota"]["weekly_limit_percent"],
            "reset_time": usage["quota"]["reset_time"],
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None)):
    return await handle_proxy(request, "chat/completions", authorization)


@app.post("/v1/responses")
async def responses(request: Request, authorization: str | None = Header(default=None)):
    return await handle_proxy(request, "responses", authorization)


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    try:
        await current_user(authorization)
    except HTTPException as exc:
        return error(str(exc.detail), exc.status_code, "authentication_failed")
    status_code, data, status = await fetch_upstream_models()
    if status == "success":
        return JSONResponse(data, status_code=status_code)
    return JSONResponse(data, status_code=status_code)


def admin_allowed(value: str | None) -> bool:
    configured = settings.admin_key
    if not configured:
        try:
            configured = open(settings.secrets_dir + "/admin_key.txt", encoding="utf-8").read().strip()
        except OSError:
            return False
    return bool(value) and value == configured


async def admin_session_allowed(request: Request) -> bool:
    return await valid_session(state.db, request.cookies.get("admin_session"))


async def require_admin(request: Request, x_admin_key: str | None) -> None:
    # Keep the existing header for automation, while browser access must use
    # the expiring, HttpOnly session cookie.
    if admin_allowed(x_admin_key) or await admin_session_allowed(request):
        return
    raise HTTPException(401, "admin authentication required")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return render("admin_login.html")


@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        supplied = str(payload.get("password", ""))
    else:
        supplied = parse_qs(body.decode("utf-8", "ignore")).get("password", [""])[0]
    encoded = password_hash()
    if not encoded:
        return error("admin password is not configured", 503, "admin_password_not_configured")
    if not verify_password(supplied, encoded):
        return error("invalid admin credentials", 401, "admin_authentication_failed")
    await state.db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (iso(utc_now()),))
    token, expires_at = await create_session(state.db)
    response = RedirectResponse("/admin/dashboard", status_code=303)
    response.set_cookie(
        "admin_session", token, max_age=settings.admin_session_ttl_seconds,
        expires=expires_at, httponly=True, secure=settings.session_cookie_secure,
        samesite="lax", path="/admin",
    )
    return response


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("admin_session")
    await delete_session(state.db, token)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("admin_session", path="/admin")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not await admin_session_allowed(request):
        return RedirectResponse("/admin/login", status_code=303)
    return render("admin_dashboard.html")


@app.get("/admin/summary")
async def admin_summary(request: Request, x_admin_key: str | None = Header(default=None)):
    await require_admin(request, x_admin_key)
    users = await state.db.fetchall(
        """SELECT u.id,u.username,u.weekly_limit,u.weekly_used,u.weekly_reserved,u.reset_time,u.enabled,u.status,u.created_at,
                  (SELECT COUNT(*) FROM security_events s WHERE s.user_id=u.id) AS risk_count
           FROM users u ORDER BY u.id"""
    )
    tasks = await state.db.fetchall("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status")
    usage = await state.db.fetchone("SELECT COALESCE(SUM(total_tokens),0) AS total_tokens, COUNT(*) AS requests FROM usage_logs WHERE created_at >= datetime('now','-7 days')")
    return {"users": [dict(row) for row in users], "tasks": [dict(row) for row in tasks], "week": dict(usage) if usage else {"total_tokens": 0, "requests": 0}}


@app.get("/admin/security-events")
async def admin_security_events(request: Request, limit: int = 100, x_admin_key: str | None = Header(default=None)):
    await require_admin(request, x_admin_key)
    bounded_limit = min(max(1, limit), 500)
    rows = await state.db.fetchall(
        """SELECT s.id,s.user_id,u.username,s.event_type,s.risk_level,s.ip,s.timestamp,s.action,s.details
           FROM security_events s LEFT JOIN users u ON u.id=s.user_id
           ORDER BY s.id DESC LIMIT ?""",
        (bounded_limit,),
    )
    return {"events": [dict(row) for row in rows]}


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not await admin_session_allowed(request):
        return RedirectResponse("/admin/login", status_code=303)
    return render("admin_dashboard.html")


@app.get("/admin/usage")
async def admin_usage_endpoint(request: Request, x_admin_key: str | None = Header(default=None)):
    await require_admin(request, x_admin_key)
    return await admin_usage(state.db)
