"""
Proxy health — is each admin proxy alive, and if not, why?
===========================================================
Added 2026-10-19 (owner: "a check that tells me whether a proxy is alive or
dead, to know if a failure is the proxy"). Until now a dead proxy was only
noticed when downloads through it failed — YouTube depends on one.

Every 10 minutes (Celery beat) and on demand from the admin, every proxy of
every admin pool (`proxy_pool:*` in Redis) is checked:

  * reach   — GET https://ipinfo.io/json THROUGH the proxy: alive or not,
              latency, exit IP and country;
  * youtube — for the youtube pool also GET youtube.com/generate_204 through
              it (204 = YouTube answers this exit IP).

A failure is classified (traffic_exhausted, auth_failed, timeout,
connect_failed, tunnel_failed, http_<code>, youtube_blocked) so the admin
sees WHY. Effects:

  * a dead proxy is put in cooldown (proxy_manager.mark_proxy_bad), so the
    pool's priority order (proxy_pool.get_proxy_from_pool) moves to the backup
    BEFORE a user's download hits it; an alive one has its cooldown cleared;
  * Telegram when a proxy changes alive <-> dead (masked, never credentials);
  * results kept in Redis (`proxy_health:{id}`, summary `proxy_health:last`)
    for the admin Proxy page.

Cost: one ipinfo call (~1 KB) per proxy per run, plus one 204 for YouTube's —
negligible even on per-GB residential plans. Never raises.
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.china_proxy_test import mask

RESULT_KEY = "proxy_health:{pid}"
SUMMARY_KEY = "proxy_health:last"
LOCK_KEY = "proxy_health:lock"
RESULT_TTL = 3 * 3600
DEAD_COOLDOWN_S = 20 * 60          # rechecked every 10 min; cleared as soon as alive
TIMEOUT_S = 15
PARALLEL = 6
IP_URL = "https://ipinfo.io/json"
YT_URL = "https://www.youtube.com/generate_204"
YT_TRIES = 3

# wording: BA review
KIND_LABEL = {
    "ok": "Sống",
    "traffic_exhausted": "Hết dung lượng",
    "auth_failed": "Sai tài khoản/mật khẩu hoặc hết dung lượng (407)",
    "timeout": "Quá thời gian chờ",
    "connect_failed": "Không kết nối được tới proxy",
    "tunnel_failed": "Proxy từ chối mở kết nối",
    "youtube_blocked": "Sống nhưng YouTube chặn IP này",
    "invalid_format": "Sai định dạng proxy — xoá và thêm lại",
    "error": "Lỗi không rõ nguyên nhân",
}


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _d(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def classify_error(text: str) -> str:
    t = (text or "").lower()
    if "unsupported proxy syntax" in t or "malformed" in t or "bad url" in t:
        return "invalid_format"
    if "traffic" in t and ("exhaust" in t or "limit" in t) or "402" in t or "payment required" in t:
        return "traffic_exhausted"
    if "407" in t or "proxy authentication" in t:
        return "auth_failed"
    if "timed out" in t or "timeout" in t:
        return "timeout"
    if "tunnel" in t or "connect tunnel" in t or "proxy connect" in t:
        return "tunnel_failed"
    if ("could not resolve" in t or "connection refused" in t or "failed to connect" in t
            or "couldn't connect" in t or "resolve proxy" in t):
        return "connect_failed"
    return "error"


def _get(url: str, proxy: str):
    from curl_cffi import requests as cr  # noqa: PLC0415
    return cr.get(url, timeout=TIMEOUT_S, allow_redirects=False,
                  proxies={"http": proxy, "https": proxy}, impersonate="chrome")


def check(proxy: str, youtube: bool = False) -> Dict[str, Any]:
    """One proxy. {alive, kind, label, ms, ip, country, youtube_ok, detail, at}."""
    out: Dict[str, Any] = {"alive": False, "kind": "error", "ms": None, "ip": None, "country": None,
                           "youtube_ok": None, "sticky": None, "detail": None, "at": int(time.time())}
    t0 = time.time()
    try:
        r = _get(IP_URL, proxy)
        out["ms"] = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            body = (r.text or "")[:200]
            out["kind"] = classify_error(f"{r.status_code} {body}") if r.status_code in (402, 407) \
                else f"http_{r.status_code}"
            out["detail"] = f"HTTP {r.status_code}"
            return _labelled(out)
        try:
            j = r.json()
        except Exception:
            j = {}
        out["ip"], out["country"] = j.get("ip"), j.get("country")
    except Exception as exc:  # noqa: BLE001
        out["ms"] = int((time.time() - t0) * 1000)
        out["kind"] = classify_error(f"{type(exc).__name__} {exc}")
        out["detail"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        # never echo a credential that a library put into its message
        out["detail"] = out["detail"].replace(proxy, mask(proxy))
        return _labelled(out)
    out["alive"], out["kind"] = True, "ok"
    if youtube:
        # YouTube's media URLs are tied to the exit IP that fetched the page:
        # a rotating exit can switch between the two and get 403. Two calls in
        # a row tell whether this proxy keeps its IP (a sticky session — set
        # when generating the credentials at the provider).
        try:
            r2 = _get(IP_URL, proxy)
            ip2 = (r2.json() or {}).get("ip") if r2.status_code == 200 else None
            out["sticky"] = bool(ip2) and ip2 == out["ip"]
        except Exception:  # noqa: BLE001
            out["sticky"] = None
        # Up to YT_TRIES calls: a rotating proxy gets a new exit each time and
        # YouTube blocks some exits but not others (measured 19/10 on the
        # proxy-cheap pool: 204 on one run, blocked on the next). One bad
        # exit must not flag — and cool — the whole proxy.
        out["youtube_ok"] = False
        for _ in range(YT_TRIES):
            try:
                if _get(YT_URL, proxy).status_code == 204:
                    out["youtube_ok"] = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not out["youtube_ok"]:
            out["alive"], out["kind"] = False, "youtube_blocked"
    return _labelled(out)


def _labelled(out: Dict[str, Any]) -> Dict[str, Any]:
    out["label"] = KIND_LABEL.get(out["kind"]) or (
        f"Lỗi HTTP {out['kind'][5:]}" if str(out["kind"]).startswith("http_") else KIND_LABEL["error"])
    return out


def pool_entries() -> List[Tuple[str, int, str]]:
    """[(pool, index, proxy_url)] for every admin pool, in pool order."""
    rc = _r()
    out = []
    try:
        keys = sorted({_d(k) for k in rc.scan_iter(match="proxy_pool:*", count=200)})
    except Exception:
        return []
    for key in keys:
        pool = key.split(":", 1)[1]
        try:
            items = rc.lrange(key, 0, -1) or []
        except Exception:
            continue
        for i, raw in enumerate(items):
            p = _d(raw).strip()
            if p:
                out.append((pool, i, p))
    return out


def stored(proxy: str) -> Optional[dict]:
    from app.core.proxy_manager import _proxy_id  # noqa: PLC0415
    try:
        raw = _r().get(RESULT_KEY.format(pid=_proxy_id(proxy)))
        return json.loads(_d(raw)) if raw else None
    except Exception:
        return None


def cooling_seconds(proxy: str) -> int:
    from app.core.proxy_manager import _proxy_id  # noqa: PLC0415
    try:
        ttl = _r().ttl(f"proxy_bad:{_proxy_id(proxy)}")
        return max(0, int(ttl or 0))
    except Exception:
        return 0


def _apply(pool: str, index: int, proxy: str, res: Dict[str, Any]) -> Optional[str]:
    """Store, cooldown/clear, and return a Telegram line when the state changed."""
    from app.core.proxy_manager import _proxy_id, mark_proxy_bad  # noqa: PLC0415
    pid = _proxy_id(proxy)
    prev = stored(proxy)
    try:
        _r().set(RESULT_KEY.format(pid=pid), json.dumps(res), ex=RESULT_TTL)
        if res["alive"]:
            _r().delete(f"proxy_bad:{pid}")
        else:
            mark_proxy_bad(proxy, ttl=DEAD_COOLDOWN_S)
    except Exception:
        pass
    was_alive = (prev or {}).get("alive")
    name = f"{pool} #{index} ({mask(proxy)})"
    if res["alive"] and was_alive is False:
        return f"🟢 Proxy sống lại: {name}"
    if not res["alive"] and was_alive is not False:
        return f"🔴 Proxy chết: {name} — {res['label']}"
    return None


def run(only_pool: Optional[str] = None) -> Dict[str, Any]:
    """Check every pooled proxy (in parallel). Returns the summary it stores."""
    try:
        if not _r().set(LOCK_KEY, "1", nx=True, ex=300):
            last = last_summary()
            return {**(last or {}), "skipped": "a check is already running"}
    except Exception:
        pass
    try:
        entries = [e for e in pool_entries() if not only_pool or e[0] == only_pool]
        # One check per distinct proxy (it may sit in several pools); the
        # YouTube test runs when any of its pools is youtube.
        where: Dict[str, List[Tuple[str, int]]] = {}
        for pool, i, p in entries:
            where.setdefault(p, []).append((pool, i))
        results: List[Dict[str, Any]] = []
        alerts: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL) as ex:
            futs = {ex.submit(check, p, any(pl == "youtube" for pl, _ in locs)): p
                    for p, locs in where.items()}
            for fut in concurrent.futures.as_completed(futs):
                p = futs[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = _labelled({"alive": False, "kind": "error", "detail": str(exc)[:160],
                                     "at": int(time.time())})
                pool0, i0 = where[p][0]
                line = _apply(pool0, i0, p, res)
                if line:
                    alerts.append(line)
                for pool, i in where[p]:
                    results.append({"pool": pool, "index": i, "proxy": mask(p), **res})
        results.sort(key=lambda r: (r["pool"], r["index"]))
        summary = {"at": int(time.time()), "total": len(results),
                   "alive": sum(1 for r in results if r["alive"]), "results": results}
        if not only_pool:
            try:
                _r().set(SUMMARY_KEY, json.dumps(summary), ex=RESULT_TTL)
            except Exception:
                pass
        if alerts:
            try:
                from app.core.notifications import send_telegram_message_sync  # noqa: PLC0415
                send_telegram_message_sync("<b>Kiểm tra proxy: có thay đổi</b>\n" + "\n".join(alerts[:20]))
            except Exception:
                pass
        return summary
    finally:
        try:
            _r().delete(LOCK_KEY)
        except Exception:
            pass


def last_summary() -> Optional[dict]:
    try:
        raw = _r().get(SUMMARY_KEY)
        return json.loads(_d(raw)) if raw else None
    except Exception:
        return None
