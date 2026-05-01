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
    import os
    import hmac
    import hashlib
    import json

    webhook_secret = os.getenv("PAYMENT_WEBHOOK_SECRET")
    if webhook_secret:
        signature = request.headers.get("X-Signature")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing signature")
            
        body = await request.body()
        expected_signature = hmac.new(
            webhook_secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
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
