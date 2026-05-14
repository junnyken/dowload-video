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

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Initialize Rate Limiter (stricter: 60 req/min per IP default)
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

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

        ip = get_remote_address(request)
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
from app.api.routes import router as api_router
from app.api.admin import router as admin_router
from app.api.payments import router as payments_router


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - runs on startup and shutdown."""
    # --- Startup ---
    print("Starting Video Downloader API...")
    init_db()

    # Send Telegram startup notification (non-blocking, failure is OK)
    try:
        from app.core.notifications import notify_system_startup
        await notify_system_startup()
    except Exception as e:
        print(f"[Startup] Telegram notification failed (non-critical): {e}")

    yield
    # --- Shutdown ---
    print("Shutting down Video Downloader API...")


# ── Disable API docs in production ───────────────────────────────────
is_dev = os.getenv("ENV", "production").lower() in ("dev", "development", "local")

app = FastAPI(
    title="Video Downloader API",
    description="API for downloading and managing video files",
    version="1.0.0",
    lifespan=lifespan,
    # Hide Swagger/ReDoc in production to prevent API analysis
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)

# Register Security Headers
app.add_middleware(SecurityHeadersMiddleware)

# Register Per-IP daily quota (no-op when DAILY_QUOTA_PER_IP=0)
app.add_middleware(DailyIPQuotaMiddleware)

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Allow Chrome Extension origins (chrome-extension://...)
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],  # Only methods we actually use
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


# ── Include Router ───────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(payments_router, prefix="/api/v1/payments", tags=["Payments"])


# ── Health Check ─────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok"}


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health: disk, Redis queue depth, yt-dlp version."""
    import shutil, importlib.metadata

    # Disk usage
    download_dir = os.getenv("DOWNLOAD_DIR", "/app/downloads")
    try:
        total, used, free = shutil.disk_usage(download_dir)
        disk_used_pct = round(used / total * 100, 1) if total else 0
    except Exception:
        total = used = free = 0
        disk_used_pct = -1

    # Redis queue depth (number of tasks in default celery queue)
    queue_depth = -1
    redis_ok = False
    try:
        import redis as _r
        rc = _r.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        rc.ping()
        redis_ok = True
        queue_depth = rc.llen("celery")
    except Exception:
        pass

    # yt-dlp version
    try:
        ytdlp_version = importlib.metadata.version("yt-dlp")
    except Exception:
        ytdlp_version = "unknown"

    return {
        "status": "ok",
        "disk": {
            "used_pct": disk_used_pct,
            "used_gb": round(used / 1e9, 2),
            "free_gb": round(free / 1e9, 2),
        },
        "redis": {"ok": redis_ok, "queue_depth": queue_depth},
        "ytdlp_version": ytdlp_version,
    }
