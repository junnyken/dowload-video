"""
Referral & Affiliate Program
============================
Allows authenticated users to share a unique referral link (?ref=REFXXXXXXXX).
Tracks clicks, sign-ups, and Pro conversions in Redis, and automatically grants
+7 bonus Pro days to the referrer whenever a referred user upgrades to Pro.

Routes:
  GET  /api/v1/referrals/me     — current user's referral code, link, and stats
  POST /api/v1/referrals/click  — record a unique referral link visit (per IP/24h)
  POST /api/v1/referrals/claim  — bind a signed-in user to their referrer once
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.client_ip import get_client_ip
from app.core.database import get_service_client
from app.core.redis_client import get_redis

router = APIRouter(prefix="/referrals", tags=["Referrals"])

REWARD_DAYS_PER_PRO = int(os.getenv("REFERRAL_REWARD_DAYS", "7") or 7)
_REF_RE = re.compile(r"^REF[0-9A-F]{8}$")
_SITE_URL = (
    os.getenv("VIDGRAB_WEB_URL", "").strip().rstrip("/")
    or os.getenv("FRONTEND_URL", "").split(",")[0].strip().rstrip("/")
    or "https://dowloadvideo.io.vn"
)


def referral_code_for(user_id: str) -> str:
    clean = (user_id or "").replace("-", "").upper()[:8]
    return f"REF{clean}"


def _normalize_code(raw: str) -> str:
    c = (raw or "").strip().upper()
    return c if _REF_RE.match(c) else ""


def _read_stats(user_id: str) -> Dict[str, int]:
    out = {"clicks": 0, "signups": 0, "pro_conversions": 0, "reward_days": 0}
    try:
        raw = get_redis().hgetall(f"referral:stats:{user_id}") or {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else str(v)
            if key in out:
                out[key] = int(val or 0)
    except Exception:
        pass
    return out


class ReferralCodePayload(BaseModel):
    code: str


@router.get("/me")
async def get_my_referral(user: Dict[str, Any] = Depends(get_required_user)):
    """Return current user's referral code, share URL, and affiliate stats."""
    uid = str(user["id"])
    code = referral_code_for(uid)
    try:
        get_redis().set(f"referral:code:{code}", uid)
    except Exception:
        pass
    stats = _read_stats(uid)
    return {
        "code": code,
        "url": f"{_SITE_URL}/?ref={code}",
        "clicks": stats["clicks"],
        "signups": stats["signups"],
        "pro_conversions": stats["pro_conversions"],
        "reward_days": stats["reward_days"],
        "reward_per_pro_days": REWARD_DAYS_PER_PRO,
    }


@router.post("/click")
async def record_referral_click(payload: ReferralCodePayload, request: Request):
    """Record a unique referral link visit (deduplicated per IP per 24 hours)."""
    code = _normalize_code(payload.code)
    if not code:
        return {"recorded": False}
    try:
        rc = get_redis()
        referrer_id = rc.get(f"referral:code:{code}")
        if isinstance(referrer_id, bytes):
            referrer_id = referrer_id.decode()
        if not referrer_id:
            return {"recorded": False}
        ip = get_client_ip(request)
        dedup_key = f"referral:click_ip:{code}:{ip}"
        if rc.set(dedup_key, "1", nx=True, ex=86400):
            rc.hincrby(f"referral:stats:{referrer_id}", "clicks", 1)
            return {"recorded": True}
    except Exception:
        pass
    return {"recorded": False}


@router.post("/claim")
async def claim_referral(
    payload: ReferralCodePayload,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Bind a newly signed-in user to a referral code once."""
    uid = str(user["id"])
    code = _normalize_code(payload.code)
    if not code or code == referral_code_for(uid):
        return {"claimed": False, "reason": "self_or_invalid"}
    try:
        rc = get_redis()
        referrer_id = rc.get(f"referral:code:{code}")
        if isinstance(referrer_id, bytes):
            referrer_id = referrer_id.decode()
        if not referrer_id or referrer_id == uid:
            return {"claimed": False, "reason": "unknown_code"}
        if rc.set(f"referral:referred_by:{uid}", referrer_id, nx=True):
            rc.hincrby(f"referral:stats:{referrer_id}", "signups", 1)
            return {"claimed": True}
    except Exception:
        pass
    return {"claimed": False, "reason": "already_claimed"}


def reward_referrer_on_pro_upgrade(referred_user_id: str) -> bool:
    """
    Called when a user upgrades to Pro. If they were referred by another user
    and haven't triggered a reward yet, grants +REWARD_DAYS_PER_PRO days of Pro
    to the referrer.
    """
    if not referred_user_id:
        return False
    try:
        rc = get_redis()
        referrer_id = rc.get(f"referral:referred_by:{referred_user_id}")
        if isinstance(referrer_id, bytes):
            referrer_id = referrer_id.decode()
        if not referrer_id or referrer_id == referred_user_id:
            return False
        if not rc.set(f"referral:rewarded:{referred_user_id}", referrer_id, nx=True):
            return False
        rc.hincrby(f"referral:stats:{referrer_id}", "pro_conversions", 1)
        rc.hincrby(f"referral:stats:{referrer_id}", "reward_days", REWARD_DAYS_PER_PRO)

        sb = get_service_client()
        res = (
            sb.table("profiles")
            .select("tier, subscription_expiry")
            .eq("id", referrer_id)
            .single()
            .execute()
        )
        row = res.data or {}
        now = datetime.now(timezone.utc)
        base = now
        exp_str = row.get("subscription_expiry")
        if exp_str:
            try:
                parsed = datetime.fromisoformat(str(exp_str).replace("Z", "+00:00"))
                if parsed > now:
                    base = parsed
            except Exception:
                pass
        new_expiry = (base + timedelta(days=REWARD_DAYS_PER_PRO)).isoformat()
        sb.table("profiles").update({
            "tier": "pro",
            "subscription_expiry": new_expiry,
        }).eq("id", referrer_id).execute()
        return True
    except Exception as exc:
        print(f"[Referral] reward failed for {referred_user_id}: {exc}")
        return False
