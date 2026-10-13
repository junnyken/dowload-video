"""
Plans (docs/PLANS-FREE-PRO.md, owner 13/10).

  GET  /api/v1/plans                    public: the Khách/Free/Pro table, prices,
                                        bank-transfer details
  GET  /api/v1/plans/me                 signed-in: effective tier, expiry and the
                                        transfer code to put in the bank memo
  GET  /api/v1/admin/plans/settings     admin: price + bank details
  POST /api/v1/admin/plans/settings     admin: update them (audited)
  GET  /api/v1/admin/plans/find?code=   admin: the account behind a transfer code
Granting Pro for N days: POST /api/v1/admin/update-user {plan:'pro', days:N}.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core import plans
from app.core.audit import log_admin_action
from app.core.auth_middleware import get_required_user

public_router = APIRouter()
admin_router = APIRouter()


class SettingsBody(BaseModel):
    price_month_vnd: Optional[int] = None
    price_year_vnd: Optional[int] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    bank_holder: Optional[str] = None
    contact: Optional[str] = None


def _profile(user_id: str) -> Dict[str, Any]:
    from app.core.quotas import _get_profile  # noqa: PLC0415
    return _get_profile(user_id) or {}


@public_router.get("/plans")
async def get_plans():
    return plans.build_plans()


@public_router.get("/plans/me")
async def get_my_plan(user: Dict[str, Any] = Depends(get_required_user)):
    from app.core.entitlements import resolve_effective_tier  # noqa: PLC0415
    uid = str(user["id"])
    prof = _profile(uid)
    return {
        "tier": resolve_effective_tier(prof) if prof else "free",
        "billing_status": prof.get("billing_status") or "none",
        "expiry": prof.get("subscription_expiry"),
        "transfer_code": plans.transfer_code(uid),
    }


@admin_router.get("/plans/settings")
async def admin_get_settings(_=Depends(verify_admin)):
    return plans.get_settings()


@admin_router.post("/plans/settings")
async def admin_set_settings(body: SettingsBody, request: Request, _=Depends(verify_admin)):
    old = plans.get_settings()
    try:
        new = plans.set_settings(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    changed = {k: {"old": old.get(k), "new": v} for k, v in new.items() if old.get(k) != v}
    try:
        log_admin_action(request, "admin.plans.settings", resource_type="plans",
                         resource_id="settings", metadata=changed)
    except Exception:  # noqa: BLE001
        pass
    return new


@admin_router.get("/plans/find")
async def admin_find_by_code(code: str = Query(..., max_length=20), _=Depends(verify_admin)):
    prefix = plans.code_prefix(code)
    if not prefix:
        raise HTTPException(status_code=422, detail="Mã có dạng VG + 8 ký tự (vd VG1A2B3C4D)")
    from app.core.database import get_supabase_client  # noqa: PLC0415
    from app.core.entitlements import resolve_effective_tier  # noqa: PLC0415
    lo = f"{prefix}-0000-0000-0000-000000000000"
    hi = f"{prefix}-ffff-ffff-ffff-ffffffffffff"
    rows = (get_supabase_client().table("profiles")
            .select("id, email, tier, billing_status, subscription_expiry")
            .gte("id", lo).lte("id", hi).limit(5).execute()).data or []
    return {"matches": [{**r, "effective_tier": resolve_effective_tier(r),
                         "transfer_code": plans.transfer_code(r["id"])} for r in rows]}
