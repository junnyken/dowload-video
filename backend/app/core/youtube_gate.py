"""
YouTube Proxy Gate — Phase 8 (additive, no schema changes)
==========================================================
The safety envelope for re-enabling YouTube downloads through the paid
residential proxy (Proxying.io / IPRoyal, env IPROYAL_PROXY, ~$1.50/GB).

YouTube on this box is unusual: the Oracle datacenter IP is bot-blocked, so
metadata is extracted via the residential proxy AND the resulting CDN URLs are
IP-locked to that proxy exit — meaning the *bytes* must also flow through the
paid proxy (Phase B). That is the only way YouTube works here, but it costs
per-GB, so every YouTube download must pass this gate first.

Five guards, each independently tunable / disableable:
  1. Feature flag      — master kill switch (env + Redis admin override)   (P8)
  2. Tier gating       — which quality may use the proxy (4K never)        (P4)
  3. Cost guard        — daily proxy-byte ceiling, auto-disable on breach  (P3)
  4. Circuit breaker   — 5 proxy fails / 5 min → open 15 min               (P5)
  5. Metrics + alerts  — requests / success / bytes / cost / CB state      (P9)

All state lives in Redis `vidgrab:youtube:*` (keys carry TTL where unbounded
growth is possible — safe under the broker's volatile-lru policy). Every
function is best-effort: a Redis hiccup must never crash a download request,
and on read-failure the gate FAILS OPEN for availability EXCEPT the cost guard
and circuit breaker, which fail CLOSED (cost safety wins over availability).
"""

from __future__ import annotations

import os
import time
import datetime as _dt
from typing import Optional

from app.core.redis_client import get_redis

# ── Config (ENV-tunable) ─────────────────────────────────────────────
PROXY_COST_PER_GB        = float(os.getenv("YOUTUBE_PROXY_COST_PER_GB", "1.5"))
DAILY_LIMIT_GB           = float(os.getenv("YOUTUBE_PROXY_DAILY_LIMIT_GB", "2"))
MAX_PROXY_HEIGHT         = int(os.getenv("YOUTUBE_MAX_PROXY_HEIGHT", "720"))
# Circuit breaker
CB_FAIL_THRESHOLD        = int(os.getenv("YOUTUBE_CB_THRESHOLD", "5"))
CB_FAIL_WINDOW_SEC       = int(os.getenv("YOUTUBE_CB_WINDOW_SEC", "300"))    # 5 min
CB_OPEN_SEC              = int(os.getenv("YOUTUBE_CB_OPEN_SEC", "900"))      # 15 min
# Alert thresholds
ALERT_BYTES_PCT          = float(os.getenv("YOUTUBE_ALERT_BYTES_PCT", "0.80"))
ALERT_MIN_SAMPLES        = int(os.getenv("YOUTUBE_ALERT_MIN_SAMPLES", "10"))

_BYTES_PER_GB = 1024 ** 3

# ── Redis keys ───────────────────────────────────────────────────────
_ENABLED_OVERRIDE = "vidgrab:youtube:enabled"          # admin toggle (no TTL)
_BYTES_KEY        = "vidgrab:youtube:proxy_bytes:{date}"
_STATS_KEY        = "vidgrab:youtube:stats:{date}"
_CB_STATE         = "vidgrab:youtube:cb:state"
_CB_FAILS         = "vidgrab:youtube:cb:fails"
_CB_OPEN_SINCE    = "vidgrab:youtube:cb:open_since"
_ALERT_CD         = "vidgrab:alert:cd:{tag}"


def _today() -> str:
    return _dt.date.today().isoformat()


# ═════════════════════════════════════════════════════════════════════
# 1. Feature flag (P8) — env default, Redis admin override (no redeploy)
# ═════════════════════════════════════════════════════════════════════

def _env_enabled() -> bool:
    return os.getenv("YOUTUBE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def is_youtube_enabled() -> bool:
    """Master switch. Redis override (set by the admin toggle) wins over env so
    YouTube can be flipped on/off without a redeploy. Fails back to env on error."""
    try:
        v = get_redis().get(_ENABLED_OVERRIDE)
        if v is not None:
            return v.strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return _env_enabled()


def set_youtube_enabled(enabled: bool) -> bool:
    """Admin toggle — persists an override in Redis. Returns the new state."""
    try:
        get_redis().set(_ENABLED_OVERRIDE, "true" if enabled else "false")
    except Exception:
        pass
    return enabled


def clear_youtube_override() -> None:
    """Drop the admin override → fall back to the env default."""
    try:
        get_redis().delete(_ENABLED_OVERRIDE)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════
# 2. Tier gating (P4) — which quality may flow through the paid proxy
# ═════════════════════════════════════════════════════════════════════

_FOURK_ALIASES = {"video_4k", "4k", "mp4_4k", "video_2160"}


def _requested_height(quality: str) -> Optional[int]:
    """Parse a target height from a quality string. None = audio / unknown-best."""
    q = (quality or "").lower()
    if q.startswith("mp3") or q.startswith("audio"):
        return 0           # audio — tiny, always allowed
    if q in _FOURK_ALIASES:
        return 2160
    if q == "video_fast":
        return 0           # pre-merged low — treat as always-allowed
    if q.startswith("video_") and q != "video":
        try:
            return int(q.split("_")[1])
        except (ValueError, IndexError):
            return None
    return None            # "video" = best/unknown → clamp downstream


def tier_decision(quality: str, confirmed: bool = False) -> dict:
    """Decide whether `quality` may download YouTube bytes through the proxy.

    Returns {allow, needs_confirmation, reason, suggested, effective_height}:
      • allow=True                 → proceed (effective_height = proxy cap to apply)
      • needs_confirmation=True     → 1080p tier, ask user then resend confirmed=True
      • allow=False (hard block)    → 4K, or >cap and not 1080p
    """
    h = _requested_height(quality)

    # 4K is NEVER allowed through the proxy (one 4K ≈ 250 MP3s in cost).
    if h is not None and h >= 2160:
        return {"allow": False, "needs_confirmation": False,
                "reason": "4k_blocked",
                "message": "Chất lượng 4K hiện không khả dụng cho YouTube. "
                           f"Vui lòng chọn ≤{MAX_PROXY_HEIGHT}p.",
                "suggested": f"video_{MAX_PROXY_HEIGHT}", "effective_height": None}

    # Audio / fast / unknown-best → allow, clamped to the proxy cap downstream.
    if h is None or h == 0:
        return {"allow": True, "needs_confirmation": False, "reason": "ok",
                "message": "", "suggested": None, "effective_height": MAX_PROXY_HEIGHT}

    # Within the default cap (e.g. 720) → allow.
    if h <= MAX_PROXY_HEIGHT:
        return {"allow": True, "needs_confirmation": False, "reason": "ok",
                "message": "", "suggested": None, "effective_height": h}

    # Above cap but ≤1080 → require explicit confirmation (bigger per-GB cost).
    if h <= 1080:
        if confirmed:
            return {"allow": True, "needs_confirmation": False, "reason": "ok_confirmed",
                    "message": "", "suggested": None, "effective_height": h}
        return {"allow": False, "needs_confirmation": True, "reason": "confirm_1080",
                "message": f"Tải {h}p qua proxy tốn nhiều băng thông hơn (~29¢/video). "
                           "Xác nhận để tiếp tục, hoặc chọn 720p.",
                "suggested": f"video_{MAX_PROXY_HEIGHT}", "effective_height": h}

    # >1080 and <2160 (e.g. 1440) → block (treat like premium tier).
    return {"allow": False, "needs_confirmation": False, "reason": "above_cap_blocked",
            "message": f"Chất lượng này hiện không khả dụng cho YouTube. "
                       f"Vui lòng chọn ≤{MAX_PROXY_HEIGHT}p.",
            "suggested": f"video_{MAX_PROXY_HEIGHT}", "effective_height": None}


# ═════════════════════════════════════════════════════════════════════
# 3. Cost guard (P3) — daily proxy-byte ceiling
# ═════════════════════════════════════════════════════════════════════

def add_proxy_bytes(num_bytes: int) -> int:
    """Record proxy bytes spent on a completed YouTube download. Returns the new
    running total for today (0 on error)."""
    if num_bytes <= 0:
        return 0
    try:
        rc = get_redis()
        key = _BYTES_KEY.format(date=_today())
        total = rc.incrby(key, int(num_bytes))
        rc.expire(key, 86400 * 3)   # keep 3 days for the dashboard, then auto-expire
        return int(total)
    except Exception:
        return 0


def add_proxy_megabytes(mb: float) -> int:
    return add_proxy_bytes(int((mb or 0) * 1024 * 1024))


def get_proxy_bytes_today() -> int:
    try:
        v = get_redis().get(_BYTES_KEY.format(date=_today()))
        return int(v) if v else 0
    except Exception:
        return 0


def daily_limit_bytes() -> int:
    return int(DAILY_LIMIT_GB * _BYTES_PER_GB)


def over_daily_limit() -> bool:
    """True when today's proxy bytes meet/exceed the configured ceiling.
    Fails CLOSED (returns True) on Redis error → cost safety over availability."""
    try:
        return get_proxy_bytes_today() >= daily_limit_bytes()
    except Exception:
        return True


def cost_estimate_today() -> float:
    return round(get_proxy_bytes_today() / _BYTES_PER_GB * PROXY_COST_PER_GB, 4)


def bytes_pct_today() -> float:
    lim = daily_limit_bytes()
    return (get_proxy_bytes_today() / lim) if lim else 0.0


# ═════════════════════════════════════════════════════════════════════
# 4. Circuit breaker (P5) — 5 proxy fails / 5 min → open 15 min → half-open
# ═════════════════════════════════════════════════════════════════════

def circuit_state() -> str:
    """'closed' | 'open' | 'half'. Auto-transitions open→half after the cooldown."""
    try:
        rc = get_redis()
        state = rc.get(_CB_STATE) or "closed"
        if state == "open":
            since = rc.get(_CB_OPEN_SINCE)
            if since and (time.time() - float(since)) >= CB_OPEN_SEC:
                rc.set(_CB_STATE, "half")
                return "half"
        return state
    except Exception:
        # Fail CLOSED for cost safety: if we can't read state, assume tripped.
        return "open"


def circuit_open() -> bool:
    return circuit_state() == "open"


def circuit_cooldown_remaining() -> int:
    try:
        since = get_redis().get(_CB_OPEN_SINCE)
        if since:
            return max(0, int(CB_OPEN_SEC - (time.time() - float(since))))
    except Exception:
        pass
    return 0


def record_proxy_success() -> None:
    """A YouTube proxy download succeeded → close the circuit, clear fails."""
    try:
        rc = get_redis()
        if (rc.get(_CB_STATE) or "closed") in ("half", "open"):
            print("[YouTubeCB] success → CLOSED")
        rc.set(_CB_STATE, "closed")
        rc.delete(_CB_FAILS, _CB_OPEN_SINCE)
    except Exception:
        pass


def record_proxy_failure() -> None:
    """A YouTube proxy download failed. In HALF → reopen immediately. In CLOSED →
    count within the rolling window; trip OPEN at the threshold."""
    try:
        rc = get_redis()
        state = rc.get(_CB_STATE) or "closed"
        if state == "half":
            rc.set(_CB_STATE, "open")
            rc.set(_CB_OPEN_SINCE, str(time.time()))
            print("[YouTubeCB] HALF probe failed → OPEN 15m")
            return
        fails = rc.incr(_CB_FAILS)
        if fails == 1:
            rc.expire(_CB_FAILS, CB_FAIL_WINDOW_SEC)   # rolling window
        if fails >= CB_FAIL_THRESHOLD:
            rc.set(_CB_STATE, "open")
            rc.set(_CB_OPEN_SINCE, str(time.time()))
            print(f"[YouTubeCB] {fails} fails/{CB_FAIL_WINDOW_SEC}s → OPEN 15m")
            _fire_alert("yt_circuit",
                        f"🔴 <b>YouTube circuit breaker MỞ</b> ({fails} proxy fail "
                        f"/{CB_FAIL_WINDOW_SEC//60}m). Tạm tắt YouTube {CB_OPEN_SEC//60}m.")
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════
# Master gate (used by routes/tasks BEFORE extraction)
# ═════════════════════════════════════════════════════════════════════

class YouTubeBlocked(Exception):
    """Raised to reject a YouTube request. `code` is a stable reason, `http` the
    status to surface, `payload` extra fields (e.g. needs_confirmation)."""
    def __init__(self, code: str, message: str, http: int = 503, payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.payload = payload or {}


def preflight(quality: str, confirmed: bool = False) -> dict:
    """Run all guards for a YouTube request. Returns {effective_height} on pass,
    raises YouTubeBlocked otherwise. Order: flag → circuit → cost → tier."""
    youtube_metric("requests")

    if not is_youtube_enabled():
        raise YouTubeBlocked("youtube_disabled",
                             "YouTube hiện tạm thời không khả dụng.", http=503)

    if circuit_open():
        raise YouTubeBlocked("youtube_circuit_open",
                             "YouTube tạm thời không khả dụng. Vui lòng thử lại sau ít phút.",
                             http=503, payload={"cooldown_sec": circuit_cooldown_remaining()})

    if over_daily_limit():
        # Auto-flip the admin override off so the whole system agrees + dashboard
        # shows red, until the next day's counter (or an admin re-enable).
        raise YouTubeBlocked("youtube_cost_limit",
                             "YouTube tạm thời không khả dụng do giới hạn băng thông.",
                             http=503, payload={"cost_today": cost_estimate_today()})

    decision = tier_decision(quality, confirmed=confirmed)
    if decision["needs_confirmation"]:
        raise YouTubeBlocked("youtube_confirm_required", decision["message"],
                             http=409, payload={"needs_confirmation": True,
                                                "suggested": decision["suggested"]})
    if not decision["allow"]:
        raise YouTubeBlocked(decision["reason"], decision["message"],
                             http=400, payload={"suggested": decision["suggested"]})

    return {"effective_height": decision["effective_height"]}


def record_result(success: bool, file_size_mb: float = 0.0) -> None:
    """Call AFTER a YouTube proxy download attempt finishes. Updates the circuit,
    metrics, and (on success) the daily proxy-byte counter."""
    if success:
        record_proxy_success()
        youtube_metric("success")
        if file_size_mb and file_size_mb > 0:
            add_proxy_megabytes(file_size_mb)
            youtube_metric("bytes", int(file_size_mb * 1024 * 1024))
    else:
        record_proxy_failure()
        youtube_metric("fail")


# ═════════════════════════════════════════════════════════════════════
# 5. Metrics + alerts (P9)
# ═════════════════════════════════════════════════════════════════════

def youtube_metric(field: str, n: int = 1) -> None:
    try:
        rc = get_redis()
        key = _STATS_KEY.format(date=_today())
        rc.hincrby(key, field, n)
        rc.expire(key, 86400 * 8)
    except Exception:
        pass


def get_youtube_stats(date: Optional[str] = None) -> dict:
    date = date or _today()
    try:
        raw = get_redis().hgetall(_STATS_KEY.format(date=date)) or {}
    except Exception:
        raw = {}
    return {k: int(v) for k, v in raw.items()}


def _fire_alert(tag: str, message: str, cooldown: int = 1800) -> bool:
    try:
        rc = get_redis()
        if not rc.set(_ALERT_CD.format(tag=tag), "1", nx=True, ex=cooldown):
            return False
    except Exception:
        return False
    try:
        from app.core.notifications import send_telegram_message_sync
        return send_telegram_message_sync(message)
    except Exception:
        return False


def check_health_and_alert() -> dict:
    """Beat-driven watchdog (P9). Fires Telegram alerts on:
      • proxy bytes > 80% of the daily ceiling,
      • YouTube success rate < 60% (min sample),
      • circuit OPEN (also fired at trip time).
    Returns a snapshot."""
    snap = dashboard_snapshot()

    if bytes_pct_today() >= ALERT_BYTES_PCT and not over_daily_limit():
        _fire_alert("yt_bytes",
                    f"🟡 <b>YouTube proxy {snap['bytes_pct']*100:.0f}%</b> ngưỡng ngày "
                    f"({snap['bytes_gb']:.2f}/{DAILY_LIMIT_GB}GB ≈ ${snap['cost_today']}). "
                    f"Sắp tự tắt YouTube.", cooldown=1800)

    ok, fail = snap["success"], snap["fail"]
    if ok + fail >= ALERT_MIN_SAMPLES:
        rate = ok / (ok + fail)
        if rate < 0.60:
            _fire_alert("yt_success",
                        f"🔴 <b>YouTube success {rate*100:.0f}%</b> (&lt;60%, "
                        f"{ok}/{ok+fail}). Kiểm tra proxy / circuit.", cooldown=1800)

    return snap


def dashboard_snapshot() -> dict:
    """Everything the admin dashboard needs for the YouTube panel (P3/P9)."""
    bytes_today = get_proxy_bytes_today()
    pct = bytes_pct_today()
    state = circuit_state()
    stats = get_youtube_stats()
    ok, fail = stats.get("success", 0), stats.get("fail", 0)
    return {
        "enabled": is_youtube_enabled(),
        "proxy_download_enabled": os.getenv("YOUTUBE_PROXY_DOWNLOAD", "0").strip().lower()
                                  in ("1", "true", "yes", "on"),
        "bytes_today": bytes_today,
        "bytes_gb": round(bytes_today / _BYTES_PER_GB, 3),
        "limit_gb": DAILY_LIMIT_GB,
        "bytes_pct": round(pct, 3),
        "cost_today": cost_estimate_today(),
        "cost_per_gb": PROXY_COST_PER_GB,
        "over_limit": bytes_today >= daily_limit_bytes(),
        "max_proxy_height": MAX_PROXY_HEIGHT,
        "circuit_state": state,
        "circuit_cooldown_sec": circuit_cooldown_remaining() if state == "open" else 0,
        "requests": stats.get("requests", 0),
        "success": ok,
        "fail": fail,
        "success_rate": round(ok / (ok + fail) * 100, 1) if (ok + fail) else 100.0,
        # UI color hint: red if over/circuit-open, yellow if ≥80%, else green
        "status_color": (
            "red" if (bytes_today >= daily_limit_bytes() or state == "open" or not is_youtube_enabled())
            else "yellow" if pct >= ALERT_BYTES_PCT
            else "green"
        ),
    }
