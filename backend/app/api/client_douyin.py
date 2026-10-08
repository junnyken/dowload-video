"""
Douyin for the Windows app (task #6055, app 0.5.0)
==================================================
The app downloads with its own yt-dlp, which cannot read Douyin (signature
cookies). These two routes let the server do the part it can — the managed
Apify route of the China access layer — and hand the app a direct media URL
it downloads itself.

  POST /client/douyin/channel  {url, limit}
      List the newest videos of a Douyin profile (or of the author of a
      Douyin video link: one extra managed call to read the author id). Nothing is counted; the
      scan is capped at what the requester can still download today (guest
      ≤ 5, signed-in ≤ 20, admin ≤ 100), and every listed video is cached so
      the call below costs nothing more for it.
  POST /client/douyin/video    {url}
      Direct media URL for one Douyin video (a cache hit after a channel
      scan, otherwise one budgeted managed resolve). Counts one download
      against the requester's daily allowance on success, like /fetch-link.

Only douyin.com / iesdouyin.com URLs are accepted, so no other host is ever
fetched. Media URLs are returned to the caller only — never logged.
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth_middleware import get_optional_user
from app.main import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

PLATFORM = "douyin"
_DOUYIN_HOST = re.compile(r"^https?://([a-z0-9-]+\.)*(douyin\.com|iesdouyin\.com)(/|$)", re.IGNORECASE)
# What the Douyin CDN expects from a downloader (same as the server's own path).
MEDIA_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)
    limit: int = Field(default=20, ge=1, le=500)


class VideoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)


def _err(status: int, message: str, code: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, "error_code": code, **extra})


def _clean_url(raw: str) -> Optional[str]:
    """Share text → the Douyin URL inside it, or None when it is not Douyin."""
    from app.core.url_normalizer import extract_share_url  # noqa: PLC0415
    url = (extract_share_url(raw) or raw or "").strip()
    return url if _DOUYIN_HOST.match(url) else None


def _requester(request: Request, user: Optional[dict]):
    """PLAN-32E G2: this route is app-only, so a guest is counted under the
    app's machine (X-VG-Device → dev:<32 hex>, the bucket of its local
    downloads), capped per IP like /client/quota/claim."""
    from app.core.quotas import resolve_requester  # noqa: PLC0415
    return resolve_requester(request, user_id=(user or {}).get("id"),
                             device_id=request.headers.get("X-VG-Device"))


def _bind(request: Request, user: Optional[dict]):
    from app.services.china_platforms.integration import bind_request_context  # noqa: PLC0415
    from app.services.china_platforms.normalized_models import current_context  # noqa: PLC0415
    token = bind_request_context(request, user)
    return token, current_context()


def _unbind(token) -> None:
    if token is not None:
        from app.services.china_platforms.normalized_models import reset_context  # noqa: PLC0415
        reset_context(token)


_STATUS = {"unsupported_url": 400, "platform_disabled": 503, "quota_exceeded": 422,
           "budget_exceeded": 422, "already_processing": 409, "no_media_found": 404,
           "provider_unavailable": 503}


@router.post("/client/douyin/channel")
@limiter.limit("6/minute")
async def douyin_channel(payload: ChannelIn, request: Request, user=Depends(get_optional_user)):
    from app.services.china_platforms import channel_listing  # noqa: PLC0415

    url = _clean_url(payload.url)
    if not url:
        return _err(400, channel_listing.MSG_BAD_URL, "unsupported_url")
    # A profile link, a v.douyin.com short link or a VIDEO link (its author's
    # channel is scanned) — list_douyin_profile sorts them out.

    token, ctx = _bind(request, user)
    # Task #6125: a guest's scan cap is what THIS machine can still download
    # (dev:<32 hex>, the bucket its claims count against), not its IP's web
    # bucket. Provider budgets stay on the IP.
    req = _requester(request, user)
    cap_key = req.key if req.kind == "device" else None
    try:
        listing = await channel_listing.list_douyin_profile(url, payload.limit, ctx, cap_key=cap_key)
    except channel_listing.ChannelListingError as exc:
        return _err(_STATUS.get(exc.code, 503), str(exc), exc.code)
    finally:
        _unbind(token)

    return {
        "platform": PLATFORM,
        "channelTitle": listing.channel_title,
        "channelUrl": f"https://www.douyin.com/user/{listing.sec_uid}",
        "cap": listing.cap,
        "fromCache": listing.from_cache,
        "items": [{
            "id": v.video_id, "url": v.url, "title": v.title or f"Douyin {v.video_id[-6:]}",
            "thumbnail": v.thumbnail_url, "durationSec": v.duration_sec,
        } for v in listing.videos],
    }


@router.post("/client/douyin/video")
@limiter.limit("30/minute")
async def douyin_video(payload: VideoIn, request: Request, user=Depends(get_optional_user)):
    import asyncio  # noqa: PLC0415

    from app.core import quotas  # noqa: PLC0415
    from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure  # noqa: PLC0415
    from app.services.china_platforms.integration import _layer_active, _user_message  # noqa: PLC0415
    from app.services.china_platforms.normalized_models import ChinaResolveRequest  # noqa: PLC0415
    from app.services.china_platforms.provider_router import ProviderRouter  # noqa: PLC0415

    url = _clean_url(payload.url)
    if not url:
        return _err(400, "Link không phải video Douyin.", "unsupported_url")
    if not _layer_active(PLATFORM):
        return _err(503, "Tải Douyin trên app tạm thời chưa mở. Bạn có thể tải trên web.", "platform_disabled")

    from app.api import client_quota  # noqa: PLC0415
    from app.core import desktop_signals  # noqa: PLC0415
    from app.core.client_ip import get_client_ip  # noqa: PLC0415
    req = _requester(request, user)
    ip = get_client_ip(request) or "unknown"
    q = quotas.check_platform_quota(req, PLATFORM, url)
    if not q.get("allowed"):
        client_quota.record_route_stat("server", "refused", req)
        return _err(403 if req.kind == quotas.REQ_USER else 429, q.get("message") or "Đã hết lượt tải hôm nay.",
                    "quota_exceeded_daily", remaining=0, reset_time_vn=q.get("reset_time_vn"))
    if not q.get("already_counted") and client_quota.ip_cap_exceeded(req, ip, PLATFORM, url):
        client_quota.record_route_stat("server", "refused", req)
        desktop_signals.note_ip_limit(ip)
        return _err(429, client_quota.ip_cap_message(), "quota_exceeded_daily", remaining=0,
                    reason="ip_limit", reset_time_vn=q.get("reset_time_vn"))

    token, ctx = _bind(request, user)
    try:
        result = await ProviderRouter().resolve(ChinaResolveRequest(url=url, operation="single_media"), ctx)
    except ChinaAccessFailure as exc:
        return _err(404 if exc.category in ("parse_failed", "unsupported_url") else 503,
                    _user_message(exc), exc.error_code)
    except AlreadyProcessing:
        return _err(409, "Video này đang được xử lý cho một yêu cầu khác. Vui lòng thử lại sau ít phút.",
                    "already_processing")
    finally:
        _unbind(token)

    media = result.primary_video_url()
    if not media:
        return _err(404, "Không tìm thấy nội dung video trong URL này.", "no_media_found")
    if req.kind != quotas.REQ_ADMIN:
        counted = await asyncio.to_thread(quotas.record_platform_download, req, PLATFORM, url)
        client_quota.note_device_counted(req, ip, counted)
        desktop_signals.note_app_download(req, ip, request.headers.get("X-VG-Device"))
    client_quota.record_route_stat("server", "ok", req)   # admin "App Windows" stats (task #6090)
    from app.services.china_platforms.request_cache import media_expiry_ts  # noqa: PLC0415
    exp = media_expiry_ts(result)
    from datetime import datetime, timezone  # noqa: PLC0415
    out = {
        "platform": PLATFORM,
        "id": result.media_id,
        "url": result.canonical_url,
        "title": result.title,
        "uploader": result.uploader,
        "thumbnail": result.thumbnail_url,
        "durationSec": result.duration_sec,
        "directUrl": media,
        "audioUrl": result.audio_url() or None,
        "headers": MEDIA_HEADERS,
        "expiresAt": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else None,
        "cacheHit": bool(result.cache_hit),
    }
    # PLAN-32E P3 (task #6172): Rust in app >= 0.10 needs a signed token for
    # these CDN links (counted above). Absent without CLIENT_QUOTA_SIGNING_KEY.
    from app.core.claim_signing import server_route_token  # noqa: PLC0415
    vg = server_route_token([out["directUrl"], out["audioUrl"]],
                            quotas.valid_device_hash(request.headers.get("X-VG-Device")))
    if vg:
        out["vgToken"] = vg
    return out
