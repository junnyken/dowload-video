"""
Extension session endpoints (owner 22/10/2026 — see app.core.extension_session)

  POST   /extension/session   Bearer <web Supabase JWT | current vgx_ token>
                              -> {token, expiresAt, email}
  DELETE /extension/session   Bearer <either>  -> every extension token of the
                              user stops working (sign-out)

Signed-in callers only; a guest gets 401.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core import extension_session
from app.core.auth_middleware import get_optional_user
from app.main import limiter

router = APIRouter()

# wording: BA review
MSG_LOGIN = "Hãy đăng nhập VidGrab để kết nối tiện ích."
MSG_OFF = "Máy chủ chưa sẵn sàng cấp phiên cho tiện ích. Hãy thử lại sau."


@router.post("/extension/session")
@limiter.limit("30/minute")
async def create(request: Request, user=Depends(get_optional_user)):
    if not user or user.get("via_api_key"):
        return JSONResponse(status_code=401, content={"detail": MSG_LOGIN, "error_code": "login_required"})
    from app.core import device_link  # noqa: PLC0415
    out = extension_session.issue(str(user["id"]), user.get("email"), email_confirmed_at=user.get("email_confirmed_at"),
                                  device=device_link.device_from_request(request))
    if not out:
        return JSONResponse(status_code=503, content={"detail": MSG_OFF, "error_code": "extension_session_unavailable"})
    return {**out, "email": user.get("email")}


@router.delete("/extension/session")
@limiter.limit("30/minute")
async def revoke(request: Request, user=Depends(get_optional_user)):
    if not user or user.get("via_api_key"):
        return JSONResponse(status_code=401, content={"detail": MSG_LOGIN, "error_code": "login_required"})
    from app.core import device_link  # noqa: PLC0415
    dev = device_link.device_from_request(request)
    if dev:
        # 24/10: only this device signs out (it used to end the extension on every device)
        device_link.forget_device(str(user["id"]), dev)   # no longer one of the account's devices
        return {"revoked": extension_session.revoke_device(str(user["id"]), dev)}
    return {"revoked": extension_session.revoke(str(user["id"]))}
