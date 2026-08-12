"""
YouTube Proxy Health Probe — Phase 26-A
=========================================
Cached connectivity check for YTDL_PROXY.

Three states:
  no_proxy   — YTDL_PROXY env var not set
  unhealthy  — YTDL_PROXY set but connectivity check failed
  healthy    — YTDL_PROXY set and probe succeeded

Probe result is cached in Redis for PROBE_TTL seconds (default 45) to avoid
spamming the proxy on every request. Cache miss triggers a sync HTTP GET via
the proxy to a lightweight Google endpoint (generate_204 → 204 instantly).

Usage (sync, e.g. Celery / expanders):
    health = get_youtube_proxy_health()
    if health.state != "healthy": ...

Usage (async, FastAPI endpoints):
    health = await get_youtube_proxy_health_async()
    if health.state != "healthy": ...

The probe fails CLOSED (returns "unhealthy") on any exception so we never let
a broken proxy through on an error.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import time
from typing import Literal

logger = logging.getLogger(__name__)

ProxyState = Literal["no_proxy", "unhealthy", "healthy"]

PROBE_TTL     = int(os.getenv("YTDL_PROXY_PROBE_TTL", "45"))   # seconds
PROBE_URL     = "https://www.google.com/generate_204"
PROBE_TIMEOUT = float(os.getenv("YTDL_PROXY_PROBE_TIMEOUT", "8"))  # seconds


@dataclasses.dataclass
class ProxyHealth:
    state:             ProxyState
    proxy_configured:  bool
    checked_at:        float
    error:             str = ""

    def is_usable(self) -> bool:
        return self.state == "healthy"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _proxy_url() -> str:
    return os.environ.get("YTDL_PROXY") or os.environ.get("RESIDENTIAL_PROXY_URL", "")


def _cache_key(proxy: str) -> str:
    h = hashlib.sha256(proxy.encode()).hexdigest()[:16]
    return f"vidgrab:yt_proxy:health:{h}"


def _from_cache(proxy: str) -> ProxyHealth | None:
    try:
        from app.core.redis_client import get_redis
        raw = get_redis().get(_cache_key(proxy))
        if raw:
            d = json.loads(raw)
            return ProxyHealth(**d)
    except Exception:
        pass
    return None


def _to_cache(proxy: str, health: ProxyHealth) -> None:
    try:
        from app.core.redis_client import get_redis
        get_redis().setex(_cache_key(proxy), PROBE_TTL, json.dumps(dataclasses.asdict(health)))
    except Exception:
        pass


def _run_probe(proxy: str) -> ProxyHealth:
    """Run a sync HTTP GET through the proxy to verify connectivity."""
    import httpx
    try:
        with httpx.Client(proxy=proxy, timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = client.get(PROBE_URL)
        if resp.status_code in (200, 204, 301, 302, 307):
            return ProxyHealth(
                state="healthy", proxy_configured=True, checked_at=time.time(),
            )
        return ProxyHealth(
            state="unhealthy", proxy_configured=True, checked_at=time.time(),
            error=f"HTTP {resp.status_code}",
        )
    except Exception as exc:
        err = str(exc)[:120]
        # Redact credential info from proxy URL before logging
        safe_suffix = proxy.split("@")[-1][:30] if "@" in proxy else proxy[-20:]
        logger.warning(
            "yt_proxy_probe_failed endpoint=%s error=%.120s", safe_suffix, err,
        )
        return ProxyHealth(
            state="unhealthy", proxy_configured=True, checked_at=time.time(), error=err,
        )


# ── Public API ────────────────────────────────────────────────────────────────

def get_youtube_proxy_health(force: bool = False) -> ProxyHealth:
    """
    Return cached proxy health. Triggers a live probe on cache miss.
    Safe for sync callers (Celery tasks, expanders).
    Fails CLOSED: any exception during probe → unhealthy.
    """
    proxy = _proxy_url()
    if not proxy:
        return ProxyHealth(
            state="no_proxy", proxy_configured=False,
            checked_at=time.time(), error="YTDL_PROXY not configured",
        )

    if not force:
        cached = _from_cache(proxy)
        if cached is not None:
            logger.debug("yt_proxy_health cache_hit state=%s", cached.state)
            return cached

    health = _run_probe(proxy)
    _to_cache(proxy, health)
    logger.info("yt_proxy_health probed state=%s error=%.80s", health.state, health.error)
    return health


async def get_youtube_proxy_health_async(force: bool = False) -> ProxyHealth:
    """
    Async-safe version — reads cache first (instant), probes in thread on miss.
    Use this from FastAPI async endpoints.
    """
    import asyncio

    proxy = _proxy_url()
    if not proxy:
        return ProxyHealth(
            state="no_proxy", proxy_configured=False,
            checked_at=time.time(), error="YTDL_PROXY not configured",
        )

    if not force:
        cached = _from_cache(proxy)
        if cached is not None:
            logger.debug("yt_proxy_health cache_hit state=%s", cached.state)
            return cached

    # Cache miss — run blocking probe off the event loop
    health = await asyncio.to_thread(_run_probe, proxy)
    _to_cache(proxy, health)
    logger.info("yt_proxy_health probed state=%s error=%.80s", health.state, health.error)
    return health


def invalidate_proxy_health_cache() -> None:
    """Force the next call to re-probe (useful after a proxy reconfiguration)."""
    proxy = _proxy_url()
    if not proxy:
        return
    try:
        from app.core.redis_client import get_redis
        get_redis().delete(_cache_key(proxy))
    except Exception:
        pass
