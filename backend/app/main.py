"""
Video Downloader - FastAPI Backend
====================================
Main application entry point with CORS configuration,
Security hardening, and Supabase database initialization.
"""

import os
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# ── Sentry (optional — only active when SENTRY_DSN is set) ───────────
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FastApiIntegration(), CeleryIntegration()],
        traces_sample_rate=0.1,   # 10% of requests captured for performance
        send_default_pii=False,   # no PII in payloads
    )
    print(f"[Sentry] Initialized (DSN configured)")

import re
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.client_ip import get_client_ip

# Initialize Rate Limiter (stricter: 60 req/min per IP default).
# key_func=get_client_ip (not slowapi's get_remote_address) — the latter
# trusts request.client.host, which uvicorn's --forwarded-allow-ips '*'
# sets from the FIRST (client-spoofable) X-Forwarded-For entry.
# storage_uri: without this, slowapi falls back to in-process MemoryStorage,
# which is NOT shared across the 4 uvicorn workers — each worker counts
# independently, so the real limit is up to 4x the configured number and
# unpredictable which worker a given request lands on.
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=["60/minute"],
    storage_uri=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    # Survive a Redis outage instead of taking the whole API down with it.
    #
    # Previously neither option was set, so a redis.ConnectionError propagated
    # out of the limiter and EVERY request 500'd — default_limits applies to
    # all routes, so a Redis blip killed even GET /api/v1/ping, the endpoint
    # the extension and frontend poll to decide whether the server is alive.
    #
    # in_memory_fallback_enabled: when Redis is unreachable, keep enforcing the
    #   same limits from per-process memory (degraded accuracy across workers,
    #   but still real protection) rather than dropping limiting entirely.
    # swallow_errors: last-resort backstop so no limiter failure can ever
    #   become a user-visible 500. Matches DailyIPQuotaMiddleware, which
    #   already swallows its own store errors.
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)

# ── Per-IP Daily Quota Middleware ────────────────────────────────────
# Counts heavy endpoints (/fetch-link, /bulk-download) per IP per UTC day.
# Configurable via DAILY_QUOTA_PER_IP env var (0 = disabled).
_DAILY_QUOTA = int(os.getenv("DAILY_QUOTA_PER_IP", "0"))
_QUOTA_ENDPOINTS = {"/api/v1/fetch-link", "/api/v1/bulk-download"}

class DailyIPQuotaMiddleware(BaseHTTPMiddleware):
    """Block IPs that exceed DAILY_QUOTA_PER_IP requests/day (Redis counter)."""

    async def dispatch(self, request: Request, call_next):
        if _DAILY_QUOTA <= 0 or request.method != "POST":
            return await call_next(request)
        if request.url.path not in _QUOTA_ENDPOINTS:
            return await call_next(request)

        ip = get_client_ip(request)
        today = __import__("datetime").date.today().isoformat()
        redis_key = f"quota:{ip}:{today}"

        try:
            import redis as _redis_lib
            _r = _redis_lib.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
            count = _r.incr(redis_key)
            if count == 1:
                _r.expire(redis_key, 86400)   # expire at end of UTC day
            if count > _DAILY_QUOTA:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Daily limit of {_DAILY_QUOTA} requests reached. Try again tomorrow."},
                )
        except Exception:
            pass   # Redis unavailable → allow request (fail open)

        return await call_next(request)

from app.core.database import init_db
from app.core.structured_log import configure_logging, StructuredLoggingMiddleware
from app.core.disk_guardrail import setup_disk_guardrail
from app.api.routes import router as api_router

# Phase 17: structured JSON logging (runs before anything else)
configure_logging(level=os.getenv("LOG_LEVEL", "INFO"))
from app.api.admin import router as admin_router
from app.api.payments import router as payments_router
from app.api.user import router as user_router
from app.api.flow_cleanup import router as flow_cleanup_router
from app.api import push as push_api
from app.api import merge as merge_api
from app.api import watermark as watermark_api
from app.api import playlists as playlists_api
from app.api import webhook as webhook_api


# ── Correlation ID Middleware ────────────────────────────────────────
class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique X-Request-ID to every request/response pair.
    Clients may supply their own; if absent, one is generated.
    The ID is echoed back in the response header for easy log correlation.
    """
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = req_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


# ── Security Headers Middleware ──────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-related headers to every response."""
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Hide server technology
        if "server" in response.headers:
            del response.headers["server"]
        if "x-powered-by" in response.headers:
            del response.headers["x-powered-by"]
        return response


WATCHDOG_BEAT_KEY = "vidgrab:watchdog_last_run_at"


def _publish_watchdog_beat() -> None:
    """Record that the worker-liveness watchdog just completed a check."""
    from datetime import datetime, timezone
    try:
        from app.core.redis_client import get_redis
        # TTL is generous relative to the check interval so a single slow tick
        # does not read as "watchdog dead", but short enough that a stopped
        # watchdog disappears rather than lingering as a stale success.
        get_redis().set(
            WATCHDOG_BEAT_KEY,
            datetime.now(timezone.utc).isoformat(),
            ex=int(os.getenv("WORKER_WATCHDOG_INTERVAL_SEC", "120")) * 3,
        )
    except Exception:
        pass


def _watchdog_status() -> dict:
    """
    Report whether the worker-liveness watchdog is running, and when it last
    ran. `enabled` reflects config; `alive` reflects reality — they disagree
    when the loop has died or has not passed its startup grace yet.
    """
    enabled = os.getenv("WORKER_WATCHDOG_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    last = None
    try:
        from app.core.redis_client import get_redis
        raw = get_redis().get(WATCHDOG_BEAT_KEY)
        if raw:
            last = raw if isinstance(raw, str) else raw.decode()
    except Exception:
        pass
    return {"enabled": enabled, "alive": last is not None, "last_run_at": last}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - runs on startup and shutdown."""
    # --- Startup ---
    print("Starting Video Downloader API...")
    init_db()

    # Validate that required Supabase columns exist (non-blocking)
    try:
        import asyncio as _asyncio
        from app.core.database import validate_schema
        await _asyncio.to_thread(validate_schema)
    except Exception as _schema_err:
        print(f"[Startup] Schema validation skipped: {_schema_err}")

    # Recovery scan: reclassify jobs stuck in 'processing' from the last deploy/crash
    try:
        import asyncio as _asyncio
        from app.core.recovery import startup_recovery_scan
        await _asyncio.to_thread(startup_recovery_scan)
    except Exception as _rec_err:
        print(f"[Startup] Recovery scan skipped: {_rec_err}")

    # Send Telegram startup notification (non-blocking, failure is OK)
    try:
        from app.core.notifications import notify_system_startup
        await notify_system_startup()
    except Exception as e:
        print(f"[Startup] Telegram notification failed (non-critical): {e}")

    # Worker liveness watchdog.
    #
    # Both previous "no Celery workers" checks ran inside Celery tasks, so they
    # could only execute while a worker was alive to run them — neither could
    # ever fire. This loop lives in the API process, which keeps serving when
    # every worker is gone, and only reads the beacon workers publish.
    # send_admin_alert throttles on alert_key, so a sustained outage is one
    # message per cooldown, not one per tick.
    _watchdog_task = None
    if os.getenv("WORKER_WATCHDOG_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on"):
        import asyncio as _asyncio

        _wd_interval = int(os.getenv("WORKER_WATCHDOG_INTERVAL_SEC", "120"))
        # Grace period before the first check. It must exceed the time a cold
        # start needs to publish its first beacon: celery beat only dispatches
        # worker_heartbeat_check every 120s and beat itself has to come up
        # first, so a 120s grace would page on every single deploy. 5 minutes
        # matches the staleness threshold.
        _wd_grace = max(int(os.getenv("WORKER_WATCHDOG_GRACE_SEC", "300")), _wd_interval)

        async def _worker_watchdog():
            await _asyncio.sleep(_wd_grace)
            first = True
            while True:
                try:
                    from app.core.alerts import check_worker_liveness
                    await _asyncio.to_thread(check_worker_liveness)
                    # Publish our own liveness. A one-shot log line was the
                    # first attempt and was the wrong shape: this container's
                    # log window holds about three minutes of Celery chatter,
                    # so the line scrolls away long before anyone asks whether
                    # the watchdog is running — the same unobservability that
                    # let the checks this replaced stay dead for months.
                    # /health reads this key, so the answer is available on
                    # demand instead of only in a log race.
                    await _asyncio.to_thread(_publish_watchdog_beat)
                    if first:
                        print("[Watchdog] worker liveness watchdog is live "
                              "(first check completed)", flush=True)
                        first = False
                except _asyncio.CancelledError:
                    raise
                except Exception as _wd_err:
                    print(f"[Watchdog] worker liveness check failed: {_wd_err}")
                await _asyncio.sleep(_wd_interval)

        _watchdog_task = _asyncio.create_task(_worker_watchdog())
        print(f"[Watchdog] armed — first check in {_wd_grace}s, "
              f"then every {_wd_interval}s", flush=True)

    yield
    # --- Shutdown ---
    if _watchdog_task:
        _watchdog_task.cancel()
    print("Shutting down Video Downloader API...")


# ── Disable API docs in production ───────────────────────────────────
is_dev = os.getenv("ENV", "production").lower() in ("dev", "development", "local")

APP_VERSION = os.getenv("APP_VERSION", "1.6.0")

app = FastAPI(
    title="Video Downloader API",
    description="API for downloading and managing video files",
    version=APP_VERSION,
    lifespan=lifespan,
    # Hide Swagger/ReDoc in production to prevent API analysis
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)

# Register Security Headers
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Phase 17: structured JSON logging per request (correlation ID → JSON log)
app.add_middleware(StructuredLoggingMiddleware)

# Register Per-IP daily quota (no-op when DAILY_QUOTA_PER_IP=0)
app.add_middleware(DailyIPQuotaMiddleware)

# Phase 17: disk pressure guardrail (blocks heavy endpoints when disk is critical)
setup_disk_guardrail(app)

# Register SlowAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS Configuration ──────────────────────────────────────────────
# Only allow known frontend origins
origins = [
    "http://localhost:5173",     # Vite dev
    "http://localhost:3000",     # Local frontend
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "https://dowload-video-trieunt.dev.matbao.ai",  # Production preview
    "https://dowload-video.mk.dev.matbao.ai",
]

# Also allow any configured production domain
prod_domain = os.getenv("FRONTEND_URL", "")
if prod_domain and prod_domain not in origins:
    origins.append(prod_domain)

# Chrome extension origins.
#
# The previous config was `allow_origin_regex=r"^chrome-extension://.*$"` with
# allow_credentials=True — i.e. ANY extension the user had installed could make
# credentialed cross-origin calls here and read the responses, including the
# cookie-authenticated endpoints (smart_analysis reads `user_id`, mobile reads
# `vg_session_id`). That is a browser-wide read primitive on this API.
#
# Our own extension does not need this grant: it declares `*://*.matbao.ai/*`
# in host_permissions, and MV3 exempts host_permissions fetches from CORS
# entirely. So the default is now "no extension origin allowed".
#
# If a build is served from a host NOT covered by host_permissions and really
# needs the grant, list the specific extension IDs:
#   ALLOWED_EXTENSION_IDS=abcdefghijklmnopabcdefghijklmnop,....
_ext_ids = [i.strip() for i in os.getenv("ALLOWED_EXTENSION_IDS", "").split(",") if i.strip()]
_ext_origin_regex = (
    r"^chrome-extension://(" + "|".join(re.escape(i) for i in _ext_ids) + r")$"
    if _ext_ids else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=_ext_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-API-Key", "X-VG-Source", "X-Session-ID"],
)


# ── Include Router ───────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(payments_router, prefix="/api/v1/payments", tags=["Payments"])
app.include_router(user_router, prefix="/api/v1/user", tags=["User"])
app.include_router(flow_cleanup_router, prefix="/api/v1/flow-cleanup", tags=["Flow/Veo Cleanup"])
app.include_router(push_api.router, prefix="/api/v1/push", tags=["push"])
app.include_router(merge_api.router,      prefix="/api/v1",              tags=["Merge"])
app.include_router(watermark_api.router,  prefix="/api/v1",              tags=["Watermark"])
app.include_router(playlists_api.router,  prefix="/api/v1/playlists",    tags=["Playlists"])
app.include_router(webhook_api.router,    prefix="/api/v1/user/webhook", tags=["Webhook"])

from app.api import notes as notes_api

app.include_router(notes_api.router, prefix="/api/v1", tags=["Notes"])

from app.api import batch_rename as batch_rename_api
from app.api import search_videos as search_videos_api
from app.api import archive as archive_api
from app.api import schedule as schedule_api
from app.api import storage as storage_api
from app.api import workspace as workspace_api
from app.api import invites as invites_api
from app.api import audit_api
from app.api import approvals as approvals_api
from app.api import exports as exports_api

app.include_router(batch_rename_api.router, prefix="/api/v1", tags=["Batch Rename"])
app.include_router(search_videos_api.router, prefix="/api/v1", tags=["Search"])
app.include_router(archive_api.router,       prefix="/api/v1", tags=["Archive"])
app.include_router(schedule_api.router,      prefix="/api/v1", tags=["Schedule"])
app.include_router(storage_api.router,       prefix="/api/v1", tags=["Storage"])
# ── Phase 11: Workspace, RBAC, Audit, Approvals, Exports ─────────────────────
app.include_router(workspace_api.router,  prefix="/api/v1", tags=["Workspace"])
app.include_router(invites_api.router,    prefix="/api/v1", tags=["Invites"])
app.include_router(audit_api.router,      prefix="/api/v1", tags=["Audit"])
app.include_router(approvals_api.router,  prefix="/api/v1", tags=["Approvals"])
app.include_router(exports_api.router,    prefix="/api/v1", tags=["Exports"])

# Phase 12 — Intelligence API
from app.api.intelligence import router as intelligence_router
app.include_router(intelligence_router, prefix="/api/v1/intelligence", tags=["Intelligence"])

from app.api.feedback import router as feedback_router
app.include_router(feedback_router, prefix="/api/v1", tags=["Feedback"])

# Phase 10 — API Key Management + Telegram Account Linking
from app.api.api_keys import router as api_keys_router
from app.api.telegram_link import router as telegram_link_router
app.include_router(api_keys_router, prefix="/api/v1/api-keys", tags=["API Keys"])
app.include_router(telegram_link_router, prefix="/api/v1/telegram", tags=["Telegram Link"])

from app.api.yt_health import router as yt_health_router
app.include_router(yt_health_router, prefix="/api/v1/admin", tags=["YouTube Health"])

from app.api.ops import router as ops_router
app.include_router(ops_router, prefix="/api/v1/admin", tags=["Ops Signals"])

# Phase 25 — Container Sources deep workflow
from app.api.container import router as container_router
app.include_router(container_router, prefix="/api/v1", tags=["Container"])

from app.api import events as events_api
app.include_router(events_api.router, prefix="/api/v1/events", tags=["Events"])

# Phase 18 — Smart AI Analysis
from app.api import smart_analysis as smart_analysis_api
app.include_router(smart_analysis_api.router, prefix="/api/v1", tags=["Smart Analysis"])

# Transcript Translation — batch .srt/.vtt upload + LLM translation
from app.api import transcript_translate as transcript_translate_api
app.include_router(transcript_translate_api.router, prefix="/api/v1/transcript-translate", tags=["Transcript Translation"])

# Transcript ASR — auto-generate subtitles from a downloaded video's audio
from app.api import transcript_asr as transcript_asr_api
app.include_router(transcript_asr_api.router, prefix="/api/v1/transcript-asr", tags=["Transcript ASR"])

# Phase 19 — Mobile / PWA endpoints
from app.api import mobile as mobile_api
app.include_router(mobile_api.router, prefix="/api/v1", tags=["Mobile"])

# Phase 16 — Partner / Multi-tenant API
from app.api import partner as partner_api
from app.api import billing as billing_api
from app.api import tenant_api_keys as tenant_api_keys_api

app.include_router(partner_api.router, tags=["Partner API"])
app.include_router(billing_api.router,         prefix="/api/v1", tags=["Billing"])
app.include_router(tenant_api_keys_api.router, prefix="/api/v1", tags=["Partner API Keys"])

# Phase 20 — Billing Ops Admin Dashboard
from app.api import billing_ops as billing_ops_api
app.include_router(billing_ops_api.router, prefix="/api/v1", tags=["Billing Ops"])

# Phase 21 — User Presets & Platform Preferences
from app.api import presets as presets_api
app.include_router(presets_api.router, prefix="/api/v1", tags=["Presets"])

# Phase 22 — Post-Processing Suite 2.0
from app.api import processing as processing_api
app.include_router(processing_api.router, prefix="/api/v1", tags=["Processing"])

# Phase 24 — Universal Input Resolver + Platforms Capabilities
from app.api import resolve_input as resolve_input_api
app.include_router(resolve_input_api.router, prefix="/api/v1", tags=["Resolve Input"])


# ── Health Check ─────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok"}


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health: disk, Redis, Celery workers, pending jobs, yt-dlp version."""
    import shutil, importlib.metadata

    # Disk usage
    download_dir = os.getenv("DOWNLOAD_DIR", "/app/downloads")
    try:
        total, used, free = shutil.disk_usage(download_dir)
        disk_used_pct = round(used / total * 100, 1) if total else 0
    except Exception:
        total = used = free = 0
        disk_used_pct = -1

    # Redis + Phase 11 metrics
    queue_depth       = -1
    downloads_queue   = -1
    redis_ok          = False
    celery_worker_cnt = -1
    pending_tasks_set = -1
    last_cleanup_at   = None
    try:
        import redis as _r
        rc = _r.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        rc.ping()
        redis_ok      = True
        queue_depth   = rc.llen("celery")
        downloads_queue = rc.llen("downloads")
        pending_tasks_set = rc.scard("vidgrab:pending_tasks")
        last_cleanup_raw = rc.get("vidgrab:last_cleanup_at")
        if last_cleanup_raw:
            last_cleanup_at = last_cleanup_raw if isinstance(last_cleanup_raw, str) else last_cleanup_raw.decode()
    except Exception:
        pass

    # Celery worker count
    try:
        from app.core.queue_intelligence import _get_active_workers
        # Was len(...) on an int — TypeError on every call, so the except below
        # swallowed it and /health reported worker_count: -1 forever.
        celery_worker_cnt = _get_active_workers()
    except Exception:
        pass

    # Pending jobs in DB (status=pending or processing)
    pending_jobs_db = -1
    stale_jobs_count = -1
    try:
        from app.core.database import get_service_client as _get_svc
        _sb = _get_svc()
        _pj = _sb.table("download_jobs").select("id", count="exact").in_("status", ["pending", "processing"]).execute()
        pending_jobs_db = _pj.count if hasattr(_pj, "count") else len(_pj.data or [])
        # Stale = processing for >8 min
        from datetime import datetime, timezone, timedelta
        _stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat()
        _sj = _sb.table("download_jobs").select("id", count="exact").eq("status", "processing").lt("updated_at", _stale_cutoff).execute()
        stale_jobs_count = _sj.count if hasattr(_sj, "count") else len(_sj.data or [])
    except Exception:
        pass

    # YouTube circuit breaker state
    youtube_cb_state = "unknown"
    try:
        from app.core import youtube_gate as _ytg
        # youtube_gate has no get_status() — that was calling a function that
        # doesn't exist, so this field was silently stuck at "unknown" behind
        # the blanket except below. circuit_state() (not the heavier
        # dashboard_snapshot(), which also computes proxy byte/cost stats
        # this field doesn't need) is the right-weight call for a health check.
        youtube_cb_state = _ytg.circuit_state()
    except Exception:
        pass

    # yt-dlp version
    try:
        ytdlp_version = importlib.metadata.version("yt-dlp")
    except Exception:
        ytdlp_version = "unknown"

    return {
        "status": "ok",
        "app_version": APP_VERSION,
        "disk": {
            "used_pct": disk_used_pct,
            "used_gb":  round(used / 1e9, 2),
            "free_gb":  round(free / 1e9, 2),
        },
        "redis": {
            "ok":                    redis_ok,
            "queue_depth":           queue_depth,
            "downloads_queue_depth": downloads_queue,
        },
        "celery": {
            "worker_count":     celery_worker_cnt,
            "pending_tasks_set": pending_tasks_set,
        },
        "jobs": {
            "pending_db":    pending_jobs_db,
            "stale_count":   stale_jobs_count,
            "last_cleanup":  last_cleanup_at,
        },
        "youtube_circuit_breaker": youtube_cb_state,
        "ytdlp_version": ytdlp_version,
        # Is the thing that watches the workers itself alive? Without this the
        # only answer was "grep the logs and hope the line has not rotated".
        "worker_watchdog": _watchdog_status(),
    }


@app.get("/api/v1/api-info", tags=["Public API"])
async def api_info():
    """
    Public API metadata: version, rate limits, supported public endpoints, auth guide.
    Safe to call unauthenticated. Never exposes secrets or internal details.
    """
    return {
        "api_version": "v1",
        "app_version": APP_VERSION,
        "base_url": "/api/v1",
        "auth": {
            "type": "bearer_token",
            "header": "Authorization: Bearer <token>",
            "api_key_endpoint": "POST /api/v1/user/api-key/generate",
            "note": "Public endpoints (ping, fetch-link, progress) work without auth up to rate limits.",
        },
        "rate_limits": {
            "default": "60 requests/minute per IP",
            "heavy": "5 requests/minute per IP for /fetch-link and /bulk-download",
            "daily_quota": "Configurable via DAILY_QUOTA_PER_IP env var (0 = unlimited)",
        },
        "public_endpoints": [
            {"method": "GET",  "path": "/",                            "description": "Basic liveness check"},
            {"method": "GET",  "path": "/health",                      "description": "Disk, Redis, yt-dlp version, app version"},
            {"method": "GET",  "path": "/api/v1/api-info",             "description": "This document"},
            {"method": "POST", "path": "/api/v1/fetch-link",           "description": "Start a download — returns token for polling"},
            {"method": "GET",  "path": "/api/v1/progress/{token}",     "description": "Poll job status and result"},
            {"method": "GET",  "path": "/api/v1/history",              "description": "Recent jobs (unauthenticated: current session only)"},
            {"method": "GET",  "path": "/api/v1/extension/download",   "description": "Extension distribution metadata"},
        ],
        "authenticated_endpoints": [
            {"method": "GET",  "path": "/api/v1/user/history/export",  "description": "Export history as CSV or JSON"},
            {"method": "GET",  "path": "/api/v1/archive",              "description": "Personal archive with search and filters"},
            {"method": "GET",  "path": "/api/v1/archive/export",       "description": "Export archive items"},
            {"method": "GET",  "path": "/api/v1/user/me",              "description": "Current user profile"},
            {"method": "GET",  "path": "/api/v1/user/usage",           "description": "Quota and usage stats"},
        ],
        "error_format": {
            "error_code": "string — machine-readable code (see /api/v1/error-codes)",
            "user_message": "string — safe user-facing message in Vietnamese",
            "retryable": "boolean — true = safe to retry with backoff",
            "suggested_action": "string — one-sentence next step for the user",
        },
        "docs": {
            "api_reference": "/api-docs",
            "openapi_json": "/openapi.json (dev only)",
        },
    }


@app.get("/api/v1/tenant/branding", tags=["Public API"])
async def tenant_branding(request: Request):
    """
    Returns white-label branding config for the current tenant.
    Resolved by custom_domain header or X-Tenant-ID header.
    Falls back to default VidGrab branding when no white-label tenant found.
    Safe to call unauthenticated — returns only public branding fields.
    """
    _DEFAULT = {
        "app_name": "VidGrab",
        "logo_url": None,
        "favicon_url": None,
        "primary_color": "#6366f1",
        "accent_color": "#8b5cf6",
        "support_email": "support@vidgrab.dev",
        "hide_powered_by": False,
        "is_white_label": False,
    }
    try:
        from app.core.database import get_service_client as _get_svc
        _sb = _get_svc()
        # Resolve tenant from custom_domain or X-Tenant-ID header
        domain = request.headers.get("X-Tenant-Domain") or request.headers.get("host", "")
        tenant_id = request.headers.get("X-Tenant-ID")
        row = None
        if tenant_id:
            r = _sb.from_("tenant_settings").select("branding, features").eq("tenant_id", tenant_id).maybe_single().execute()
            row = r.data if r else None
        elif domain and "vidgrab" not in domain.lower() and "localhost" not in domain.lower():
            # Look up by custom_domain
            r = _sb.from_("tenant_settings").select("branding, features, tenant_id").eq("custom_domain", domain).maybe_single().execute()
            row = r.data if r else None
        if not row:
            return _DEFAULT
        branding = row.get("branding") or {}
        features = row.get("features") or {}
        if not features.get("white_label", False):
            return _DEFAULT
        return {
            "app_name": branding.get("app_name") or "VidGrab",
            "logo_url": branding.get("logo_url"),
            "favicon_url": branding.get("favicon_url"),
            "primary_color": branding.get("primary_color") or "#6366f1",
            "accent_color": branding.get("accent_color") or "#8b5cf6",
            "support_email": branding.get("support_email") or "support@vidgrab.dev",
            "hide_powered_by": branding.get("hide_powered_by", False),
            "is_white_label": True,
        }
    except Exception:
        return _DEFAULT


@app.get("/api/v1/error-codes", tags=["Public API"])
async def list_error_codes():
    """Public catalogue of all structured error codes. Useful for client-side mapping."""
    from app.core.error_codes import ERROR_META
    # Strip internal-only detail — return only the user-safe fields
    return {
        code: {
            "user_message": meta["user_message"],
            "retryable": meta["retryable"],
            "suggested_action": meta["suggested_action"],
        }
        for code, meta in ERROR_META.items()
    }
