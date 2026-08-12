"""
Phase 14 — Admin Alert Engine
===============================
Threshold-based Telegram alerts for ops monitoring.
Called from Celery beat tasks — sync only.

Alert types:
  • platform_fail_rate  — platform error rate spiked
  • queue_depth         — Celery queue backlog high
  • disk_usage          — disk > threshold
  • no_workers          — no Celery workers detected
  • proxy_exhausted     — proxy credits low
  • daily_digest        — daily ops summary
"""

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.notifications import send_telegram_message_sync


def _classify_platform(url: str) -> str:
    """Lowercase platform slug from a URL (mirrors app.tasks.video_tasks._get_platform).
    download_jobs has no `platform` column — it must be derived from original_url."""
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "tiktok.com" in u: return "tiktok"
    if "instagram.com" in u: return "instagram"
    if "douyin.com" in u: return "douyin"
    if "threads.net" in u or "threads.com" in u: return "threads"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "reddit.com" in u: return "reddit"
    if "pinterest.com" in u or "pin.it" in u: return "pinterest"
    if "linkedin.com" in u: return "linkedin"
    return "other"

# ── Thresholds ────────────────────────────────────────────────────────
FAIL_RATE_WARNING   = 0.30   # 30% fail rate → warning
FAIL_RATE_CRITICAL  = 0.60   # 60% fail rate → critical
QUEUE_DEPTH_WARNING = 50
QUEUE_DEPTH_CRITICAL = 200
DISK_WARNING_PCT    = 0.75
DISK_CRITICAL_PCT   = 0.90
ALERT_COOLDOWN_S    = 1800   # 30 min between same alert type

# ── Cooldown store (in-process, resets on worker restart) ────────────
_last_alert: dict = {}

def _throttle(key: str) -> bool:
    """Return True if alert should be suppressed (cooldown not elapsed)."""
    now = time.time()
    last = _last_alert.get(key, 0)
    if now - last < ALERT_COOLDOWN_S:
        return True
    _last_alert[key] = now
    return False


def _vn_time() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%H:%M %d/%m")


def _emoji(level: str) -> str:
    return {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "📊")


def send_admin_alert(level: str, title: str, body: str, alert_key: str = "") -> None:
    """
    Send a leveled admin alert to Telegram.
    level: info | warning | critical
    alert_key: used for cooldown deduplication (empty = always send)
    """
    if alert_key and _throttle(alert_key):
        return
    e = _emoji(level)
    msg = f"{e} <b>[{level.upper()}] {title}</b>\n{body}\n<i>{_vn_time()} UTC+7</i>"
    send_telegram_message_sync(msg)


# ── Specific alert helpers ────────────────────────────────────────────

def check_platform_fail_rates(window_minutes: int = 30) -> None:
    """
    Pull recent download_jobs and alert if any platform has high fail rate.
    Called from Celery beat every 5 min.
    """
    try:
        from app.core.database import get_supabase_client
        sb = get_supabase_client()
        since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        rows = sb.table("download_jobs").select("original_url,status") \
            .gte("created_at", since).limit(2000).execute()

        counts: dict = {}
        for r in (rows.data or []):
            p = _classify_platform(r.get("original_url", ""))
            s = r.get("status", "")
            counts.setdefault(p, {"ok": 0, "fail": 0})
            if s == "success":
                counts[p]["ok"] += 1
            elif s in ("failed", "error"):
                counts[p]["fail"] += 1

        for platform, c in counts.items():
            total = c["ok"] + c["fail"]
            if total < 5:
                continue
            rate = c["fail"] / total
            if rate >= FAIL_RATE_CRITICAL:
                send_admin_alert(
                    "critical",
                    f"Platform Fail Rate: {platform}",
                    f"Lỗi: <b>{rate:.0%}</b> ({c['fail']}/{total} jobs, {window_minutes}m)\nCần kiểm tra ngay!",
                    alert_key=f"fail_critical_{platform}",
                )
            elif rate >= FAIL_RATE_WARNING:
                send_admin_alert(
                    "warning",
                    f"Platform Fail Rate: {platform}",
                    f"Lỗi: <b>{rate:.0%}</b> ({c['fail']}/{total} jobs, {window_minutes}m)",
                    alert_key=f"fail_warning_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_platform_fail_rates error: {e}")


def check_queue_depth() -> None:
    """Alert if Celery queue is backing up."""
    try:
        import redis as _redis
        rc = _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        depth = rc.llen("celery") + rc.llen("downloads")
        if depth >= QUEUE_DEPTH_CRITICAL:
            send_admin_alert(
                "critical",
                "Queue Depth Critical",
                f"Queue depth: <b>{depth}</b> jobs đang chờ\nCần thêm worker ngay!",
                alert_key="queue_critical",
            )
        elif depth >= QUEUE_DEPTH_WARNING:
            send_admin_alert(
                "warning",
                "Queue Depth High",
                f"Queue depth: <b>{depth}</b> jobs đang chờ",
                alert_key="queue_warning",
            )
    except Exception as e:
        print(f"[Alerts] check_queue_depth error: {e}")


def check_disk_usage() -> None:
    """Alert if disk is getting full."""
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        pct = used / total
        download_dir = os.getenv("DOWNLOAD_DIR", "/tmp/downloads")
        try:
            dt, du, df = shutil.disk_usage(download_dir)
            free_gb = df / (1024**3)
        except Exception:
            free_gb = free / (1024**3)

        if pct >= DISK_CRITICAL_PCT:
            send_admin_alert(
                "critical",
                "Disk Usage Critical",
                f"Disk: <b>{pct:.0%}</b> đã dùng — còn {free_gb:.1f} GB\nCần dọn dẹp ngay!",
                alert_key="disk_critical",
            )
        elif pct >= DISK_WARNING_PCT:
            send_admin_alert(
                "warning",
                "Disk Usage Warning",
                f"Disk: <b>{pct:.0%}</b> đã dùng — còn {free_gb:.1f} GB",
                alert_key="disk_warning",
            )
    except Exception as e:
        print(f"[Alerts] check_disk_usage error: {e}")


def check_worker_count() -> None:
    """Alert if no Celery workers are running."""
    try:
        from app.core.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        workers = inspect.active()
        if not workers:
            send_admin_alert(
                "critical",
                "No Celery Workers",
                "Không có worker nào đang chạy!\nMọi download đang bị treo trong queue.",
                alert_key="no_workers",
            )
    except Exception as e:
        print(f"[Alerts] check_worker_count error: {e}")


STALE_JOB_WARNING  = int(os.getenv("ALERT_STALE_JOB_WARNING",  "3"))
STALE_JOB_CRITICAL = int(os.getenv("ALERT_STALE_JOB_CRITICAL", "10"))
RETRY_STORM_PER_MIN = float(os.getenv("ALERT_RETRY_STORM_PER_MIN", "3.0"))
QUOTA_DENIAL_WARNING = int(os.getenv("ALERT_QUOTA_DENIAL_WARNING", "50"))


def check_stale_jobs() -> None:
    """Alert if too many jobs are stuck in the stale state."""
    try:
        from app.core.metrics import get_stale_job_count
        count = get_stale_job_count()
        if count < 0:
            return  # DB unavailable — skip
        if count >= STALE_JOB_CRITICAL:
            send_admin_alert(
                "critical",
                "Stale Jobs — Critical",
                f"<b>{count}</b> jobs mắc kẹt ở trạng thái 'stale'.\nScanner có thể bị lỗi hoặc không chạy.",
                alert_key="stale_critical",
            )
        elif count >= STALE_JOB_WARNING:
            send_admin_alert(
                "warning",
                "Stale Jobs",
                f"<b>{count}</b> jobs đang ở trạng thái 'stale'.\nKiểm tra recovery scanner.",
                alert_key="stale_warning",
            )
    except Exception as e:
        print(f"[Alerts] check_stale_jobs error: {e}")


def check_cookie_pool_health() -> None:
    """Alert if an authenticated platform has its entire cookie pool blocked."""
    try:
        from app.core.metrics import get_cookie_pool_health
        health = get_cookie_pool_health()
        for platform, h in health.items():
            if h["total"] > 0 and h["available"] == 0:
                blocked = h["blocked_hard"] + h["blocked_soft"]
                send_admin_alert(
                    "critical",
                    f"Cookie Pool Depleted — {platform}",
                    f"Tất cả <b>{blocked}</b> cookie của {platform} đang bị block!\n"
                    f"Cứng: {h['blocked_hard']} | Mềm: {h['blocked_soft']}\n"
                    "Cần thêm cookie mới hoặc đợi cooldown.",
                    alert_key=f"cookie_depleted_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_cookie_pool_health error: {e}")


def check_retry_storm(window_minutes: int = 10) -> None:
    """Alert if retry rate suggests a storm (many jobs looping in retry)."""
    try:
        from app.core.metrics import get_job_metrics
        metrics = get_job_metrics(window_minutes=window_minutes)
        retrying = metrics["by_event"].get("retrying", 0)
        per_min = retrying / window_minutes if window_minutes > 0 else 0
        if per_min >= RETRY_STORM_PER_MIN:
            send_admin_alert(
                "warning",
                "Retry Storm Detected",
                f"<b>{retrying}</b> retry events trong {window_minutes}m "
                f"({per_min:.1f}/min).\nCó thể có job lặp retry vô hạn.",
                alert_key="retry_storm",
            )
    except Exception as e:
        print(f"[Alerts] check_retry_storm error: {e}")


def check_quota_denial_rate(window_minutes: int = 30) -> None:
    """Alert if quota/rate-limit denial rate is unusually high."""
    try:
        from app.core.metrics import get_quota_denial_count
        count = get_quota_denial_count(window_minutes=window_minutes)
        if count >= QUOTA_DENIAL_WARNING:
            send_admin_alert(
                "warning",
                "High Quota Denial Rate",
                f"<b>{count}</b> lần từ chối quota trong {window_minutes}m.\n"
                "Có thể đang bị lạm dụng hoặc cần tăng giới hạn.",
                alert_key="quota_denial_high",
            )
    except Exception as e:
        print(f"[Alerts] check_quota_denial_rate error: {e}")


def run_all_health_checks() -> None:
    """Master health check — called by Celery beat every 5 min."""
    check_disk_usage()
    check_queue_depth()
    check_worker_count()
    check_platform_fail_rates(window_minutes=30)
    check_stale_jobs()
    check_cookie_pool_health()
    check_retry_storm()
    check_quota_denial_rate()
    # Phase 27 signals
    check_p27_cookie_pool_low()
    check_p27_block_surge()
    check_p27_delayed_backlog()
    check_p27_lane_stuck_degraded()
    check_p27_no_success_healthy()


# ── Phase 27 alert signals ────────────────────────────────────────────────────

P27_COOKIE_POOL_LOW_THRESHOLD = int(os.getenv("ALERT_P27_COOKIE_LOW", "2"))
P27_SOFT_BLOCK_SURGE_RATE     = float(os.getenv("ALERT_P27_SOFT_BLOCK_SURGE", "0.40"))
P27_HARD_BLOCK_SURGE_RATE     = float(os.getenv("ALERT_P27_HARD_BLOCK_SURGE", "0.25"))
P27_DELAYED_BACKLOG_DEFAULT   = int(os.getenv("ALERT_P27_DELAYED_BACKLOG", "30"))
P27_LANE_DEGRADED_MINUTES     = int(os.getenv("ALERT_P27_LANE_DEGRADED_MIN", "30"))


def check_p27_cookie_pool_low() -> None:
    """Alert when any platform has fewer than threshold healthy cookies."""
    try:
        from app.core.cookie_pool import get_pool_status
        healths = get_pool_status()
        for platform, h in healths.items():
            healthy = h.get("healthy", 0)
            total   = h.get("total", 0)
            if total > 0 and healthy < P27_COOKIE_POOL_LOW_THRESHOLD:
                send_admin_alert(
                    "warning",
                    f"Cookie Pool Low — {platform}",
                    f"Chỉ còn <b>{healthy}/{total}</b> cookie khỏe mạnh cho {platform}.\n"
                    f"Ngưỡng cảnh báo: {P27_COOKIE_POOL_LOW_THRESHOLD}.\n"
                    "Hành động: Thêm cookie mới hoặc kiểm tra xem cookie có bị block không.",
                    alert_key=f"p27_cookie_low_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_p27_cookie_pool_low error: {e}")


def check_p27_block_surge(window_minutes: int = 15) -> None:
    """Alert if soft/hard block rate spikes above threshold on any platform."""
    try:
        from app.core.obs_recorder import get_platform_failure_breakdown
        platforms = ["instagram", "twitter", "reddit", "facebook", "tiktok", "youtube"]
        for platform in platforms:
            breakdown = get_platform_failure_breakdown(platform, window_minutes)
            total = breakdown.get("total", 0)
            if total < 5:
                continue
            soft_rate = breakdown.get("soft_block", 0) / total
            hard_rate = breakdown.get("hard_block", 0) / total
            if hard_rate >= P27_HARD_BLOCK_SURGE_RATE:
                send_admin_alert(
                    "critical",
                    f"Hard Block Surge — {platform}",
                    f"<b>{hard_rate:.0%}</b> requests bị hard block trên {platform} trong {window_minutes}m.\n"
                    f"Total: {total} | Hard: {breakdown.get('hard_block', 0)}\n"
                    "Có thể IP hoặc cookie bị ban vĩnh viễn.",
                    alert_key=f"p27_hard_block_{platform}",
                )
            elif soft_rate >= P27_SOFT_BLOCK_SURGE_RATE:
                send_admin_alert(
                    "warning",
                    f"Soft Block Surge — {platform}",
                    f"<b>{soft_rate:.0%}</b> requests bị soft block trên {platform} trong {window_minutes}m.\n"
                    f"Total: {total} | Soft: {breakdown.get('soft_block', 0)}\n"
                    "Xem xét giảm tốc độ hoặc xoay cookie.",
                    alert_key=f"p27_soft_block_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_p27_block_surge error: {e}")


def check_p27_delayed_backlog() -> None:
    """Alert if delayed queue depth exceeds platform threshold."""
    try:
        from app.core.delayed_queue import get_all_queue_depths
        from app.core.platform_policy import get_platform_policy
        depths = get_all_queue_depths()
        for platform, depth in depths.items():
            try:
                threshold = get_platform_policy(platform).delayed_backlog_alert
            except Exception:
                threshold = P27_DELAYED_BACKLOG_DEFAULT
            if depth >= threshold:
                send_admin_alert(
                    "warning",
                    f"Delayed Queue Backlog — {platform}",
                    f"<b>{depth}</b> jobs đang chờ trong delayed queue của {platform}.\n"
                    f"Ngưỡng cảnh báo: {threshold}.\n"
                    "Kiểm tra lane state và xem xét tăng wave size hoặc thêm cookie.",
                    alert_key=f"p27_delayed_backlog_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_p27_delayed_backlog error: {e}")


_LANE_STATE_SINCE_KEY = "vidgrab:p27:lane_state_since:{platform}"


def _lane_state_since(platform: str, state: str) -> float:
    """
    Track (in Redis) the epoch time `platform` entered its current lane state.
    lane_observer recomputes state fresh on every read and keeps no history, so
    this is the only place that remembers "how long has it been like this" —
    needed to alert only once a degraded state has actually persisted.
    Resets whenever the state changes.
    """
    from app.core.redis_client import get_redis
    rc = get_redis()
    key = _LANE_STATE_SINCE_KEY.format(platform=platform)
    now = time.time()
    try:
        raw = rc.get(key)
        if raw:
            stored_state, since = (raw.decode() if isinstance(raw, bytes) else raw).split("|", 1)
            if stored_state == state:
                return float(since)
        rc.setex(key, 3600, f"{state}|{now}")
    except Exception:
        pass
    return now


def check_p27_lane_stuck_degraded() -> None:
    """Alert if any platform has been in degraded/paused/disabled state > threshold minutes."""
    try:
        from app.core.lane_observer import observe_all_platforms
        degraded_states = {"degraded", "paused", "disabled"}
        now = time.time()
        for obs in observe_all_platforms():
            platform = obs["platform"]
            state = obs.get("laneState", "healthy")
            since_ts = _lane_state_since(platform, state)
            if state not in degraded_states:
                continue
            minutes_stuck = (now - since_ts) / 60
            if minutes_stuck >= P27_LANE_DEGRADED_MINUTES:
                send_admin_alert(
                    "warning",
                    f"Platform Stuck — {platform} ({state})",
                    f"{platform} đã ở trạng thái <b>{state}</b> trong <b>{minutes_stuck:.0f} phút</b>.\n"
                    f"Ngưỡng: {P27_LANE_DEGRADED_MINUTES}m.\n"
                    "Xem xét dùng /policy/platform/{platform}/mode để override hoặc thêm cookie.",
                    alert_key=f"p27_lane_stuck_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_p27_lane_stuck_degraded error: {e}")


def check_p27_no_success_healthy() -> None:
    """Alert if a healthy-lane platform has 0 successes in last 20 min (silent failure)."""
    try:
        from app.core.lane_observer import observe_all_platforms
        from app.core.obs_recorder import get_platform_failure_breakdown
        for obs in observe_all_platforms():
            platform = obs["platform"]
            if obs.get("laneState", "") != "healthy":
                continue
            breakdown = get_platform_failure_breakdown(platform, window_minutes=20)
            if breakdown.get("total", 0) < 3:
                continue  # not enough volume to tell
            if breakdown.get("success", 0) == 0:
                send_admin_alert(
                    "critical",
                    f"Silent Failure — {platform} (healthy but 0 success)",
                    f"{platform} được đánh giá <b>healthy</b> nhưng không có lần tải thành công nào trong 20 phút.\n"
                    f"Total attempts: {breakdown.get('total', 0)} | Errors: {breakdown.get('error', 0)}\n"
                    "Có thể lane observer đang lag hoặc extractor bị lỗi silently.",
                    alert_key=f"p27_silent_failure_{platform}",
                )
    except Exception as e:
        print(f"[Alerts] check_p27_no_success_healthy error: {e}")
