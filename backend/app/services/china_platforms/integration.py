"""
Hooks between the existing download path and the China access layer.

Contract with the existing code: while CHINA_ACCESS_ENABLED is off (default)
every function here returns after reading ONE environment variable — no
Redis, no network, no state — so today's Douyin, TikTok, Bilibili and generic
paths behave exactly as before.

When on (and CHINA_ACCESS_DOUYIN_ENABLED on), Douyin single media is resolved
by the router: native chain first (ScraperAPI skipped — paid, unbudgeted),
then the managed actor if the managed mode allows it for this request.

Phase 32B-3: the same holds for Kuaishou (managed only) and Xiaohongshu
(native extractor, then managed) behind CHINA_ACCESS_KUAISHOU_ENABLED /
CHINA_ACCESS_XIAOHONGSHU_ENABLED. china_platform_for_download() is the gate
the downloader uses; with the master flag off it returns after one env read.
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
    Same definition of "admin" as the per-platform download allowance
    (app.core.quotas.is_admin_request): the raw admin password is deliberately
    not accepted here — this header is read on a public endpoint."""
    from app.core.quotas import is_admin_request  # noqa: PLC0415
    return is_admin_request(request)


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
        if not settings.apify_configured():
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


class ChinaUserError(ValueError):
    """User-facing failure of the access layer for Kuaishou / Xiaohongshu.
    str() is the Vietnamese text; error_code drives the HTTP status
    (app.core.extraction_errors) on /fetch-link."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


# Failure categories that mean "this link has no video we can get" (dead or
# expired share link, deleted video, a link that is not a video).
_LINK_DEAD = ("parse_failed", "unsupported_url")


def _hit_daily_limit(exc: ChinaAccessFailure) -> bool:
    """budget_exceeded at the per-person level (router detail "user_quota: …"),
    not a platform / vendor spending ceiling."""
    return any(f.category == "budget_exceeded" and (f.internal_detail or "").startswith("user_quota")
               for f in exc.failures)


def _hook_platform_error(platform: str, exc: ChinaAccessFailure, ctx) -> ChinaUserError:
    """Owner 2026-10-07 (task #6055): one message per cause instead of
    "Nền tảng nguồn tạm thời không phản hồi." for everything."""
    name = _DISPLAY.get(platform, platform)
    if _hit_daily_limit(exc):
        from app.core import quotas  # noqa: PLC0415
        if (ctx.requester_key or "").startswith("user:"):
            msg = (f"Bạn đã dùng hết lượt tải {name} hôm nay. "
                   f"Lượt mới được cộng lại lúc {quotas.reset_time_vn_text()} (giờ Việt Nam).")
        else:
            msg = (f"Bạn đã dùng hết lượt tải {name} của khách hôm nay. "
                   f"Đăng nhập để có {quotas.platform_limit_user()} lượt/ngày.")
        return ChinaUserError(msg, "china_daily_limit")
    if exc.category in _LINK_DEAD:
        return ChinaUserError(
            f"Link {name} này không còn video hoặc đã hết hạn. "
            f"Hãy mở app {name}, bấm Chia sẻ → Sao chép liên kết rồi dán lại.",
            "china_link_unavailable")
    if exc.category == "private_or_login_required":
        return ChinaUserError(exc.user_message, "private_or_login_required")
    return ChinaUserError("Nền tảng nguồn tạm thời không phản hồi. Thử lại sau vài phút.",
                          "china_source_unavailable")


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


# Platforms the downloader hands to the layer through
# china_platform_for_download(). Douyin has its own hook inside the Douyin
# branch (resolve_douyin_via_access_layer) and is not listed here.
DOWNLOAD_HOOK_PLATFORMS = ("kuaishou", "xiaohongshu")

_DISPLAY = {"douyin": "Douyin", "kuaishou": "Kuaishou", "xiaohongshu": "Xiaohongshu"}


def china_platform_for_download(url: str) -> Optional[str]:
    """Kuaishou / Xiaohongshu single-video URL whose access layer is active →
    the platform name; anything else → None (the caller runs its legacy path
    unchanged). Order: master env flag, platform env flag, URL match, then the
    Redis kill switches — so with flags off nothing but env is read."""
    if not settings.master_enabled():
        return None
    try:
        for platform in DOWNLOAD_HOOK_PLATFORMS:
            if not settings.platform_env_enabled(platform):
                continue
            adapter = registry.get_adapter(platform)
            if adapter is not None and adapter.matches(url or ""):
                return platform if _layer_active(platform) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access platform gate failed: %s", type(exc).__name__)
    return None


def _resolve_via_access_layer(platform: str, url: str, original_url: str, quality: str,
                              user_cookies_file: Optional[str]) -> Dict[str, Any]:
    from app.services.china_platforms.provider_router import ProviderRouter  # noqa: PLC0415

    ctx = current_context()
    if user_cookies_file:
        ctx = ctx.with_(private=True, user_cookies_file=user_cookies_file)
    # Celery jobs get their requester from process_video_task (_requester /
    # user_id, see video_tasks._bind_china_worker_context); jobs with neither
    # (crash recovery, partner API) use the shared "unknown" bucket at the
    # anonymous limit (docs/china-access/01 C8).
    req = ChinaResolveRequest(url=url, operation="single_media", requested_quality=quality)
    try:
        result = asyncio.run(ProviderRouter().resolve(req, ctx))
    except ChinaAccessFailure as exc:
        if platform in DOWNLOAD_HOOK_PLATFORMS:
            raise _hook_platform_error(platform, exc, ctx) from None
        raise ValueError(_user_message(exc)) from None
    except AlreadyProcessing:
        # Wording pending BA review.
        raise ValueError(f"Video {_DISPLAY.get(platform, platform)} này đang được xử lý cho một yêu cầu khác. "
                         "Vui lòng thử lại sau ít phút.") from None
    out = _to_legacy_dict(result, original_url, quality)
    if platform != "douyin":
        out["platform"] = platform
        out["uploader"] = result.uploader
        out["managed"] = result.provider_mode == "managed"
    return out


def resolve_douyin_via_access_layer(douyin_input: str, original_url: str, quality: str,
                                    user_cookies_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """None → access layer inactive, caller runs the legacy path unchanged.
    dict → resolved metadata in the legacy shape. Raises ValueError (with a
    user-safe message) when the layer is active and resolution failed."""
    if not _layer_active("douyin"):
        return None
    return _resolve_via_access_layer("douyin", douyin_input, original_url, quality, user_cookies_file)


def list_douyin_channel_via_access_layer(channel_url: str, max_videos: int) -> Optional[Dict[str, Any]]:
    """Douyin profile → the downloader's channel-scrape shape (task #6055).
    None when the managed channel route is not open for the current requester
    (the caller keeps its legacy scrapers). Otherwise one budgeted Apify run,
    capped at what the requester can still download today; a refusal or
    failure raises ChannelListingError (a ValueError, user-safe text) and the
    legacy scrapers are NOT tried — their per-video jobs would bypass the cap."""
    from app.services.china_platforms import channel_listing  # noqa: PLC0415
    ctx = current_context()
    if not channel_listing.route_open(ctx):
        return None
    listing = asyncio.run(channel_listing.list_douyin_profile(channel_url, max_videos, ctx))
    return listing.to_bulk_result()


def resolve_platform_via_access_layer(platform: str, url: str, original_url: str, quality: str,
                                      user_cookies_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Kuaishou / Xiaohongshu counterpart of resolve_douyin_via_access_layer:
    None when the layer is not active for `platform`, else the legacy-shaped
    dict (plus "platform", "uploader", "managed"), or ValueError."""
    if platform not in DOWNLOAD_HOOK_PLATFORMS or not _layer_active(platform):
        return None
    return _resolve_via_access_layer(platform, url, original_url, quality, user_cookies_file)
