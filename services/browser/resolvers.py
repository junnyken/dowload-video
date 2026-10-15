"""Pure page-data parsers (unit-tested without a browser)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

PLATFORM_HOSTS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "kuaishou": ("kuaishou.com", "chenzhongtech.com"),
}
REFERERS = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "kuaishou": "https://www.kuaishou.com/",
}
_MEDIA_HOST = re.compile(r"^https://[^/]*(douyinvod|douyincdn|amemv|snssdk|bytecdn|zjcdn|xhscdn|kwaicdn|kwimgs|yximgs|kuaishou)\.", re.I)


def platform_of(url: str) -> Optional[str]:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return None
    for p, hosts in PLATFORM_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return p
    return None


def https(u: Any) -> Optional[str]:
    if not isinstance(u, str) or not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    return u.replace("/playwm/", "/play/") if u.startswith("https://") else None


def is_media_url(u: str) -> bool:
    return bool(u) and bool(_MEDIA_HOST.match(u))


def douyin_from_detail(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """aweme/v1/web/aweme/detail JSON → result."""
    a = (data or {}).get("aweme_detail") or {}
    v = a.get("video") or {}
    url = None
    for key in ("play_addr_h264", "play_addr", "play_addr_265"):
        for u in ((v.get(key) or {}).get("url_list") or []):
            url = https(u)
            if url:
                break
        if url:
            break
    if not url:
        for br in v.get("bit_rate") or []:
            for u in ((br.get("play_addr") or {}).get("url_list") or []):
                url = https(u)
                if url:
                    break
            if url:
                break
    if not url:
        return None
    cover = next((https(u) for u in ((v.get("cover") or {}).get("url_list") or []) if https(u)), None)
    dur = v.get("duration") or a.get("duration")
    return {
        "video_url": url,
        "title": a.get("desc") or "",
        "thumbnail": cover,
        "duration_sec": round(dur / 1000, 1) if isinstance(dur, (int, float)) and dur > 1000 else dur,
        "uploader": (a.get("author") or {}).get("nickname"),
        "media_id": a.get("aweme_id"),
    }


def xhs_from_state(state: Dict[str, Any], note_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """window.__INITIAL_STATE__ of a Xiaohongshu note page → result."""
    m = ((state or {}).get("note") or {}).get("noteDetailMap") or {}
    entry = (m.get(note_id) if note_id else None) or (next(iter(m.values())) if m else None)
    note = (entry or {}).get("note") or {}
    if not note:
        return None
    out = {"title": note.get("title") or note.get("desc") or "", "media_id": note.get("noteId") or note_id,
           "uploader": (note.get("user") or {}).get("nickname")}
    stream = (((note.get("video") or {}).get("media") or {}).get("stream") or {})
    for codec in ("h264", "h265", "av1"):
        for s in stream.get(codec) or []:
            u = https(s.get("masterUrl")) or next((https(b) for b in s.get("backupUrls") or [] if https(b)), None)
            if u:
                out["video_url"] = u
                out["duration_sec"] = (s.get("duration") or 0) / 1000 or None
                break
        if out.get("video_url"):
            break
    images = [https(i.get("urlDefault") or i.get("url")) for i in note.get("imageList") or []]
    images = [i for i in images if i]
    if images:
        out["thumbnail"] = images[0]
        out["images"] = images
    return out if (out.get("video_url") or out.get("images")) else None


def kuaishou_from_apollo(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """window.__APOLLO_STATE__ of a Kuaishou short-video page → result."""
    client = (state or {}).get("defaultClient") or state or {}
    for k, v in client.items():
        if isinstance(v, dict) and str(k).startswith("VisionVideoDetailPhoto:"):
            u = https(v.get("photoUrl") or v.get("photoH265Url"))
            if u:
                return {"video_url": u, "title": v.get("caption") or "", "thumbnail": https(v.get("coverUrl")),
                        "duration_sec": (v.get("duration") or 0) / 1000 or None, "media_id": v.get("id")}
    return None


def looks_like_captcha(title: str, body_text: str) -> bool:
    t = f"{title}\n{(body_text or '')[:3000]}".lower()
    return any(k in t for k in ("验证码", "captcha", "安全验证", "verify", "滑块", "请完成下列验证"))


def parse_json_maybe(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None
