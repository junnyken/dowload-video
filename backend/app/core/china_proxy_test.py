"""
Which admin proxies can reach Douyin? (owner 14/10: "proxy-cheap for the
platforms that need another IP — adjust the China platforms").

Measured 14/10 from the server IP: the iesdouyin share page still answers but
carries NO video data (anti-bot), and every aweme/v1/play link returns 0
bytes. Whether a proxy fixes that depends on where its exit IP is, so this
measures instead of guessing:

  for each proxy in the admin pools douyin / xiaohongshu / kuaishou / all:
    exit country            (ipinfo.io through the proxy)
    douyin_meta             share page has videoInfoRes.item_list
    douyin_bytes_proxy      first KiB of the play link through the proxy
  plus, once, douyin_bytes_direct (server IP, no proxy)

Never logs or returns a credential: proxies are shown as host:port of the
pool entry, nothing else. Result kept in Redis `china:proxy_test:last`.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

TEST_VIDEO_ID = "7677628923164790070"   # public Douyin video the owner tested 14/10
POOLS = ("douyin", "xiaohongshu", "kuaishou", "all")
MAX_PROXIES = 8
RESULT_KEY = "china:proxy_test:last"
LOCK_KEY = "china:proxy_test:lock"
_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def mask(proxy_url: str) -> str:
    """host:port only — never the user/password (proxy-cheap puts the
    region/session in the username, the password is a secret)."""
    try:
        u = urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
        return f"{u.hostname}:{u.port}" if u.port else str(u.hostname)
    except Exception:  # noqa: BLE001
        return "?"


def _get(url: str, proxy: Optional[str], rng: Optional[str] = None, timeout: int = 20):
    from curl_cffi import requests as cr  # noqa: PLC0415
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    if rng:
        headers["Range"] = rng
    return cr.get(url, headers=headers, timeout=timeout, allow_redirects=True,
                  proxies={"http": proxy, "https": proxy} if proxy else None,
                  impersonate="safari_ios")


def _router_play_url(html: str) -> Optional[str]:
    m = re.search(r"_ROUTER_DATA\s*=\s*(\{.+?\})\s*</script>", html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    for v in (data.get("loaderData") or {}).values():
        items = ((v or {}).get("videoInfoRes") or {}).get("item_list") if isinstance(v, dict) else None
        if items:
            urls = (((items[0].get("video") or {}).get("play_addr") or {}).get("url_list") or [])
            if urls:
                return urls[0].replace("/playwm/", "/play/")
    return None


def _bytes(url: str, proxy: Optional[str]) -> int:
    try:
        r = _get(url, proxy, rng="bytes=0-1023")
        return len(r.content or b"") if r.status_code in (200, 206) else 0
    except Exception:  # noqa: BLE001
        return -1


def test_proxy(proxy: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"proxy": mask(proxy) if proxy else "direct (server IP)"}
    t0 = time.time()
    try:
        r = _get("https://ipinfo.io/json", proxy, timeout=15)
        j = r.json() if r.status_code == 200 else {}
        out["country"] = j.get("country")
        out["region"] = j.get("region")
    except Exception as exc:  # noqa: BLE001
        out["country"] = None
        out["error"] = type(exc).__name__
        return out
    play = None
    try:
        r = _get(f"https://www.iesdouyin.com/share/video/{TEST_VIDEO_ID}/", proxy)
        play = _router_play_url(r.text or "") if r.status_code == 200 else None
        out["douyin_meta"] = bool(play)
    except Exception as exc:  # noqa: BLE001
        out["douyin_meta"] = False
        out["error"] = type(exc).__name__
    if play:
        out["douyin_bytes_proxy"] = _bytes(play, proxy)
        out["_play"] = play                    # internal: reused for the direct check, not returned
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _pool_entries() -> List[tuple]:
    from app.core.redis_client import get_redis  # noqa: PLC0415
    rc = get_redis()
    seen, out = set(), []
    for pool in POOLS:
        for raw in rc.lrange(f"proxy_pool:{pool}", 0, -1) or []:
            p = (raw.decode() if isinstance(raw, bytes) else raw).strip()
            if p and p not in seen:
                seen.add(p)
                out.append((pool, p))
    return out[:MAX_PROXIES]


def run() -> Dict[str, Any]:
    """Test every pooled proxy (max MAX_PROXIES) + the server IP. Never raises."""
    results: List[Dict[str, Any]] = []
    entries = _pool_entries()
    print(f"[ProxyTest] start: {len(entries)} proxies in pools {','.join(POOLS)}")
    direct = test_proxy(None)
    any_play = direct.pop("_play", None)
    for pool, p in entries:
        res = test_proxy(p)
        res["pool"] = pool
        any_play = any_play or res.get("_play")
        res.pop("_play", None)
        results.append(res)
        print(f"[ProxyTest] {json.dumps(res)}")
    if any_play:
        direct["douyin_bytes_direct"] = _bytes(any_play, None)
    report = {"at": int(time.time()), "video_id": TEST_VIDEO_ID, "direct": direct, "proxies": results}
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        get_redis().set(RESULT_KEY, json.dumps(report), ex=30 * 86400)
    except Exception:  # noqa: BLE001
        pass
    print(f"[ProxyTest] direct {json.dumps(direct)}")
    if not results:
        print("[ProxyTest] no proxy in pools douyin/xiaohongshu/kuaishou/all")
    return report


def run_once_per_deploy() -> None:
    """Startup hook: one worker runs it (Redis lock, 30 min)."""
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        # One run per container (each deploy is a new container / hostname).
        key = f"{LOCK_KEY}:{os.getenv('HOSTNAME', '')}"
        if not get_redis().set(key, "1", nx=True, ex=1800):
            return
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[ProxyTest] skipped ({type(exc).__name__})")
