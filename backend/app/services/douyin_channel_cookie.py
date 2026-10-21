"""
Douyin cookie for whole-channel listings (owner 21/10/2026)
============================================================

The shared visitor jar (douyin_visitor) only ever gets the first page of a
profile (~41 videos). Measured 21/10: the douyin.com cookies of the owner's
own Chrome (no Douyin account, just history — ttwid + odin_tt matter;
UIFID + s_v_web_id alone or the jar without ttwid/odin_tt get an empty
answer) page a whole channel through the signed web API: 11 pages of 18,
166 videos, has_more=0.

So the admin pastes such cookies here (its own admin section; the cookie
pool used for downloads is not touched) and the server lists whole Douyin
channels for signed-in users. Several cookies are kept and taken in turn;
a refused first page moves on to the next live one. When none is usable,
switched off or over the hourly cap, callers fall back to the visitor's
first page and the extension relay. A beat task tests every cookie each
12 hours (probe_all) so a dead one is found before a user meets it.

Redis
  douyin:chcookie:jars       hash id -> cookie_crypto-encrypted JSON {cookies, ua}
  douyin:chcookie:metas      hash id -> JSON: label, saved_at, names, ok, fail,
                             fail_streak, last_ok_at, last_fail_at, last_fail, last_test
  douyin:chcookie:rr         turn counter
  douyin:chcookie:auto       JSON: the last scheduled test run
  douyin:chcookie:settings   JSON: enabled, per_hour, test_url
  douyin:chcookie:rate:h:<hour>   page calls this hour
  douyin:chcookie:list:<sec_uid>  last whole listing (30 min)
  (douyin:chcookie:jar / :meta: the single cookie of the first version,
  moved into the hash on first read)

Never logs or returns a cookie value.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.douyin_channel_cookie")

JARS_KEY = "douyin:chcookie:jars"
METAS_KEY = "douyin:chcookie:metas"
RR_KEY = "douyin:chcookie:rr"
AUTO_KEY = "douyin:chcookie:auto"
OLD_JAR_KEY = "douyin:chcookie:jar"
OLD_META_KEY = "douyin:chcookie:meta"
SETTINGS_KEY = "douyin:chcookie:settings"
LIST_KEY = "douyin:chcookie:list:{}"
LIST_TTL_S = 1800

PAGE_COUNT = 18            # what the measured run used; Douyin's own page asks 18
PAGE_PAUSE_S = 0.6
MAX_PAGES = 40             # 720 items, well past MAX_ITEMS
MAX_ITEMS = 500            # the /channel/preview limit
TIME_BUDGET_S = 50         # stay under proxy timeouts; a longer channel returns partial
DEFAULT_PER_HOUR = 150     # ~13 whole channels of ~170 videos
FAIL_STREAK_DEAD = 3
MAX_COOKIES = 20
FAILOVER_TRIES = 3         # cookies tried for one listing's first page
PROBE_RETRY_S = 60         # a scheduled test that fails is repeated once after this
# The measured channel (作品 195): proof that page 2 comes back.
DEFAULT_TEST_URL = "https://www.douyin.com/user/MS4wLjABAAAAkQs2tRzfg_eQ94ErmIEe4ba2ADzMVA7N7PbV-92qtgw3tLxfFB15eEW4hYgnRYPh"
# Matches douyin_sign's browser params (Chrome 130, Windows).
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
REQUIRED = ("ttwid", "odin_tt")

_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
_PROFILE = re.compile(r"^https://www\.douyin\.com/user/(MS4w[A-Za-z0-9_-]{10,120})(?:[/?#].*)?$")

# wording: BA review
MSG_EMPTY = "Không tìm thấy cookie nào của douyin.com trong nội dung đã dán."
MSG_MISSING = "Cookie thiếu {names}. Hãy xuất lại cookie khi đang mở trang douyin.com trong Chrome."
MSG_FULL = "Kho đã đủ {n} cookie. Hãy xoá bớt cookie đã chết trước."


class CookieError(ValueError):
    pass


def _redis():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _json(raw) -> Dict[str, Any]:
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


# ── parsing ──────────────────────────────────────────────────────────────────

def _douyin_domain(domain: str) -> bool:
    d = (domain or "").strip().lstrip(".").lower()
    return d == "douyin.com" or d.endswith(".douyin.com")


def parse_cookies(text: str) -> Dict[str, str]:
    """douyin.com cookies from a Netscape cookies.txt, a JSON export (list of
    {domain,name,value} or a {name: value} object) or a "a=b; c=d" header.
    Other sites' cookies in the paste are dropped here and never stored."""
    text = (text or "").strip()
    out: Dict[str, str] = {}

    def put(name, value):
        name, value = str(name or "").strip(), str(value or "").strip()
        if _NAME_OK.match(name) and value and len(value) <= 4096 and not any(c in value for c in "\r\n;"):
            out[name] = value

    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            data = data.get("cookies", data)
        if isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and _douyin_domain(str(c.get("domain") or "")):
                    put(c.get("name"), c.get("value"))
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str):
                    put(k, v)
        if data is not None:
            return out
    if "\t" in text:
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and _douyin_domain(parts[0]):
                put(parts[5], parts[6])
        return out
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            put(k, v)
    return out


# ── settings / storage ───────────────────────────────────────────────────────

def get_settings(rc=None) -> Dict[str, Any]:
    s = _json((rc or _redis()).get(SETTINGS_KEY))
    return {
        "enabled": bool(s.get("enabled", True)),
        "per_hour": int(s.get("per_hour") or DEFAULT_PER_HOUR),
        "test_url": str(s.get("test_url") or DEFAULT_TEST_URL),
    }


def save_settings(*, enabled: Optional[bool] = None, per_hour: Optional[int] = None,
                  test_url: Optional[str] = None, rc=None) -> Dict[str, Any]:
    rc = rc or _redis()
    raw = _json(rc.get(SETTINGS_KEY))
    if enabled is not None:
        raw["enabled"] = bool(enabled)
    if per_hour is not None:
        if not 1 <= int(per_hour) <= 5000:
            raise ValueError("per_hour phải trong khoảng 1–5000")
        raw["per_hour"] = int(per_hour)
    if test_url is not None:
        url = test_url.strip()
        if url and not _PROFILE.match(url):
            raise ValueError("Kênh thử phải là link https://www.douyin.com/user/…")
        raw["test_url"] = url
    rc.set(SETTINGS_KEY, json.dumps(raw))
    return get_settings(rc)


def _migrate(rc) -> None:
    """Move the first version's single cookie into the pool."""
    old = rc.get(OLD_JAR_KEY)
    if not old:
        return
    meta = _json(rc.get(OLD_META_KEY))
    cid = _new_id()
    rc.hset(JARS_KEY, cid, old)
    rc.hset(METAS_KEY, cid, json.dumps(meta))
    rc.delete(OLD_JAR_KEY, OLD_META_KEY)


def _new_id() -> str:
    import secrets  # noqa: PLC0415
    return secrets.token_hex(4)


def _ids(rc) -> List[str]:
    """Pool ids, oldest first."""
    _migrate(rc)
    metas = rc.hgetall(METAS_KEY) or {}
    jars = set(rc.hkeys(JARS_KEY) or [])
    return sorted((i for i in metas if i in jars), key=lambda i: (_json(metas[i]).get("saved_at") or 0, i))


def _meta(rc, cid: str) -> Dict[str, Any]:
    return _json(rc.hget(METAS_KEY, cid))


def _dead(meta: Dict[str, Any]) -> bool:
    return int(meta.get("fail_streak") or 0) >= FAIL_STREAK_DEAD


def store(text: str, label: str = "", rc=None) -> Dict[str, Any]:
    """Parse and add a cookie to the pool. A cookie with the same ttwid as
    one already there replaces it. Returns {"id", "names"} (no values)."""
    cookies = parse_cookies(text)
    if not cookies:
        raise CookieError(MSG_EMPTY)
    missing = [n for n in REQUIRED if n not in cookies]
    from app.services.douyin_sign import pick_uifid  # noqa: PLC0415
    if not pick_uifid(cookies):
        missing.append("UIFID")
    if missing:
        raise CookieError(MSG_MISSING.format(names=", ".join(missing)))
    rc = rc or _redis()
    ids = _ids(rc)
    cid = None
    for other in ids:
        jar = _load(rc, other)
        if jar and jar[1].get("ttwid") == cookies["ttwid"]:
            cid = other
            break
    if cid is None:
        if len(ids) >= MAX_COOKIES:
            raise CookieError(MSG_FULL.format(n=MAX_COOKIES))
        cid = _new_id()
    old_label = _meta(rc, cid).get("label") or ""
    from app.services.douyin_visitor import _enc  # noqa: PLC0415
    rc.hset(JARS_KEY, cid, _enc(json.dumps({"cookies": cookies, "ua": USER_AGENT})))
    names = sorted(cookies)
    rc.hset(METAS_KEY, cid, json.dumps({
        "label": (label or "").strip()[:60] or old_label, "saved_at": time.time(), "names": names,
        "ok": 0, "fail": 0, "fail_streak": 0}))
    return {"id": cid, "names": names}


def drop(cid: str, rc=None) -> bool:
    rc = rc or _redis()
    _migrate(rc)
    return bool(rc.hdel(JARS_KEY, cid)) | bool(rc.hdel(METAS_KEY, cid))


def _load(rc, cid: str) -> Optional[Tuple[str, Dict[str, str]]]:
    stored = rc.hget(JARS_KEY, cid)
    if not stored:
        return None
    try:
        from app.services.douyin_visitor import _dec  # noqa: PLC0415
        jar = json.loads(_dec(stored))
        return str(jar.get("ua") or USER_AGENT), dict(jar["cookies"])
    except Exception:  # noqa: BLE001
        return None


def _rate_key(now: Optional[float] = None) -> str:
    return f"douyin:chcookie:rate:h:{int(now if now is not None else time.time()) // 3600}"


def _cookie_status(cid: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": cid,
        "label": meta.get("label") or "",
        "saved_at": meta.get("saved_at"),
        "names": meta.get("names") or [],
        "ok": int(meta.get("ok") or 0),
        "fail": int(meta.get("fail") or 0),
        "fail_streak": int(meta.get("fail_streak") or 0),
        "dead": _dead(meta),
        "last_ok_at": meta.get("last_ok_at"),
        "last_fail_at": meta.get("last_fail_at"),
        "last_fail": meta.get("last_fail"),
        "last_test": meta.get("last_test"),
    }


def status(rc=None) -> Dict[str, Any]:
    rc = rc or _redis()
    cookies = [_cookie_status(i, _meta(rc, i)) for i in _ids(rc)]
    return {
        **get_settings(rc),
        "cookies": cookies,
        "has_cookie": bool(cookies),
        "live": sum(1 for c in cookies if not c["dead"]),
        "calls_this_hour": int(rc.get(_rate_key()) or 0),
        "auto": _json(rc.get(AUTO_KEY)) or None,
    }


def _live_ids(rc) -> List[str]:
    return [i for i in _ids(rc) if not _dead(_meta(rc, i))]


def usable(rc=None) -> bool:
    """Switched on and at least one cookie not refused 3 times in a row."""
    try:
        rc = rc or _redis()
        return get_settings(rc)["enabled"] and bool(_live_ids(rc))
    except Exception:  # noqa: BLE001
        return False


def _report(rc, cid: str, ok: bool, reason: str = "", *, kill: bool = False) -> None:
    meta = _meta(rc, cid)
    if not meta and not rc.hexists(JARS_KEY, cid):
        return  # deleted meanwhile
    now = time.time()
    if ok:
        meta.update({"ok": int(meta.get("ok") or 0) + 1, "fail_streak": 0, "last_ok_at": now})
    else:
        streak = int(meta.get("fail_streak") or 0) + 1
        meta.update({"fail": int(meta.get("fail") or 0) + 1,
                     "fail_streak": max(streak, FAIL_STREAK_DEAD) if kill else streak,
                     "last_fail_at": now, "last_fail": reason[:200]})
    rc.hset(METAS_KEY, cid, json.dumps(meta))


def _take_slot(rc, per_hour: int) -> bool:
    k = _rate_key()
    n = rc.incr(k)
    if n == 1:
        rc.expire(k, 7200)
    return n <= per_hour


def _in_turn(rc) -> List[str]:
    """Live cookies, starting from the next one in turn."""
    live = _live_ids(rc)
    if not live:
        return []
    start = int(rc.incr(RR_KEY)) % len(live)
    return live[start:] + live[:start]


# ── listing ──────────────────────────────────────────────────────────────────

def _fetch_page(ua: str, cookies: Dict[str, str], sec_uid: str, cursor: str) -> Tuple[Optional[dict], str]:
    from curl_cffi import requests as cr  # noqa: PLC0415
    from app.services.douyin_sign import post_request  # noqa: PLC0415
    req = post_request(sec_uid, cookies, ua, PAGE_COUNT, cursor)
    if not req:
        return None, "bad_request"
    url, headers = req
    try:
        r = cr.get(url, headers=headers, cookies=cookies, impersonate="chrome131", timeout=25)
    except Exception as exc:  # noqa: BLE001
        return None, f"network {type(exc).__name__}"
    body = r.text or ""
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError:
        return None, "non-json"
    if not isinstance(data, dict) or "aweme_list" not in data:
        return None, "empty 200"
    return data, ""


def list_channel(sec_uid: str, limit: int, *, use_cache: bool = True, rc=None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Up to `limit` newest videos of a profile, paging with the pool's cookies.

    Returns ({"channel_title", "videos", "has_more", "pages"} or None, reason),
    the video dicts in douyin_extractor.signed_profile_listing's shape. A
    refused first page moves on to the next live cookie (up to
    FAILOVER_TRIES); None when every one tried failed. A later page's failure
    returns what was read with has_more=True. Blocking (curl_cffi)."""
    from app.services.douyin_extractor import _is_playable_video, _remember_listed  # noqa: PLC0415
    rc = rc or _redis()
    limit = max(1, min(int(limit), MAX_ITEMS))
    if use_cache:
        cached = _json(rc.get(LIST_KEY.format(sec_uid)))
        if cached.get("videos") and (not cached.get("has_more") or len(cached["videos"]) >= limit):
            return {**cached, "videos": cached["videos"][:limit],
                    "has_more": bool(cached.get("has_more")) or len(cached["videos"]) > limit}, ""
    if not get_settings(rc)["enabled"]:
        return None, "unusable"
    order = _in_turn(rc)
    if not order:
        return None, "unusable"
    per_hour = get_settings(rc)["per_hour"]
    from app.services import douyin_visitor  # noqa: PLC0415
    first, why, cid, ua, cookies, calls = None, "no_cookie", "", "", {}, 0
    for cid in order[:FAILOVER_TRIES]:
        jar = _load(rc, cid)
        if not jar:
            continue
        if not _take_slot(rc, per_hour):
            douyin_visitor_slots(douyin_visitor, rc, calls)
            return None, "rate_cap"
        ua, cookies = jar
        calls += 1
        first, why = _fetch_page(ua, cookies, sec_uid, "0")
        if first is not None:
            _report(rc, cid, True)
            break
        _report(rc, cid, False, f"page 1: {why}")
    if first is None:
        douyin_visitor_slots(douyin_visitor, rc, calls)
        return None, why
    videos: List[Dict[str, Any]] = []
    seen: set = set()
    title, cursor, more, pages, data = "", "0", True, 0, first
    started = time.monotonic()
    while True:
        pages += 1
        awemes = [a for a in (data.get("aweme_list") or []) if isinstance(a, dict)]
        playable = [a for a in awemes if _is_playable_video(a)]
        _remember_listed(playable)
        for a in awemes:
            title = title or str(((a.get("author") or {}).get("nickname")) or "")
        for a in playable:
            vid = str(a.get("aweme_id") or "")
            if not vid.isdigit() or vid in seen:
                continue
            seen.add(vid)
            video = a.get("video") or {}
            cover = ((video.get("cover") or {}).get("url_list")) or []
            ms = video.get("duration") or 0
            videos.append({"id": vid, "title": (a.get("desc") or "").strip()[:300],
                           "thumbnail": cover[0] if cover else None,
                           "duration_sec": round(ms / 1000, 1) if ms else None})
        nxt = str(data.get("max_cursor") or "")
        more = bool(data.get("has_more")) and bool(awemes) and nxt.isdigit() and nxt != cursor
        cursor = nxt
        if not more or len(videos) >= limit or pages >= MAX_PAGES:
            break
        if time.monotonic() - started > TIME_BUDGET_S or not _take_slot(rc, per_hour):
            break
        calls += 1
        time.sleep(PAGE_PAUSE_S)
        data, why = _fetch_page(ua, cookies, sec_uid, cursor)
        if data is None:
            _report(rc, cid, False, f"page {pages + 1}: {why}")
            break
    douyin_visitor_slots(douyin_visitor, rc, calls)
    has_more = more or len(videos) > limit
    listed = {"channel_title": title, "videos": videos, "has_more": has_more, "pages": pages}
    try:
        rc.set(LIST_KEY.format(sec_uid), json.dumps(listed), ex=LIST_TTL_S)
    except Exception:  # noqa: BLE001
        pass
    logger.info("douyin channel cookie listing pages=%s videos=%s more=%s", pages, len(videos), has_more)
    return {**listed, "videos": videos[:limit]}, ""


def douyin_visitor_slots(douyin_visitor, rc, pages: int) -> None:
    """Count these calls in the visitor's per-IP counters too: Douyin throttles
    by IP, whichever jar asks."""
    for _ in range(pages):
        try:
            douyin_visitor.take_slot(rc)
        except Exception:  # noqa: BLE001
            return


# ── tests ────────────────────────────────────────────────────────────────────

def _run_test(rc, cid: str) -> Dict[str, Any]:
    """Two pages of the test channel with one cookie: page 2 is what a
    visitor jar never gets. Does not report; callers decide."""
    m = _PROFILE.match(get_settings(rc)["test_url"])
    jar = _load(rc, cid)
    if not jar:
        return {"ok": False, "pages": 0, "items": 0, "reason": "no_cookie"}
    if not m:
        return {"ok": False, "pages": 0, "items": 0, "reason": "bad_test_url"}
    ua, cookies = jar
    pages, items, cursor, reason = 0, 0, "0", ""
    for _ in range(2):
        if not _take_slot(rc, get_settings(rc)["per_hour"]):
            reason = "rate_cap"
            break
        data, why = _fetch_page(ua, cookies, m.group(1), cursor)
        if data is None:
            reason = why
            break
        pages += 1
        items += len(data.get("aweme_list") or [])
        cursor = str(data.get("max_cursor") or "")
        if not data.get("has_more") or not cursor.isdigit():
            break
        time.sleep(PAGE_PAUSE_S)
    ok = pages >= 2 or (pages == 1 and not reason)
    return {"ok": ok, "pages": pages, "items": items, "reason": reason}


def _keep_test(rc, cid: str, out: Dict[str, Any], *, auto: bool = False) -> None:
    meta = _meta(rc, cid)
    if meta:
        meta["last_test"] = {**out, "at": time.time(), "auto": auto}
        rc.hset(METAS_KEY, cid, json.dumps(meta))


def test(cid: str, rc=None) -> Dict[str, Any]:
    """The admin's "Thử": one try, counted like a listing (a failure is one strike)."""
    rc = rc or _redis()
    out = _run_test(rc, cid)
    if out["reason"] not in ("rate_cap", "no_cookie", "bad_test_url"):
        _report(rc, cid, out["ok"], "" if out["ok"] else f"test page {out['pages'] + 1}: {out['reason']}")
    _keep_test(rc, cid, out)
    return out


def probe_all(rc=None, *, sleep=time.sleep) -> Dict[str, Any]:
    """The 12-hourly check: test every cookie, dead ones too (a cookie that
    works again comes back). A failure is repeated once after PROBE_RETRY_S;
    failing twice marks the cookie dead at once, unless every cookie failed:
    that is the server's IP being throttled, not the cookies."""
    rc = rc or _redis()
    ids = _ids(rc)
    results: Dict[str, Dict[str, Any]] = {}
    for cid in ids:
        results[cid] = _run_test(rc, cid)
    retry = [c for c, r in results.items() if not r["ok"] and r["reason"] not in ("rate_cap", "no_cookie", "bad_test_url")]
    if retry:
        sleep(PROBE_RETRY_S)
        for cid in retry:
            results[cid] = _run_test(rc, cid)
    tested = [c for c, r in results.items() if r["reason"] not in ("rate_cap", "no_cookie", "bad_test_url")]
    failed = [c for c in tested if not results[c]["ok"]]
    ip_blocked = bool(tested) and len(failed) == len(tested) and len(tested) > 1
    for cid in tested:
        r = results[cid]
        if r["ok"]:
            _report(rc, cid, True)
        elif not ip_blocked:
            _report(rc, cid, False, f"tự thử: trang {r['pages'] + 1}: {r['reason']}", kill=True)
        _keep_test(rc, cid, r, auto=True)
    summary = {"at": time.time(), "tested": len(tested), "ok": len(tested) - len(failed),
               "dead": 0 if ip_blocked else len(failed), "ip_blocked": ip_blocked,
               "skipped": len(ids) - len(tested)}
    rc.set(AUTO_KEY, json.dumps(summary))
    logger.info("douyin channel cookie probe %s", summary)
    return summary
