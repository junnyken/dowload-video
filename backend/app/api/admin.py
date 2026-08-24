"""
Admin API Routes — VidGrab Administration
==========================================
Endpoints for admin dashboard:
  GET  /stats          — Overview stats (downloads, users, credits)
  GET  /analytics      — 7-day/30-day trend data for charts
  GET  /active-jobs    — Real-time active processing jobs
  POST /update-user    — Toggle user plan (free/pro)
  POST /send-test-notification — Send test Telegram message
"""

import glob
import json
import os
import base64
import secrets
import datetime as _dt
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from app.core.database import get_supabase_client
from app.core.audit import log_admin_action, log_access_denied
from app.core.client_ip import get_client_ip
from datetime import datetime, timezone, timedelta

router = APIRouter()

# ── Server-side admin auth ───────────────────────────────────────────
_ADMIN_TOKEN_HEADER = APIKeyHeader(name="X-Admin-Token", auto_error=False)
_BEARER_HEADER      = HTTPBearer(auto_error=False)

# ADMIN_PASSWORD must be set explicitly — no insecure default.
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Optional comma-separated IP allowlist for admin endpoints.
# e.g. ADMIN_ALLOWED_IPS="203.0.113.10,203.0.113.11"
# When empty (default), all IPs are allowed (backward-compatible).
_ADMIN_ALLOWED_IPS: set[str] = {
    ip.strip()
    for ip in os.getenv("ADMIN_ALLOWED_IPS", "").split(",")
    if ip.strip()
}

_SESSION_TTL        = 8 * 60 * 60        # 8-hour session TTL (seconds)
_LOCKOUT_TTL        = 15 * 60            # 15-minute lockout after 3 bad attempts
_MAX_ATTEMPTS       = 3                  # attempts before lockout
_ATTEMPT_WINDOW     = 5 * 60            # attempt counter TTL (seconds)


def _redis():
    """Lazy-import redis connection to avoid circular import at module level."""
    from app.core.redis_client import get_redis
    return get_redis()


def _session_key(token: str) -> str:
    return f"admin:session:{token}"


def _attempt_key(ip: str) -> str:
    return f"admin:attempts:{ip}"


def _lockout_key(ip: str) -> str:
    return f"admin:lockout:{ip}"


async def verify_admin(
    request: Request,
    legacy_token: Optional[str] = Depends(_ADMIN_TOKEN_HEADER),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_BEARER_HEADER),
):
    """
    Authenticate admin requests.
    Accepts two methods (in priority order):
      1. Bearer session token — issued by POST /admin/login (preferred)
      2. X-Admin-Token header — legacy, kept for backward compat

    Also enforces ADMIN_ALLOWED_IPS if configured and logs denied attempts.
    """
    r = _redis()
    ip = get_client_ip(request)

    # ── IP allowlist check (when configured) ────────────────────────
    if _ADMIN_ALLOWED_IPS and ip not in _ADMIN_ALLOWED_IPS:
        log_access_denied(request, "/admin", reason="ip_not_allowed", metadata={"ip": ip})
        raise HTTPException(status_code=403, detail="Forbidden")

    # ── Bearer session token (preferred) ────────────────────────────
    if bearer and bearer.credentials:
        # Sessions live in Redis. If the store is unreachable we cannot confirm
        # the token, so treat it as unauthenticated and fall through to a clean
        # 401 — an auth check must fail closed, and it must not surface as a 500
        # that looks like a server fault rather than a refusal.
        try:
            sess = r.get(_session_key(bearer.credentials))
        except Exception:
            sess = None
        if sess:
            try:
                r.expire(_session_key(bearer.credentials), _SESSION_TTL)
            except Exception:
                pass
            return

    # ── Legacy X-Admin-Token (kept for backward compat) ─────────────
    if legacy_token:
        if _ADMIN_PASSWORD and legacy_token == _ADMIN_PASSWORD:
            return
        # Wrong token — log and deny
        log_access_denied(request, "/admin", reason="wrong_legacy_token", metadata={"ip": ip})
        raise HTTPException(status_code=401, detail="Unauthorized")

    log_access_denied(request, "/admin", reason="no_credentials", metadata={"ip": ip})
    raise HTTPException(status_code=401, detail="Unauthorized")


class AdminLoginRequest(BaseModel):
    password: str


@router.post("/login")
async def admin_login(payload: AdminLoginRequest, request: Request):
    """
    Exchange admin password for a session token.
    Enforces per-IP lockout after 3 failed attempts in 5 minutes.
    """
    if not _ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password not configured")

    r = _redis()
    ip = get_client_ip(request)

    # Check lockout
    if r.exists(_lockout_key(ip)):
        ttl = r.ttl(_lockout_key(ip))
        log_access_denied(request, "/admin/login", reason="ip_locked_out", metadata={"ip": ip})
        raise HTTPException(
            status_code=429,
            detail={"error": "locked_out", "retry_after_seconds": max(ttl, 1)},
        )

    if payload.password != _ADMIN_PASSWORD:
        # Increment attempt counter
        attempts = r.incr(_attempt_key(ip))
        r.expire(_attempt_key(ip), _ATTEMPT_WINDOW)
        if attempts >= _MAX_ATTEMPTS:
            r.setex(_lockout_key(ip), _LOCKOUT_TTL, "1")
            r.delete(_attempt_key(ip))
            log_access_denied(request, "/admin/login", reason="locked_out_after_attempts", metadata={"ip": ip, "attempts": attempts})
            raise HTTPException(
                status_code=429,
                detail={"error": "locked_out", "retry_after_seconds": _LOCKOUT_TTL},
            )
        log_access_denied(request, "/admin/login", reason="wrong_password", metadata={"ip": ip, "attempts": attempts})
        raise HTTPException(status_code=401, detail={"error": "wrong_password", "attempts_left": _MAX_ATTEMPTS - attempts})

    # Password correct — clear attempts, issue session token
    r.delete(_attempt_key(ip))
    token = secrets.token_urlsafe(32)
    r.setex(_session_key(token), _SESSION_TTL, "1")

    log_admin_action(request, "admin.login.success", metadata={"ip": ip})
    return {"session_token": token, "expires_in": _SESSION_TTL}


class UpdateUserRequest(BaseModel):
    user_id: str
    plan: str  # 'free' or 'pro'


# ═════════════════════════════════════════════════════════════════════
# GET /stats — Overview Dashboard Data
# ═════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_admin_stats(_=Depends(verify_admin)):
    supabase = get_supabase_client()
    try:
        # Sum of downloads_today
        usage_res = supabase.table("user_usage").select("downloads_today").execute()
        total_downloads = sum(record.get("downloads_today", 0) for record in usage_res.data) if usage_res.data else 0
        total_users = len(usage_res.data) if usage_res.data else 0
        
        # Provider credits
        providers = {}
        try:
            provider_res = supabase.table("provider_status").select("*").execute()
            if provider_res.data:
                providers = {p["provider_name"]: p["remaining_credits"] for p in provider_res.data}
        except Exception:
            pass
        
        # Recent failed jobs
        failed_jobs_res = supabase.table("download_jobs").select("*").eq("status", "failed").order("created_at", desc=True).limit(10).execute()
        
        # Recent users
        recent_users_res = supabase.table("user_usage").select("*").order("last_reset_at", desc=True).limit(20).execute()
        
        # Real-time ScraperAPI credits — active key only (for legacy providers dict)
        try:
            from app.core.scraperapi_pool import fetch_credits, get_active_key
            _active_key = get_active_key()
            if _active_key:
                _credits = fetch_credits(_active_key, use_cache=True)
                if _credits is not None:
                    providers["ScraperAPI"] = _credits
                    try:
                        supabase.table("provider_status").upsert({
                            "provider_name": "ScraperAPI",
                            "remaining_credits": _credits
                        }).execute()
                    except Exception:
                        pass
                    try:
                        from app.core.notifications import notify_credits_low, CREDITS_WARNING_THRESHOLD
                        if _credits < CREDITS_WARNING_THRESHOLD:
                            await notify_credits_low("ScraperAPI", _credits)
                    except Exception:
                        pass
        except Exception as e:
            print(f"ScraperAPI fetch error: {e}")
                
        total_users = len(usage_res.data) if usage_res.data else 0
        return {
            "success": True,
            "total_downloads_today": total_downloads,
            "total_users": total_users,
            "providers": providers,
            "failed_jobs": failed_jobs_res.data if failed_jobs_res.data else [],
            "recent_users": recent_users_res.data if recent_users_res.data else [],
        }
    except Exception as e:
        print(f"Admin Stats Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# GET /analytics — Trend Data for Charts (7-day / 30-day)
# ═════════════════════════════════════════════════════════════════════

@router.get("/analytics")
async def get_admin_analytics(days: int = 7, _=Depends(verify_admin)):
    """
    Returns daily aggregated data for the admin charts.
    Query parameter: days (default: 7, max: 30)

    Response:
    {
        "daily_stats": [
            {"date": "2026-04-25", "total": 45, "success": 40, "failed": 5},
            ...
        ],
        "platform_stats": [
            {"platform": "TikTok", "count": 120},
            ...
        ],
        "summary": {
            "total_jobs": 300,
            "total_success": 270,
            "total_failed": 30,
            "success_rate": 90.0,
            "avg_daily": 42.9
        }
    }
    """
    days = min(max(days, 1), 30)  # Clamp between 1-30
    supabase = get_supabase_client()

    try:
        # Calculate date range
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_iso = start_date.isoformat()

        # Fetch all jobs in date range
        jobs_res = (
            supabase.table("download_jobs")
            .select("status, original_url, created_at")
            .gte("created_at", start_iso)
            .order("created_at", desc=False)
            .limit(5000)
            .execute()
        )

        jobs = jobs_res.data if jobs_res.data else []

        # ── Aggregate daily stats ────────────────────────
        daily_map: Dict[str, Dict[str, int]] = {}
        platform_map: Dict[str, int] = {}

        for job in jobs:
            created_at = job.get("created_at", "")
            status = job.get("status", "")
            url = job.get("original_url", "")

            # Parse date (extract YYYY-MM-DD)
            date_str = created_at[:10] if created_at else "unknown"

            if date_str not in daily_map:
                daily_map[date_str] = {"total": 0, "success": 0, "failed": 0, "processing": 0, "pending": 0}

            daily_map[date_str]["total"] += 1
            if status in daily_map[date_str]:
                daily_map[date_str][status] += 1

            # ── Platform classification ──────────────────
            platform = _classify_platform(url)
            platform_map[platform] = platform_map.get(platform, 0) + 1

        # Fill in missing dates (so chart has no gaps)
        daily_stats = []
        for i in range(days):
            date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            stats = daily_map.get(date, {"total": 0, "success": 0, "failed": 0, "processing": 0, "pending": 0})
            daily_stats.append({"date": date, **stats})

        # Sort platform stats by count descending
        platform_stats = sorted(
            [{"platform": k, "count": v} for k, v in platform_map.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        # ── Summary calculations ─────────────────────────
        total_jobs = sum(d["total"] for d in daily_stats)
        total_success = sum(d["success"] for d in daily_stats)
        total_failed = sum(d["failed"] for d in daily_stats)
        success_rate = round(total_success / total_jobs * 100, 1) if total_jobs > 0 else 100.0
        avg_daily = round(total_jobs / days, 1)

        return {
            "success": True,
            "days": days,
            "daily_stats": daily_stats,
            "platform_stats": platform_stats,
            "summary": {
                "total_jobs": total_jobs,
                "total_success": total_success,
                "total_failed": total_failed,
                "success_rate": success_rate,
                "avg_daily": avg_daily,
            },
        }

    except Exception as e:
        print(f"Admin Analytics Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# GET /active-jobs — Real-time Active Jobs Monitor
# ═════════════════════════════════════════════════════════════════════

@router.get("/active-jobs")
async def get_active_jobs(_=Depends(verify_admin)):
    """Get currently processing and pending jobs for real-time monitoring."""
    supabase = get_supabase_client()

    try:
        # Processing jobs
        processing_res = (
            supabase.table("download_jobs")
            .select("id, batch_id, original_url, status, created_at")
            .eq("status", "processing")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        # Pending jobs
        pending_res = (
            supabase.table("download_jobs")
            .select("id, batch_id, original_url, status, created_at")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        processing = processing_res.data if processing_res.data else []
        pending = pending_res.data if pending_res.data else []

        return {
            "success": True,
            "processing": processing,
            "pending": pending,
            "processing_count": len(processing),
            "pending_count": len(pending),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# POST /send-test-notification — Test Telegram
# ═════════════════════════════════════════════════════════════════════

@router.post("/send-test-notification")
async def send_test_notification(request: Request, _=Depends(verify_admin)):
    """Send a test notification to Telegram to verify configuration."""
    try:
        from app.core.notifications import send_telegram_message

        result = await send_telegram_message(
            "🧪 <b>Test Notification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Telegram notification đang hoạt động!\n"
            "📡 Gửi từ: VidGrab Admin Dashboard"
        )

        log_admin_action(request, "admin.notification.test_sent", metadata={"success": result})
        return {
            "success": result,
            "message": "Notification sent successfully!" if result else "Failed to send. Check bot token and chat ID.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# POST /update-user — Toggle User Plan
# ═════════════════════════════════════════════════════════════════════

@router.post("/update-user")
async def update_user(req: UpdateUserRequest, request: Request, _=Depends(verify_admin)):
    """
    Toggle user tier.  Writes to BOTH tables:
      - profiles.tier        — source of truth for quota checks
      - user_usage.plan      — denormalized copy for stats queries
    Also clears billing fields when downgrading to free.
    """
    supabase = get_supabase_client()
    try:
        if req.plan not in ("free", "pro"):
            raise HTTPException(status_code=400, detail="Invalid plan — must be 'free' or 'pro'")

        # Primary: update profiles.tier (this is what check_user_quota reads)
        profile_update: Dict[str, Any] = {"tier": req.plan}
        if req.plan == "free":
            profile_update.update({
                "billing_status":          "none",
                "subscription_expiry":     None,
                "stripe_subscription_id":  None,
            })
        else:
            profile_update["billing_status"] = "active"

        p_res = supabase.table("profiles").update(profile_update).eq("id", req.user_id).execute()

        # Secondary: keep user_usage.plan in sync (used by admin stats queries)
        u_res = supabase.table("user_usage").update({"plan": req.plan}).eq("user_id", req.user_id).execute()
        if not u_res.data:
            supabase.table("user_usage").insert({
                "user_id":       req.user_id,
                "plan":          req.plan,
                "downloads_today":       0,
                "downloads_this_month":  0,
                "last_reset_at": datetime.now(timezone.utc).isoformat(),
            }).execute()

        updated = bool(p_res.data)
        if not updated:
            # profiles is the source of truth check_user_quota reads. If that
            # write matched nothing, the plan did not change — don't report a
            # success the database never performed.
            return {
                "success": False,
                "updated": False,
                "message": f"Không đổi được gói cho {req.user_id} — không tìm thấy hồ sơ.",
            }
        log_admin_action(
            request, "admin.user.tier_changed",
            resource_type="user", resource_id=req.user_id,
            metadata={"new_tier": req.plan},
        )
        return {
            "success": True,
            "updated": updated,
            "message": f"User {req.user_id} → {req.plan}",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# POST /reset-user-quota — Admin reset daily quota for one user
# ═════════════════════════════════════════════════════════════════════

class ResetQuotaRequest(BaseModel):
    user_id: str


@router.post("/reset-user-quota")
async def reset_user_quota(req: ResetQuotaRequest, request: Request, _=Depends(verify_admin)):
    """Reset downloads_today to 0 for a single user (manual quota reset)."""
    supabase = get_supabase_client()
    try:
        supabase.table("user_usage").update({
            "downloads_today": 0,
            "last_reset_at":   datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", req.user_id).execute()
        log_admin_action(
            request, "admin.user.quota_reset",
            resource_type="user", resource_id=req.user_id,
        )
        return {"success": True, "message": f"Quota reset for {req.user_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# GET /errors — Error Monitor (Tab 2)
# ═════════════════════════════════════════════════════════════════════

@router.get("/errors")
async def get_error_monitor(_=Depends(verify_admin)):
    """
    Detailed error analysis:
    - Recent 50 failed jobs
    - Error pattern grouping (timeout / private / 403 / captcha / etc.)
    - Per-platform failure rates
    """
    supabase = get_supabase_client()
    try:
        # Recent 50 failed jobs
        failed_res = (
            supabase.table("download_jobs")
            .select("id, original_url, error_message, created_at")
            .eq("status", "failed")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        failed_jobs = failed_res.data or []

        # All jobs last 24h for failure rate calculation
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent_res = (
            supabase.table("download_jobs")
            .select("status, original_url, error_message")
            .gte("created_at", since)
            .neq("original_url", "batch_zip")
            .limit(2000)
            .execute()
        )
        recent = recent_res.data or []

        # Error pattern grouping
        pattern_map: Dict[str, int] = {}
        platform_fail: Dict[str, Dict[str, int]] = {}

        for job in recent:
            platform = _classify_platform(job.get("original_url", ""))
            if platform not in platform_fail:
                platform_fail[platform] = {"total": 0, "failed": 0}
            platform_fail[platform]["total"] += 1
            if job.get("status") == "failed":
                platform_fail[platform]["failed"] += 1

                msg = (job.get("error_message") or "").lower()
                if "timeout" in msg or "quá thời gian" in msg:
                    key = "⏱ Timeout / Captcha"
                elif "private" in msg or "riêng tư" in msg:
                    key = "🔒 Video riêng tư"
                elif "not found" in msg or "không tồn tại" in msg or "404" in msg:
                    key = "🚫 Video đã xóa / 404"
                elif "403" in msg or "forbidden" in msg or "bị chặn" in msg:
                    key = "🛡 IP bị block / 403"
                elif "sabr" in msg or "cobalt" in msg:
                    key = "🎬 YouTube SABR"
                elif "captcha" in msg:
                    key = "🤖 Captcha"
                elif "extract" in msg or "trích xuất" in msg:
                    key = "❌ Extract thất bại"
                else:
                    key = "❓ Lỗi khác"
                pattern_map[key] = pattern_map.get(key, 0) + 1

        # Build platform failure rate list
        platform_rates = []
        for platform, counts in platform_fail.items():
            if platform in ("ZIP", "Other") or counts["total"] == 0:
                continue
            rate = round(counts["failed"] / counts["total"] * 100, 1)
            platform_rates.append({
                "platform": platform,
                "total": counts["total"],
                "failed": counts["failed"],
                "fail_rate": rate,
            })
        platform_rates.sort(key=lambda x: x["fail_rate"], reverse=True)

        # Sort error patterns
        error_patterns = sorted(
            [{"pattern": k, "count": v} for k, v in pattern_map.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        total_24h = len(recent)
        total_failed_24h = sum(1 for j in recent if j.get("status") == "failed")

        return {
            "success": True,
            "recent_errors": failed_jobs,
            "error_patterns": error_patterns,
            "platform_fail_rates": platform_rates,
            "summary_24h": {
                "total": total_24h,
                "failed": total_failed_24h,
                "fail_rate": round(total_failed_24h / total_24h * 100, 1) if total_24h > 0 else 0,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# GET /users — User Analytics (Tab 3)
# ═════════════════════════════════════════════════════════════════════

@router.get("/users")
async def get_user_analytics(_=Depends(verify_admin)):
    """
    User behavior analysis:
    - Top IPs by download count (abuse detection)
    - Batch size distribution
    - Users flagged for high usage
    """
    supabase = get_supabase_client()
    try:
        # All user usage
        users_res = supabase.table("user_usage").select("*").order("downloads_today", desc=True).limit(100).execute()
        users = users_res.data or []

        # Batch jobs from last 48h — group by batch_id to get batch sizes
        since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        batch_res = (
            supabase.table("download_jobs")
            .select("batch_id, original_url")
            .gte("created_at", since)
            .neq("original_url", "batch_zip")
            .limit(5000)
            .execute()
        )
        batch_jobs = batch_res.data or []

        # Count jobs per batch
        batch_counts: Dict[str, int] = {}
        for job in batch_jobs:
            bid = job.get("batch_id", "")
            if bid:
                batch_counts[bid] = batch_counts.get(bid, 0) + 1

        # Batch size distribution buckets
        dist = {"1-5": 0, "6-20": 0, "21-50": 0, "51-200": 0, "200+": 0}
        for count in batch_counts.values():
            if count <= 5:
                dist["1-5"] += 1
            elif count <= 20:
                dist["6-20"] += 1
            elif count <= 50:
                dist["21-50"] += 1
            elif count <= 200:
                dist["51-200"] += 1
            else:
                dist["200+"] += 1

        # Flag heavy users (>= 50 downloads today)
        ABUSE_THRESHOLD = 50
        flagged = [u for u in users if (u.get("downloads_today") or 0) >= ABUSE_THRESHOLD]

        batch_distribution = [{"range": k, "count": v} for k, v in dist.items()]

        return {
            "success": True,
            "top_users": users[:30],
            "flagged_users": flagged,
            "batch_distribution": batch_distribution,
            "total_users": len(users),
            "total_batches_48h": len(batch_counts),
            "abuse_threshold": ABUSE_THRESHOLD,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# GET /users/signups — Daily new signup trend
# ═════════════════════════════════════════════════════════════════════

@router.get("/accounts")
async def list_accounts(
    q: str = "",
    tier: str = "",
    limit: int = 50,
    offset: int = 0,
    _=Depends(verify_admin),
):
    """
    Registered accounts, newest first — the roster behind the signup counts.

    /users reads user_usage, so it only ever showed people who had already
    downloaded something, keyed by an opaque user_id. /users/signups counts
    registrations per day but lists nobody. Neither could answer "who signed up,
    and put this one on Pro", which is the whole point of having the page.

    q       — substring match on email
    tier    — exact tier filter
    """
    supabase = get_supabase_client()
    try:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        query = (
            supabase.table("profiles")
            .select("id, email, display_name, tier, billing_status, plan_name, created_at",
                    count="exact")
            .order("created_at", desc=True)
        )
        if q.strip():
            query = query.ilike("email", f"%{q.strip()}%")
        if tier.strip():
            query = query.eq("tier", tier.strip().lower())

        res = query.range(offset, offset + limit - 1).execute()
        rows = res.data or []
        total = res.count if getattr(res, "count", None) is not None else len(rows)

        # Daily usage lives in a separate table; fold it in so the operator can
        # see consumption next to the tier they are about to change.
        usage_by_id: Dict[str, int] = {}
        ids = [r["id"] for r in rows if r.get("id")]
        if ids:
            try:
                u = (supabase.table("user_usage")
                     .select("user_id, downloads_today")
                     .in_("user_id", ids).execute())
                usage_by_id = {x["user_id"]: x.get("downloads_today", 0) for x in (u.data or [])}
            except Exception:
                pass

        from app.core.quotas import TIER_PERMISSIONS
        accounts = [{
            "user_id":         r.get("id"),
            "email":           r.get("email"),
            "display_name":    r.get("display_name"),
            "tier":            (r.get("tier") or "free"),
            "billing_status":  r.get("billing_status"),
            "plan_name":       r.get("plan_name"),
            "created_at":      r.get("created_at"),
            "downloads_today": usage_by_id.get(r.get("id"), 0),
            "daily_limit":     TIER_PERMISSIONS.get(
                                   (r.get("tier") or "free"), TIER_PERMISSIONS["free"]
                               )["daily_limit"],
        } for r in rows]

        return {
            "success":       True,
            "accounts":      accounts,
            "total":         total,
            "limit":         limit,
            "offset":        offset,
            "available_tiers": sorted(TIER_PERMISSIONS),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "accounts": [], "total": 0}


@router.get("/users/signups")
async def get_user_signups(days: int = 30, _=Depends(verify_admin)):
    """
    New registrations per day from the profiles table.
    Returns daily_signups[], total_period, today, tier_breakdown.
    """
    supabase = get_supabase_client()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        res = (
            supabase.table("profiles")
            .select("created_at, tier")
            .gte("created_at", since)
            .order("created_at", desc=False)
            .limit(10000)
            .execute()
        )
        rows = res.data or []

        # Group by UTC date
        from collections import defaultdict
        daily: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "free": 0, "pro": 0, "enterprise": 0, "team": 0})
        tier_total: Dict[str, int] = defaultdict(int)

        for row in rows:
            ts = row.get("created_at", "")
            tier = (row.get("tier") or "free").lower()
            date_str = ts[:10] if ts else ""
            if not date_str:
                continue
            daily[date_str]["total"] += 1
            daily[date_str][tier] = daily[date_str].get(tier, 0) + 1
            tier_total[tier] += 1

        # Fill every date in range with 0 if missing
        today_utc = datetime.now(timezone.utc).date()
        all_dates = [(today_utc - timedelta(days=i)) for i in range(days - 1, -1, -1)]
        daily_signups = []
        for d in all_dates:
            key = d.isoformat()
            entry = daily.get(key, {"total": 0, "free": 0, "pro": 0, "enterprise": 0})
            daily_signups.append({
                "date":       key,
                "total":      entry.get("total", 0),
                "free":       entry.get("free", 0),
                "pro":        entry.get("pro", 0),
                "enterprise": entry.get("enterprise", 0),
            })

        today_count = daily.get(today_utc.isoformat(), {}).get("total", 0)

        return {
            "success":        True,
            "days":           days,
            "daily_signups":  daily_signups,
            "total_period":   len(rows),
            "today":          today_count,
            "tier_breakdown": dict(tier_total),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# POST /backup/run — Run the database backup now
# ═════════════════════════════════════════════════════════════════════

@router.post("/backup/run")
async def run_backup_now(_=Depends(verify_admin)):
    """
    Run the nightly database backup immediately and return what it did.

    The scheduled run is once a day at 19:00 UTC, which is a long time to wait
    to find out whether a destination was configured correctly — and a backup
    nobody has ever watched succeed is a backup nobody should trust. This gives
    an operator the answer in seconds: how many rows and tables were captured,
    how large the file is, whether each destination accepted it, and any
    warnings the run produced.

    Runs inline rather than dispatching to Celery, so the caller gets the real
    result instead of a task id and a shrug.
    """
    from app.tasks.backup_tasks import backup_database_daily
    return backup_database_daily()


# ═════════════════════════════════════════════════════════════════════
# POST /po-token/test — Test bgutil-pot + PO token cache
# ═════════════════════════════════════════════════════════════════════

@router.post("/po-token/test")
async def test_po_token(_=Depends(verify_admin)):
    """Test bgutil-pot connectivity and force-refresh PO token cache."""
    import os, httpx
    from app.core.po_token_cache import get_po_token, get_cache_ttl, refresh_po_token, invalidate_po_token

    result: Dict[str, Any] = {"success": True}

    # 1. Current cache state
    cached = get_po_token.__wrapped__() if hasattr(get_po_token, '__wrapped__') else None
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        raw = rc.get("youtube:po_token")
        cached_token = raw.decode() if isinstance(raw, bytes) else raw
        ttl = rc.ttl("youtube:po_token")
    except Exception as e:
        cached_token = None
        ttl = -1
    result["cache"] = {"token_present": bool(cached_token), "token_prefix": (cached_token[:16] + "...") if cached_token else None, "ttl_seconds": ttl}

    # 2. Ping each bgutil-pot instance AND call /get_pot to see raw response
    bgutil_urls = [u.strip() for u in os.getenv("BGUTIL_POT_URL", "").split(",") if u.strip()]
    instances = []
    for url in bgutil_urls:
        info: Dict[str, Any] = {"url": url}
        try:
            r = httpx.get(url, timeout=10.0)
            info["reachable"] = True
            info["status"] = r.status_code
        except Exception as e:
            info["reachable"] = False
            info["error"] = str(e)[:100]
            instances.append(info)
            continue
        # Call /get_pot to see actual raw response keys
        try:
            pr = httpx.post(f"{url}/get_pot", json={"videoId": "dQw4w9WgXcQ"}, timeout=30.0)
            pr.raise_for_status()
            raw_data = pr.json()
            info["get_pot_keys"] = list(raw_data.keys())
            info["has_visitor_data"] = bool(
                raw_data.get("visitor_data") or raw_data.get("visitorData")
            )
            info["visitor_data_prefix"] = (
                (raw_data.get("visitor_data") or raw_data.get("visitorData") or "")[:20] + "..."
            )
        except Exception as pe:
            info["get_pot_error"] = str(pe)[:100]
        instances.append(info)
    result["bgutil_instances"] = instances

    # 3. Also check visitor_data in Redis
    try:
        from app.core.redis_client import get_redis
        rc2 = get_redis()
        vd_raw = rc2.get("youtube:po_visitor_data")
        cached_vd = vd_raw.decode() if isinstance(vd_raw, bytes) else vd_raw
        result["cache"]["visitor_data_present"] = bool(cached_vd)
        result["cache"]["visitor_data_prefix"] = (cached_vd[:20] + "...") if cached_vd else None
    except Exception:
        pass

    # 4. Force refresh (call /get_pot on each instance)
    invalidate_po_token()
    new_token = refresh_po_token()
    result["refresh"] = {"ok": bool(new_token), "token_prefix": (new_token[:16] + "...") if new_token else None}

    # 5. Check visitor_data after refresh
    try:
        from app.core.redis_client import get_redis
        rc3 = get_redis()
        vd_after = rc3.get("youtube:po_visitor_data")
        vd_after_str = vd_after.decode() if isinstance(vd_after, bytes) else vd_after
        result["refresh"]["visitor_data_after"] = bool(vd_after_str)
        result["refresh"]["visitor_data_prefix"] = (vd_after_str[:20] + "...") if vd_after_str else None
    except Exception:
        pass

    return result


# ═════════════════════════════════════════════════════════════════════
# GET /system-health — System Health (Tab 4)
# ═════════════════════════════════════════════════════════════════════

@router.get("/system-health")
async def get_system_health(_=Depends(verify_admin)):
    """
    Infrastructure health check:
    - Disk usage (downloads folder)
    - Redis memory
    - Celery queue depth
    - Cobalt API ping
    - yt-dlp version
    - Proxy status
    """
    import shutil
    import httpx
    import os
    import time

    result: Dict[str, Any] = {"success": True}

    # ── Disk usage ───────────────────────────────────────
    try:
        dl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "downloads")
        os.makedirs(dl_dir, exist_ok=True)
        total, used, free = shutil.disk_usage(dl_dir)
        folder_size = sum(
            os.path.getsize(os.path.join(dl_dir, f))
            for f in os.listdir(dl_dir)
            if os.path.isfile(os.path.join(dl_dir, f))
        )
        result["disk"] = {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "downloads_folder_mb": round(folder_size / (1024**2), 1),
            "downloads_file_count": len(os.listdir(dl_dir)),
            "used_pct": round(used / total * 100, 1),
        }
    except Exception as e:
        result["disk"] = {"error": str(e)}

    # ── Redis memory ─────────────────────────────────────
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_connect_timeout=3)
        info = r.info("memory")
        result["redis"] = {
            "status": "ok",
            "used_mb": round(info["used_memory"] / (1024**2), 1),
            "peak_mb": round(info["used_memory_peak"] / (1024**2), 1),
            "max_mb": 256,
            "used_pct": round(info["used_memory"] / (256 * 1024**2) * 100, 1),
        }
        # Queue depth
        celery_queues = r.llen("celery")
        result["redis"]["celery_queue_depth"] = celery_queues
    except Exception as e:
        result["redis"] = {"status": "error", "error": str(e)}

    # ── Cobalt API ping ──────────────────────────────────
    cobalt_url = os.getenv("COBALT_API_URL", "http://cobalt-api:9000")
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{cobalt_url}/")
        latency = round((time.time() - t0) * 1000)
        result["cobalt"] = {
            "status": "ok" if resp.status_code < 500 else "degraded",
            "latency_ms": latency,
            "http_code": resp.status_code,
        }
    except Exception as e:
        result["cobalt"] = {"status": "down", "error": str(e)}

    # ── yt-dlp version ───────────────────────────────────
    try:
        import yt_dlp
        result["ytdlp"] = {"version": yt_dlp.version.__version__}
    except Exception:
        result["ytdlp"] = {"version": "unknown"}

    # ── Proxy status ─────────────────────────────────────
    from app.core.proxy_manager import get_proxy_stats
    result["proxy"] = get_proxy_stats()

    # ── Supabase ping ────────────────────────────────────
    try:
        supabase = get_supabase_client()
        t0 = time.time()
        supabase.table("download_jobs").select("id").limit(1).execute()
        result["supabase"] = {"status": "ok", "latency_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        result["supabase"] = {"status": "error", "error": str(e)}

    # ── Flatten for frontend compatibility ───────────────
    # Frontend reads health.services.redis, health.services.cobalt_api, etc.
    result["services"] = {
        "redis":               result.get("redis",    {}).get("status") == "ok",
        "cobalt_api":          result.get("cobalt",   {}).get("status") == "ok",
        "cobalt_latency_ms":   result.get("cobalt",   {}).get("latency_ms"),
        "supabase":            result.get("supabase", {}).get("status") == "ok",
        "supabase_latency_ms": result.get("supabase", {}).get("latency_ms"),
    }
    # Frontend reads health.ytdlp_version (flat), not health.ytdlp.version
    result["ytdlp_version"] = result.get("ytdlp", {}).get("version", "unknown")

    return result


# ═════════════════════════════════════════════════════════════════════
# GET /platform-stats — Per-Platform Download Counters (Redis, 7 days)
# ═════════════════════════════════════════════════════════════════════

@router.get("/platform-stats")
async def get_platform_stats(days: int = 7, _=Depends(verify_admin)):
    """
    Returns per-platform ok/err counts for the last N days (default 7).
    Data is sourced from Redis hashes: vidgrab:stats:YYYY-MM-DD
    Each hash has fields like  youtube:ok, youtube:err, tiktok:ok, ...
    """
    days = min(max(days, 1), 30)
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")

    now = datetime.now(timezone.utc)
    result: Dict[str, Any] = {"success": True, "days": days, "daily": [], "totals": {}}
    platform_totals: Dict[str, Dict[str, int]] = {}

    for i in range(days):
        date = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        key = f"vidgrab:stats:{date}"
        try:
            raw = rc.hgetall(key)
        except Exception:
            raw = {}

        day_entry: Dict[str, Any] = {"date": date}
        for field_bytes, val_bytes in raw.items():
            field = field_bytes.decode() if isinstance(field_bytes, bytes) else field_bytes
            val = int(val_bytes) if val_bytes else 0
            day_entry[field] = val

            # Accumulate totals by platform
            if ":" in field:
                plat, kind = field.split(":", 1)
                if plat not in platform_totals:
                    platform_totals[plat] = {"ok": 0, "err": 0}
                if kind in platform_totals[plat]:
                    platform_totals[plat][kind] += val

        result["daily"].append(day_entry)

    # Build totals list sorted by total volume
    totals_list = []
    for plat, counts in platform_totals.items():
        ok = counts.get("ok", 0)
        err = counts.get("err", 0)
        total = ok + err
        totals_list.append({
            "platform": plat,
            "ok": ok,
            "err": err,
            "total": total,
            "success_rate": round(ok / total * 100, 1) if total > 0 else 100.0,
        })
    totals_list.sort(key=lambda x: x["total"], reverse=True)
    result["totals"] = totals_list
    return result


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _classify_platform(url: str) -> str:
    """Classify a URL into its platform name for analytics."""
    if not url:
        return "Other"

    url_lower = url.lower()

    if "tiktok.com" in url_lower:
        return "TikTok"
    elif "douyin.com" in url_lower or "iesdouyin.com" in url_lower:
        return "Douyin"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "Facebook"
    elif "instagram.com" in url_lower:
        return "Instagram"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "X (Twitter)"
    elif "spotify.com" in url_lower:
        return "Spotify"
    elif "batch_zip" in url_lower:
        return "ZIP"
    else:
        return "Other"


# ═════════════════════════════════════════════════════════════════════
# Phase 13 — Deep Analytics Endpoints
# ═════════════════════════════════════════════════════════════════════

@router.get("/platform-analytics")
async def get_platform_analytics_deep(days: int = 7, _=Depends(verify_admin)):
    """
    Per-platform deep analytics: success rate, avg file size, top errors, retry rate.
    Aggregates from download_jobs over the last N days (max 30).
    """
    days = min(max(days, 1), 30)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("download_jobs")
            .select("original_url,status,file_size_mb,error_message,error_type,retry_count,created_at,completed_at")
            .gte("created_at", since)
            .neq("original_url", "batch_zip")
            .limit(10000)
            .execute()
        )
        jobs = res.data or []

        # Aggregate per platform
        plat_map: Dict[str, Dict[str, Any]] = {}
        for job in jobs:
            plat = _classify_platform(job.get("original_url", ""))
            if plat not in plat_map:
                plat_map[plat] = {
                    "total": 0, "success": 0, "failed": 0,
                    "file_sizes": [], "retry_counts": [],
                    "errors": {},
                }
            p = plat_map[plat]
            p["total"] += 1
            status = job.get("status", "")
            if status == "success":
                p["success"] += 1
                if job.get("file_size_mb"):
                    p["file_sizes"].append(float(job["file_size_mb"]))
            elif status in ("failed", "error"):
                p["failed"] += 1
                err = (job.get("error_message") or "unknown")[:60]
                p["errors"][err] = p["errors"].get(err, 0) + 1
            retry = job.get("retry_count") or 0
            if retry > 0:
                p["retry_counts"].append(retry)

        result_list = []
        for plat, data in plat_map.items():
            total = data["total"]
            success = data["success"]
            failed = data["failed"]
            sizes = data["file_sizes"]
            retries = data["retry_counts"]
            top_errors = sorted(data["errors"].items(), key=lambda x: x[1], reverse=True)[:5]
            result_list.append({
                "platform": plat,
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": round(success / total * 100, 1) if total else 100.0,
                "avg_file_size_mb": round(sum(sizes) / len(sizes), 2) if sizes else 0,
                "retry_rate": round(len(retries) / total * 100, 1) if total else 0.0,
                "top_errors": [{"msg": e[0], "count": e[1]} for e in top_errors],
            })
        result_list.sort(key=lambda x: x["total"], reverse=True)
        return {"success": True, "days": days, "platforms": result_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user-analytics-deep")
async def get_user_analytics_deep(days: int = 7, _=Depends(verify_admin)):
    """
    Deep per-user analytics: tier distribution, top downloaders, suspicious flags.
    """
    days = min(max(days, 1), 30)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    supabase = get_supabase_client()
    try:
        # User profiles with tier info
        profiles_res = (
            supabase.table("profiles")
            .select("id,tier,created_at")
            .limit(5000)
            .execute()
        )
        profiles = profiles_res.data or []
        tier_dist: Dict[str, int] = {}
        for p in profiles:
            t = p.get("tier") or "free"
            tier_dist[t] = tier_dist.get(t, 0) + 1

        # Jobs per user in window
        jobs_res = (
            supabase.table("download_jobs")
            .select("user_id,status,original_url,created_at,error_type")
            .gte("created_at", since)
            .not_.is_("user_id", "null")
            .neq("original_url", "batch_zip")
            .limit(20000)
            .execute()
        )
        jobs = jobs_res.data or []

        user_stats: Dict[str, Dict[str, Any]] = {}
        for job in jobs:
            uid = job.get("user_id")
            if not uid:
                continue
            if uid not in user_stats:
                user_stats[uid] = {
                    "user_id": uid, "total": 0, "success": 0,
                    "failed": 0, "permanent_fails": 0, "platforms": {},
                }
            s = user_stats[uid]
            s["total"] += 1
            status = job.get("status", "")
            if status == "success":
                s["success"] += 1
            elif status in ("failed", "error"):
                s["failed"] += 1
                if job.get("error_type") == "permanent":
                    s["permanent_fails"] += 1
            plat = _classify_platform(job.get("original_url", ""))
            s["platforms"][plat] = s["platforms"].get(plat, 0) + 1

        # Compute per-user metrics and suspicious flags
        user_list = []
        for uid, s in user_stats.items():
            total = s["total"]
            failed = s["failed"]
            fail_rate = round(failed / total * 100, 1) if total else 0.0
            top_plats = sorted(s["platforms"].items(), key=lambda x: x[1], reverse=True)[:3]
            flags = []
            if total >= 100:
                flags.append("high_volume")
            if fail_rate >= 50 and total >= 10:
                flags.append("high_fail_rate")
            if s["permanent_fails"] >= 5:
                flags.append("many_permanent_fails")
            user_list.append({
                "user_id": uid,
                "total_jobs": total,
                "success": s["success"],
                "failed": failed,
                "fail_rate": fail_rate,
                "top_platforms": [{"platform": p[0], "count": p[1]} for p in top_plats],
                "flags": flags,
            })
        user_list.sort(key=lambda x: x["total_jobs"], reverse=True)

        suspicious = [u for u in user_list if u["flags"]]

        return {
            "success": True,
            "days": days,
            "tier_distribution": [{"tier": k, "count": v} for k, v in sorted(tier_dist.items())],
            "top_users": user_list[:30],
            "suspicious_users": suspicious[:20],
            "total_active_users": len(user_list),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/revenue-signals")
async def get_revenue_signals(days: int = 7, _=Depends(verify_admin)):
    """
    Revenue / upgrade signals: paywall hits, tier conversion, feature usage triggers.
    Aggregates from download_jobs quality field and error codes that indicate paywall.
    """
    days = min(max(days, 1), 30)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    supabase = get_supabase_client()
    try:
        # All jobs in window (limited fields for speed)
        res = (
            supabase.table("download_jobs")
            .select("user_id,status,original_url,selected_quality,error_message,source_surface,created_at")
            .gte("created_at", since)
            .neq("original_url", "batch_zip")
            .limit(20000)
            .execute()
        )
        jobs = res.data or []

        # Paywall proxy: jobs that failed with quota/paywall error
        _paywall_keywords = ("quota exceeded", "daily limit", "upgrade", "paywall", "rate limit")
        paywall_hits = 0
        feature_triggers: Dict[str, int] = {
            "youtube": 0, "youtube_4k": 0, "spotify": 0,
            "bulk": 0, "mp3": 0,
        }
        source_dist: Dict[str, int] = {}
        for job in jobs:
            err = (job.get("error_message") or "").lower()
            if any(kw in err for kw in _paywall_keywords):
                paywall_hits += 1
            url = (job.get("original_url") or "").lower()
            q = (job.get("selected_quality") or "").lower()
            if "youtube.com" in url or "youtu.be" in url:
                feature_triggers["youtube"] += 1
                if "4k" in q or "2160" in q:
                    feature_triggers["youtube_4k"] += 1
            if "spotify.com" in url:
                feature_triggers["spotify"] += 1
            if "mp3" in q:
                feature_triggers["mp3"] += 1
            src = job.get("source_surface") or "web"
            source_dist[src] = source_dist.get(src, 0) + 1

        # Count batch/bulk jobs (jobs with same batch_id)
        batch_res = (
            supabase.table("download_jobs")
            .select("batch_id")
            .gte("created_at", since)
            .not_.is_("batch_id", "null")
            .neq("original_url", "batch_zip")
            .limit(10000)
            .execute()
        )
        batch_jobs = batch_res.data or []
        seen_batches: set = set()
        for bj in batch_jobs:
            bid = bj.get("batch_id")
            if bid and bid not in seen_batches:
                seen_batches.add(bid)
                feature_triggers["bulk"] += 1

        # Profile tier counts
        tier_res = supabase.table("profiles").select("tier").limit(5000).execute()
        tier_data = tier_res.data or []
        tier_counts: Dict[str, int] = {}
        for t in tier_data:
            tier = t.get("tier") or "free"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        total_users = sum(tier_counts.values())
        pro_users = tier_counts.get("pro", 0)
        conversion_rate = round(pro_users / total_users * 100, 2) if total_users else 0.0

        return {
            "success": True,
            "days": days,
            "paywall_hits": paywall_hits,
            "feature_triggers": [{"feature": k, "count": v} for k, v in sorted(feature_triggers.items(), key=lambda x: x[1], reverse=True)],
            "source_distribution": [{"source": k, "count": v} for k, v in sorted(source_dist.items(), key=lambda x: x[1], reverse=True)],
            "tier_summary": {
                "total_users": total_users,
                "pro_users": pro_users,
                "free_users": tier_counts.get("free", 0),
                "conversion_rate_pct": conversion_rate,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# Cookie Pool Management
# ═════════════════════════════════════════════════════════════════════

class CookieAddRequest(BaseModel):
    platform: str       # youtube | tiktok | facebook | instagram
    cookies_b64: str    # base64-encoded Netscape cookies.txt
    label: str = ""     # human-friendly name, e.g. "Channel A - @channelname"

class CookieRemoveRequest(BaseModel):
    platform: str
    index: int  # position in pool (0-based)


_VALID_PLATFORMS = {
    "youtube", "tiktok", "facebook", "instagram",
    "twitter", "x", "reddit", "bilibili",
    "threads", "soundcloud", "spotify",
}


@router.get("/cookies/status")
async def cookie_pool_status(_=Depends(verify_admin)):
    """Show healthy/blocked count per platform."""
    try:
        from app.core.cookie_pool import get_pool_status
        return {"success": True, "pools": get_pool_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cookies/list/{platform}")
async def cookie_pool_list(platform: str, _=Depends(verify_admin)):
    """List all cookies with hash, health, expiry, and label."""
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    try:
        from app.core.cookie_pool import get_expiry_report
        items = get_expiry_report(platform)
        return {"success": True, "platform": platform, "total": len(items), "cookies": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cookies/expiry")
async def cookie_expiry_report(_=Depends(verify_admin)):
    """Full expiry report across all platforms — for dashboard / monitoring."""
    try:
        from app.core.cookie_pool import get_expiry_report
        report = {}
        has_warnings = False
        for platform in ("youtube", "tiktok", "facebook", "instagram"):
            entries = get_expiry_report(platform)
            report[platform] = entries
            if any(e["expiry_status"] in ("expired", "critical", "expiring_soon") for e in entries):
                has_warnings = True
        return {"success": True, "has_warnings": has_warnings, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cookies/check-expiry")
async def cookie_trigger_expiry_check(_=Depends(verify_admin)):
    """Manually trigger cookie expiry Telegram alert (same as daily Celery task)."""
    try:
        from app.core.celery_app import celery_app as _celery
        task = _celery.send_task("check_cookie_expiry")
        return {"success": True, "task_id": task.id, "message": "Cookie expiry check triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cookies/upload")
async def cookie_pool_upload(
    platform: str = Form(...),
    label: str = Form(""),
    file: UploadFile = File(...),
    request: Request = None,
    _=Depends(verify_admin),
):
    """
    Upload cookies.txt file directly — no base64 needed.
    Accepts Netscape format cookies.txt from browser extension.
    label: optional human-friendly name, e.g. "@channelname" or "Account 1"
    """
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    try:
        content = await file.read()
        cookies_b64 = base64.b64encode(content).decode("utf-8")
        from app.core.cookie_pool import add_cookie, get_expiry_report
        new_size = add_cookie(platform, cookies_b64, label=label)
        # Return expiry info for the newly added cookie
        report = get_expiry_report(platform)
        log_admin_action(
            request, "admin.cookie.uploaded",
            resource_type="cookie_pool", resource_id=platform,
            metadata={"label": label, "pool_size": new_size},
        )
        return {
            "success": True, "platform": platform, "pool_size": new_size,
            "message": f"Cookie added to {platform} pool (total: {new_size})",
            "cookies": report,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cookies/test")
async def test_cookie(req: CookieAddRequest, _=Depends(verify_admin)):
    """
    Validate a cookie without saving it.
    Checks format, required auth keys, and expiry.
    Returns {ok, message, found_keys, missing_keys, expires_at_str}
    """
    from app.core.cookie_pool import _AUTH_COOKIES
    platform = req.platform.lower()
    try:
        raw = base64.b64decode(req.cookies_b64).decode("utf-8", errors="ignore")
    except Exception:
        return {"ok": False, "message": "Cannot decode cookie data (invalid base64)"}

    # Detect format and extract cookie names
    cookie_names: set[str] = set()
    expiry_ts: int = 0

    raw_stripped = raw.strip()
    if raw_stripped.startswith("["):
        # JSON array
        try:
            import json as _json
            items = _json.loads(raw_stripped)
            for item in items:
                if isinstance(item, dict) and "name" in item:
                    cookie_names.add(item["name"])
                    exp = item.get("expirationDate") or item.get("expires") or 0
                    try:
                        exp_int = int(exp)
                        if exp_int > expiry_ts:
                            expiry_ts = exp_int
                    except Exception:
                        pass
        except Exception:
            return {"ok": False, "message": "Cookie data looks like JSON but could not be parsed"}
    elif "\t" in raw_stripped:
        # Netscape format — track expiry only for auth cookies (not all cookies)
        import time as _time
        _now_ts = int(_time.time())
        for line in raw_stripped.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 6:
                cname = parts[5]
                cookie_names.add(cname)
                try:
                    exp = int(parts[4])
                    # Only consider future expiry of known auth cookies
                    if exp > _now_ts and (not required or cname in required):
                        if exp > expiry_ts:
                            expiry_ts = exp
                except Exception:
                    pass
    else:
        # Raw header: "name=value; name2=value2"
        for pair in raw_stripped.split(";"):
            pair = pair.strip()
            if "=" in pair:
                cookie_names.add(pair.split("=", 1)[0].strip())

    if not cookie_names:
        return {"ok": False, "message": "No cookies found in the provided data"}

    required = _AUTH_COOKIES.get(platform, set())
    found = cookie_names & required
    missing = required - cookie_names

    from datetime import datetime as _dt, timezone as _tz
    expiry_str: Optional[str] = None
    expired = False
    if expiry_ts > 0:
        exp_dt = _dt.fromtimestamp(expiry_ts, tz=_tz.utc)
        expiry_str = exp_dt.strftime("%Y-%m-%d")
        expired = exp_dt < _dt.now(_tz.utc)

    if expired:
        return {
            "ok": False,
            "message": f"Cookie has expired ({expiry_str}). Please export a fresh cookie.",
            "found_keys": sorted(found),
            "missing_keys": sorted(missing),
            "expires_at_str": expiry_str,
        }

    if required and not found:
        return {
            "ok": False,
            "message": f"Missing required auth cookies for {platform}: {', '.join(sorted(missing)[:3])}",
            "found_keys": sorted(found),
            "missing_keys": sorted(missing),
            "expires_at_str": expiry_str,
        }

    msg_parts = [f"Found {len(cookie_names)} cookie(s)"]
    if found:
        msg_parts.append(f"auth keys: {', '.join(sorted(found)[:4])}")
    if expiry_str and not expired:
        msg_parts.append(f"expires {expiry_str}")
    if missing:
        msg_parts.append(f"optional missing: {', '.join(sorted(missing)[:2])}")

    return {
        "ok": True,
        "message": " · ".join(msg_parts),
        "found_keys": sorted(found),
        "missing_keys": sorted(missing),
        "expires_at_str": expiry_str,
    }


@router.post("/cookies/add")
async def cookie_pool_add(req: CookieAddRequest, request: Request, _=Depends(verify_admin)):
    """Add a cookie (base64) to the rotating pool. Use /cookies/upload for file upload."""
    if req.platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    if not req.cookies_b64.strip():
        raise HTTPException(status_code=400, detail="cookies_b64 is required")
    try:
        from app.core.cookie_pool import add_cookie
        new_size = add_cookie(req.platform, req.cookies_b64.strip(), label=req.label)
        log_admin_action(
            request, "admin.cookie.added",
            resource_type="cookie_pool", resource_id=req.platform,
            metadata={"label": req.label or "", "pool_size": new_size},
        )
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cookies/remove")
async def cookie_pool_remove(req: CookieRemoveRequest, request: Request, _=Depends(verify_admin)):
    """Remove a cookie by index from the pool."""
    try:
        from app.core.cookie_pool import remove_cookie
        new_size = remove_cookie(req.platform, req.index)
        log_admin_action(
            request, "admin.cookie.removed",
            resource_type="cookie_pool", resource_id=req.platform,
            metadata={"index": req.index, "pool_size": new_size},
        )
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# Proxy Pool Management
# ═════════════════════════════════════════════════════════════════════

class ProxyAddRequest(BaseModel):
    platform: str   # youtube | tiktok | facebook | instagram | douyin | twitter | default
    proxy_url: str  # http://user:pass@host:port

class ProxyRemoveRequest(BaseModel):
    platform: str
    index: int


@router.get("/proxies/status")
async def proxy_pool_status(_=Depends(verify_admin)):
    """Show proxy counts per platform (Redis + env fallback)."""
    try:
        from app.core.proxy_pool import get_pool_status
        return {"success": True, "pools": get_pool_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxies/add")
async def proxy_pool_add(req: ProxyAddRequest, request: Request, _=Depends(verify_admin)):
    """Add a proxy URL to the pool for a platform."""
    if not req.proxy_url.strip():
        raise HTTPException(status_code=400, detail="proxy_url is required")
    if not req.proxy_url.startswith(("http://", "https://", "socks5://")):
        raise HTTPException(status_code=400, detail="proxy_url must start with http://, https://, or socks5://")
    try:
        from app.core.proxy_pool import add_proxy
        url = req.proxy_url.strip()
        new_size = add_proxy(req.platform, url)
        # Mask credentials in audit log (keep scheme+host only)
        import re as _re
        masked = _re.sub(r"(https?://|socks5://)[^@]+@", r"\1***@", url)
        log_admin_action(
            request, "admin.proxy.added",
            resource_type="proxy_pool", resource_id=req.platform,
            metadata={"proxy_masked": masked, "pool_size": new_size},
        )
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/proxies/remove")
async def proxy_pool_remove(req: ProxyRemoveRequest, request: Request, _=Depends(verify_admin)):
    """Remove a proxy by index."""
    try:
        from app.core.proxy_pool import remove_proxy
        new_size = remove_proxy(req.platform, req.index)
        log_admin_action(
            request, "admin.proxy.removed",
            resource_type="proxy_pool", resource_id=req.platform,
            metadata={"index": req.index, "pool_size": new_size},
        )
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/proxies/list/{platform}")
async def proxy_pool_list(platform: str, _=Depends(verify_admin)):
    """List all proxies in Redis pool for a platform (masked credentials)."""
    import re as _re
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        pool_key = f"proxy_pool:{platform}"
        raw_list = rc.lrange(pool_key, 0, -1) or []
        masked = []
        for i, p in enumerate(raw_list):
            url = p.strip() if isinstance(p, str) else p.decode() if isinstance(p, bytes) else str(p)
            m = _re.sub(r"(https?://|socks5://)[^@]+@", r"\1***:***@", url)
            masked.append({"index": i, "masked_url": m})
        return {"success": True, "platform": platform, "proxies": masked, "count": len(masked)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# ScraperAPI Key Pool
# ═════════════════════════════════════════════════════════════════════

@router.get("/scraperapi/keys")
async def scraperapi_keys(_=Depends(verify_admin)):
    """Return credit status for all configured ScraperAPI keys."""
    from app.core.scraperapi_pool import fetch_all_credits
    keys = fetch_all_credits(use_cache=True)
    total = sum(k["credits"] or 0 for k in keys)
    return {"success": True, "keys": keys, "total_credits": total, "key_count": len(keys)}


class ScraperAPIKeyRequest(BaseModel):
    key: str


@router.post("/scraperapi/keys/add")
async def scraperapi_add_key(req: ScraperAPIKeyRequest, request: Request, _=Depends(verify_admin)):
    """Add a ScraperAPI key to the pool (stored in Redis, no .env needed)."""
    from app.core.scraperapi_pool import add_key, fetch_credits
    key = req.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    # Validate key against ScraperAPI account endpoint
    credits = fetch_credits(key, use_cache=False)
    if credits is None:
        raise HTTPException(status_code=400, detail="Invalid key or ScraperAPI unreachable")
    pool_size = add_key(key)
    log_admin_action(
        request, "admin.scraperapi.key_added",
        resource_type="scraperapi_pool",
        metadata={"key_prefix": key[:8] + "***", "credits": credits, "pool_size": pool_size},
    )
    return {"success": True, "pool_size": pool_size, "credits": credits, "key_prefix": key[:8] + "***"}


@router.delete("/scraperapi/keys/remove")
async def scraperapi_remove_key(index: int, request: Request, _=Depends(verify_admin)):
    """Remove a ScraperAPI key by index from the pool."""
    from app.core.scraperapi_pool import remove_key, get_all_keys
    keys = get_all_keys()
    if index < 0 or index >= len(keys):
        raise HTTPException(status_code=400, detail=f"Index {index} out of range (pool size: {len(keys)})")
    pool_size = remove_key(index)
    log_admin_action(
        request, "admin.scraperapi.key_removed",
        resource_type="scraperapi_pool",
        metadata={"index": index, "pool_size": pool_size},
    )
    return {"success": True, "pool_size": pool_size}


@router.post("/scraperapi/rotate")
async def scraperapi_rotate(_=Depends(verify_admin)):
    """Manually rotate to the next ScraperAPI key."""
    from app.core.scraperapi_pool import rotate_key, get_active_key
    rotate_key(reason="manual-admin")
    active = get_active_key()
    return {"success": True, "message": "Rotated to next key", "active_key_prefix": (active[:8] + "***") if active else "none"}


@router.post("/scraperapi/refresh-credits")
async def scraperapi_refresh_credits(_=Depends(verify_admin)):
    """Force-refresh credits for all keys (bypass cache)."""
    from app.core.scraperapi_pool import fetch_all_credits
    keys = fetch_all_credits(use_cache=False)
    total = sum(k["credits"] or 0 for k in keys)
    return {"success": True, "keys": keys, "total_credits": total}


# ═════════════════════════════════════════════════════════════════════
# Throwaway Account Management
# ═════════════════════════════════════════════════════════════════════

_THROWAWAY_REDIS_KEY = "throwaway:accounts"
_THROWAWAY_MAX_ENTRIES = 1000

# Default path inside Docker container; override via THROWAWAY_LOG_PATH env var
_THROWAWAY_LOG_PATH = os.getenv("THROWAWAY_LOG_PATH", "/app/throwaway_accounts.json")


def _mask_password(password: Optional[str]) -> str:
    """Show first 3 chars then *** for security."""
    if not password:
        return "***"
    prefix = password[:3]
    return f"{prefix}***"


def _format_account(raw: dict) -> dict:
    """Normalize an account dict and mask the password."""
    return {
        "platform": raw.get("platform"),
        "email": raw.get("email"),
        "password": _mask_password(raw.get("password")),
        "phone": raw.get("phone"),
        "birthday": raw.get("birthday"),
        "success": raw.get("success"),
        "created_at": raw.get("created_at"),
        "exit_ip": raw.get("exit_ip"),
        "exit_country": raw.get("exit_country"),
    }


class ThrowawayAccountPayload(BaseModel):
    platform: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[str] = None
    success: Optional[bool] = None
    created_at: Optional[str] = None
    exit_ip: Optional[str] = None
    exit_country: Optional[str] = None
    exit_city: Optional[str] = None


@router.get("/throwaway/accounts")
async def get_throwaway_accounts(_=Depends(verify_admin)):
    """
    Return throwaway account log merged from:
      1. Redis key  throwaway:accounts  (primary, written by POST endpoint)
      2. Local JSON file at THROWAWAY_LOG_PATH  (fallback / legacy)
    Passwords are masked in the response.
    """
    import json as _json

    accounts: List[dict] = []
    seen_emails: set = set()

    # ── 1. Redis source ──────────────────────────────────
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        raw_list = rc.lrange(_THROWAWAY_REDIS_KEY, 0, -1)
        for raw_str in raw_list:
            try:
                obj = _json.loads(raw_str)
                accounts.append(obj)
                if obj.get("email"):
                    seen_emails.add(obj["email"])
            except Exception:
                pass
    except Exception as redis_err:
        print(f"[throwaway] Redis read error: {redis_err}")

    # ── 2. JSON file source (fallback) ───────────────────
    try:
        if os.path.exists(_THROWAWAY_LOG_PATH):
            with open(_THROWAWAY_LOG_PATH, "r", encoding="utf-8") as fh:
                file_data = _json.load(fh)
            if isinstance(file_data, list):
                for obj in file_data:
                    email = obj.get("email")
                    if email and email in seen_emails:
                        continue  # already have it from Redis
                    accounts.append(obj)
                    if email:
                        seen_emails.add(email)
    except Exception as file_err:
        print(f"[throwaway] File read error ({_THROWAWAY_LOG_PATH}): {file_err}")

    # ── Format + stats ────────────────────────────────────
    formatted = [_format_account(a) for a in accounts]
    success_count = sum(1 for a in accounts if a.get("success") is True)
    fail_count = sum(1 for a in accounts if a.get("success") is False)

    return {
        "success": True,
        "accounts": formatted,
        "total": len(formatted),
        "success_count": success_count,
        "fail_count": fail_count,
    }


@router.post("/throwaway/accounts")
async def post_throwaway_account(
    payload: ThrowawayAccountPayload,
    _=Depends(verify_admin),
):
    """
    Store a throwaway account result in Redis (centralised).
    Called by the throwaway script after each account attempt.
    Stores up to THROWAWAY_MAX_ENTRIES entries (LPUSH + LTRIM).
    """
    import json as _json

    try:
        from app.core.redis_client import get_redis
        rc = get_redis()

        entry = payload.model_dump(exclude_none=False)
        # Fill created_at if missing
        if not entry.get("created_at"):
            entry["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        serialized = _json.dumps(entry, ensure_ascii=False)
        pipe = rc.pipeline()
        pipe.lpush(_THROWAWAY_REDIS_KEY, serialized)
        pipe.ltrim(_THROWAWAY_REDIS_KEY, 0, _THROWAWAY_MAX_ENTRIES - 1)
        pipe.execute()

        return {"success": True, "message": "Account stored in Redis"}
    except Exception as e:
        print(f"[throwaway] POST store error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# POST /debug-youtube — Full YouTube extraction diagnostic
# ═════════════════════════════════════════════════════════════════════

class DebugYouTubeRequest(BaseModel):
    url: str
    quality: str = "video_fast"


@router.post("/debug-youtube")
async def debug_youtube(payload: DebugYouTubeRequest, _=Depends(verify_admin)):
    """
    Run yt-dlp extraction with verbose logging and return all captured output.
    Useful for diagnosing YouTube bot detection / PO token issues.
    """
    import os
    import yt_dlp
    import httpx
    from app.core.po_token_cache import get_po_token, get_po_visitor_data, get_cache_ttl
    from app.services.cobalt_service import is_cobalt_available, fetch_cobalt_stream

    captured_logs: list = []

    class _CapturingLogger:
        def debug(self, msg: str) -> None:
            if not msg.startswith("[debug] "):
                captured_logs.append(f"[debug] {msg}")
        def info(self, msg: str) -> None:
            captured_logs.append(f"[info] {msg}")
        def warning(self, msg: str) -> None:
            captured_logs.append(f"[WARN] {msg}")
        def error(self, msg: str) -> None:
            captured_logs.append(f"[ERROR] {msg}")

    result: Dict[str, Any] = {"url": payload.url}

    # ── PO token state ──────────────────────────────────────
    cached_pot = get_po_token()
    visitor_data = get_po_visitor_data()
    result["po_token"] = {
        "present": bool(cached_pot),
        "prefix": (cached_pot[:20] + "...") if cached_pot else None,
        "ttl_seconds": get_cache_ttl(),
        "visitor_data_present": bool(visitor_data),
    }

    # ── Cobalt check ────────────────────────────────────────
    cobalt_available = is_cobalt_available()
    result["cobalt"] = {"available": cobalt_available}
    if cobalt_available:
        try:
            cobalt_resp = fetch_cobalt_stream(payload.url, video_quality="720", download_mode="auto")
            result["cobalt"]["response"] = {
                "status": cobalt_resp.get("status"),
                "has_url": bool(cobalt_resp.get("url")),
                "error": cobalt_resp.get("error"),
            }
        except Exception as ce:
            result["cobalt"]["error"] = str(ce)

    # ── yt-dlp extraction — use real downloader opts (proxy + cookies included) ──
    from app.services.downloader import _get_base_opts

    opts = _get_base_opts(payload.url, phase="metadata")
    opts.update({
        "format": "best[height<=720]",
        "quiet": False,
        "no_warnings": False,
        "ignoreerrors": False,
        "no_color": True,
        "socket_timeout": 30,
        "retries": 2,
        "logger": _CapturingLogger(),
    })
    result["proxy_used"] = opts.get("proxy", "none (server IP)")
    result["cookiefile_set"] = bool(opts.get("cookiefile"))

    try:
        import asyncio
        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(payload.url, download=False)
        info = await asyncio.to_thread(_run)
        if info:
            result["yt_dlp"] = {
                "success": True,
                "title": info.get("title"),
                "formats_count": len(info.get("formats", [])),
                "extractor": info.get("extractor"),
                "height": info.get("height"),
            }
        else:
            result["yt_dlp"] = {"success": False, "info_is_none": True}
    except Exception as e:
        result["yt_dlp"] = {"success": False, "exception": str(e)[:500]}

    result["yt_dlp_logs"] = captured_logs[-50:]  # last 50 log lines
    return result


# ═════════════════════════════════════════════════════════════════════
# Flow/Veo Cleanup — Admin / QA endpoints
# Reads metadata.json written by flow_cleanup.py into each job's
# temp work_dir. Jobs are ephemeral (~20 min window).
# SCOPE: visible on-frame logo cleanup only. SynthID not in scope.
# ═════════════════════════════════════════════════════════════════════

_FLOW_DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/app/downloads")
_FLOW_PREFIX = "flow_"


@router.get("/flow-cleanup/jobs")
async def list_flow_cleanup_jobs(_=Depends(verify_admin)):
    """List all live Flow/Veo cleanup jobs from temp directories."""
    jobs = []
    pattern = os.path.join(_FLOW_DOWNLOAD_DIR, f"{_FLOW_PREFIX}*", "metadata.json")
    for meta_path in glob.glob(pattern):
        try:
            with open(meta_path) as mf:
                meta = json.load(mf)
            work_dir = os.path.dirname(meta_path)
            files = os.listdir(work_dir)
            meta["has_preview"] = "preview.jpg" in files
            meta["has_output"] = any(
                f.startswith("cleaned_") and f.endswith(".mp4") for f in files
            )
            jobs.append(meta)
        except Exception:
            continue
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/flow-cleanup/jobs/{temp_id}")
async def get_flow_cleanup_job(temp_id: str, _=Depends(verify_admin)):
    """Get detail + file inventory for one Flow/Veo cleanup job."""
    import re as _re
    safe = _re.sub(r"[^a-f0-9]", "", temp_id or "")
    if len(safe) < 8:
        raise HTTPException(status_code=400, detail="Invalid temp_id")
    work_dir = os.path.join(_FLOW_DOWNLOAD_DIR, f"{_FLOW_PREFIX}{safe}")
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Job không tồn tại hoặc đã hết hạn.")
    meta: dict = {}
    meta_path = os.path.join(work_dir, "metadata.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as mf:
                meta = json.load(mf)
        except Exception:
            pass
    files = os.listdir(work_dir)
    meta["_files"] = files
    meta["has_preview"] = "preview.jpg" in files
    meta["has_output"] = any(f.startswith("cleaned_") and f.endswith(".mp4") for f in files)
    meta["preview_url"] = f"/api/v1/flow-cleanup/frame/{safe}" if "preview.jpg" in files else None
    return meta


# ═════════════════════════════════════════════════════════════════════
# GET /ops-health — Phase 12 Operational Health Summary
# ═════════════════════════════════════════════════════════════════════

@router.get("/ops-health")
async def get_ops_health(_=Depends(verify_admin)):
    """
    Phase 12 operational health summary.
    Aggregates anomaly detector, queue intelligence, auto-tuner,
    platform fallback stats, playbook suggestions, and schedule drift.
    """
    from app.core.anomaly_detector import get_active_anomalies
    from app.core.queue_intelligence import get_queue_health
    from app.core.auto_tuner import get_all_params
    from app.core.redis_client import get_redis

    result: Dict[str, Any] = {}

    # ── Anomaly detector ─────────────────────────────────
    try:
        active_anomalies = get_active_anomalies()
    except Exception as e:
        active_anomalies = []
        result["anomaly_error"] = str(e)

    result["anomaly_count"] = len(active_anomalies)
    result["active_anomalies"] = active_anomalies

    # ── Queue intelligence ───────────────────────────────
    try:
        result["queue_health"] = get_queue_health()
    except Exception as e:
        result["queue_health"] = {"error": str(e)}

    # ── Auto-tuner params ────────────────────────────────
    try:
        result["auto_tune_params"] = get_all_params()
    except Exception as e:
        result["auto_tune_params"] = {"error": str(e)}

    # ── Fallback summary (platform → {success_rate, top_layer}) ──
    try:
        rc = get_redis()
        now = datetime.now(timezone.utc)
        fallback_summary: Dict[str, Any] = {}
        # Read today's platform stats from Redis hashes
        date_str = now.strftime("%Y-%m-%d")
        raw_today = rc.hgetall(f"vidgrab:stats:{date_str}") or {}
        # Also pull fallback-layer counters: vidgrab:fallback:<platform>:<layer>
        fallback_keys = rc.keys("vidgrab:fallback:*") or []
        layer_counts: Dict[str, Dict[str, int]] = {}
        for fk in fallback_keys:
            fk_str = fk.decode() if isinstance(fk, bytes) else fk
            parts = fk_str.split(":")
            if len(parts) >= 4:
                plat = parts[2]
                layer = parts[3]
                val_raw = rc.get(fk_str)
                val = int(val_raw) if val_raw else 0
                if plat not in layer_counts:
                    layer_counts[plat] = {}
                layer_counts[plat][layer] = layer_counts[plat].get(layer, 0) + val

        # Build per-platform summary from today's stats hash
        platform_set: set = set()
        for field_raw in raw_today:
            field = field_raw.decode() if isinstance(field_raw, bytes) else field_raw
            if ":" in field:
                platform_set.add(field.split(":")[0])

        for plat in platform_set:
            ok_raw = raw_today.get(f"{plat}:ok".encode(), raw_today.get(f"{plat}:ok", 0))
            err_raw = raw_today.get(f"{plat}:err".encode(), raw_today.get(f"{plat}:err", 0))
            ok = int(ok_raw) if ok_raw else 0
            err = int(err_raw) if err_raw else 0
            total = ok + err
            success_rate = round(ok / total * 100, 1) if total > 0 else 100.0
            # Determine top fallback layer by highest hit count
            plat_layers = layer_counts.get(plat, {})
            top_layer = max(plat_layers, key=lambda k: plat_layers[k]) if plat_layers else "primary"
            fallback_summary[plat] = {
                "success_rate": success_rate,
                "top_layer": top_layer,
            }
        result["fallback_summary"] = fallback_summary
    except Exception as e:
        result["fallback_summary"] = {"error": str(e)}

    # ── Last intelligence run ────────────────────────────
    try:
        rc = get_redis()
        last_run_raw = rc.get("vidgrab:ops:last_intelligence_run")
        result["last_intelligence_run"] = (
            last_run_raw.decode() if isinstance(last_run_raw, bytes) else (last_run_raw or "")
        )
    except Exception as e:
        result["last_intelligence_run"] = ""

    # ── Playbook suggestions (match anomaly types) ───────
    try:
        from app.core.playbooks import match_active_playbooks
        result["playbook_suggestions"] = match_active_playbooks(active_anomalies)
    except Exception as e:
        result["playbook_suggestions"] = []
        result["playbook_error"] = str(e)

    # ── Schedule drift count ─────────────────────────────
    try:
        rc = get_redis()
        drift_raw = rc.get("vidgrab:schedule:drift_alerts")
        result["schedule_drift_count"] = int(drift_raw) if drift_raw else 0
    except Exception as e:
        result["schedule_drift_count"] = 0

    return result


# ══════════════════════════════════════════════════════════════════════
# Spotify cache invalidation (P4b) — force-refresh a mis-matched resolve
# ══════════════════════════════════════════════════════════════════════

@router.delete("/cache/spotify-resolve")
async def invalidate_spotify_resolve(query: str, _=Depends(verify_admin)):
    """Drop the cached Spotify→YouTube resolve for ONE track when a user reports
    a wrong/mismatched song.

    `query` must be the exact search query the downloader cached on, i.e.
    'ytsearch1:<artist> - <title> audio' (the track's `search_query` field).
    The resolve cache is keyed by md5(search_query), not by Spotify track id —
    so pass the query, not the id.
    """
    from app.core.spotify_artist_ops import invalidate_resolve, resolve_cache_key
    deleted = invalidate_resolve(query)
    return {"success": True, "query": query, "key": resolve_cache_key(query), "deleted": deleted}


@router.delete("/cache/spotify-artist/{artist_id}")
async def invalidate_spotify_artist_cache(artist_id: str, _=Depends(verify_admin)):
    """Drop the 30-min cached artist overview so the next load re-fetches fresh
    metadata/albums (use when an artist's catalog changed)."""
    from app.core.spotify_artist_ops import invalidate_artist
    return {"success": True, "artist_id": artist_id, "deleted": invalidate_artist(artist_id)}


# ══════════════════════════════════════════════════════════════════════
# YouTube proxy gate — dashboard + toggle (Phase 8 / P3 / P8)
# ══════════════════════════════════════════════════════════════════════

@router.get("/youtube/status")
async def youtube_status(_=Depends(verify_admin)):
    """Live YouTube panel: feature flag, proxy bytes used / limit, cost estimate,
    circuit-breaker state, success rate, and a status_color (green/yellow/red)."""
    from app.core.youtube_gate import dashboard_snapshot
    return {"success": True, **dashboard_snapshot()}


class YouTubeToggleRequest(BaseModel):
    enabled: bool


@router.post("/youtube/toggle")
async def youtube_toggle(req: YouTubeToggleRequest, request: Request, _=Depends(verify_admin)):
    """Enable/disable YouTube WITHOUT a redeploy (Redis override beats env).
    Disabling stops all extraction + proxy spend immediately."""
    from app.core.youtube_gate import set_youtube_enabled, dashboard_snapshot
    set_youtube_enabled(req.enabled)
    log_admin_action(
        request, "admin.youtube.toggle",
        resource_type="feature_flag", resource_id="youtube",
        metadata={"enabled": req.enabled},
    )
    return {"success": True, "enabled": req.enabled, **dashboard_snapshot()}


@router.delete("/youtube/override")
async def youtube_clear_override(request: Request, _=Depends(verify_admin)):
    """Drop the Redis toggle so YouTube falls back to the YOUTUBE_ENABLED env."""
    from app.core.youtube_gate import clear_youtube_override, dashboard_snapshot
    clear_youtube_override()
    log_admin_action(
        request, "admin.youtube.override_cleared",
        resource_type="feature_flag", resource_id="youtube",
    )
    return {"success": True, **dashboard_snapshot()}


# ═════════════════════════════════════════════════════════════════════
# Phase 14 — Surface Breakdown / User Detail / Admin User Actions
# ═════════════════════════════════════════════════════════════════════

@router.get("/surface-breakdown")
async def get_surface_breakdown(days: int = 7, _=Depends(verify_admin)):
    """
    Phase 14: Breakdown of downloads by surface (web/extension/telegram_bot/api).
    Also shows event counts by source from analytics_events.
    """
    try:
        from datetime import datetime, timezone, timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        supabase = get_supabase_client()

        # Download jobs by source
        rows = supabase.table("download_jobs").select("source,status") \
            .gte("created_at", since).limit(20000).execute()

        surface_stats: dict = {}
        for r in (rows.data or []):
            src = r.get("source") or "web"
            surface_stats.setdefault(src, {"total": 0, "success": 0, "failed": 0})
            surface_stats[src]["total"] += 1
            if r["status"] == "success":
                surface_stats[src]["success"] += 1
            elif r["status"] in ("failed", "error"):
                surface_stats[src]["failed"] += 1

        surfaces = []
        for src, c in surface_stats.items():
            surfaces.append({
                "source": src,
                "total": c["total"],
                "success": c["success"],
                "failed": c["failed"],
                "success_rate": round(c["success"] / max(c["total"], 1) * 100, 1),
            })
        surfaces.sort(key=lambda x: -x["total"])

        # Event counts by source from analytics_events (best-effort)
        event_by_source: dict = {}
        try:
            ev_rows = supabase.table("analytics_events").select("source,event_name") \
                .gte("created_at", since).limit(10000).execute()
            for r in (ev_rows.data or []):
                src = r.get("source") or "web"
                event_by_source.setdefault(src, {})
                en = r.get("event_name", "")
                event_by_source[src][en] = event_by_source[src].get(en, 0) + 1
        except Exception:
            pass

        return {
            "success": True,
            "days": days,
            "surfaces": surfaces,
            "event_by_source": event_by_source,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "surfaces": []}


@router.get("/user-detail/{user_id}")
async def get_user_detail(user_id: str, days: int = 30, _=Depends(verify_admin)):
    """
    Phase 14: Detailed profile for a specific user.
    Shows tier, quota, recent jobs, source usage, API keys count.
    """
    try:
        from datetime import datetime, timezone, timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        supabase = get_supabase_client()

        # Profile
        profile_resp = supabase.table("profiles").select("*").eq("id", user_id).limit(1).execute()
        profile = (profile_resp.data or [{}])[0]

        # Jobs
        # The column is original_url — every other query in this file uses that
        # name. Selecting a column that does not exist is a 400 from PostgREST,
        # which took the whole user-detail panel down rather than one field.
        jobs_resp = supabase.table("download_jobs").select("id,original_url,status,platform,source,created_at,error_message,file_size_mb") \
            .eq("user_id", user_id).gte("created_at", since) \
            .order("created_at", desc=True).limit(50).execute()
        jobs = jobs_resp.data or []

        total = len(jobs)
        success = sum(1 for j in jobs if j["status"] == "success")
        failed  = sum(1 for j in jobs if j["status"] in ("failed", "error"))
        sources: dict = {}
        platforms: dict = {}
        for j in jobs:
            src = j.get("source") or "web"
            sources[src] = sources.get(src, 0) + 1
            plat = j.get("platform") or "unknown"
            platforms[plat] = platforms.get(plat, 0) + 1

        # API keys count
        api_keys_count = 0
        try:
            ak_resp = supabase.table("api_keys").select("id", count="exact") \
                .eq("user_id", user_id).eq("active", True).execute()
            api_keys_count = ak_resp.count or 0
        except Exception:
            pass

        # Telegram linked
        telegram_linked = False
        try:
            # telegram_links is keyed by telegram_user_id and points back via
            # vidgrab_user_id — it has neither an `id` nor a `user_id` column.
            # Both names were wrong, so this query always raised and the except
            # below reported "not linked" for everyone, including linked users.
            tl_resp = supabase.table("telegram_links").select("telegram_user_id", count="exact") \
                .eq("vidgrab_user_id", user_id).execute()
            telegram_linked = (tl_resp.count or 0) > 0
        except Exception:
            pass

        return {
            "success": True,
            "user_id": user_id,
            "profile": {
                "email": profile.get("email"),
                "tier": profile.get("tier", "free"),
                "downloads_today": profile.get("downloads_today", 0),
                "created_at": profile.get("created_at"),
            },
            "stats": {
                "total_jobs": total,
                "success": success,
                "failed": failed,
                "fail_rate": round(failed / max(total, 1) * 100, 1),
                "sources": sources,
                "platforms": platforms,
            },
            "api_keys_count": api_keys_count,
            "telegram_linked": telegram_linked,
            "recent_jobs": jobs[:20],
            "days": days,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/user-action")
async def admin_user_action(request: Request, _=Depends(verify_admin)):
    """
    Phase 14: One-click admin actions on a user.
    Actions: reset_quota | set_tier | revoke_api_keys | retry_failed_jobs
    """
    try:
        body = await request.json()
        action  = body.get("action")
        user_id = body.get("user_id")
        params  = body.get("params", {})

        if not action or not user_id:
            return {"success": False, "error": "action and user_id required"}

        supabase = get_supabase_client()

        if action == "reset_quota":
            # Daily counters live in user_usage, not profiles — profiles has no
            # downloads_today column, so this wrote to a column that does not
            # exist and the reset never happened.
            supabase.table("user_usage").update({
                "downloads_today": 0,
                "last_reset_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }).eq("user_id", user_id).execute()
            log_admin_action(request, "admin.user.quota_reset", resource_type="user", resource_id=user_id)
            return {"success": True, "action": "reset_quota", "user_id": user_id}

        elif action == "set_tier":
            # Valid tiers come from the quota table rather than a hardcoded
            # pair: 'team' and 'enterprise' are fully defined in
            # quotas.TIER_PERMISSIONS and entitlements.PLAN_DEFS, but this
            # endpoint rejected them, so there was no supported way to grant
            # unlimited downloads at all.
            from app.core.quotas import TIER_PERMISSIONS
            tier = (params.get("tier") or "free").lower()
            if tier not in TIER_PERMISSIONS:
                return {"success": False,
                        "error": f"tier must be one of {sorted(TIER_PERMISSIONS)}"}

            # billing_status has to move with the tier. entitlements.py
            # downgrades any non-free tier back to free when billing_status is
            # 'none', while quotas.py honours the tier as declared — so setting
            # tier alone left the two disagreeing and the upgrade looked like it
            # silently did nothing.
            update = {"tier": tier}
            if tier == "free":
                update.update({"billing_status": "none", "plan_name": "Free",
                               "subscription_expiry": None})
            else:
                from app.core.entitlements import get_plan_def
                update.update({
                    "billing_status": "active",
                    "plan_name": get_plan_def(tier)["name"],
                    # Admin grants are not subscriptions; an expiry here would
                    # only matter for canceling/past_due, but leaving a stale
                    # one behind is how a grant quietly lapses.
                    "subscription_expiry": None,
                })

            res = supabase.table("profiles").update(update).eq("id", user_id).execute()
            # An update that matches no row is not an error to PostgREST — it
            # returns an empty list. Reporting success regardless is how this
            # told an operator a customer was on Pro while the row still said
            # free (RLS was silently dropping every write). Say what happened.
            if not (res.data or []):
                return {"success": False, "action": "set_tier", "user_id": user_id,
                        "error": (f"Không cập nhật được gói cho user {user_id} — "
                                  "không tìm thấy hồ sơ nào khớp.")}

            log_admin_action(request, "admin.user.tier_changed", resource_type="user",
                             resource_id=user_id, metadata={"new_tier": tier})
            return {"success": True, "action": "set_tier", "tier": tier,
                    "applied": update, "user_id": user_id}

        elif action == "revoke_api_keys":
            # Column is is_active (migration 010), not active. Writing 'active'
            # made PostgREST reject the whole request with PGRST204, so the one
            # control an operator has for a leaked key never revoked anything.
            res = (supabase.table("api_keys")
                   .update({"is_active": False})
                   .eq("user_id", user_id)
                   .execute())
            revoked = len(res.data or [])
            log_admin_action(request, "admin.user.api_keys_revoked", resource_type="user",
                             resource_id=user_id, metadata={"revoked": revoked})
            return {"success": True, "action": "revoke_api_keys", "user_id": user_id,
                    "revoked": revoked}

        elif action == "retry_failed_jobs":
            # Re-queue failed jobs from last 24h
            from datetime import datetime, timezone, timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            failed_jobs = supabase.table("download_jobs") \
                .select("id,original_url,selected_quality,user_id") \
                .eq("user_id", user_id) \
                .in_("status", ["failed", "error"]) \
                .gte("created_at", since).limit(10).execute()

            requeued = 0
            for job in (failed_jobs.data or []):
                try:
                    supabase.table("download_jobs").update({"status": "pending", "error_message": None}) \
                        .eq("id", job["id"]).execute()
                    from app.tasks.video_tasks import process_video_task
                    process_video_task.delay(
                        job["id"], job.get("original_url", ""),
                        None, job.get("selected_quality", "best"),
                        False, False,
                        priority=5,
                    )
                    requeued += 1
                except Exception:
                    pass
            log_admin_action(request, "admin.user.jobs_retried", resource_type="user", resource_id=user_id, metadata={"requeued": requeued})
            return {"success": True, "action": "retry_failed_jobs", "requeued": requeued}

        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ═════════════════════════════════════════════════════════════════════
# Security Visibility — Audit Log + Security Events
# ═════════════════════════════════════════════════════════════════════

@router.get("/audit-log")
async def get_audit_log(limit: int = 50, _=Depends(verify_admin)):
    """Recent admin audit events (tier changes, cookie ops, feature toggles, etc.)."""
    from app.core.audit import get_recent_admin_actions
    limit = min(max(limit, 1), 200)
    entries = get_recent_admin_actions(limit=limit)
    return {"success": True, "count": len(entries), "entries": entries}


@router.get("/security-events")
async def get_security_events(limit: int = 100, _=Depends(verify_admin)):
    """
    Recent access-denied events + live lockout state.
    Useful for spotting brute-force attempts or misconfigured callers.
    """
    from app.core.audit import get_recent_access_denials
    limit = min(max(limit, 1), 500)
    denials = get_recent_access_denials(limit=limit)

    # Current lockout state from Redis
    r = _redis()
    try:
        lockout_keys = r.keys("admin:lockout:*")
        locked_ips = []
        for k in lockout_keys:
            key_str = k.decode() if isinstance(k, bytes) else k
            ip = key_str.replace("admin:lockout:", "")
            ttl = r.ttl(k)
            locked_ips.append({"ip": ip, "ttl_seconds": max(ttl, 0)})
    except Exception:
        locked_ips = []

    return {
        "success": True,
        "denied_count": len(denials),
        "locked_ips": locked_ips,
        "entries": denials,
    }


# ═════════════════════════════════════════════════════════════════════
# Phase 2 — Platform Health (circuit breaker visibility + control)
# ═════════════════════════════════════════════════════════════════════

_PLATFORM_META: Dict[str, Dict[str, Any]] = {
    "youtube":     {"cookie_required": True,  "proxy_required": True},
    "instagram":   {"cookie_required": True,  "proxy_required": False},
    "facebook":    {"cookie_required": True,  "proxy_required": False},
    "tiktok":      {"cookie_required": True,  "proxy_required": False},
    "twitter":     {"cookie_required": True,  "proxy_required": False},
    "reddit":      {"cookie_required": False, "proxy_required": False},
    "bilibili":    {"cookie_required": False, "proxy_required": False},
    "threads":     {"cookie_required": False, "proxy_required": False},
    "pinterest":   {"cookie_required": False, "proxy_required": False},
    "douyin":      {"cookie_required": False, "proxy_required": False},
    "spotify":     {"cookie_required": False, "proxy_required": False},
    "soundcloud":  {"cookie_required": False, "proxy_required": False},
    "xiaohongshu": {"cookie_required": True,  "proxy_required": True},
    "vimeo":       {"cookie_required": False, "proxy_required": False},
    "rumble":      {"cookie_required": False, "proxy_required": False},
    "odysee":      {"cookie_required": False, "proxy_required": False},
    "dailymotion": {"cookie_required": False, "proxy_required": False},
    "vk":          {"cookie_required": False, "proxy_required": False},
    "lemon8":      {"cookie_required": True,  "proxy_required": False},
}


def _fmt_ago(dt_str: Optional[str]) -> str:
    """Convert ISO timestamp → human-readable 'Xm ago'."""
    if not dt_str:
        return "never"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return dt_str


@router.get("/platforms/health")
async def get_platforms_health(_=Depends(verify_admin)):
    """
    Live platform health overview — circuit state + 1h fail rate + active jobs.
    Aggregates circuit breaker state (Redis) + recent job stats (Supabase).
    """
    from app.core.platform_circuit import get_state as cb_state, cooldown_remaining, _EXEMPT

    # 1h window
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    platforms = list(_PLATFORM_META.keys())

    # Fetch last-1h jobs per platform from Supabase
    jobs_by_platform: Dict[str, Dict[str, Any]] = {}
    try:
        sb = get_supabase_client()
        rows = (
            sb.table("download_jobs")
            .select("platform,status,created_at,completed_at")
            .gte("created_at", since)
            .execute()
        ).data or []
        for row in rows:
            p = (row.get("platform") or "other").lower()
            if p not in jobs_by_platform:
                jobs_by_platform[p] = {"total": 0, "failed": 0, "last_success": None}
            jobs_by_platform[p]["total"] += 1
            if row.get("status") in ("failed", "error"):
                jobs_by_platform[p]["failed"] += 1
            elif row.get("status") == "completed":
                ts = row.get("completed_at") or row.get("created_at")
                prev = jobs_by_platform[p]["last_success"]
                if ts and (not prev or ts > prev):
                    jobs_by_platform[p]["last_success"] = ts
    except Exception as e:
        print(f"[admin/platforms/health] Supabase error: {e}")

    # Active jobs from Redis queue intelligence (best-effort)
    active_by_platform: Dict[str, int] = {}
    try:
        from app.core.queue_intelligence import get_queue_health
        qh = get_queue_health()
        for job in qh.get("active_jobs", []):
            p = (job.get("platform") or "other").lower()
            active_by_platform[p] = active_by_platform.get(p, 0) + 1
    except Exception:
        pass

    result = []
    for p in platforms:
        state = cb_state(p)
        exempt = p in _EXEMPT
        if exempt:
            state = "exempt"

        stats = jobs_by_platform.get(p, {})
        total = stats.get("total", 0)
        failed = stats.get("failed", 0)
        fail_rate = round(failed / total * 100) if total > 0 else 0
        last_success = _fmt_ago(stats.get("last_success"))

        # Derive status
        if state == "open":
            status = "critical"
        elif state == "half" or fail_rate >= 20:
            status = "warning"
        elif fail_rate >= 10:
            status = "warning"
        else:
            status = "healthy"

        cooldown = cooldown_remaining(p) if state == "open" else 0

        result.append({
            "platform": p,
            "status": status,
            "circuitState": state,
            "lastSuccessAt": last_success,
            "failRate1h": fail_rate,
            "totalJobs1h": total,
            "activeJobs": active_by_platform.get(p, 0),
            "cookieRequired": _PLATFORM_META[p]["cookie_required"],
            "proxyRequired": _PLATFORM_META[p]["proxy_required"],
            "cooldownRemaining": cooldown,
        })

    # Sort: critical first, then warning, then healthy
    order = {"critical": 0, "warning": 1, "healthy": 2}
    result.sort(key=lambda x: (order.get(x["status"], 3), x["platform"]))
    return {"success": True, "platforms": result, "count": len(result)}


class CircuitActionRequest(BaseModel):
    action: str  # "force_open" | "force_close" | "reset"


@router.post("/platforms/{platform}/circuit")
async def set_platform_circuit(
    platform: str,
    req: CircuitActionRequest,
    request: Request,
    _=Depends(verify_admin),
):
    """Force-open, force-close, or reset a platform circuit breaker."""
    if platform not in _PLATFORM_META:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")
    if req.action not in ("force_open", "force_close", "reset"):
        raise HTTPException(status_code=400, detail="action must be force_open | force_close | reset")

    try:
        r = _redis()
        from app.core.platform_circuit import _k
        if req.action == "force_open":
            import time as _time
            r.set(_k(platform, "state"), "open")
            r.set(_k(platform, "open_since"), str(_time.time()))
            new_state = "open"
        elif req.action == "force_close":
            r.set(_k(platform, "state"), "closed")
            r.delete(_k(platform, "fails"), _k(platform, "open_since"))
            new_state = "closed"
        else:  # reset
            r.delete(_k(platform, "state"), _k(platform, "fails"), _k(platform, "open_since"))
            new_state = "closed"
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    log_admin_action(
        request, f"admin.platform.circuit_{req.action}",
        resource_type="platform", resource_id=platform,
        metadata={"action": req.action, "new_state": new_state},
    )
    return {"success": True, "platform": platform, "action": req.action, "new_state": new_state}


@router.get("/platforms/{platform}/detail")
async def get_platform_detail(platform: str, _=Depends(verify_admin)):
    """
    Per-platform detail: circuit state, recent jobs (last 20), error breakdown, phase stats.
    """
    if platform not in _PLATFORM_META:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")

    from app.core.platform_circuit import get_state as cb_state, cooldown_remaining, _EXEMPT

    state = cb_state(platform)
    if platform in _EXEMPT:
        state = "exempt"

    # Recent jobs (last 20)
    recent_jobs = []
    error_counts: Dict[str, int] = {}
    phase_stats: Dict[str, Dict[str, Any]] = {}
    last_success = None

    try:
        sb = get_supabase_client()
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = (
            sb.table("download_jobs")
            .select("id,status,original_url,error_message,error_type,created_at,completed_at,file_size_mb")
            .eq("platform", platform)
            .gte("created_at", since_24h)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        ).data or []

        for row in rows[:20]:
            status = row.get("status", "pending")
            result_label = "success" if status == "completed" else ("failed" if status in ("failed", "error") else "running")
            started = row.get("created_at", "")
            completed = row.get("completed_at")
            dur_ms = 0
            if started and completed:
                try:
                    t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                    dur_ms = int((t1 - t0).total_seconds() * 1000)
                except Exception:
                    pass
            recent_jobs.append({
                "id": row["id"],
                "result": result_label,
                "url": row.get("original_url", ""),
                "durationMs": dur_ms,
                "startedAt": _fmt_ago(started),
                "error": row.get("error_message"),
            })
            if result_label == "success" and not last_success:
                last_success = started

        # Error breakdown across all 50 rows
        for row in rows:
            if row.get("status") in ("failed", "error"):
                etype = row.get("error_type") or "unknown"
                error_counts[etype] = error_counts.get(etype, 0) + 1

    except Exception as e:
        print(f"[admin/platforms/{platform}/detail] Supabase error: {e}")

    total_errors = sum(error_counts.values())
    error_breakdown = [
        {"errorType": k, "count": v, "pct": round(v / total_errors * 100) if total_errors else 0}
        for k, v in sorted(error_counts.items(), key=lambda x: -x[1])
    ]

    # Description
    if state == "open":
        desc = f"Circuit breaker OPEN — platform is temporarily suspended. Cooldown: {cooldown_remaining(platform)}s remaining."
    elif state == "half":
        desc = "Circuit breaker HALF-OPEN — allowing probe requests. Next success will restore normal operation."
    elif len(recent_jobs) == 0:
        desc = "No recent jobs in the last 24h."
    elif error_breakdown:
        top_err = error_breakdown[0]["errorType"]
        desc = f"Operational with some errors. Most common: {top_err} ({error_breakdown[0]['pct']}%)."
    else:
        desc = "Operating normally. No recent errors."

    # Derive status
    if state == "open":
        status = "critical"
    elif state == "half" or (total_errors > 0 and total_errors / max(len(rows), 1) > 0.2):
        status = "warning"
    else:
        status = "healthy"

    meta = _PLATFORM_META[platform]
    config = {
        "rateLimit": "auto (circuit breaker)",
        "cookiePool": "Required" if meta["cookie_required"] else "Not required",
        "proxy": "Required" if meta["proxy_required"] else "Not configured",
        "retryPolicy": "3 retries, exponential backoff",
    }

    return {
        "success": True,
        "platform": platform,
        "status": status,
        "circuitState": state,
        "description": desc,
        "lastSuccessAt": _fmt_ago(last_success),
        "recentJobs": recent_jobs,
        "errorBreakdown": error_breakdown,
        "phaseStats": list(phase_stats.values()),
        "config": config,
        "cooldownRemaining": cooldown_remaining(platform) if state == "open" else 0,
    }


# ═════════════════════════════════════════════════════════════════════
# Phase 2 — Queue & Workers
# ═════════════════════════════════════════════════════════════════════

@router.get("/queue/workers")
async def get_queue_workers(_=Depends(verify_admin)):
    """
    Live queue state: Celery worker list (Redis inspect), queue depths, dead-letter jobs.
    """
    r = _redis()
    result: Dict[str, Any] = {"success": True}

    # Queue depths
    try:
        result["queues"] = {
            "default":  r.llen("celery"),
            "priority": r.llen("priority"),
            "archive":  r.llen("archive"),
            "analysis": r.llen("analysis"),
        }
        result["queues"]["total"] = sum(result["queues"].values())
    except Exception as e:
        result["queues"] = {"error": str(e)}

    # Active jobs from queue_intelligence
    active_jobs = []
    try:
        from app.core.queue_intelligence import get_queue_health
        qh = get_queue_health()
        active_jobs = qh.get("active_jobs", [])
        result["stale_jobs"] = qh.get("stale_jobs", [])
        result["queue_health"] = {
            "ok": qh.get("ok", True),
            "throughput_per_min": qh.get("throughput_per_min", 0),
        }
    except Exception as e:
        result["stale_jobs"] = []
        result["queue_health"] = {"error": str(e)}

    result["active_jobs"] = active_jobs
    result["active_count"] = len(active_jobs)

    # Dead-letter: failed jobs in last 1h not retried
    dead_letter = []
    try:
        sb = get_supabase_client()
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        dl_rows = (
            sb.table("download_jobs")
            .select("id,platform,original_url,error_message,created_at,retry_count")
            .in_("status", ["failed", "error"])
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        ).data or []
        for row in dl_rows:
            dead_letter.append({
                "id": row["id"],
                "platform": row.get("platform", "unknown"),
                "url": row.get("original_url", ""),
                "error": row.get("error_message", ""),
                "retries": row.get("retry_count", 0),
                "failedAt": _fmt_ago(row.get("created_at")),
            })
    except Exception as e:
        print(f"[admin/queue/workers] dead-letter error: {e}")

    result["dead_letter"] = dead_letter
    result["dead_letter_count"] = len(dead_letter)
    return result


class JobActionRequest(BaseModel):
    pass  # no body needed for retry/cancel


@router.post("/queue/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request, _=Depends(verify_admin)):
    """Re-queue a failed job by job_id."""
    try:
        sb = get_supabase_client()
        row = sb.table("download_jobs").select("*").eq("id", job_id).single().execute()
        if not row.data:
            raise HTTPException(status_code=404, detail="Job not found")
        job = row.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    url = job.get("original_url", "")
    if not url:
        raise HTTPException(status_code=400, detail="Job has no original_url to retry")

    try:
        from app.tasks.video_tasks import process_video_task
        sb = get_supabase_client()
        sb.table("download_jobs").update({"status": "pending", "error_message": None}).eq("id", job_id).execute()
        new_task = process_video_task.delay(
            job_id, url, job.get("user_id"), job.get("selected_quality", "best"), False, False
        )
        log_admin_action(
            request, "admin.job.retry",
            resource_type="job", resource_id=job_id,
            metadata={"new_task_id": new_task.id, "url": url[:100]},
        )
        return {"success": True, "job_id": job_id, "new_task_id": new_task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue retry: {e}")


@router.post("/queue/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, _=Depends(verify_admin)):
    """Mark a pending/running job as cancelled."""
    try:
        sb = get_supabase_client()
        sb.table("download_jobs").update({"status": "cancelled"}).eq("id", job_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    log_admin_action(
        request, "admin.job.cancel",
        resource_type="job", resource_id=job_id,
    )
    return {"success": True, "job_id": job_id, "status": "cancelled"}


# Phase 3 System Config (Redis-backed)
_CONFIG_PREFIX = "admin:config:"
_CONFIG_INDEX = "admin:config:__index__"

class ConfigSetRequest(BaseModel):
    value: str
    description: Optional[str] = None

@router.get("/config")
async def get_config(_=Depends(verify_admin)):
    r = _redis()
    try:
        keys = r.smembers(_CONFIG_INDEX)
        entries = []
        for k in keys:
            k_str = k.decode() if isinstance(k, bytes) else k
            val = r.get(f"{_CONFIG_PREFIX}{k_str}")
            desc = r.get(f"{_CONFIG_PREFIX}{k_str}:desc")
            entries.append({"key": k_str, "value": val.decode() if isinstance(val, bytes) else (val or ""), "description": desc.decode() if isinstance(desc, bytes) else (desc or "")})
        entries.sort(key=lambda x: x["key"])
        return {"success": True, "entries": entries, "count": len(entries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/config/{key}")
async def set_config(key: str, req: ConfigSetRequest, request: Request, _=Depends(verify_admin)):
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail="Invalid key")
    r = _redis()
    try:
        r.set(f"{_CONFIG_PREFIX}{key}", req.value)
        if req.description:
            r.set(f"{_CONFIG_PREFIX}{key}:desc", req.description)
        r.sadd(_CONFIG_INDEX, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    log_admin_action(request, "admin.config.set", resource_type="config", resource_id=key, metadata={"value_len": len(req.value)})
    return {"success": True, "key": key, "value": req.value}

@router.delete("/config/{key}")
async def delete_config(key: str, request: Request, _=Depends(verify_admin)):
    r = _redis()
    try:
        r.delete(f"{_CONFIG_PREFIX}{key}", f"{_CONFIG_PREFIX}{key}:desc")
        r.srem(_CONFIG_INDEX, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    log_admin_action(request, "admin.config.delete", resource_type="config", resource_id=key)
    return {"success": True, "key": key}

# Phase 3 Access Management
@router.get("/access/sessions")
async def list_admin_sessions(_=Depends(verify_admin)):
    r = _redis()
    try:
        session_keys = r.keys("admin:session:*")
        sessions = []
        for k in session_keys:
            k_str = k.decode() if isinstance(k, bytes) else k
            token_fragment = k_str.replace("admin:session:", "")[:8] + "..."
            ttl = r.ttl(k)
            sessions.append({"token_fragment": token_fragment, "ttl_seconds": max(ttl, 0), "expires_in_human": f"{ttl // 3600}h {(ttl % 3600) // 60}m" if ttl > 0 else "expired"})
        sessions.sort(key=lambda x: -x["ttl_seconds"])
        return {"success": True, "sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/access/sessions")
async def revoke_all_sessions(request: Request, _=Depends(verify_admin)):
    r = _redis()
    try:
        keys = r.keys("admin:session:*")
        if keys:
            r.delete(*keys)
        count = len(keys)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    log_admin_action(request, "admin.access.sessions_revoked_all", metadata={"count": count})
    return {"success": True, "revoked": count}

# ─────────────────────────────────────────────────────────────
# Phase 4 — SSE real-time stream + Export endpoints
# ─────────────────────────────────────────────────────────────
import asyncio as _asyncio
import csv as _csv
import io as _io

@router.get("/stream")
async def admin_sse_stream(request: Request, _=Depends(verify_admin)):
    """SSE stream: pushes platform circuit events + job counters every 5s."""
    async def event_generator():
        from app.core.platform_circuit import _k as _circuit_k
        r = _redis()
        while True:
            if await request.is_disconnected():
                break
            try:
                # Collect platform circuit states
                circuits = {}
                for p in list(_PLATFORM_META.keys())[:8]:  # top 8 to keep payload small
                    s = r.get(_circuit_k(p, "state"))
                    circuits[p] = (s.decode() if s else "closed")

                sb = get_supabase_client()
                # Active job count
                active = sb.table("download_jobs").select("id", count="exact").in_("status", ["pending", "processing"]).execute()
                active_count = active.count or 0

                # Dead letter count (last 1h)
                _1h_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                dead = sb.table("download_jobs").select("id", count="exact").eq("status", "failed").gt("updated_at", _1h_ago).execute()
                dead_count = dead.count or 0

                payload = json.dumps({
                    "circuits": circuits,
                    "active_jobs": active_count,
                    "dead_letter_1h": dead_count,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                yield f"data: {payload}\n\n"
            except Exception as _e:
                yield f"data: {json.dumps({'error': str(_e)})}\n\n"
            await _asyncio.sleep(5)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/audit-log/export")
async def export_audit_log_csv(limit: int = 1000, _=Depends(verify_admin)):
    """Download audit log as CSV."""
    try:
        from app.core.audit import get_recent_admin_actions
        rows = get_recent_admin_actions(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    output = _io.StringIO()
    writer = _csv.writer(output)
    writer.writerow(["time", "action", "actor", "resource_type", "resource_id", "detail"])
    for r in rows:
        writer.writerow([
            r.get("time", ""),
            r.get("action", ""),
            r.get("actor", ""),
            r.get("resource_type", ""),
            r.get("resource_id", ""),
            r.get("detail", ""),
        ])
    csv_bytes = output.getvalue().encode("utf-8")

    from fastapi.responses import Response as _Resp
    return _Resp(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


@router.get("/analytics/export")
async def export_analytics_csv(days: int = 30, _=Depends(verify_admin)):
    """Download daily analytics as CSV."""
    try:
        sb = get_supabase_client()
        _since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        jobs = sb.table("download_jobs").select("created_at, status, platform").gt("created_at", _since).execute()
        rows_raw = jobs.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Aggregate by date
    from collections import defaultdict as _dd
    by_date = _dd(lambda: {"total": 0, "success": 0, "failed": 0})
    for j in rows_raw:
        d = (j.get("created_at") or "")[:10]
        if not d:
            continue
        by_date[d]["total"] += 1
        if j.get("status") == "completed":
            by_date[d]["success"] += 1
        elif j.get("status") == "failed":
            by_date[d]["failed"] += 1

    output = _io.StringIO()
    writer = _csv.writer(output)
    writer.writerow(["date", "total", "success", "failed", "success_rate"])
    for date in sorted(by_date.keys()):
        s = by_date[date]
        rate = round(s["success"] / s["total"] * 100, 1) if s["total"] else 0
        writer.writerow([date, s["total"], s["success"], s["failed"], f"{rate}%"])
    csv_bytes = output.getvalue().encode("utf-8")

    from fastapi.responses import Response as _Resp
    return _Resp(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analytics_{days}d.csv"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 Enterprise Admin — Tenants, API Keys, Webhooks, Usage
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/enterprise/tenants")
async def list_enterprise_tenants(
    status: Optional[str] = None,
    plan: Optional[str] = None,
    limit: int = 100,
    _=Depends(verify_admin),
):
    supabase = get_supabase_client()
    q = supabase.table("tenants").select(
        "id, name, slug, plan, plan_seats, plan_api_calls_per_month, plan_storage_gb, "
        "status, trial_ends_at, subscription_id, license_expires_at, created_at, updated_at, "
        "tenant_settings(custom_domain, features)"
    )
    if status:
        q = q.eq("status", status)
    if plan:
        q = q.eq("plan", plan)
    resp = q.order("created_at", desc=True).limit(limit).execute()
    tenants = resp.data or []

    today = datetime.utcnow().date().isoformat()
    usage_resp = supabase.table("tenant_usage_daily").select(
        "tenant_id, api_calls, downloads, batch_jobs, webhook_deliveries, storage_bytes_used"
    ).eq("date", today).execute()
    usage_by_tenant = {u["tenant_id"]: u for u in (usage_resp.data or [])}
    for t in tenants:
        t["today_usage"] = usage_by_tenant.get(t["id"], {})

    all_resp = supabase.table("tenants").select("plan, status").execute()
    plan_counts: dict = {}
    status_counts: dict = {}
    for t in (all_resp.data or []):
        plan_counts[t["plan"]] = plan_counts.get(t["plan"], 0) + 1
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1

    return {
        "tenants": tenants,
        "total": len(tenants),
        "plan_counts": plan_counts,
        "status_counts": status_counts,
    }


@router.post("/enterprise/tenants/{tenant_id}/status")
async def update_tenant_status(tenant_id: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    body = await request.json()
    new_status = body.get("status")
    if new_status not in ("active", "suspended", "canceled"):
        raise HTTPException(status_code=400, detail="Invalid status")
    supabase.table("tenants").update({"status": new_status}).eq("id", tenant_id).execute()
    log_admin_action(request, f"admin.tenant.status_{new_status}", resource_type="tenant", resource_id=tenant_id)
    return {"success": True, "tenant_id": tenant_id, "status": new_status}


@router.post("/enterprise/tenants/{tenant_id}/plan")
async def update_tenant_plan(tenant_id: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    body = await request.json()
    new_plan = body.get("plan")
    if new_plan not in ("starter", "growth", "enterprise"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    supabase.table("tenants").update({"plan": new_plan}).eq("id", tenant_id).execute()
    log_admin_action(request, "admin.tenant.plan_changed", resource_type="tenant", resource_id=tenant_id)
    return {"success": True, "tenant_id": tenant_id, "plan": new_plan}


@router.get("/enterprise/api-keys")
async def list_enterprise_api_keys(
    tenant_id: Optional[str] = None,
    active_only: bool = False,
    limit: int = 200,
    _=Depends(verify_admin),
):
    supabase = get_supabase_client()
    q = supabase.table("tenant_api_keys").select(
        "id, tenant_id, key_prefix, label, scopes, rate_limit_per_min, rate_limit_per_day, "
        "is_active, created_by, last_used_at, requests_today, requests_this_month, "
        "requests_total, ip_allowlist, expires_at, created_at, "
        "tenants(name, slug, plan)"
    )
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    if active_only:
        q = q.eq("is_active", True)
    resp = q.order("created_at", desc=True).limit(limit).execute()
    keys = resp.data or []

    return {
        "api_keys": keys,
        "total": len(keys),
        "total_active": sum(1 for k in keys if k.get("is_active")),
        "total_requests_today": sum(k.get("requests_today", 0) for k in keys),
        "total_requests_month": sum(k.get("requests_this_month", 0) for k in keys),
    }


@router.post("/enterprise/api-keys/{key_id}/toggle")
async def toggle_api_key(key_id: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    resp = supabase.table("tenant_api_keys").select("is_active").eq("id", key_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="API key not found")
    new_state = not resp.data["is_active"]
    supabase.table("tenant_api_keys").update({"is_active": new_state}).eq("id", key_id).execute()
    action = "activated" if new_state else "deactivated"
    log_admin_action(request, f"admin.api_key.{action}", resource_type="api_key", resource_id=key_id)
    return {"success": True, "key_id": key_id, "is_active": new_state}


@router.get("/enterprise/webhooks")
async def list_enterprise_webhooks(
    tenant_id: Optional[str] = None,
    active_only: bool = False,
    _=Depends(verify_admin),
):
    supabase = get_supabase_client()
    q = supabase.table("webhook_endpoints").select(
        "id, tenant_id, url, events, is_active, created_by, last_triggered_at, "
        "total_deliveries, successful_deliveries, failed_deliveries, created_at, "
        "tenants(name, slug, plan)"
    )
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    if active_only:
        q = q.eq("is_active", True)
    resp = q.order("created_at", desc=True).execute()
    endpoints = resp.data or []

    return {
        "endpoints": endpoints,
        "total": len(endpoints),
        "total_active": sum(1 for e in endpoints if e.get("is_active")),
        "total_deliveries": sum(e.get("total_deliveries", 0) for e in endpoints),
        "total_failed": sum(e.get("failed_deliveries", 0) for e in endpoints),
    }


@router.get("/enterprise/webhooks/{endpoint_id}/deliveries")
async def get_webhook_deliveries(
    endpoint_id: str, limit: int = 50, _=Depends(verify_admin)
):
    supabase = get_supabase_client()
    resp = (
        supabase.table("webhook_deliveries")
        .select("id, event_type, status, attempt_count, last_attempt_at, response_status, error_message, created_at")
        .eq("endpoint_id", endpoint_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"deliveries": resp.data or []}


@router.post("/enterprise/webhooks/{endpoint_id}/toggle")
async def toggle_webhook_endpoint(endpoint_id: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    resp = supabase.table("webhook_endpoints").select("is_active").eq("id", endpoint_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    new_state = not resp.data["is_active"]
    supabase.table("webhook_endpoints").update({"is_active": new_state}).eq("id", endpoint_id).execute()
    action = "activated" if new_state else "deactivated"
    log_admin_action(request, f"admin.webhook.{action}", resource_type="webhook", resource_id=endpoint_id)
    return {"success": True, "endpoint_id": endpoint_id, "is_active": new_state}


@router.get("/enterprise/usage")
async def get_enterprise_usage(
    days: int = 30,
    tenant_id: Optional[str] = None,
    _=Depends(verify_admin),
):
    supabase = get_supabase_client()
    from datetime import timedelta as _td
    since = (datetime.utcnow().date() - _td(days=days)).isoformat()

    q = supabase.table("tenant_usage_daily").select(
        "tenant_id, date, api_calls, downloads, batch_jobs, webhook_deliveries, "
        "storage_bytes_used, active_seats, tenants(name, slug, plan)"
    ).gte("date", since)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    resp = q.order("date", desc=True).execute()
    rows = resp.data or []

    totals = {
        "api_calls": sum(r.get("api_calls", 0) for r in rows),
        "downloads": sum(r.get("downloads", 0) for r in rows),
        "batch_jobs": sum(r.get("batch_jobs", 0) for r in rows),
        "webhook_deliveries": sum(r.get("webhook_deliveries", 0) for r in rows),
        "storage_bytes_used": max((r.get("storage_bytes_used", 0) for r in rows), default=0),
    }

    by_date: dict = {}
    for r in rows:
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "api_calls": 0, "downloads": 0, "batch_jobs": 0, "webhook_deliveries": 0}
        by_date[d]["api_calls"] += r.get("api_calls", 0)
        by_date[d]["downloads"] += r.get("downloads", 0)
        by_date[d]["batch_jobs"] += r.get("batch_jobs", 0)
        by_date[d]["webhook_deliveries"] += r.get("webhook_deliveries", 0)

    return {
        "usage_rows": rows,
        "daily_aggregated": sorted(by_date.values(), key=lambda x: x["date"]),
        "totals": totals,
        "days": days,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — AI Analysis + Billing Admin
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/enterprise/analysis/jobs")
async def list_analysis_jobs(
    status: Optional[str] = None,
    limit: int = 100,
    _=Depends(verify_admin),
):
    supabase = get_supabase_client()
    q = supabase.table("analysis_jobs").select(
        "id, user_id, tenant_id, media_url, media_fingerprint, duration_seconds, "
        "analyses_requested, status, analyzer_version, error_message, "
        "created_at, updated_at, expires_at"
    )
    if status:
        q = q.eq("status", status)
    resp = q.order("created_at", desc=True).limit(limit).execute()
    jobs = resp.data or []

    status_counts: dict = {}
    for j in jobs:
        s = j.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    analysis_type_counts: dict = {}
    for j in jobs:
        for t in (j.get("analyses_requested") or []):
            analysis_type_counts[t] = analysis_type_counts.get(t, 0) + 1

    return {
        "jobs": jobs,
        "total": len(jobs),
        "status_counts": status_counts,
        "analysis_type_counts": analysis_type_counts,
    }


@router.get("/enterprise/analysis/stats")
async def get_analysis_stats(days: int = 30, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    from datetime import timedelta as _td
    since = (datetime.utcnow() - _td(days=days)).isoformat()

    jobs_resp = supabase.table("analysis_jobs").select(
        "status, created_at, analyses_requested, duration_seconds"
    ).gte("created_at", since).execute()
    jobs = jobs_resp.data or []

    usage_resp = supabase.table("analysis_usage").select(
        "date, analyses_count, media_minutes_analyzed, ai_apply_jobs_count"
    ).gte("date", (datetime.utcnow() - _td(days=days)).date().isoformat()).execute()
    usage_rows = usage_resp.data or []

    status_counts: dict = {}
    type_counts: dict = {}
    for j in jobs:
        s = j.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
        for t in (j.get("analyses_requested") or []):
            type_counts[t] = type_counts.get(t, 0) + 1

    total_analyses = sum(r.get("analyses_count", 0) for r in usage_rows)
    total_minutes = round(sum(r.get("media_minutes_analyzed", 0) for r in usage_rows), 1)

    return {
        "total_jobs": len(jobs),
        "status_counts": status_counts,
        "analysis_type_counts": type_counts,
        "total_analyses_metered": total_analyses,
        "total_media_minutes": total_minutes,
        "days": days,
    }


@router.get("/enterprise/billing/setup-status")
async def get_billing_setup_status(_=Depends(verify_admin)):
    """
    Check whether Phase-20 billing tables exist.
    Returns {ready, missing_tables, migration_file, sql_hint}.
    """
    supabase = get_supabase_client()
    required = ["plans", "payment_events", "credit_grants", "usage_events", "user_credits"]
    missing = []
    for tbl in required:
        try:
            supabase.table(tbl).select("*").limit(1).execute()
        except Exception:
            missing.append(tbl)

    # Read migration SQL for display
    import os as _os, pathlib as _pl
    migration_path = _pl.Path(__file__).parents[3] / "database" / "migrations" / "015_phase20_billing.sql"
    sql_hint = ""
    if migration_path.exists():
        sql_hint = migration_path.read_text()[:8000]  # first 8KB for display

    return {
        "ready": len(missing) == 0,
        "missing_tables": missing,
        "migration_file": "database/migrations/015_phase20_billing.sql",
        "sql_hint": sql_hint if missing else "",
    }


@router.get("/enterprise/billing/overview")
async def get_billing_overview(_=Depends(verify_admin)):
    supabase = get_supabase_client()

    try:
        profiles_resp = supabase.table("profiles").select("tier, billing_status").execute()
        profiles = profiles_resp.data or []
    except Exception:
        profiles = []

    plan_counts: dict = {}
    for p in profiles:
        plan = p.get("tier") or p.get("plan") or "free"
        plan_counts[plan] = plan_counts.get(plan, 0) + 1

    _PLAN_MRR: dict = {"free": 0, "pro": 9.99, "team": 29.99, "api": 19.99, "enterprise": 0}
    mrr = sum(_PLAN_MRR.get(plan, 0) * count for plan, count in plan_counts.items())

    try:
        plans_resp = supabase.table("plans").select("code, name, price_monthly_cents, limits, features, sort_order").order("sort_order").execute()
        plans_data = plans_resp.data or []
    except Exception:
        plans_data = []

    try:
        payment_resp = supabase.table("payment_events").select(
            "event_type, processed, created_at"
        ).order("created_at", desc=True).limit(20).execute()
        payment_data = payment_resp.data or []
    except Exception:
        payment_data = []

    try:
        credits_resp = supabase.table("credit_grants").select(
            "user_id, amount, reason, granted_by, created_at"
        ).order("created_at", desc=True).limit(10).execute()
        credits_data = credits_resp.data or []
    except Exception:
        credits_data = []

    return {
        "plan_counts": plan_counts,
        "total_users": len(profiles),
        "mrr_usd": round(mrr, 2),
        "paying_users": sum(v for k, v in plan_counts.items() if k != "free"),
        "plans": plans_data,
        "recent_payment_events": payment_data,
        "recent_credit_grants": credits_data,
    }


@router.get("/enterprise/billing/revenue")
async def get_billing_revenue(days: int = 30, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    from datetime import timedelta as _td
    since = (datetime.utcnow() - _td(days=days)).isoformat()

    resp = supabase.table("usage_events").select(
        "event_type, metric, quantity, plan, created_at"
    ).gte("created_at", since).execute()
    events = resp.data or []

    by_date: dict = {}
    metric_totals: dict = {}
    plan_activity: dict = {}
    for e in events:
        d = (e.get("created_at") or "")[:10]
        if d and d not in by_date:
            by_date[d] = {}
        if d:
            et = e.get("event_type", "other")
            by_date[d][et] = by_date[d].get(et, 0) + (e.get("quantity") or 1)

        m = e.get("metric", "other")
        metric_totals[m] = metric_totals.get(m, 0) + (e.get("quantity") or 1)

        plan = e.get("plan", "free")
        plan_activity[plan] = plan_activity.get(plan, 0) + (e.get("quantity") or 1)

    daily = [{"date": d, **counts} for d, counts in sorted(by_date.items())]

    return {
        "daily": daily,
        "metric_totals": metric_totals,
        "plan_activity": plan_activity,
        "total_events": len(events),
        "days": days,
    }


@router.post("/enterprise/billing/credits/grant")
async def admin_grant_credits(request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    body = await request.json()
    user_id = body.get("user_id", "").strip()
    amount = int(body.get("amount", 0))
    reason = body.get("reason", "admin_comp").strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    if reason not in ("welcome_bonus", "refund", "promo", "admin_comp", "referral"):
        raise HTTPException(status_code=400, detail="Invalid reason")

    supabase.table("credit_grants").insert({
        "user_id": user_id,
        "amount": amount,
        "reason": reason,
        "granted_by": "admin",
        "is_active": True,
    }).execute()

    existing = supabase.table("user_credits").select("balance").eq("user_id", user_id).maybe_single().execute()
    if existing.data:
        new_balance = (existing.data.get("balance") or 0) + amount
        supabase.table("user_credits").update({
            "balance": new_balance,
            "total_earned": supabase.table("user_credits").select("total_earned").eq("user_id", user_id).single().execute().data.get("total_earned", 0) + amount,
        }).eq("user_id", user_id).execute()
    else:
        supabase.table("user_credits").insert({
            "user_id": user_id, "balance": amount, "total_earned": amount, "total_spent": 0
        }).execute()

    log_admin_action(request, "admin.billing.credits_granted", resource_type="user", resource_id=user_id)
    return {"success": True, "user_id": user_id, "amount": amount, "reason": reason}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Presets Admin (user_presets, user_platform_prefs)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/enterprise/presets/stats")
async def get_presets_stats(_=Depends(verify_admin)):
    supabase = get_supabase_client()

    presets_resp = supabase.table("user_presets").select(
        "id, platform, is_default, is_system, settings, created_at"
    ).execute()
    presets = presets_resp.data or []

    prefs_resp = supabase.table("user_platform_prefs").select(
        "platform, prefs, last_used_at"
    ).execute()
    prefs = prefs_resp.data or []

    by_platform: dict = {}
    system_count = 0
    default_count = 0
    for p in presets:
        pl = p.get("platform") or "universal"
        by_platform[pl] = by_platform.get(pl, 0) + 1
        if p.get("is_system"):
            system_count += 1
        if p.get("is_default"):
            default_count += 1

    prefs_by_platform: dict = {}
    for p in prefs:
        pl = p.get("platform", "unknown")
        prefs_by_platform[pl] = prefs_by_platform.get(pl, 0) + 1

    # Most common settings keys across all presets
    settings_key_count: dict = {}
    for p in presets:
        for k in (p.get("settings") or {}).keys():
            settings_key_count[k] = settings_key_count.get(k, 0) + 1

    return {
        "total_presets": len(presets),
        "total_prefs": len(prefs),
        "system_count": system_count,
        "default_count": default_count,
        "by_platform": by_platform,
        "prefs_by_platform": prefs_by_platform,
        "popular_settings_keys": sorted(settings_key_count.items(), key=lambda x: -x[1])[:10],
    }


@router.get("/enterprise/presets/system")
async def list_system_presets(_=Depends(verify_admin)):
    supabase = get_supabase_client()
    resp = supabase.table("user_presets").select("*").eq("is_system", True).order("sort_order").execute()
    return {"presets": resp.data or []}


@router.post("/enterprise/presets/system")
async def create_system_preset(request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    body = await request.json()
    name = body.get("name", "").strip()
    platform = body.get("platform") or None
    settings = body.get("settings") or {}
    sort_order = int(body.get("sort_order", 0))

    if not name:
        raise HTTPException(status_code=400, detail="name required")

    resp = supabase.table("user_presets").insert({
        "user_id": "system",
        "name": name,
        "platform": platform,
        "settings": settings,
        "is_system": True,
        "is_default": False,
        "sort_order": sort_order,
    }).execute()
    log_admin_action(request, "admin.preset.system_created", resource_type="preset")
    return {"success": True, "preset": resp.data[0] if resp.data else {}}


@router.delete("/enterprise/presets/system/{preset_id}")
async def delete_system_preset(preset_id: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    check = supabase.table("user_presets").select("is_system").eq("id", preset_id).maybe_single().execute()
    if not check.data or not check.data.get("is_system"):
        raise HTTPException(status_code=404, detail="System preset not found")
    supabase.table("user_presets").delete().eq("id", preset_id).execute()
    log_admin_action(request, "admin.preset.system_deleted", resource_type="preset", resource_id=preset_id)
    return {"success": True}


# ── Phase 27 — Smart Throughput Orchestrator (Admin Observability) ──────────

@router.get("/orchestrator/lanes")
async def get_lane_states(_=Depends(verify_admin)):
    """Per-platform lane state machine snapshot."""
    try:
        from app.core.platform_scheduler import get_all_lane_states
        return {"success": True, "lanes": get_all_lane_states()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orchestrator/throughput")
async def get_throughput_snapshot(_=Depends(verify_admin)):
    """Per-platform RPM current vs ceiling."""
    try:
        from app.core.throughput_policy import get_all_throughput_snapshot
        return {"success": True, "throughput": get_all_throughput_snapshot()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orchestrator/fairness")
async def get_fairness_snapshot(_=Depends(verify_admin)):
    """Global queue depth + top active users."""
    try:
        from app.core.fair_queue import get_fairness_snapshot
        return {"success": True, "fairness": get_fairness_snapshot()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orchestrator/cookie-scores/{platform}")
async def get_cookie_score_distribution(platform: str, _=Depends(verify_admin)):
    """Cookie score distribution for a platform."""
    try:
        from app.core.cookie_score import get_score_distribution
        return {"success": True, "platform": platform, "scores": get_score_distribution(platform)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orchestrator/summary")
async def get_orchestrator_summary(_=Depends(verify_admin)):
    """Combined Phase 27 health dashboard — lanes + throughput + fairness."""
    try:
        from app.core.platform_scheduler import get_all_lane_states
        from app.core.throughput_policy import get_all_throughput_snapshot
        from app.core.fair_queue import get_fairness_snapshot
        lanes      = get_all_lane_states()
        throughput = get_all_throughput_snapshot()
        fairness   = get_fairness_snapshot()
        # Count by state
        states: dict = {}
        for l in lanes:
            states[l["state"]] = states.get(l["state"], 0) + 1
        return {
            "success":   True,
            "lanes":     lanes,
            "throughput": throughput,
            "fairness":  fairness,
            "summary":   {
                "total_platforms":  len(lanes),
                "by_state":         states,
                "queue_depth":      fairness.get("global_depth", 0),
                "over_limit_count": sum(1 for t in throughput if t.get("over_limit")),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Phase 27A — Lane Observability (read-only, no behavior changes) ──────────

@router.get("/platforms/lane-snapshot")
async def get_lane_snapshot(_=Depends(verify_admin)):
    """27A: Per-platform lane health observation. Pure read — no scheduling side effects."""
    try:
        from datetime import datetime, timezone
        from app.core.lane_observer import observe_all_platforms
        return {
            "success":   True,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "lanes":     observe_all_platforms(),
        }
    except Exception as e_obs:
        raise HTTPException(status_code=500, detail=str(e_obs))


@router.get("/platforms/{platform}/lane-detail")
async def get_platform_lane_detail(platform: str, _=Depends(verify_admin)):
    """27A: Single-platform lane observation with full breakdown."""
    try:
        from app.core.lane_observer import observe_platform
        obs = observe_platform(platform)
        return {"success": True, **obs}
    except Exception as e_obs:
        raise HTTPException(status_code=500, detail=str(e_obs))


# ── Phase 27B — Scored Cookie Selection (Admin Observability) ─────────────────

@router.get("/cookies/scores/{platform}")
async def get_cookie_score_breakdown(platform: str, _=Depends(verify_admin)):
    """27B: Per-cookie score breakdown for a platform."""
    try:
        from app.core.cookie_score import get_platform_score_summary
        return {"success": True, **get_platform_score_summary(platform)}
    except Exception as e_sc:
        raise HTTPException(status_code=500, detail=str(e_sc))


@router.get("/cookies/selection-history/{platform}")
async def get_cookie_selection_history(platform: str, _=Depends(verify_admin)):
    """27B: Recent cookie selection log for a platform (last 1h, hash prefixes only)."""
    try:
        from app.core.cookie_score import get_selection_history, is_scoring_enabled, is_shadow_mode
        return {
            "success":        True,
            "platform":       platform,
            "scoringEnabled": is_scoring_enabled(platform),
            "shadowMode":     is_shadow_mode(),
            "recent":         get_selection_history(platform),
        }
    except Exception as e_sc:
        raise HTTPException(status_code=500, detail=str(e_sc))


@router.post("/cookies/priority/{platform}/{cookie_hash}")
async def set_cookie_priority(
    platform: str, cookie_hash: str, request: Request, _=Depends(verify_admin),
):
    """27B: Set manual priority weight (0.0–1.0) for a specific cookie."""
    body  = await request.json()
    weight = float(body.get("weight", 0.5))
    if not 0.0 <= weight <= 1.0:
        raise HTTPException(status_code=400, detail="weight must be 0.0–1.0")
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.hset(f"p27b:priority:{platform}:{cookie_hash}", "weight", str(weight))
        log_admin_action(request, "admin.cookie.priority_set", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash, "weight": weight}
    except Exception as e_pr:
        raise HTTPException(status_code=500, detail=str(e_pr))


@router.post("/cookies/disable/{platform}/{cookie_hash}")
async def disable_cookie_from_scoring(
    platform: str, cookie_hash: str, request: Request, _=Depends(verify_admin),
):
    """27B: Manually exclude a cookie from scored selection (admin gate, not hard-block)."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.set(f"p27b:disabled:{platform}:{cookie_hash}", "1")
        log_admin_action(request, "admin.cookie.scoring_disabled", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash, "disabled": True}
    except Exception as e_dis:
        raise HTTPException(status_code=500, detail=str(e_dis))


@router.delete("/cookies/disable/{platform}/{cookie_hash}")
async def enable_cookie_for_scoring(
    platform: str, cookie_hash: str, request: Request, _=Depends(verify_admin),
):
    """27B: Re-enable a manually-disabled cookie."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.delete(f"p27b:disabled:{platform}:{cookie_hash}")
        log_admin_action(request, "admin.cookie.scoring_enabled", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash, "disabled": False}
    except Exception as e_en:
        raise HTTPException(status_code=500, detail=str(e_en))


# ── Cookie pool-level disable / expired mark (Phase 29 mini) ─────────────────
# These operate on cookie_health:{platform}:{hash} directly (no TTL = permanent
# until admin re-enables). Different from p27b:disabled (scoring-level only).

class _PoolDisableReq(BaseModel):
    reason: Optional[str] = ""


@router.post("/cookies/pool-state/{platform}/{cookie_hash}/disable")
async def pool_disable_cookie(
    platform: str, cookie_hash: str, body: _PoolDisableReq,
    request: Request, _=Depends(verify_admin),
):
    """Mark cookie as admin-disabled in the pool (no TTL — must re-enable manually)."""
    try:
        from app.core.redis_client import get_redis
        import time as _t
        rc = get_redis()
        rc.set(f"cookie_health:{platform}:{cookie_hash}", "disabled")
        rc.delete(f"cookie_cooldown:{platform}:{cookie_hash}")
        # store reason in meta if provided
        if body.reason:
            meta_key = f"cookie_meta:{platform}:{cookie_hash}"
            raw = rc.get(meta_key)
            import json as _json
            meta = _json.loads(raw) if raw else {}
            meta["disabled_reason"] = body.reason
            meta["disabled_at"]     = int(_t.time())
            rc.set(meta_key, _json.dumps(meta))
        log_admin_action(request, "admin.cookie.pool_disabled", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash,
                "health_status": "disabled", "reason": body.reason}
    except Exception as e_pd:
        raise HTTPException(status_code=500, detail=str(e_pd))


@router.delete("/cookies/pool-state/{platform}/{cookie_hash}/disable")
async def pool_enable_cookie(
    platform: str, cookie_hash: str, request: Request, _=Depends(verify_admin),
):
    """Re-enable a pool-level disabled cookie (clears cookie_health key)."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        current = rc.get(f"cookie_health:{platform}:{cookie_hash}")
        if current == "disabled":
            rc.delete(f"cookie_health:{platform}:{cookie_hash}")
            # clear reason from meta
            meta_key = f"cookie_meta:{platform}:{cookie_hash}"
            raw = rc.get(meta_key)
            import json as _json
            if raw:
                meta = _json.loads(raw)
                meta.pop("disabled_reason", None)
                meta.pop("disabled_at", None)
                rc.set(meta_key, _json.dumps(meta))
        log_admin_action(request, "admin.cookie.pool_enabled", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash, "health_status": "healthy"}
    except Exception as e_pe:
        raise HTTPException(status_code=500, detail=str(e_pe))


@router.post("/cookies/pool-state/{platform}/{cookie_hash}/mark-expired")
async def pool_mark_expired(
    platform: str, cookie_hash: str, request: Request, _=Depends(verify_admin),
):
    """Manually mark a cookie as expired (admin override — no TTL, stays excluded)."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.set(f"cookie_health:{platform}:{cookie_hash}", "expired")
        rc.delete(f"cookie_cooldown:{platform}:{cookie_hash}")
        log_admin_action(request, "admin.cookie.marked_expired", resource_type="cookie",
                         resource_id=f"{platform}:{cookie_hash}")
        return {"success": True, "platform": platform, "hash": cookie_hash, "health_status": "expired"}
    except Exception as e_me:
        raise HTTPException(status_code=500, detail=str(e_me))

# ── Phase 27C — Fairness and Admission Control endpoints ──────────────────────

@router.get("/fairness/overview")
async def fairness_overview(_=Depends(verify_admin)):
    """27C: Cross-platform fairness + admission control overview."""
    try:
        from app.core.admission_control import get_all_admission_summary
        from app.core.delayed_queue import get_all_queue_depths
        from app.core.fairness_control import get_fairness_snapshot
        import os

        summaries  = get_all_admission_summary()
        queue_depths = get_all_queue_depths()
        total_delayed = sum(queue_depths.values())
        under_pressure = [
            p for p, d in queue_depths.items() if d > 0
        ]

        return {
            "success":               True,
            "admissionEnabled":      os.getenv("ADMISSION_CONTROL_ENABLED", "false").lower() in ("1","true","yes"),
            "fairnessEnabled":       os.getenv("FAIRNESS_CONTROL_ENABLED",  "false").lower() in ("1","true","yes"),
            "delayedAcceptEnabled":  os.getenv("DELAYED_ACCEPT_ENABLED",    "false").lower() in ("1","true","yes"),
            "shadowMode":            os.getenv("FAIRNESS_SHADOW_LOG",        "false").lower() in ("1","true","yes"),
            "totalDelayed":          total_delayed,
            "platformsUnderPressure": under_pressure,
            "queueDepths":           queue_depths,
            "platformSummaries":     summaries,
        }
    except Exception as e_fo:
        raise HTTPException(status_code=500, detail=str(e_fo))


@router.get("/fairness/platform/{platform}")
async def fairness_platform_detail(platform: str, _=Depends(verify_admin)):
    """27C: Full fairness + admission snapshot for one platform."""
    try:
        from app.core.fairness_control import get_fairness_snapshot
        from app.core.admission_control import get_admission_counters
        from app.core.delayed_queue import get_platform_snapshot

        fairness  = get_fairness_snapshot(platform)
        admission = get_admission_counters(platform)
        queue     = get_platform_snapshot(platform)

        total = sum(admission.values()) or 1
        return {
            "success":   True,
            "platform":  platform,
            "fairness":  fairness,
            "admission": {
                **admission,
                "total":        total,
                "rejectRate":   round((admission.get("temp_reject", 0) + admission.get("unavail", 0)) / total, 3),
                "delayRate":    round(admission.get("delayed", 0) / total, 3),
            },
            "delayedQueue": queue,
        }
    except Exception as e_fp:
        raise HTTPException(status_code=500, detail=str(e_fp))


@router.get("/fairness/delayed-queues")
async def fairness_delayed_queues(_=Depends(verify_admin)):
    """27C: All delayed queues snapshot (for admin home alert bar)."""
    try:
        from app.core.delayed_queue import get_all_queue_depths, get_oldest_wait_sec
        depths = get_all_queue_depths()
        items  = []
        for p, d in sorted(depths.items(), key=lambda x: -x[1]):
            items.append({
                "platform":     p,
                "depth":        d,
                "oldestWaitSec": get_oldest_wait_sec(p),
            })
        return {"success": True, "totalDelayed": sum(depths.values()), "queues": items}
    except Exception as e_dq:
        raise HTTPException(status_code=500, detail=str(e_dq))


@router.post("/fairness/promote/{platform}")
async def fairness_force_promote(platform: str, request: Request, _=Depends(verify_admin)):
    """27C: Manually trigger promotion of the top delayed job on a platform."""
    try:
        from app.core.delayed_queue import try_promote
        entry = try_promote(platform)
        log_admin_action(request, "admin.fairness.force_promote", resource_type="platform",
                         resource_id=platform)
        return {"success": True, "platform": platform, "promoted": entry is not None, "entry": entry}
    except Exception as e_prom:
        raise HTTPException(status_code=500, detail=str(e_prom))


# ── Phase 27D — Adaptive Wave Scheduler endpoints ────────────────────────────

@router.get("/orchestrator/wave-params/{platform}")
async def wave_params_platform(platform: str, _=Depends(verify_admin)):
    """27D: Full adaptive wave state + history for one platform."""
    try:
        from app.core.wave_scheduler import get_wave_params, get_platform_wave_snapshot
        import os
        snapshot = get_platform_wave_snapshot(platform)
        # Also compute current params (updates snapshot)
        current_params = get_wave_params(platform)
        return {
            "success":        True,
            "platform":       platform,
            "current":        current_params.to_dict(),
            "snapshot":       snapshot,
            "adaptiveEnabled": os.getenv("ADAPTIVE_WAVE_SCHEDULING_ENABLED", "false").lower() in ("1","true","yes"),
            "shadowOnly":      os.getenv("ADAPTIVE_MODE_SHADOW_ONLY", "false").lower() in ("1","true","yes"),
        }
    except Exception as e_wp:
        raise HTTPException(status_code=500, detail=str(e_wp))


@router.get("/orchestrator/wave-snapshot")
async def wave_params_all(_=Depends(verify_admin)):
    """27D: Wave state across all platforms that have active adaptive state."""
    try:
        from app.core.wave_scheduler import get_all_wave_snapshots
        import os
        snapshots = get_all_wave_snapshots()
        return {
            "success":         True,
            "adaptiveEnabled": os.getenv("ADAPTIVE_WAVE_SCHEDULING_ENABLED", "false").lower() in ("1","true","yes"),
            "shadowOnly":      os.getenv("ADAPTIVE_MODE_SHADOW_ONLY", "false").lower() in ("1","true","yes"),
            "platforms":       snapshots,
        }
    except Exception as e_ws:
        raise HTTPException(status_code=500, detail=str(e_ws))


# ── Phase 28 — Policy and Runtime Override endpoints ─────────────────────────

@router.get("/policy/platform/{platform}")
async def policy_platform(platform: str, _=Depends(verify_admin)):
    """28: Effective policy for a platform (merged: static < env < runtime)."""
    try:
        from app.core.platform_policy import get_platform_policy
        from app.core.runtime_overrides import (
            get_platform_mode_override, is_adaptive_frozen, is_scoring_frozen,
        )
        policy = get_platform_policy(platform)
        return {
            "success":       True,
            "platform":      platform,
            "policy":        policy.to_dict(),
            "modeOverride":  get_platform_mode_override(platform),
            "adaptiveFrozen": is_adaptive_frozen(platform),
            "scoringFrozen":  is_scoring_frozen(platform),
        }
    except Exception as e_pp:
        raise HTTPException(status_code=500, detail=str(e_pp))


@router.get("/policy/all")
async def policy_all(_=Depends(verify_admin)):
    """28: Effective policy for all known platforms."""
    try:
        from app.core.platform_policy import get_all_platform_policies
        from app.core.runtime_overrides import get_all_overrides, is_global_safe_mode
        policies = {p: pol.to_dict() for p, pol in get_all_platform_policies().items()}
        return {
            "success":        True,
            "policies":       policies,
            "overrides":      get_all_overrides(),
            "globalSafeMode": is_global_safe_mode(),
        }
    except Exception as e_pa:
        raise HTTPException(status_code=500, detail=str(e_pa))


class _OverrideModeReq(BaseModel):
    mode:   str
    reason: str = ""
    ttl:    Optional[int] = None


@router.post("/policy/platform/{platform}/mode")
async def set_platform_override_mode(
    platform: str, body: _OverrideModeReq,
    request: Request, _=Depends(verify_admin),
):
    """28: Place a platform into manual operating mode (no restart needed)."""
    try:
        from app.core.runtime_overrides import set_platform_mode
        ip = get_client_ip(request)[:16]
        set_platform_mode(platform, body.mode, body.reason, body.ttl, set_by=ip + "**")
        log_admin_action(request, "admin.policy.set_mode", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "mode": body.mode}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e_sm:
        raise HTTPException(status_code=500, detail=str(e_sm))


@router.delete("/policy/platform/{platform}/mode")
async def clear_platform_override_mode(
    platform: str, request: Request, _=Depends(verify_admin),
):
    """28: Remove manual mode override — platform returns to auto-derived state."""
    try:
        from app.core.runtime_overrides import clear_platform_mode
        clear_platform_mode(platform)
        log_admin_action(request, "admin.policy.clear_mode", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "modeOverride": None}
    except Exception as e_cm:
        raise HTTPException(status_code=500, detail=str(e_cm))


class _FreezeReq(BaseModel):
    reason: str = ""
    ttl:    int = 3600


@router.post("/policy/platform/{platform}/freeze-adaptive")
async def freeze_adaptive_platform(
    platform: str, body: _FreezeReq,
    request: Request, _=Depends(verify_admin),
):
    """28: Freeze adaptive wave scheduling for a platform (TTL seconds, default 1h)."""
    try:
        from app.core.runtime_overrides import freeze_adaptive
        freeze_adaptive(platform, body.reason, body.ttl)
        log_admin_action(request, "admin.policy.freeze_adaptive", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "adaptiveFrozen": True, "ttl": body.ttl}
    except Exception as e_fa:
        raise HTTPException(status_code=500, detail=str(e_fa))


@router.delete("/policy/platform/{platform}/freeze-adaptive")
async def unfreeze_adaptive_platform(
    platform: str, request: Request, _=Depends(verify_admin),
):
    """28: Unfreeze adaptive wave scheduling for a platform."""
    try:
        from app.core.runtime_overrides import unfreeze_adaptive
        unfreeze_adaptive(platform)
        log_admin_action(request, "admin.policy.unfreeze_adaptive", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "adaptiveFrozen": False}
    except Exception as e_ua:
        raise HTTPException(status_code=500, detail=str(e_ua))


@router.post("/policy/platform/{platform}/freeze-scoring")
async def freeze_scoring_platform(
    platform: str, body: _FreezeReq,
    request: Request, _=Depends(verify_admin),
):
    """28: Freeze scored cookie selection for a platform (falls back to LRU)."""
    try:
        from app.core.runtime_overrides import freeze_scoring
        freeze_scoring(platform, body.reason, body.ttl)
        log_admin_action(request, "admin.policy.freeze_scoring", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "scoringFrozen": True, "ttl": body.ttl}
    except Exception as e_fs:
        raise HTTPException(status_code=500, detail=str(e_fs))


@router.delete("/policy/platform/{platform}/freeze-scoring")
async def unfreeze_scoring_platform(
    platform: str, request: Request, _=Depends(verify_admin),
):
    """28: Unfreeze scored cookie selection for a platform."""
    try:
        from app.core.runtime_overrides import unfreeze_scoring
        unfreeze_scoring(platform)
        log_admin_action(request, "admin.policy.unfreeze_scoring", resource_type="platform", resource_id=platform)
        return {"success": True, "platform": platform, "scoringFrozen": False}
    except Exception as e_us:
        raise HTTPException(status_code=500, detail=str(e_us))


@router.post("/policy/safe-mode")
async def activate_global_safe_mode(
    request: Request, _=Depends(verify_admin),
):
    """28: Enable global safe mode — all platforms freeze adaptive + use min wave params."""
    try:
        body = await request.json()
        reason = body.get("reason", "admin_manual")
    except Exception:
        reason = "admin_manual"
    try:
        from app.core.runtime_overrides import set_global_safe_mode
        ip = get_client_ip(request)[:16]
        set_global_safe_mode(reason, set_by=ip + "**")
        log_admin_action(request, "admin.policy.global_safe_mode_on", resource_type="system", resource_id="global")
        return {"success": True, "globalSafeMode": True, "reason": reason}
    except Exception as e_gsm:
        raise HTTPException(status_code=500, detail=str(e_gsm))


@router.delete("/policy/safe-mode")
async def deactivate_global_safe_mode(request: Request, _=Depends(verify_admin)):
    """28: Disable global safe mode."""
    try:
        from app.core.runtime_overrides import clear_global_safe_mode
        clear_global_safe_mode()
        log_admin_action(request, "admin.policy.global_safe_mode_off", resource_type="system", resource_id="global")
        return {"success": True, "globalSafeMode": False}
    except Exception as e_cgsm:
        raise HTTPException(status_code=500, detail=str(e_cgsm))


@router.get("/policy/overrides")
async def get_all_overrides_view(_=Depends(verify_admin)):
    """28: All active overrides across all platforms + audit log."""
    try:
        from app.core.runtime_overrides import get_all_overrides, get_override_audit_log, is_global_safe_mode, get_global_safe_mode_info
        return {
            "success":        True,
            "globalSafeMode": is_global_safe_mode(),
            "globalInfo":     get_global_safe_mode_info(),
            "overrides":      get_all_overrides(),
            "auditLog":       get_override_audit_log(20),
        }
    except Exception as e_ao:
        raise HTTPException(status_code=500, detail=str(e_ao))
