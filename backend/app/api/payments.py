from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.core.database import get_supabase_client
from datetime import datetime, timezone, timedelta

router = APIRouter()

class WebhookPayload(BaseModel):
    user_id: str
    tier: str # 'pro', 'vip'
    duration_days: int
    transaction_id: str

@router.post("/webhook")
async def payment_webhook(payload: WebhookPayload, request: Request):
    """
    Handle successful payments from Momo/Stripe.
    In production, always verify webhook signatures here!
    """
    # TODO: Verify Signature using request.headers
    
    supabase = get_supabase_client()
    now_utc = datetime.now(timezone.utc)
    expiry = now_utc + timedelta(days=payload.duration_days)
    
    try:
        # Update or Insert to profiles directly using supabase rules
        # Make sure user exists in Supabase. For simplicity, we upsert based on user_id.
        existing = supabase.table("profiles").select("id").eq("id", payload.user_id).execute()
        
        if existing.data:
            supabase.table("profiles").update({
                "tier": payload.tier,
                "subscription_expiry": expiry.isoformat()
            }).eq("id", payload.user_id).execute()
        else:
            supabase.table("profiles").insert({
                "id": payload.user_id,
                "tier": payload.tier,
                "subscription_expiry": expiry.isoformat()
            }).execute()
            
        return {"success": True, "message": "Tier updated", "user": payload.user_id, "tier": payload.tier}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update failed: {e}")
