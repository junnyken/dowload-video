"""
Keyword-based Video Search
===========================
POST /api/v1/search-videos
Body: { keyword, platform, count, sort, min_duration, max_duration }
Response: array of { url, title, creator, duration_seconds, view_count, upload_date, thumbnail_url }

Platforms:
  - youtube: yt-dlp ytsearch (via residential proxy)
  - tiktok:  TikWM /api/feed/search (free, no-auth, fast)
Cache: Redis 10 min
Rate limit: 10/hour per IP
"""
import hashlib, json
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.main import limiter
from app.core.redis_client import get_redis

router = APIRouter()

_SUPPORTED_PLATFORMS = {"youtube", "tiktok"}
_CACHE_TTL = 600
_TIKWM_SEARCH = "https://www.tikwm.com/api/feed/search"


class SearchRequest(BaseModel):
    keyword:      str           = Field(..., min_length=2, max_length=200)
    platform:     str           = Field(..., pattern="^(youtube|tiktok)$")
    count:        int           = Field(default=10, ge=5, le=50)
    sort:         str           = Field(default="relevance", pattern="^(relevance|date|view_count)$")
    min_duration: Optional[int] = Field(default=None, ge=0)
    max_duration: Optional[int] = Field(default=None, ge=0)


def _cache_key(req: SearchRequest) -> str:
    data = {
        "kw":  req.keyword.lower().strip(),
        "pl":  req.platform,
        "ct":  req.count,
        "so":  req.sort,
        "min": req.min_duration,
        "max": req.max_duration,
    }
    return "search:" + hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _parse_ud(s) -> Optional[str]:
    if s and len(str(s)) == 8:
        s = str(s)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def _duration_ok(dur, req: SearchRequest) -> bool:
    if dur is None:
        return True
    if req.min_duration and dur < req.min_duration:
        return False
    if req.max_duration and dur > req.max_duration:
        return False
    return True


# ── YouTube via yt-dlp ────────────────────────────────────────────────────────

def _search_youtube(req: SearchRequest) -> list:
    import yt_dlp
    opts = {
        "extract_flat": True,
        "quiet":        True,
        "ignoreerrors": True,
        "noplaylist":   False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{req.count}:{req.keyword.strip()}", download=False)

    if not info or not info.get("entries"):
        return []

    results = []
    for entry in info["entries"]:
        if not entry:
            continue
        dur = entry.get("duration")
        if not _duration_ok(dur, req):
            continue
        eid = entry.get("id") or entry.get("display_id")
        url = entry.get("url") or entry.get("webpage_url") or ""
        if not url and eid:
            url = f"https://www.youtube.com/watch?v={eid}"
        if not url:
            continue
        results.append({
            "url":              url,
            "title":            entry.get("title") or "Untitled",
            "creator":          entry.get("uploader") or entry.get("channel") or entry.get("creator"),
            "duration_seconds": dur,
            "view_count":       entry.get("view_count"),
            "upload_date":      _parse_ud(entry.get("upload_date")),
            "thumbnail_url":    entry.get("thumbnail"),
        })

    if req.sort == "view_count":
        results.sort(key=lambda r: r.get("view_count") or 0, reverse=True)
    elif req.sort == "date":
        results.sort(key=lambda r: r.get("upload_date") or "", reverse=True)

    return results[:req.count]


# ── TikTok via TikWM search API ───────────────────────────────────────────────

def _search_tiktok(req: SearchRequest) -> list:
    import httpx
    params = {
        "keywords": req.keyword.strip(),
        "count":    min(req.count * 2, 50),  # over-fetch to allow duration filter
        "cursor":   0,
        "HD":       1,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.tikwm.com/",
    }
    resp = httpx.get(_TIKWM_SEARCH, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 0:
        return []

    videos = body.get("data", {}).get("videos") or []
    results = []
    for v in videos:
        dur = v.get("duration")
        if not _duration_ok(dur, req):
            continue

        author = v.get("author") or {}
        unique_id = author.get("unique_id") if isinstance(author, dict) else None
        nickname  = author.get("nickname")  if isinstance(author, dict) else str(author)
        vid_id    = v.get("video_id") or v.get("id")

        url = f"https://www.tiktok.com/@{unique_id}/video/{vid_id}" if unique_id and vid_id else ""
        if not url:
            continue

        results.append({
            "url":              url,
            "title":            v.get("title") or "TikTok Video",
            "creator":          nickname,
            "duration_seconds": dur,
            "view_count":       v.get("play_count"),
            "upload_date":      None,
            "thumbnail_url":    v.get("cover") or v.get("origin_cover"),
        })

    if req.sort == "view_count":
        results.sort(key=lambda r: r.get("view_count") or 0, reverse=True)

    return results[:req.count]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/search-videos")
@limiter.limit("10/hour")
async def search_videos(payload: SearchRequest, request: Request):
    """Search videos by keyword. YouTube uses yt-dlp; TikTok uses TikWM API."""
    _ = request  # consumed by @limiter.limit decorator
    kw = payload.keyword.strip()

    cache_key = _cache_key(payload)
    try:
        rc = get_redis()
        cached = rc.get(cache_key)
        if cached:
            return {"results": json.loads(cached), "cached": True, "keyword": kw}
    except Exception:
        pass

    try:
        if payload.platform == "youtube":
            results = _search_youtube(payload)
        else:
            results = _search_tiktok(payload)
    except HTTPException:
        raise
    except Exception as e:
        err = str(e).lower()
        if "rate" in err or "too many" in err or "429" in err:
            raise HTTPException(429, detail={
                "error_code": "search_rate_limited",
                "message": "Tìm kiếm quá nhiều lần. Thử lại sau vài phút.",
            })
        raise HTTPException(500, detail=f"Tìm kiếm thất bại: {str(e)[:200]}")

    if not results:
        raise HTTPException(400, detail={
            "error_code": "search_no_results",
            "message": f"Không tìm thấy video nào cho từ khóa '{kw}'.",
        })

    try:
        rc = get_redis()
        rc.setex(cache_key, _CACHE_TTL, json.dumps(results))
    except Exception:
        pass

    return {"results": results, "cached": False, "keyword": kw, "total": len(results)}
