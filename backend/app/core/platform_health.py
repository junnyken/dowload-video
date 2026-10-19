"""
Platform health — "is a platform broken because of US or because it changed?"
==============================================================================
Added 2026-10-19. Platforms change their pages without notice; until now the
service could see that (probes every 30 min, Telegram) but did nothing about
it, and its failure-rate alert read download_jobs — the table /fetch-link only
writes for a signed-in user's SUCCESS — so a TikTok outage on the main path
never reached it.

This module reads the outcome store (download_outcomes: every attempt,
counted once, with a classified error code) and separates three things:

  * user errors   — video removed / private / unsupported link (4xx codes):
                    never a reason to alert;
  * system errors — blocked, timeout, upstream 5xx, ...;
  * extractor_broken — the extractor reached the page and could not read it,
                    i.e. what a platform redesign looks like.

and acts on them:

  * Telegram: critical / warning on the system-error rate, and a dedicated
    "platform changed" alert on extractor_broken;
  * remediation: enqueue app.core.ytdlp_channel's try-a-newer-yt-dlp task
    (also called when a probe flips to FAILED, see probe_tasks);
  * route override: for platforms where Cobalt can serve instead of yt-dlp
    (Facebook, Instagram, X), put Cobalt first for a few hours
    (downloader.cobalt_first reads it) — cleared when the platform recovers;
  * partner webhook: platform.degraded / platform.recovered.

Every function fails open and never raises into its caller.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Codes that say "the user's link" (deleted, private, wrong URL, needs login):
# not a platform problem. Anything else in a failed attempt is ours.
USER_CODES = frozenset({
    "video_unavailable", "private_or_login_required", "unsupported_url",
    "invalid_url", "drm_protected", "geo_blocked", "china_link_unavailable",
    "kuaishou_invalid_url", "kuaishou_redirect_rejected", "kuaishou_not_found",
    "kuaishou_unsupported_post_type", "china_daily_limit", "quota_exceeded",
})
BROKEN_CODE = "extractor_broken"

# Thresholds over the last ~2 hours (current hour + the previous one).
MIN_ATTEMPTS = 8            # below this a rate is noise
SYSTEM_WARN = 0.30
SYSTEM_CRITICAL = 0.60
BROKEN_MIN = 3              # extractor_broken attempts …
BROKEN_RATE = 0.25          # … and their share of all attempts

COBALT_ROUTE_PLATFORMS = ("facebook", "instagram", "twitter")
COBALT_OVERRIDE_KEY = "health:prefer_cobalt:{platform}"
COBALT_OVERRIDE_TTL = 6 * 3600
DEGRADED_KEY = "health:degraded"          # hash platform -> json {since, reason}
REMEDIATE_COOLDOWN_KEY = "health:remediate_cooldown:{platform}"
REMEDIATE_COOLDOWN_S = 6 * 3600


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _d(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


# ── Reading ───────────────────────────────────────────────────────────

def recent_counts(hours: int = 2, *, now: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
    """{platform: {ok, err, user_err, system_err, broken, total}} over the
    current hour and the `hours-1` before it. {} when Redis cannot be read."""
    from app.core import download_outcomes as do  # noqa: PLC0415
    now = now or datetime.now(timezone.utc)
    hrs = [do.hour_str(now - timedelta(hours=h)) for h in range(hours)]
    try:
        pipe = _r().pipeline(transaction=False)
        for h in hrs:
            pipe.hgetall(do.HOUR_KEY_TPL.format(hour=h))
        for h in hrs:
            pipe.hgetall(do.ERRCODE_HOUR_KEY_TPL.format(hour=h))
        res = pipe.execute()
    except Exception:
        return {}
    out: Dict[str, Dict[str, int]] = {}

    def slot(p):
        return out.setdefault(p, {"ok": 0, "err": 0, "user_err": 0, "system_err": 0,
                                  "broken": 0, "total": 0})
    for raw in res[:hours]:
        for p, c in do._parse_okerr(raw).items():
            s = slot(p)
            s["ok"] += c.get("ok", 0)
            s["err"] += c.get("err", 0)
    for raw in res[hours:]:
        for (p, code), n in do._parse_codes(raw).items():
            s = slot(p)
            if code in USER_CODES:
                s["user_err"] += n
            if code == BROKEN_CODE:
                s["broken"] += n
    for s in out.values():
        s["total"] = s["ok"] + s["err"]
        # err the codes do not explain (older buckets) counts as system.
        s["system_err"] = max(0, s["err"] - s["user_err"])
    return out


def verdict(c: Dict[str, int]) -> Optional[str]:
    """'broken' | 'critical' | 'warning' | None for one platform's counts.

    Rates are over the attempts that were NOT the user's link failing: a
    burst of deleted videos must neither raise an alert nor dilute a real
    outage below its threshold."""
    base = c.get("total", 0) - c.get("user_err", 0)
    if base < MIN_ATTEMPTS:
        return None
    if c.get("broken", 0) >= BROKEN_MIN and c["broken"] / base >= BROKEN_RATE:
        return "broken"
    rate = c.get("system_err", 0) / base
    if rate >= SYSTEM_CRITICAL:
        return "critical"
    if rate >= SYSTEM_WARN:
        return "warning"
    return None


# ── Route override (Cobalt first) ─────────────────────────────────────

def prefer_cobalt(platform: str) -> bool:
    """Has health put Cobalt in front of yt-dlp for `platform` right now?"""
    if platform not in COBALT_ROUTE_PLATFORMS:
        return False
    try:
        return bool(_r().exists(COBALT_OVERRIDE_KEY.format(platform=platform)))
    except Exception:
        return False


def set_prefer_cobalt(platform: str, on: bool, reason: str = "") -> bool:
    if platform not in COBALT_ROUTE_PLATFORMS:
        return False
    try:
        key = COBALT_OVERRIDE_KEY.format(platform=platform)
        if on:
            _r().set(key, json.dumps({"since": int(time.time()), "reason": reason[:200]}),
                     ex=COBALT_OVERRIDE_TTL)
        else:
            _r().delete(key)
        return True
    except Exception:
        return False


def cobalt_overrides() -> Dict[str, dict]:
    out = {}
    for p in COBALT_ROUTE_PLATFORMS:
        try:
            raw = _r().get(COBALT_OVERRIDE_KEY.format(platform=p))
            if raw:
                out[p] = json.loads(_d(raw))
        except Exception:
            continue
    return out


# ── Degraded state + partner notifications ────────────────────────────

def degraded() -> Dict[str, dict]:
    try:
        return {_d(k): json.loads(_d(v)) for k, v in (_r().hgetall(DEGRADED_KEY) or {}).items()}
    except Exception:
        return {}


def mark_degraded(platform: str, reason: str) -> bool:
    """True when this call flipped the platform to degraded (first report)."""
    try:
        won = _r().hsetnx(DEGRADED_KEY, platform,
                          json.dumps({"since": int(time.time()), "reason": reason[:200]}))
    except Exception:
        return False
    if won:
        _notify_partners("platform.degraded", platform, reason)
    return bool(won)


def mark_recovered(platform: str) -> bool:
    """True when the platform was degraded and is not any more."""
    try:
        removed = _r().hdel(DEGRADED_KEY, platform)
    except Exception:
        return False
    if removed:
        set_prefer_cobalt(platform, False)
        _notify_partners("platform.recovered", platform, "")
    return bool(removed)


def _notify_partners(event: str, platform: str, reason: str) -> None:
    """Webhook every partner endpoint subscribed to `event`. Best effort, off
    the caller's thread (dispatch is async and talks to the DB)."""
    def _send():
        try:
            import asyncio  # noqa: PLC0415
            from app.core.database import get_service_client  # noqa: PLC0415
            from app.services.webhook_dispatcher import dispatch_event  # noqa: PLC0415
            rows = (get_service_client().table("webhook_endpoints")
                    .select("tenant_id, events").eq("is_active", True).execute().data or [])
            tenants = sorted({r["tenant_id"] for r in rows if event in (r.get("events") or [])})
            payload = {"platform": platform, "status": "degraded" if event.endswith("degraded") else "ok",
                       "at": datetime.now(timezone.utc).isoformat()}
            for t in tenants:
                asyncio.run(dispatch_event(t, event, payload))
        except Exception:
            log.exception("platform_health: partner notify failed (%s %s)", event, platform)
    try:
        import threading  # noqa: PLC0415
        threading.Thread(target=_send, daemon=True, name=f"notify-{platform}").start()
    except Exception:
        pass


# ── Remediation trigger ───────────────────────────────────────────────

def request_remediation(platform: str, reason: str) -> bool:
    """Enqueue a try-a-newer-yt-dlp run for `platform`, at most once per
    REMEDIATE_COOLDOWN_S. True when enqueued."""
    try:
        won = _r().set(REMEDIATE_COOLDOWN_KEY.format(platform=platform), str(int(time.time())),
                       nx=True, ex=REMEDIATE_COOLDOWN_S)
    except Exception:
        return False
    if not won:
        return False
    try:
        from app.tasks.video_tasks import ytdlp_remediate  # noqa: PLC0415
        ytdlp_remediate.apply_async(args=[[platform], reason[:200]], queue="light")
        return True
    except Exception:
        log.exception("platform_health: could not enqueue remediation for %s", platform)
        return False


def on_platform_broken(platform: str, reason: str) -> None:
    """A probe confirmed a failure, or extractor_broken spiked: degrade,
    reroute where we can, and try a newer yt-dlp."""
    mark_degraded(platform, reason)
    if platform in COBALT_ROUTE_PLATFORMS:
        set_prefer_cobalt(platform, True, reason)
    request_remediation(platform, reason)


# ── Scheduled check (replaces alerts.check_platform_fail_rates) ───────

def probe_confirms_failure(platform: str) -> bool:
    """Our own probe (not users' traffic) says the platform is down. Anyone
    can send dead links, so traffic alone may only ALERT; the actions that
    reach partners or change what runs need this. (Security review 19/10.)"""
    try:
        from app.core import platform_probe as pp  # noqa: PLC0415
        return (pp.get_probe_states().get(platform) or {}).get("status") == pp.FAILED
    except Exception:
        return False


_DISPLAY = {"twitter": "X"}


def display(platform: str) -> str:
    return _DISPLAY.get(platform, platform)


def check_platforms(counts: Optional[Dict[str, Dict[str, int]]] = None, *,
                    now: Optional[datetime] = None) -> List[dict]:
    """Every 5 min via run_all_health_checks. Returns what it found."""
    from app.core.alerts import send_admin_alert  # noqa: PLC0415
    if counts is None:
        counts = recent_counts(now=now)
    found = []
    for platform, c in sorted(counts.items()):
        v = verdict(c)
        if not v or platform in ("other", "unknown"):
            continue
        found.append({"platform": platform, "verdict": v, **c})
        pct = round(100 * c["system_err"] / max(1, c["total"] - c["user_err"]))
        if v == "broken":
            confirmed = probe_confirms_failure(platform)
            # wording: BA review
            send_admin_alert(
                "critical", f"{display(platform)}: có thể vừa đổi cấu trúc trang",
                f"{c['broken']}/{c['total']} lượt tải 2 giờ qua lỗi 'không đọc được trang'. "
                + ("Kiểm tra tự động cũng hỏng → hệ thống tự thử yt-dlp mới hơn (nếu chưa thử trong 6 giờ qua)"
                   + (" và tạm chuyển sang Cobalt." if platform in COBALT_ROUTE_PLATFORMS else ".")
                   + " Chưa cần làm gì."
                   if confirmed else
                   "Kiểm tra tự động vẫn tải được → chưa tự xử lý (có thể do link lỗi của người dùng). Chưa cần làm gì."),
                alert_key=f"health_broken_{platform}",
            )
            if confirmed:
                on_platform_broken(platform, f"extractor_broken {c['broken']}/{c['total']}")
        else:
            # wording: BA review
            send_admin_alert(
                v, f"{display(platform)}: {pct}% lượt tải lỗi do hệ thống",
                f"{c['system_err']}/{c['total']} lượt trong 2 giờ qua (không tính video bị xoá/riêng tư: "
                f"{c['user_err']}). Xem admin → Sức khoẻ nền tảng; nếu lỗi nhiều thì kiểm tra proxy và cookie.",
                alert_key=f"health_{v}_{platform}",
            )
            if v == "critical" and probe_confirms_failure(platform):
                mark_degraded(platform, f"system errors {pct}%")
    # Recovery from traffic: a degraded platform back under the warning line.
    for platform in list(degraded()):
        c = counts.get(platform)
        if c and c["total"] >= MIN_ATTEMPTS and verdict(c) is None:
            mark_recovered(platform)
    return found
