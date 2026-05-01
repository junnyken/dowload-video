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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from app.core.database import get_supabase_client
from datetime import datetime, timezone, timedelta

router = APIRouter()


class UpdateUserRequest(BaseModel):
    user_id: str  # Normally email/ID, here using IP/identifier
    plan: str     # 'free' or 'pro'


# ═════════════════════════════════════════════════════════════════════
# GET /stats — Overview Dashboard Data
# ═════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_admin_stats(request: Request):
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
            "api_keys": {
                "ScraperAPI": os.getenv("SCRAPERAPI_API_KEY", os.getenv("SCRAPERAPI_KEY", "Not Set")),
                "IPRoyal": os.getenv("IPROYAL_PROXY", "Not Set")
            }
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
async def get_admin_analytics(days: int = 7):
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
async def get_active_jobs():
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
async def send_test_notification():
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
async def update_user(req: UpdateUserRequest):
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
