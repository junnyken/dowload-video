from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.core.database import get_supabase_client
from datetime import datetime, timezone

router = APIRouter()

class UpdateUserRequest(BaseModel):
    user_id: str  # Normally email/ID, here using IP/identifier
    plan: str     # 'free' or 'pro'

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
        
        import os
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
