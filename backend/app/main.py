"""
Video Downloader - FastAPI Backend
====================================
Main application entry point with CORS configuration
and Supabase database initialization.
"""

import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Initialize Rate Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

from app.core.database import init_db
from app.api.routes import router as api_router
from app.api.admin import router as admin_router
from app.api.payments import router as payments_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - runs on startup and shutdown."""
    # --- Startup ---
    print("Starting Video Downloader API...")
    init_db()
    yield
    # --- Shutdown ---
    print("Shutting down Video Downloader API...")


app = FastAPI(
    title="Video Downloader API",
    description="API for downloading and managing video files",
    version="1.0.0",
    lifespan=lifespan,
)

# Register SlowAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS Configuration ──────────────────────────────────────────────
# Allow the React frontend (Vite dev server) to communicate
origins = [
    "http://localhost:5173",    # Vite default
    "http://localhost:3000",    # CRA default
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Include Router ───────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(payments_router, prefix="/api/v1/payments", tags=["Payments"])


# ── Health Check ─────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    """Health check endpoint."""
    return {
        "status": "online",
        "service": "Video Downloader API",
        "version": "1.0.0",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check."""
    return {
        "status": "healthy",
        "database": "connected",
    }
