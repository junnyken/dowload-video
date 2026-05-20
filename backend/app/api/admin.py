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

import os
import base64
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from app.core.database import get_supabase_client
from datetime import datetime, timezone, timedelta

router = APIRouter()

# ── Server-side admin auth ───────────────────────────────────────────
_ADMIN_TOKEN_HEADER = APIKeyHeader(name="X-Admin-Token", auto_error=False)
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "matbaosupport")

async def verify_admin(token: Optional[str] = Depends(_ADMIN_TOKEN_HEADER)):
    if not token or token != _ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")


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
        
        import httpx
        import os
        
        # Real-time ScraperAPI credits fetch
        scraper_api_key = os.getenv("SCRAPERAPI_API_KEY", os.getenv("SCRAPERAPI_KEY", ""))
        if scraper_api_key:
            try:
                # ScraperAPI account info endpoint
                resp = httpx.get(f"http://api.scraperapi.com/account?api_key={scraper_api_key}", timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    remaining = data.get("requestLimit", 0) - data.get("requestCount", 0)
                    providers["ScraperAPI"] = remaining
                    
                    # Update DB for background sync
                    supabase.table("provider_status").upsert({
                        "provider_name": "ScraperAPI", 
                        "remaining_credits": remaining
                    }).execute()

                    # Trigger Telegram alert if credits are low
                    try:
                        from app.core.notifications import notify_credits_low, CREDITS_WARNING_THRESHOLD
                        if remaining < CREDITS_WARNING_THRESHOLD:
                            await notify_credits_low("ScraperAPI", remaining)
                    except Exception:
                        pass  # Non-critical, don't break admin stats

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
async def send_test_notification(_=Depends(verify_admin)):
    """Send a test notification to Telegram to verify configuration."""
    try:
        from app.core.notifications import send_telegram_message

        result = await send_telegram_message(
            "🧪 <b>Test Notification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Telegram notification đang hoạt động!\n"
            "📡 Gửi từ: VidGrab Admin Dashboard"
        )

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
async def update_user(req: UpdateUserRequest, _=Depends(verify_admin)):
    supabase = get_supabase_client()
    try:
        if req.plan not in ["free", "pro"]:
            raise HTTPException(status_code=400, detail="Invalid plan status")
            
        res = supabase.table("user_usage").update({"plan": req.plan}).eq("user_id", req.user_id).execute()
        if not res.data:
            # If user doesn't exist, create it
            supabase.table("user_usage").insert({
                "user_id": req.user_id,
                "plan": req.plan,
                "downloads_today": 0,
                "last_reset_at": datetime.now(timezone.utc).isoformat()
            }).execute()
            
        return {"success": True, "message": f"User {req.user_id} updated to {req.plan}"}
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
# Cookie Pool Management
# ═════════════════════════════════════════════════════════════════════

class CookieAddRequest(BaseModel):
    platform: str   # youtube | tiktok | facebook | instagram
    cookies_b64: str  # base64-encoded Netscape cookies.txt

class CookieRemoveRequest(BaseModel):
    platform: str
    index: int  # position in pool (0-based)


_VALID_PLATFORMS = {"youtube", "tiktok", "facebook", "instagram"}


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
    """List all cookies in pool with hash + health status."""
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    try:
        from app.core.cookie_pool import _hash
        from app.core.redis_client import get_redis
        rc = get_redis()
        cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
        items = []
        for i, c in enumerate(cookies):
            h = _hash(c)
            blocked = rc.get(f"cookie_health:{platform}:{h}") == "blocked"
            ttl = rc.ttl(f"cookie_health:{platform}:{h}") if blocked else None
            items.append({
                "index": i,
                "hash": h,
                "status": "blocked" if blocked else "healthy",
                "blocked_ttl_s": ttl,
                "preview": c[:20] + "...",  # first 20 chars as hint
            })
        return {"success": True, "platform": platform, "total": len(items), "cookies": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cookies/upload")
async def cookie_pool_upload(
    platform: str = Form(...),
    file: UploadFile = File(...),
    _=Depends(verify_admin),
):
    """
    Upload cookies.txt file directly — no base64 needed.
    Accepts Netscape format cookies.txt from browser extension.
    Supports multiple files in one session (call multiple times).
    """
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    try:
        content = await file.read()
        cookies_b64 = base64.b64encode(content).decode("utf-8")
        from app.core.cookie_pool import add_cookie
        new_size = add_cookie(platform, cookies_b64)
        return {"success": True, "platform": platform, "pool_size": new_size,
                "message": f"Cookie added to {platform} pool (total: {new_size})"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cookies/add")
async def cookie_pool_add(req: CookieAddRequest, _=Depends(verify_admin)):
    """Add a cookie (base64) to the rotating pool. Use /cookies/upload for file upload."""
    if req.platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {_VALID_PLATFORMS}")
    if not req.cookies_b64.strip():
        raise HTTPException(status_code=400, detail="cookies_b64 is required")
    try:
        from app.core.cookie_pool import add_cookie
        new_size = add_cookie(req.platform, req.cookies_b64.strip())
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cookies/remove")
async def cookie_pool_remove(req: CookieRemoveRequest, _=Depends(verify_admin)):
    """Remove a cookie by index from the pool."""
    try:
        from app.core.cookie_pool import remove_cookie
        new_size = remove_cookie(req.platform, req.index)
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
async def proxy_pool_add(req: ProxyAddRequest, _=Depends(verify_admin)):
    """Add a proxy URL to the pool for a platform."""
    if not req.proxy_url.strip():
        raise HTTPException(status_code=400, detail="proxy_url is required")
    if not req.proxy_url.startswith(("http://", "https://", "socks5://")):
        raise HTTPException(status_code=400, detail="proxy_url must start with http://, https://, or socks5://")
    try:
        from app.core.proxy_pool import add_proxy
        new_size = add_proxy(req.platform, req.proxy_url.strip())
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/proxies/remove")
async def proxy_pool_remove(req: ProxyRemoveRequest, _=Depends(verify_admin)):
    """Remove a proxy by index."""
    try:
        from app.core.proxy_pool import remove_proxy
        new_size = remove_proxy(req.platform, req.index)
        return {"success": True, "platform": req.platform, "pool_size": new_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
