"""
Hooks between the existing download path and the China access layer.

Contract with the existing code: while CHINA_ACCESS_ENABLED is off (default)
every function here returns after reading ONE environment variable — no
Redis, no network, no state — so today's Douyin, TikTok, Bilibili and generic
paths behave exactly as before.

When on (and CHINA_ACCESS_DOUYIN_ENABLED on), Douyin single media is resolved
by the router: native chain first (ScraperAPI skipped — paid, unbudgeted),
then the managed actor if the managed mode allows it for this request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.services.china_platforms import budget_guard, registry, settings
from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    RequestContext,
    current_context,
    set_context,
)

logger = logging.getLogger("app.china_access")


def _is_admin_session(request) -> bool:
    """Valid admin SESSION token in X-Admin-Token (issued by POST /admin/login).
    The raw admin password is deliberately not accepted here: this header is
    read on a public endpoint, and accepting the password would give it a
    brute-force surface without verify_admin's lockout."""
    try:
        token = (request.headers.get("X-Admin-Token") or "").strip()
        if not token:
            return False
        from app.api.admin import _redis, _session_is_valid  # noqa: PLC0415
        return _session_is_valid(_redis(), token)
    except Exception:  # noqa: BLE001
        return False


def bind_request_context(request, user: Optional[dict], *, has_user_cookie: bool = False):
    """Called by /fetch-link. No-op unless CHINA_ACCESS_ENABLED. Returns the
    contextvar token (or None). The context is per request task."""
    if not settings.master_enabled():
        return None
    try:
        is_admin = _is_admin_session(request)
        if is_admin:
            key = "admin"
        elif user and user.get("id"):
            key = f"user:{user['id']}"
        else:
            from app.core.client_ip import get_client_ip  # noqa: PLC0415
            key = f"ip:{get_client_ip(request)}"
        return set_context(RequestContext(requester_key=key, is_admin=is_admin, origin="request",
                                          private=has_user_cookie))
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access bind_request_context failed: %s", type(exc).__name__)
        return None


def _layer_active(platform: str) -> bool:
    """Env first (cheap); the Redis kill switches only when env says on.
    A kill switch (or an unreadable one) sends the request back to the
    untouched legacy path — that IS the rollback (plan §20)."""
    if not settings.master_enabled() or not settings.platform_env_enabled(platform):
        return False
    try:
        if budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(platform):
            return False
    except Exception:  # noqa: BLE001
        return False
    return True


def managed_route_open_for_current_request(platform: str) -> bool:
    """Would a managed provider be eligible for the current request context?
    Used by douyin_server_access_available() so the 422 cookie_required gate
    lets an admin canary request through. False when flags are off. Budget is
    not checked here (the router does that before any dispatch)."""
    if not _layer_active(platform):
        return False
    try:
        if not settings.apify_token():
            return False
        mode = registry.effective_managed_mode(platform)
        return registry.managed_allowed_for(mode, current_context())
    except Exception:  # noqa: BLE001
        return False


def _to_legacy_dict(result, original_url: str, quality: str) -> Dict[str, Any]:
    """Shape the downloader's Douyin branch already consumes."""
    video = result.primary_video_url()
    audio = result.audio_url()
    direct = audio if (quality or "").startswith("mp3") and audio else video
    return {
        "title": result.title,
        "thumbnail_url": result.thumbnail_url or "",
        "direct_mp4_url": direct,
        "audio_url": audio,
        "file_size_mb": 0,
        "duration": int(result.duration_sec or 0),
        "quality": quality,
        "original_url": original_url,
        "provider": result.provider_name,
    }


def _user_message(exc: ChinaAccessFailure) -> str:
    """Keep the existing Douyin wording whenever the chain ended on the cookie
    requirement: failure_classifier matches it (USER_ACTION, no retry) and the
    outcome store counts it as cookie_required (2026-10-05 incident fix)."""
    if exc.platform == "douyin" and (exc.has_category("cookie_required")
                                     or exc.has_category("signature_or_verification_failed")
                                     or exc.has_category("cookie_invalid")):
        from app.services.douyin_extractor import DOUYIN_COOKIE_REQUIRED_MSG  # noqa: PLC0415
        return DOUYIN_COOKIE_REQUIRED_MSG
    return exc.user_message


def resolve_douyin_via_access_layer(douyin_input: str, original_url: str, quality: str,
                                    user_cookies_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """None → access layer inactive, caller runs the legacy path unchanged.
    dict → resolved metadata in the legacy shape. Raises ValueError (with a
    user-safe message) when the layer is active and resolution failed."""
    if not _layer_active("douyin"):
        return None
    from app.services.china_platforms.provider_router import ProviderRouter  # noqa: PLC0415

    ctx = current_context()
    if user_cookies_file:
        ctx = ctx.with_(private=True, user_cookies_file=user_cookies_file)
    # Celery jobs carry no requester context: they use the shared "unknown"
    # quota bucket at the anonymous limit (docs/china-access/01 C8).
    req = ChinaResolveRequest(url=douyin_input, operation="single_media", requested_quality=quality)
    try:
        result = asyncio.run(ProviderRouter().resolve(req, ctx))
    except ChinaAccessFailure as exc:
        raise ValueError(_user_message(exc)) from None
    except AlreadyProcessing:
        # Wording pending BA review.
        raise ValueError("Video Douyin này đang được xử lý cho một yêu cầu khác. Vui lòng thử lại sau ít phút.") from None
    return _to_legacy_dict(result, original_url, quality)
