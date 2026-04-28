"""
Link Resolver — Smart Short-URL Unshortener
=============================================
Douyin short links (v.douyin.com) use HTTP 302 redirects that
yt-dlp cannot follow due to anti-bot JS challenges.  Passing them
directly to yt-dlp causes an *infinite hang*.

Strategy (fast, captcha-free):
  1. Send a HEAD/GET with follow_redirects=False.
  2. Read the 302 Location header — this is the real URL.
  3. Extract the video_id with regex and construct a clean
     canonical URL:  https://www.douyin.com/video/{video_id}
  4. If no video_id is found (e.g. user profile redirect),
     return the Location URL as-is.

This avoids downloading any HTML body and sidesteps all JS
challenges entirely.

Supports:
  v.douyin.com  ->  www.douyin.com/video/<aweme_id>
  vt.tiktok.com / vm.tiktok.com  ->  www.tiktok.com/...
"""

import re
import httpx
from typing import Optional

# ── User-Agent: Mobile Safari is treated most leniently ─────────────
# Douyin's 302-redirect endpoint for share links prefers mobile UAs.
# Desktop UAs sometimes trigger a JS challenge page instead of 302.
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)

_ANDROID_UA = (
    "com.ss.android.ugc.aweme/230904 "
    "(Linux; Android 12; SM-G998B Build/SP1A.210812.016; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

# Domains that are known to use short-link redirects
_SHORT_LINK_DOMAINS = [
    "v.douyin.com",
    "vt.tiktok.com",
    "vm.tiktok.com",
]

# ── Timeout for the resolver itself (must be fast) ───────────────────
_RESOLVE_TIMEOUT = 8.0   # seconds — hard cap per attempt


def _is_short_url(url: str) -> bool:
    """Check if a URL is a known short link that needs unshortening."""
    url_lower = url.lower()
    return any(domain in url_lower for domain in _SHORT_LINK_DOMAINS)


def _extract_video_id(url: str) -> Optional[str]:
    """
    Extract the Douyin aweme_id from any form of Douyin URL.
    Handles /video/<id>, /note/<id>, item_ids=<id>, aweme_id=<id>.
    """
    patterns = [
        r'/video/(\d{15,25})',
        r'/note/(\d{15,25})',
        r'item_ids=(\d{15,25})',
        r'aweme_id=(\d{15,25})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _clean_expanded_url(expanded: str) -> str:
    """
    Strip tracking query parameters from expanded Douyin/TikTok URLs.
    Keeps the path intact (e.g. /video/<id>).
    """
    if ("douyin.com" in expanded or "tiktok.com" in expanded) and "?" in expanded:
        expanded = expanded.split("?")[0]
    return expanded


async def _try_resolve_302(url: str, user_agent: str) -> Optional[str]:
    """
    Fastest approach: send GET with follow_redirects=False.
    Read the Location header from the 301/302 response.
    Never downloads the HTML body -> never triggers captcha.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=_RESOLVE_TIMEOUT,
        ) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if location:
                    print(f"[LinkResolver] 302 Location -> {location[:120]}")
                    return location

            # Some short-link services return 200 with meta-refresh
            # or JavaScript redirect.  Try parsing from the tiny body.
            if resp.status_code == 200:
                body = resp.text[:2000]  # only first 2KB
                # meta http-equiv="refresh" content="0;url=..."
                meta_match = re.search(
                    r'content=["\']?\d;url=(https?://[^"\'>\s]+)', body, re.IGNORECASE
                )
                if meta_match:
                    return meta_match.group(1)

            print(f"[LinkResolver] Unexpected status {resp.status_code} for {url[:60]}")
            return None

    except httpx.TimeoutException:
        print(f"[LinkResolver] Timeout (302) with UA: {user_agent[:30]}...")
        return None
    except Exception as e:
        print(f"[LinkResolver] Error (302): {e}")
        return None


async def _try_resolve_follow(url: str, user_agent: str) -> Optional[str]:
    """
    Fallback: follow all redirects and return the final URL.
    Slower but handles multi-hop chains and JS meta-refreshes.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_RESOLVE_TIMEOUT,
            limits=httpx.Limits(max_connections=5),
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (403, 503):
                return None
            expanded = str(resp.url)
            if expanded != url:
                return expanded
    except httpx.TimeoutException:
        print(f"[LinkResolver] Timeout (follow) with UA: {user_agent[:30]}...")
    except Exception as e:
        print(f"[LinkResolver] Error (follow): {e}")
    return None


async def resolve_douyin_shortlink(url: str) -> Optional[str]:
    """
    Waterfall strategy to resolve v.douyin.com shortlinks.
    Layer 1: Aggressive Mobile Spoofing (Fast & Free)
    Note: ZenRows fallback removed (API key deactivated).
          Douyin extraction is handled by Apify which resolves URLs internally.
    """
    print(f"[LinkResolver] Resolving Douyin shortlink (Waterfall): {url}")
    
    # ── Layer 1: Aggressive Mobile Spoofing ──────────────
    wechat_ua = (
        "Mozilla/5.0 (Linux; Android 10; SM-G981B Build/QP1A.190711.020; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/86.0.4240.198 "
        "Mobile Safari/537.36 MicroMessenger/8.0.2.1860(0x2800023B) WeChat/arm64 "
        "Weixin NetType/WIFI Language/zh_CN ABI/arm64"
    )
    headers = {
        "User-Agent": wechat_ua,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if location:
                    print(f"[LinkResolver] Layer 1 (WeChat) Success -> {location[:120]}")
                    return _canonicalize_douyin(location)
            elif resp.status_code == 200:
                print(f"[LinkResolver] Layer 1 (WeChat) returned 200 OK (likely Captcha)")
            else:
                print(f"[LinkResolver] Layer 1 (WeChat) failed with status {resp.status_code}")
    except httpx.TimeoutException:
        print("[LinkResolver] Layer 1 (WeChat) Timeout")
    except Exception as e:
        print(f"[LinkResolver] Layer 1 (WeChat) Error: {e}")

    return None


async def resolve_short_url_async(url: str) -> str:
    """
    Asynchronously resolve a short URL to its expanded canonical form.

    Strategy (ordered by speed):
      1. 302-Location header with Mobile Safari UA (fastest).
      2. 302-Location header with Android Douyin app UA.
      3. Full redirect-following with Mobile Safari UA (slowest fallback).
      4. If everything fails, return original URL.

    When a Douyin video_id is found in the resolved URL, we construct
    a clean canonical URL to avoid yt-dlp issues:
        https://www.douyin.com/video/{video_id}
    """
    if not _is_short_url(url):
        return url

    print(f"[LinkResolver] Resolving short URL: {url}")

    # If it's a Douyin short link, use the new Waterfall strategy
    if "douyin.com" in url.lower():
        douyin_resolved = await resolve_douyin_shortlink(url)
        if douyin_resolved:
            return douyin_resolved
        print(f"[LinkResolver] Waterfall strategy failed for Douyin, falling back to original URL")
        return url

    # For TikTok or other short links, use the existing strategy
    # ── Attempt 1: 302 Location with Mobile Safari UA ────────────
    location = await _try_resolve_302(url, _MOBILE_UA)
    if location:
        resolved = _canonicalize_douyin(location)
        print(f"[LinkResolver] Resolved (302/mobile) -> {resolved}")
        return resolved

    # ── Attempt 2: 302 Location with Android app UA ──────────────
    location = await _try_resolve_302(url, _ANDROID_UA)
    if location:
        resolved = _canonicalize_douyin(location)
        print(f"[LinkResolver] Resolved (302/android) -> {resolved}")
        return resolved

    # ── Attempt 3: Full redirect-following (last resort) ─────────
    expanded = await _try_resolve_follow(url, _MOBILE_UA)
    if expanded and expanded != url:
        resolved = _canonicalize_douyin(expanded)
        print(f"[LinkResolver] Resolved (follow) -> {resolved}")
        return resolved

    print(f"[LinkResolver] Could not resolve {url}, returning original")
    return url


def _canonicalize_douyin(raw_url: str) -> str:
    """
    Convert any resolved Douyin URL into the cleanest canonical form.
    If we can extract a video_id, use: https://www.douyin.com/video/<id>
    Otherwise clean tracking params and return as-is.
    """
    vid = _extract_video_id(raw_url)
    if vid:
        return f"https://www.douyin.com/video/{vid}"
    return _clean_expanded_url(raw_url)


def resolve_short_url(url: str) -> str:
    """
    Synchronous wrapper for resolve_short_url_async.
    Safe to call from sync contexts (yt-dlp extraction pipeline).

    Handles:
      - No event loop running  -> asyncio.run()
      - Inside existing loop   -> offload to a thread to avoid
        "cannot run nested event loop" errors.
    """
    if not _is_short_url(url):
        return url

    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, resolve_short_url_async(url))
            return future.result(timeout=20)
    else:
        return asyncio.run(resolve_short_url_async(url))


def extract_video_id_from_url(url: str) -> Optional[str]:
    """
    Extract the Douyin video/aweme ID from a canonical URL.
    Example: https://www.douyin.com/video/7394012345678901234 -> 7394012345678901234
    """
    return _extract_video_id(url)


def is_douyin_url(url: str) -> bool:
    """
    Check whether a URL belongs to Douyin (any subdomain).
    Covers v.douyin.com, www.douyin.com, iesdouyin.com, etc.
    """
    return bool(re.search(r"(douyin\.com|iesdouyin\.com)", url, re.IGNORECASE))
