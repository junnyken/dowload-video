"""
Caching Layer — URL Result Cache
=================================
Before scraping a URL, check if someone else already successfully
downloaded the same URL within the last 24 hours.  If so, return
the cached result and save 100% of the proxy cost.

This is especially valuable for viral videos that get requested
by many users in a short timeframe.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from app.core.database import get_supabase_client

# ── Configuration ────────────────────────────────────────────────────
CACHE_TTL_HOURS: int = 24


def get_cached_result(url: str) -> Optional[Dict[str, Any]]:
    """
    Check if a URL was successfully downloaded in the last 24 hours.

    Args:
        url: The original video URL to look up.

    Returns:
        A dict with cached video info if found, else None.
        {
            "title": str,
            "thumbnail_url": str,        # may be empty
            "direct_mp4_url": str,
            "cached": True,
            "cached_at": str (ISO),
        }
    """
    supabase = get_supabase_client()

    # Calculate the cutoff time
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()

    try:
        # Normalize the URL for lookup (strip trailing slashes, tracking params)
        normalized = _normalize_url(url)

        response = (
            supabase.table("download_jobs")
            .select("title, direct_mp4_url, created_at")
            .eq("original_url", normalized)
            .eq("status", "success")
            .not_.is_("direct_mp4_url", "null")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data:
            cached = response.data[0]
            direct_url = cached.get("direct_mp4_url", "")
            if direct_url:
                return {
                    "title": cached.get("title", "Unknown Title"),
                    "thumbnail_url": "",
                    "direct_mp4_url": direct_url,
                    "cached": True,
                    "cached_at": cached.get("created_at", ""),
                }

    except Exception as e:
        # Cache miss on error — fall through to live extraction
        print(f"[Cache] Lookup error for {url}: {e}")

    return None


def _normalize_url(url: str) -> str:
    """
    Normalize a URL for consistent cache lookups.
    Strips tracking parameters and trailing slashes for all platforms.
    """
    url = url.strip()

    # TikTok: strip tracking params
    if "tiktok.com" in url.lower() and "?" in url:
        url = url.split("?")[0]

    # Douyin: strip tracking params
    if "douyin.com" in url.lower() and "?" in url:
        url = url.split("?")[0]

    # YouTube: keep only video ID param
    if "youtube.com/watch" in url.lower() and "?" in url:
        import re
        vid_match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if vid_match:
            url = f"https://www.youtube.com/watch?v={vid_match.group(1)}"

    # YouTube Shorts: strip query
    if "youtube.com/shorts/" in url.lower() and "?" in url:
        url = url.split("?")[0]

    # Instagram: strip query params
    if "instagram.com" in url.lower() and "?" in url:
        url = url.split("?")[0]

    # Facebook: strip tracking (fbclid etc)
    if "facebook.com" in url.lower() and "fbclid" in url:
        url = url.split("?")[0]

    # Strip trailing slash
    url = url.rstrip("/")

    return url


def get_cache_stats() -> Dict[str, Any]:
    """
    Return cache statistics for monitoring.
    Shows how many entries are in the 24h window.
    """
    supabase = get_supabase_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()

    try:
        response = (
            supabase.table("download_jobs")
            .select("id", count="exact")
            .eq("status", "success")
            .not_.is_("direct_mp4_url", "null")
            .gte("created_at", cutoff)
            .execute()
        )

        return {
            "cached_urls_24h": response.count or 0,
            "cache_ttl_hours": CACHE_TTL_HOURS,
        }
    except Exception:
        return {
            "cached_urls_24h": -1,
            "cache_ttl_hours": CACHE_TTL_HOURS,
        }
