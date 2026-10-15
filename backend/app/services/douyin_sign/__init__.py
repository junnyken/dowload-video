"""
Douyin web API signing (owner 15/10/2026).

Douyin's /aweme/v1/web/aweme/detail/ answers a plain HTTP client when the
query carries two signatures:

  a_bogus                 abogus.py + sm3.py, copied unchanged (one import
                          path made relative) from Evil0ctal's
                          Douyin_TikTok_Download_API, commit 4f0bed8
                          (2026-10-01), Apache-2.0 — LICENSE and NOTICE sit
                          beside them. Pure computation: no I/O, no network.
  x-secsdk-web-signature  md5(f"{uifid}_{timestamp}_{SALT}_{query}"), written
                          here. It is bound to the visitor id `UIFID_TEMP`
                          that only a real browser visit to douyin.com gets
                          (douyin_visitor.py keeps one).

Measured 15/10 from a Vietnam IP, no proxy: 20/20 detail calls over 5 owner
videos returned the video and the first 64 KB downloaded, ~0.3 s per call,
with one visitor jar minted 33 minutes earlier.
"""
from __future__ import annotations

import hashlib
import time
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import quote, urlencode

DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# x-secsdk-web-signature salt for douyin_web (secsdk runtime_bundler_34.js).
SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"
UIFID_COOKIES = ("uifid", "uifid_temp", "uifidtemp", "UIFID", "UIFID_TEMP", "UIFIDTEMP")
VERIFY_FP_COOKIE = "s_v_web_id"

# The query a Douyin PC web page sends for a detail call (aweme_id last).
_DETAIL_PARAMS = (
    ("device_platform", "webapp"), ("aid", "6383"), ("channel", "channel_pc_web"),
    ("pc_client_type", "1"), ("version_code", "290100"), ("version_name", "29.1.0"),
    ("cookie_enabled", "true"), ("screen_width", "1920"), ("screen_height", "1080"),
    ("browser_language", "zh-CN"), ("browser_platform", "Win32"), ("browser_name", "Chrome"),
    ("browser_version", "130.0.0.0"), ("browser_online", "true"), ("engine_name", "Blink"),
    ("engine_version", "130.0.0.0"), ("os_name", "Windows"), ("os_version", "10"),
    ("cpu_core_num", "12"), ("device_memory", "8"), ("platform", "PC"), ("downlink", "10"),
    ("effective_type", "4g"), ("round_trip_time", "0"), ("update_version_code", "170400"),
)


def pick_uifid(cookies: Mapping[str, str]) -> Optional[str]:
    for name in UIFID_COOKIES:
        if cookies.get(name):
            return cookies[name]
    return None


def _encode(pairs) -> str:
    """URLSearchParams.toString(): the same bytes are hashed and sent."""
    return "&".join(f"{quote(k, safe='*-._')}={quote(v, safe='*-._')}" for k, v in pairs)


def web_signature(pairs, uifid: str, *, timestamp: Optional[int] = None) -> Tuple[str, Dict[str, str]]:
    """Append uifid + timestamp + x-secsdk-web-signature. Returns (query, headers)."""
    stamp = str(int(time.time() if timestamp is None else timestamp))
    covered = list(pairs)
    if not any(k == "uifid" for k, _ in covered):
        covered.append(("uifid", uifid))
    covered.append(("timestamp", stamp))
    query = _encode(covered)
    sig = hashlib.md5(f"{uifid}_{stamp}_{SALT}_{query}".encode()).hexdigest()
    headers = {"uifid": uifid, "x-secsdk-web-signature": sig, "x-secsdk-web-expire": stamp}
    return f"{query}&x-secsdk-web-signature={sig}", headers


def detail_request(aweme_id: str, cookies: Mapping[str, str], user_agent: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """(url, headers) for a signed detail call, or None when the jar has no visitor id."""
    from .abogus import ABogus  # noqa: PLC0415
    uifid = pick_uifid(cookies)
    if not uifid or not str(aweme_id).isdigit():
        return None
    params = list(_DETAIL_PARAMS) + [("aweme_id", str(aweme_id)), ("msToken", cookies.get("msToken", ""))]
    pairs = params + [("a_bogus", ABogus(user_agent).get_value(urlencode(params)))]
    vfp = cookies.get(VERIFY_FP_COOKIE)
    if vfp:
        pairs += [("verifyFp", vfp), ("fp", vfp)]
    query, sig_headers = web_signature(pairs, uifid)
    headers = {
        "User-Agent": user_agent,
        "Referer": f"https://www.douyin.com/video/{aweme_id}",
        "Accept": "application/json, text/plain, */*",
        **sig_headers,
    }
    return f"{DETAIL_API}?{query}", headers
