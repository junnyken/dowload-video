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
# wording: BA review
MSG_BAD_URL = "Chỉ hỗ trợ trang cá nhân Douyin (douyin.com/user/…)."
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
    return f"https://www.douyin.com/user/{m.group(1)}" if m else None


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
    doc = {"status": "waiting", "channelUrl": channel, "channelTitle": None, "items": [], "skipped": 0}
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
    for it in payload.items:
        m = _DOUYIN_VIDEO.match((it.url or "").split("?")[0].strip())
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = " ".join((it.title or "").split())[:300] or None
        items.append({"id": m.group(1), "url": f"https://www.douyin.com/video/{m.group(1)}", "title": title})
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
