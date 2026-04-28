"""
Proxy Manager — Hybrid Proxy Router
=====================================
Decides whether to route a request through a residential proxy,
use the server's own IP, or fall back to a Scraping API.

Cost optimization rules:
  • YouTube / Facebook  -> None (server IP — these rarely block)
  • TikTok / Douyin / Instagram -> IPROYAL_PROXY  (residential)
  • Fallback -> ScraperAPI free-trial endpoint
"""

import os
import re
from enum import Enum
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode
import httpx
import asyncio

from dotenv import load_dotenv

load_dotenv()

# ── Env vars ────────────────────────────────────────────────────────
IPROYAL_PROXY: str = os.getenv("IPROYAL_PROXY", "")
SCRAPERAPI_API_KEY: str = os.getenv("SCRAPERAPI_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Platform classification ─────────────────────────────────────────

class ProxyTier(str, Enum):
    """Which proxy tier a URL falls into."""
    DIRECT = "direct"           # server IP, $0 cost
    RESIDENTIAL = "residential" # IPRoyal, ~$0.003/req
    SCRAPING_API = "scraping_api"  # ScraperAPI fallback


# Pattern -> Tier mapping (order matters: first match wins)
_PLATFORM_RULES: list[tuple[str, ProxyTier]] = [
    # ─── Direct (free) platforms ───────────────────────────
    (r"(youtube\.com|youtu\.be)", ProxyTier.DIRECT),
    (r"facebook\.com", ProxyTier.DIRECT),
    (r"fb\.watch", ProxyTier.DIRECT),

    # ─── Residential proxy platforms ───────────────────────
    (r"tiktok\.com", ProxyTier.RESIDENTIAL),
    (r"v\.douyin\.com", ProxyTier.RESIDENTIAL),   # short links
    (r"(www\.)?douyin\.com", ProxyTier.RESIDENTIAL),  # canonical
    (r"instagram\.com", ProxyTier.RESIDENTIAL),
]


def _classify_platform(url: str) -> ProxyTier:
    """Classify a URL into a proxy tier based on domain patterns."""
    url_lower = url.lower()
    for pattern, tier in _PLATFORM_RULES:
        if re.search(pattern, url_lower):
            return tier
    # Unknown platform -> try direct first (fallback handled elsewhere)
    return ProxyTier.DIRECT


# ── Public API ───────────────────────────────────────────────────────

def get_proxy_config(url: str) -> Optional[str]:
    """
    Return the proxy string for yt-dlp based on the target URL.

    Returns:
        None   — use server IP (YouTube, Facebook, unknown sites)
        str    — IPROYAL_PROXY connection string (TikTok, Douyin, IG)
    """
    tier = _classify_platform(url)

    if tier == ProxyTier.DIRECT:
        return None

    if tier == ProxyTier.RESIDENTIAL:
        if IPROYAL_PROXY:
            return IPROYAL_PROXY
        # IPRoyal not configured — fall through to direct
        return None

    return None

# ── Notifications & Database Update ─────────────────────────────────



async def update_provider_credits(provider: str, credits: int):
    """Update credits in Supabase and trigger alert if < 100."""
    from app.core.database import get_supabase_client
    try:
        supabase = get_supabase_client()
        # Ensure Provider status gets updated
        response = supabase.table("provider_status").select("*").eq("provider_name", provider).execute()
        if response.data:
            supabase.table("provider_status").update({"remaining_credits": credits}).eq("provider_name", provider).execute()
        else:
            supabase.table("provider_status").insert({"provider_name": provider, "remaining_credits": credits}).execute()
            
        if credits < 10:
            from app.core.notifications import send_telegram_alert
            await send_telegram_alert(f"⚠️ *Low Credits Alert*\nProvider: {provider}\nRemaining: {credits}")
    except Exception as e:
        print(f"[DB] Provider status update failed: {e}")

# ── Async Dispatcher & Failover ─────────────────────────────────────

async def _fetch_with_api(api_name: str, target_url: str) -> Tuple[bool, Optional[str]]:
    """
    Fetch the target URL using a specific scraping API.
    Returns (Success, Extracted_HTML_or_None)
    """
    url = ""
    if api_name == "scraperapi" and SCRAPERAPI_API_KEY:
        params = urlencode({"api_key": SCRAPERAPI_API_KEY, "url": target_url, "render": "false"})
        url = f"http://api.scraperapi.com/?{params}"
    else:
        return False, None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            
            # Extract credits
            credits = None
            if api_name == "scraperapi" and "X-Rest-Api-Credits-Left" in resp.headers:
                credits = int(resp.headers.get("X-Rest-Api-Credits-Left", "0"))
                
            if credits is not None:
                # Fire and forget update
                asyncio.create_task(update_provider_credits(api_name, credits))
                
            if resp.status_code in [403, 429, 500]:
                print(f"[Dispatcher] {api_name} failed with {resp.status_code}")
                return False, None
                
            resp.raise_for_status()
            return True, resp.text
    except Exception as e:
        print(f"[Dispatcher] {api_name} error: {e}")
        return False, None
        
    return False, None

async def dispatch_scraping_request(target_url: str) -> Optional[str]:
    """
    Dual-Provider Proxy Logic:
    1. Determine Primary API.
    2. Fallback to Secondary API on Error.
    Returns HTML of the page.
    """
    is_tiktok_douyin = bool(re.search(r"(tiktok\.com|douyin\.com)", target_url, re.IGNORECASE))
    primary = "scraperapi"
    secondary = None
    
    # Try primary
    success, html = await _fetch_with_api(primary, target_url)
    if success:
        return html
        
    # Failover not needed since we only use scraperapi for fallback now
    # Both exhausted
    return None


def get_proxy_config_for_phase(url: str, phase: str = "metadata") -> Optional[str]:
    """
    Phase-aware proxy selection.

    Args:
        url:   The target video URL.
        phase: "metadata" (extract_info) or "download" (file fetch).

    Logic:
        • metadata phase  -> use proxy if the platform requires it
        • download phase  -> always try server IP first (CDN URLs
          are usually not geo-blocked once resolved)
    """
    if phase == "download":
        # CDN URLs from TikTok/IG are generally accessible without proxy
        # once we have the direct URL from metadata extraction.
        return None

    # metadata phase
    return get_proxy_config(url)


def get_proxy_stats() -> Dict[str, Any]:
    """Return proxy configuration status for health-check / debugging."""
    return {
        "iproyal_configured": bool(IPROYAL_PROXY),
        "scraperapi_configured": bool(SCRAPERAPI_API_KEY),
        "platform_rules_count": len(_PLATFORM_RULES),
    }
