"""
Channel relay: the browser extension reads a channel for the app / website
===========================================================================
Owner 21/10/2026. A Douyin profile pages past its first ~41 videos only for
a real browser: the server's signed call gets one page, and the app's
embedded WebView2 window gets the profile header but no grid (Douyin's
page signature rejects that environment). The VidGrab extension in the
person's own Chrome scrolls the whole channel (174 videos measured). So:

  1. app / website  POST /channel-relay {url}       → {token, openUrl}
     (signed-in callers only)
  2. it opens openUrl (the channel + "#vg-relay=<token>") in Chrome; the
     extension sees the token, scrolls the whole channel by itself and
  3. extension      PUT  /channel-relay/{token}     {items, channelTitle, done}
     (partial updates while scrolling, then done=true; loginWall=true →
     status "login": Douyin wants the visitor signed in, the extension
     resumes by itself after that)
  4. app / website  GET  /channel-relay/{token}     → {status, items, …}

The token (24 random bytes) is the only key; a relay lives 30 minutes.
Items are public video links of the channel that was asked for (same
host family, video ids only), at most 2000; nothing else is stored and
nothing is counted — downloads count when they are downloaded.

22/10/2026: also a Facebook profile's Reels / Videos tab (owner). Signed in,
the person's own Facebook lists the whole tab; the app's hidden window and
the server see ~10 reels behind a sign-in dialog.
"""

import json
import re
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.client_douyin import _err
from app.core.auth_middleware import get_optional_user
from app.main import limiter

router = APIRouter()

TTL_S = 1800
MAX_ITEMS = 2000
_KEY = "vidgrab:relay:{}"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
_DOUYIN_PROFILE = re.compile(r"^https://www\.douyin\.com/user/(MS4w[A-Za-z0-9_-]{10,120})(?:[/?#].*)?$")
_DOUYIN_VIDEO = re.compile(r"^https://www\.douyin\.com/video/(\d{15,25})$")
_FB_RESERVED = {"reel", "reels", "watch", "groups", "share", "events", "marketplace", "pages", "gaming", "login", "profile.php", "story.php", "photo", "photos", "video", "videos", "hashtag"}
_FB_NAMED = re.compile(r"^https://(?:www\.|web\.|m\.)?facebook\.com/([A-Za-z0-9.\-]{1,80})(?:/(reels|videos))?/?(?:[?#].*)?$")
_FB_PROFILE_ID = re.compile(r"^https://(?:www\.|web\.|m\.)?facebook\.com/profile\.php\?(?:[^#]*&)?id=(\d{5,25})")
# Where Facebook sends profile.php?id=… (measured 22/10): /people/<name>/<id>/?sk=reels_tab
_FB_PEOPLE = re.compile(r"^https://(?:www\.|web\.|m\.)?facebook\.com/people/[^/?#]{1,120}/(\d{5,25})/?(?:[?#].*)?$")
_FB_ITEM = re.compile(r"^https://www\.facebook\.com/(?:reel/(\d{6,25})|watch/\?v=(\d{6,25}))$")
# wording: BA review
MSG_BAD_URL = "Chỉ hỗ trợ trang cá nhân Douyin (douyin.com/user/…) hoặc trang Reels / Video của Facebook."
MSG_GONE = "Phiên lấy danh sách đã hết hạn. Hãy bấm lấy lại."
MSG_LOGIN = "Hãy đăng nhập để lấy đủ cả kênh."


def _rc():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


class StartIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)


class Item(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(max_length=300)
    title: Optional[str] = Field(default=None, max_length=500)


class PutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: List[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    channelTitle: Optional[str] = Field(default=None, max_length=300)
    done: bool = False
    skipped: int = Field(default=0, ge=0, le=100000)  # image posts etc. the extension left out
    loginWall: bool = False  # Douyin asks the visitor to sign in before showing more (5.11.1)


def _clean_profile(raw: str) -> Optional[str]:
    from app.core.url_normalizer import extract_share_url  # noqa: PLC0415
    url = (extract_share_url(raw) or raw or "").strip().replace("http://", "https://", 1)
    url = re.sub(r"^https://douyin\.com/", "https://www.douyin.com/", url)
    m = _DOUYIN_PROFILE.match(url)
    if m:
        return f"https://www.douyin.com/user/{m.group(1)}"
    url = re.sub(r"^https://facebook\.com/", "https://www.facebook.com/", url)
    m = _FB_PROFILE_ID.match(url) or _FB_PEOPLE.match(url)
    if m:
        return f"https://www.facebook.com/profile.php?id={m.group(1)}&sk=reels_tab"
    m = _FB_NAMED.match(url)
    if m and m.group(1).lower() not in _FB_RESERVED:
        return f"https://www.facebook.com/{m.group(1)}/{m.group(2) or 'reels'}/"
    return None


def _platform(url: str) -> str:
    return "facebook" if "facebook.com/" in url else "douyin"


def _clean_item(platform: str, raw: str):
    """(id, canonical url) of a video link of the channel's platform, else None."""
    u = (raw or "").strip()
    if platform == "facebook":
        m = _FB_ITEM.match(u)
        if not m:
            return None
        return (m.group(1), f"https://www.facebook.com/reel/{m.group(1)}") if m.group(1) \
            else (m.group(2), f"https://www.facebook.com/watch/?v={m.group(2)}")
    m = _DOUYIN_VIDEO.match(u.split("?")[0])
    return (m.group(1), f"https://www.douyin.com/video/{m.group(1)}") if m else None


@router.post("/channel-relay")
@limiter.limit("10/minute")
async def start(payload: StartIn, request: Request, user=Depends(get_optional_user)):
    # Signed-in only (owner 21/10): a whole channel turns into a large batch;
    # guests keep the server's first page.
    if not user:
        return _err(401, MSG_LOGIN, "login_required")
    channel = _clean_profile(payload.url)
    if not channel:
        return _err(400, MSG_BAD_URL, "unsupported_url")
    token = secrets.token_urlsafe(24)
    doc = {"status": "waiting", "channelUrl": channel, "platform": _platform(channel),
           "channelTitle": None, "items": [], "skipped": 0}
    try:
        _rc().set(_KEY.format(token), json.dumps(doc), ex=TTL_S)
    except Exception:  # noqa: BLE001
        return _err(503, "Máy chủ đang bận, hãy thử lại sau ít phút.", "provider_unavailable")
    return {"token": token, "openUrl": f"{channel}#vg-relay={token}", "expiresIn": TTL_S}


def _load(token: str):
    if not _TOKEN.match(token or ""):
        return None
    try:
        raw = _rc().get(_KEY.format(token))
    except Exception:  # noqa: BLE001
        return None
    return json.loads(raw) if raw else None


@router.put("/channel-relay/{token}")
@limiter.limit("60/minute")
async def put(token: str, payload: PutIn, request: Request):
    doc = _load(token)
    if doc is None:
        return _err(404, MSG_GONE, "relay_expired")
    seen, items = set(), []
    platform = doc.get("platform") or _platform(doc.get("channelUrl") or "")
    for it in payload.items:
        hit = _clean_item(platform, it.url)
        if not hit or hit[0] in seen:
            continue
        seen.add(hit[0])
        title = " ".join((it.title or "").split())[:300] or None
        items.append({"id": hit[0], "url": hit[1], "title": title})
    doc.update({
        "status": "done" if payload.done else "login" if payload.loginWall else "scrolling",
        "items": items,
        "skipped": payload.skipped,
        "channelTitle": (" ".join((payload.channelTitle or "").split())[:300] or doc.get("channelTitle")),
    })
    try:
        _rc().set(_KEY.format(token), json.dumps(doc), keepttl=True)
    except Exception:  # noqa: BLE001
        return _err(503, "Máy chủ đang bận, hãy thử lại sau ít phút.", "provider_unavailable")
    return {"ok": True, "count": len(items)}


@router.get("/channel-relay/{token}")
@limiter.limit("60/minute")
async def get(token: str, request: Request):
    doc = _load(token)
    if doc is None:
        return _err(404, MSG_GONE, "relay_expired")
    return doc
