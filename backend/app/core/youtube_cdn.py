"""
Request headers for googlevideo.com CDN URLs.

A signed videoplayback URL is issued to one specific InnerTube client, named in
its `c=` parameter, and the CDN rejects a fetch whose User-Agent does not match
that client with 403 Forbidden.

downloader.py extracts with player_client ["android_vr", "web_safari"], so the
URLs it produces usually carry c=ANDROID_VR — while /proxy-download sent a
desktop Chrome UA to everything. That mismatch is why 4K downloads failed with
403 even though the server's own IP matched the ip= embedded in the link, which
had made it look like an IP-lock problem.

The user-agent strings come from yt-dlp itself rather than being copied here:
it already tracks them per client and updates them, and a second copy would
drift out of sync exactly when it matters.
"""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

# Fallback used only if yt-dlp's table cannot be read. Deliberately small: the
# clients this project actually asks for.
_FALLBACK_UA: Dict[str, str] = {
    "ANDROID_VR": (
        "com.google.android.apps.youtube.vr.oculus/1.65.10 "
        "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"
    ),
    "ANDROID": "com.google.android.youtube/21.02.35 (Linux; U; Android 11) gzip",
    "IOS": "com.google.ios.youtube/21.02.3 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
}

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Clients that are browsers — only these should carry a Referer, since a native
# app client sending one is itself inconsistent.
_WEB_CLIENTS = {"WEB", "WEB_SAFARI", "WEB_EMBEDDED_PLAYER", "MWEB", "TVHTML5"}

_ua_cache: Optional[Dict[str, str]] = None


def _client_user_agents() -> Dict[str, str]:
    """clientName -> User-Agent, read from yt-dlp's own client table."""
    global _ua_cache
    if _ua_cache is not None:
        return _ua_cache

    table: Dict[str, str] = {}
    try:
        try:
            from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS  # type: ignore
        except Exception:
            from yt_dlp.extractor.youtube import INNERTUBE_CLIENTS  # type: ignore
        for cfg in INNERTUBE_CLIENTS.values():
            client = (cfg.get("INNERTUBE_CONTEXT") or {}).get("client") or {}
            name, ua = client.get("clientName"), client.get("userAgent")
            if name and ua:
                # yt-dlp appends ",gzip(gfe)" to some entries; that belongs to
                # the transport, not the UA header.
                table.setdefault(str(name).upper(), str(ua).split(",gzip(gfe)")[0])
    except Exception:
        pass

    for k, v in _FALLBACK_UA.items():
        table.setdefault(k, v)
    _ua_cache = table
    return table


def cdn_client_name(url: str) -> Optional[str]:
    """The InnerTube client a googlevideo URL was issued to, from its `c=` param."""
    try:
        values = parse_qs(urlparse(url).query).get("c") or []
        return values[0].upper() if values else None
    except Exception:
        return None


def request_headers(url: str) -> Dict[str, str]:
    """
    Headers to fetch a googlevideo URL with, matched to the client that
    requested it. Falls back to a desktop browser identity when the client is
    absent or unknown, which is the old behaviour and still right for web URLs.
    """
    client = cdn_client_name(url)
    ua = _client_user_agents().get(client or "", _DESKTOP_UA)

    headers = {"User-Agent": ua, "Accept": "*/*"}
    # Referer follows the identity we actually send, not the client name. An
    # unrecognised client falls back to a browser UA, and a browser that sends
    # no Referer is as inconsistent as a native app that sends one — deciding
    # from the name alone got that case wrong.
    if ua.startswith("Mozilla") or client in _WEB_CLIENTS:
        headers["Referer"] = "https://www.youtube.com/"
        headers["Origin"] = "https://www.youtube.com"
    return headers
