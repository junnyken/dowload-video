"""
Channel preview for the website (owner 21/10/2026)
==================================================
  POST /channel/preview  {url, limit, deep?}

Lists the videos of a Chinese-platform channel WITHOUT creating any job, so
the bulk page can show them with checkboxes; the chosen videos then go
through /bulk-download as plain video links, where today's allowance counts
them (listing counts nothing). Same answer shape as the app's
/client/douyin/channel:

  {platform, channelTitle, channelUrl, cap, fromCache,
   items: [{id, url, title, thumbnail, durationSec}], hasMore?, moreHint?}

- Douyin: the free signed first page (~41 newest, hasMore + moreHint
  "douyin_extension" when the channel has more), or with `deep` the paid
  listing for a signed-in user (client_douyin.douyin_listing_answer).
- Xiaohongshu / Kuaishou: the owner's rules of task #6171 stay (signed-in,
  ≤ 20 per scan, each scan one paid run).
- Bilibili space: the server's yt-dlp channel listing.

Only those hosts are accepted, so no other host is fetched from here.
"""

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.client_douyin import _DOUYIN_HOST, _err, douyin_listing_answer, profile_listing_answer
from app.core.auth_middleware import get_optional_user
from app.main import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

_BILI_SPACE = re.compile(r"^https?://space\.bilibili\.com/(\d{1,20})(?:/video)?/?(?:[?#].*)?$", re.IGNORECASE)
_BV = re.compile(r"/video/(BV[0-9A-Za-z]{10})")
# wording: BA review
MSG_UNSUPPORTED = ("Xem trước danh sách chỉ dành cho kênh Douyin, Kuaishou, Xiaohongshu và Bilibili. "
                   "Kênh nền tảng khác: bật Chế độ kênh rồi bấm Tải như trước.")
MSG_BILI_FAILED = "Không lấy được danh sách video của kênh Bilibili này. Hãy thử lại sau ít phút."


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)
    limit: int = Field(default=500, ge=1, le=500)
    deep: bool = False


async def _bilibili(url: str, mid: str, limit: int):
    from app.services.downloader import scrape_channel_entries_sync  # noqa: PLC0415
    try:
        res = await asyncio.to_thread(scrape_channel_entries_sync, url, limit, 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("channel preview bilibili failed %s", type(exc).__name__)
        return _err(502, MSG_BILI_FAILED, "provider_unavailable")
    items, seen = [], set()
    for e in res.get("entries") or []:
        m = _BV.search(e.get("url") or "")
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        items.append({"id": m.group(1), "url": f"https://www.bilibili.com/video/{m.group(1)}",
                      "title": (e.get("title") or m.group(1))[:300], "thumbnail": None, "durationSec": None})
    if not items:
        return _err(404, MSG_BILI_FAILED, "no_media_found")
    return {"platform": "bilibili", "channelTitle": res.get("channel_title") or f"Bilibili {mid}",
            "channelUrl": f"https://space.bilibili.com/{mid}", "cap": limit, "fromCache": False,
            "items": items[:limit]}


@router.get("/channel/preview/caps")
async def channel_preview_caps():
    """What the bulk page needs before the click: whether the server can list
    a whole Douyin channel itself (then no extension tab is opened)."""
    from app.services import douyin_channel_cookie  # noqa: PLC0415
    return {"douyinWhole": douyin_channel_cookie.usable()}


@router.post("/channel/preview")
@limiter.limit("6/minute")
async def channel_preview(payload: PreviewIn, request: Request, user=Depends(get_optional_user)):
    from app.core.url_normalizer import extract_share_url  # noqa: PLC0415
    from app.services.china_platforms import profile_listing  # noqa: PLC0415

    url = (extract_share_url(payload.url) or payload.url or "").strip()
    if _DOUYIN_HOST.match(url):
        return await douyin_listing_answer(url, payload.limit, payload.deep, request, user)
    platform: Optional[str] = profile_listing.platform_of(url)
    if platform in ("xiaohongshu", "kuaishou"):
        return await profile_listing_answer(platform, url, payload.limit, request, user)
    m = _BILI_SPACE.match(url)
    if m:
        return await _bilibili(f"https://space.bilibili.com/{m.group(1)}/video", m.group(1), payload.limit)
    return _err(400, MSG_UNSUPPORTED, "unsupported_url")
