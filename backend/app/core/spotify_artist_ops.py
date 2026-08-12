"""
Spotify Artist Ops — hardening helpers (additive, no schema changes)
====================================================================
Production hardening for the Spotify Artist feature. Everything here is
*best-effort*: a Redis/disk hiccup must NEVER break an artist request.
State lives entirely in Redis keys under the `vidgrab:artist:*` namespace
(no `download_jobs` schema change).

Covers:
  • Catalog caps (guard runaway expansion / ScraperAPI burn)         — P3
  • Disk estimate guard before large batches                         — P3
  • Artist metadata / album / tracklist cache (30 min)               — P4a
  • Daily metrics counters + top-artist leaderboard (Redis hashes)   — P5a
  • Real-time health alert to Telegram (cooldown via SETNX)          — P5b

IMPORTANT (Redis policy): every cache/metric key set here carries a TTL.
The shared Redis runs `--maxmemory-policy volatile-lru`, which evicts only
keyed-with-TTL entries — so these may be dropped under pressure (safe) while
the Celery broker queue (no TTL) is never evicted. Do NOT write a key here
without an expiry.
"""

from __future__ import annotations

import os
import json
import hashlib
import datetime as _dt
from typing import Any, Optional

from app.core.redis_client import get_redis

# ── Catalog caps (ENV-tunable) ───────────────────────────────────────
ARTIST_CAP_TOP_TRACKS = int(os.getenv("ARTIST_CAP_TOP_TRACKS", "10"))
ARTIST_CAP_ALBUMS     = int(os.getenv("ARTIST_CAP_ALBUMS", "20"))
ARTIST_CAP_SINGLES    = int(os.getenv("ARTIST_CAP_SINGLES", "20"))
ARTIST_CAP_ALL_TRACKS = int(os.getenv("ARTIST_CAP_ALL_TRACKS", "200"))
ARTIST_BATCH_SIZE     = int(os.getenv("ARTIST_BATCH_SIZE", "20"))

# ── Cache TTLs ───────────────────────────────────────────────────────
ARTIST_META_TTL = int(os.getenv("ARTIST_META_TTL_SEC", "1800"))   # 30 min

# ── Namespaced keys ──────────────────────────────────────────────────
_META_KEY  = "vidgrab:artist:meta:{aid}"
_STATS_KEY = "vidgrab:artist:stats:{date}"
_TOP_KEY   = "vidgrab:artist:top:{date}"
_ALERT_CD  = "vidgrab:alert:cd:{tag}"


def _today() -> str:
    return _dt.date.today().isoformat()


# ═════════════════════════════════════════════════════════════════════
# Cache (P4a) — best-effort JSON cache with mandatory TTL
# ═════════════════════════════════════════════════════════════════════

def cache_get(key: str) -> Optional[Any]:
    try:
        v = get_redis().get(key)
        return json.loads(v) if v else None
    except Exception:
        return None


def cache_set(key: str, val: Any, ttl: int) -> None:
    # ttl is REQUIRED — never write a non-expiring key on the shared broker Redis.
    try:
        get_redis().set(key, json.dumps(val), ex=ttl)
    except Exception:
        pass


def artist_meta_key(artist_id: str) -> str:
    return _META_KEY.format(aid=artist_id)


def get_cached_artist(artist_id: str) -> Optional[dict]:
    return cache_get(artist_meta_key(artist_id))


def cache_artist(artist_id: str, payload: dict) -> None:
    cache_set(artist_meta_key(artist_id), payload, ARTIST_META_TTL)


def invalidate_artist(artist_id: str) -> int:
    """Force-refresh: drop cached artist overview. Returns # keys deleted."""
    try:
        return int(get_redis().delete(artist_meta_key(artist_id)))
    except Exception:
        return 0


def resolve_cache_key(search_query: str) -> str:
    """Reproduce the downloader's Spotify→YouTube resolve key
    (`spotify_yt:{md5(search_query)}`, 7-day TTL) so admins can invalidate a
    single mis-matched track without flushing the whole cache."""
    return f"spotify_yt:{hashlib.md5(search_query.encode()).hexdigest()}"


def invalidate_resolve(search_query: str) -> int:
    try:
        return int(get_redis().delete(resolve_cache_key(search_query)))
    except Exception:
        return 0


# ═════════════════════════════════════════════════════════════════════
# Metrics (P5a) — daily Redis hash counters, reuse `vidgrab:stats` pattern
# ═════════════════════════════════════════════════════════════════════

def artist_metric(field: str, n: int = 1) -> None:
    """Increment a daily counter. Fields:
    requests / expand_ok / expand_fail / deduped /
    sc_resolve_ok / sc_resolve_fail / needs_confirm / capped."""
    try:
        rc = get_redis()
        key = _STATS_KEY.format(date=_today())
        rc.hincrby(key, field, n)
        rc.expire(key, 86400 * 8)
    except Exception:
        pass


def artist_seen(artist_name: str) -> None:
    """Bump the day's top-artist leaderboard (ZSET)."""
    if not artist_name:
        return
    try:
        rc = get_redis()
        key = _TOP_KEY.format(date=_today())
        rc.zincrby(key, 1, artist_name)
        rc.expire(key, 86400 * 8)
    except Exception:
        pass


def get_artist_stats(date: Optional[str] = None) -> dict:
    """Read today's counters as ints (missing → 0)."""
    date = date or _today()
    try:
        raw = get_redis().hgetall(_STATS_KEY.format(date=date)) or {}
    except Exception:
        raw = {}
    return {k: int(v) for k, v in raw.items()}


def get_top_artists(date: Optional[str] = None, n: int = 3) -> list[tuple[str, int]]:
    date = date or _today()
    try:
        rows = get_redis().zrevrange(_TOP_KEY.format(date=date), 0, n - 1, withscores=True)
        return [(name, int(score)) for name, score in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════
# Disk guard (P3)
# ═════════════════════════════════════════════════════════════════════

def disk_guard_ok(track_count: int, avg_mb: float = 8.0) -> tuple[bool, str]:
    """Reject a large artist batch if its estimated footprint exceeds 60% of
    the free disk currently available. Best-effort — on error, allow."""
    try:
        import shutil
        path = os.getenv("DOWNLOAD_DIR", "/app/downloads")
        _total, _used, free = shutil.disk_usage(path)
        free_mb = free / 1024 / 1024
        est_mb = track_count * avg_mb
        if est_mb > free_mb * 0.6:
            return False, (
                f"Ước tính ~{est_mb:.0f}MB cho {track_count} bài, vượt 60% dung "
                f"lượng trống (~{free_mb:.0f}MB). Giảm số bài hoặc chờ cleanup."
            )
    except Exception:
        pass
    return True, ""


# ═════════════════════════════════════════════════════════════════════
# Real-time health alert (P5b) — Telegram with SETNX cooldown
# ═════════════════════════════════════════════════════════════════════

def _fire_alert(tag: str, message: str, cooldown: int = 1800) -> bool:
    """Send a Telegram alert at most once per `cooldown` seconds per tag."""
    try:
        rc = get_redis()
        if not rc.set(_ALERT_CD.format(tag=tag), "1", nx=True, ex=cooldown):
            return False  # still within cooldown
    except Exception:
        return False
    try:
        from app.core.notifications import send_telegram_message_sync
        return send_telegram_message_sync(message)
    except Exception:
        return False


# Minimum sample size before a rate-based alert is meaningful.
_ALERT_MIN_SAMPLES = int(os.getenv("ARTIST_ALERT_MIN_SAMPLES", "10"))


def check_artist_health_and_alert() -> dict:
    """Evaluate today's counters and fire Telegram alerts on breach.
    Called by a Celery beat task every few minutes. Returns a small report."""
    s = get_artist_stats()
    exp_ok, exp_fail = s.get("expand_ok", 0), s.get("expand_fail", 0)
    sc_ok, sc_fail   = s.get("sc_resolve_ok", 0), s.get("sc_resolve_fail", 0)
    fired = []

    exp_total = exp_ok + exp_fail
    if exp_total >= _ALERT_MIN_SAMPLES:
        rate = exp_ok / exp_total
        if rate < 0.70:
            if _fire_alert("artist_expand",
                           f"🔴 <b>Spotify Artist expand</b> success {exp_ok}/{exp_total} "
                           f"= {rate*100:.0f}% (&lt;70%). Kiểm tra Spotify API key / quota."):
                fired.append("artist_expand")

    sc_total = sc_ok + sc_fail
    if sc_total >= _ALERT_MIN_SAMPLES:
        fail_rate = sc_fail / sc_total
        if fail_rate > 0.30:
            if _fire_alert("sc_resolve",
                           f"🔴 <b>SoundCloud resolve fail</b> {sc_fail}/{sc_total} "
                           f"= {fail_rate*100:.0f}% (&gt;30%). Spotify đang dùng SoundCloud "
                           f"làm nguồn CHÍNH — nguy cơ gãy tải nhạc."):
                fired.append("sc_resolve")

    return {"stats": s, "alerts_fired": fired}


def check_disk_and_alert(threshold: float = 0.70) -> Optional[float]:
    """Alert if disk usage exceeds threshold (e.g. after a big all_tracks job).
    Returns the usage ratio if it alerted, else None."""
    try:
        import shutil
        path = os.getenv("DOWNLOAD_DIR", "/app/downloads")
        total, used, _free = shutil.disk_usage(path)
        ratio = used / total if total else 0.0
        if ratio > threshold:
            _fire_alert("disk_high",
                        f"🟡 <b>Disk {ratio*100:.0f}%</b> (&gt;{int(threshold*100)}%) "
                        f"tại {path}. Cleanup đang chạy mỗi 5'; theo dõi nếu artist "
                        f"all_tracks lớn vừa chạy.",
                        cooldown=900)
            return ratio
    except Exception:
        pass
    return None
