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


# ── Health Check (minimal info in production) ────────────────────────
@app.get("/", tags=["Health"])
async def root():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check."""
    return {"status": "ok"}
