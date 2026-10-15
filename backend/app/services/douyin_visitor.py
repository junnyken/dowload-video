"""
Douyin visitor jar (owner 15/10/2026).

The signed web API (douyin_sign) needs the cookies of one anonymous visit to
douyin.com — above all `UIFID_TEMP`, which only Douyin's page JavaScript
creates. A small browser service (services/douyin-mint) makes that visit on
request; this module keeps the resulting jar and asks for a new one when it
gets old or Douyin starts refusing it. One jar serves every user: a browser
opens a few times a day, not once per video.

Redis
  douyin:visitor:jar        cookie_crypto-encrypted JSON {ua, cookies}
  douyin:visitor:meta       JSON: minted_at, ok, fail, fail_streak, last_ok_at,
                            last_fail_at, last_fail, last_mint_error. No cookie.
  douyin:visitor:settings   JSON: mint_url, mint_key (encrypted), max_age_h,
                            enabled. Set from admin — not env (owner rule).
  douyin:visitor:mint_lock  NX lock while one process mints
  douyin:visitor:mint_cool  set after a failed mint: no retry until it expires
  douyin:visitor:pause      set when a FRESH jar is refused too: Douyin is
                            throttling this server's IP, so the step sits out
  douyin:visitor:rate:m:<minute> / rate:h:<hour>   call counters

Measured 15/10 from one IP: 180 calls in ~4 minutes (up to 4 parallel) all
answered, then every call — old jar or freshly minted — got an empty 200.
The throttle is per IP, not per jar, hence the per-minute / per-hour caps
and the pause instead of minting again and again.

Never logs or returns a cookie value or the mint key.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("app.douyin_visitor")

JAR_KEY = "douyin:visitor:jar"
META_KEY = "douyin:visitor:meta"
SETTINGS_KEY = "douyin:visitor:settings"
LOCK_KEY = "douyin:visitor:mint_lock"
COOL_KEY = "douyin:visitor:mint_cool"

DEFAULT_MAX_AGE_H = 12.0
DEFAULT_PER_MIN = 20
DEFAULT_PER_HOUR = 300
DEFAULT_PAUSE_MIN = 30
PAUSE_KEY = "douyin:visitor:pause"
FAIL_STREAK_REMINT = 3        # refusals in a row before the jar is thrown away
MINT_COOLDOWN_S = 600
MINT_TIMEOUT_S = 75


def _redis():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _enc(text: str) -> str:
    from app.core import cookie_crypto  # noqa: PLC0415
    return cookie_crypto.encrypt(base64.b64encode(text.encode()).decode())


def _dec(stored: str) -> str:
    from app.core import cookie_crypto  # noqa: PLC0415
    return base64.b64decode(cookie_crypto.decrypt(stored)).decode()


def _json(raw: Optional[str]) -> Dict[str, Any]:
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


# ── settings (admin) ─────────────────────────────────────────────────────────

def get_settings(rc=None) -> Dict[str, Any]:
    s = _json((rc or _redis()).get(SETTINGS_KEY))
    return {
        "enabled": bool(s.get("enabled", True)),
        "mint_url": str(s.get("mint_url") or ""),
        "mint_key_enc": str(s.get("mint_key") or ""),
        "max_age_h": float(s.get("max_age_h") or DEFAULT_MAX_AGE_H),
        "per_min": int(s.get("per_min") or DEFAULT_PER_MIN),
        "per_hour": int(s.get("per_hour") or DEFAULT_PER_HOUR),
        "pause_min": int(s.get("pause_min") or DEFAULT_PAUSE_MIN),
    }


def public_settings(rc=None) -> Dict[str, Any]:
    s = get_settings(rc)
    return {"enabled": s["enabled"], "mint_url": s["mint_url"],
            "mint_key_set": bool(s["mint_key_enc"]), "max_age_h": s["max_age_h"],
            "per_min": s["per_min"], "per_hour": s["per_hour"], "pause_min": s["pause_min"]}


def save_settings(*, enabled: Optional[bool] = None, mint_url: Optional[str] = None,
                  mint_key: Optional[str] = None, max_age_h: Optional[float] = None,
                  per_min: Optional[int] = None, per_hour: Optional[int] = None,
                  pause_min: Optional[int] = None, rc=None) -> Dict[str, Any]:
    rc = rc or _redis()
    raw = _json(rc.get(SETTINGS_KEY))
    if enabled is not None:
        raw["enabled"] = bool(enabled)
    if mint_url is not None:
        url = mint_url.strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("mint_url phải bắt đầu bằng https:// hoặc http://")
        raw["mint_url"] = url
    if mint_key is not None:
        raw["mint_key"] = _enc(mint_key.strip()) if mint_key.strip() else ""
    if max_age_h is not None:
        if not 0.5 <= float(max_age_h) <= 168:
            raise ValueError("max_age_h phải trong khoảng 0.5–168 giờ")
        raw["max_age_h"] = float(max_age_h)
    for name, val, lo, hi in (("per_min", per_min, 1, 600), ("per_hour", per_hour, 1, 20000),
                              ("pause_min", pause_min, 1, 1440)):
        if val is not None:
            if not lo <= int(val) <= hi:
                raise ValueError(f"{name} phải trong khoảng {lo}–{hi}")
            raw[name] = int(val)
    rc.set(SETTINGS_KEY, json.dumps(raw))
    return public_settings(rc)


# ── jar ──────────────────────────────────────────────────────────────────────

def _meta(rc) -> Dict[str, Any]:
    return _json(rc.get(META_KEY))


def _save_meta(rc, meta: Dict[str, Any]) -> None:
    rc.set(META_KEY, json.dumps(meta))


def status(rc=None) -> Dict[str, Any]:
    rc = rc or _redis()
    meta = _meta(rc)
    has = bool(rc.get(JAR_KEY))
    minted = meta.get("minted_at")
    return {
        **public_settings(rc),
        "has_jar": has,
        "age_min": int((time.time() - minted) / 60) if has and minted else None,
        "ok": int(meta.get("ok") or 0),
        "fail": int(meta.get("fail") or 0),
        "fail_streak": int(meta.get("fail_streak") or 0),
        "last_ok_at": meta.get("last_ok_at"),
        "last_fail_at": meta.get("last_fail_at"),
        "last_fail": meta.get("last_fail"),
        "last_mint_error": meta.get("last_mint_error"),
        "mints": int(meta.get("mints") or 0),
        "cooling_down": bool(rc.get(COOL_KEY)),
        "paused_for_s": max(0, int(rc.ttl(PAUSE_KEY) or 0)),
        "calls_this_min": int(rc.get(_rate_keys()[0]) or 0),
        "calls_this_hour": int(rc.get(_rate_keys()[1]) or 0),
    }


def _rate_keys(now: Optional[float] = None) -> Tuple[str, str]:
    t = int(now if now is not None else time.time())
    return f"douyin:visitor:rate:m:{t // 60}", f"douyin:visitor:rate:h:{t // 3600}"


def take_slot(rc=None) -> bool:
    """Count one signed call against the per-minute / per-hour caps. False = over a cap."""
    rc = rc or _redis()
    s = get_settings(rc)
    km, kh = _rate_keys()
    m, h = rc.incr(km), rc.incr(kh)
    if m == 1:
        rc.expire(km, 120)
    if h == 1:
        rc.expire(kh, 7200)
    return m <= s["per_min"] and h <= s["per_hour"]


def store_jar(ua: str, cookies: Dict[str, str], rc=None) -> None:
    rc = rc or _redis()
    rc.set(JAR_KEY, _enc(json.dumps({"ua": ua, "cookies": cookies})))
    meta = _meta(rc)
    meta.update({"minted_at": time.time(), "ok": 0, "fail": 0, "fail_streak": 0,
                 "last_mint_error": None, "mints": int(meta.get("mints") or 0) + 1})
    _save_meta(rc, meta)


def drop_jar(rc=None) -> None:
    (rc or _redis()).delete(JAR_KEY)


def _load_jar(rc) -> Optional[Tuple[str, Dict[str, str], float]]:
    stored = rc.get(JAR_KEY)
    if not stored:
        return None
    try:
        jar = json.loads(_dec(stored))
    except Exception:  # noqa: BLE001 — undecryptable / corrupt: treat as absent
        logger.warning("[douyin_visitor] stored jar unreadable; dropping it")
        rc.delete(JAR_KEY)
        return None
    ua, cookies = jar.get("ua"), jar.get("cookies")
    if not isinstance(ua, str) or not isinstance(cookies, dict):
        return None
    return ua, cookies, float(_meta(rc).get("minted_at") or 0)


def _mint(rc, settings: Dict[str, Any]) -> bool:
    """Ask the mint service for a fresh jar. One process at a time; cools down on failure."""
    import httpx  # noqa: PLC0415
    if not settings["mint_url"] or not settings["mint_key_enc"]:
        return False
    if rc.get(COOL_KEY) or not rc.set(LOCK_KEY, "1", nx=True, ex=MINT_TIMEOUT_S + 15):
        return False
    err = None
    try:
        key = _dec(settings["mint_key_enc"])
        r = httpx.post(f"{settings['mint_url']}/mint/douyin", headers={"X-Mint-Key": key},
                       timeout=MINT_TIMEOUT_S)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        cookies, ua = body.get("cookies"), body.get("ua")
        if r.status_code == 200 and isinstance(cookies, dict) and isinstance(ua, str):
            from app.services.douyin_sign import pick_uifid  # noqa: PLC0415
            if pick_uifid(cookies):
                store_jar(ua.replace("HeadlessChrome", "Chrome"), cookies, rc)
                logger.info("[douyin_visitor] minted a new visitor jar")
                return True
            err = "mint trả về thiếu UIFID_TEMP"
        else:
            err = f"mint HTTP {r.status_code} {str(body.get('code') or '')}".strip()
    except Exception as exc:  # noqa: BLE001
        err = f"mint lỗi {type(exc).__name__}"
    finally:
        rc.delete(LOCK_KEY)
    meta = _meta(rc)
    meta["last_mint_error"] = err
    _save_meta(rc, meta)
    rc.set(COOL_KEY, "1", ex=MINT_COOLDOWN_S)
    logger.warning(f"[douyin_visitor] {err}")
    return False


def mint_now(rc=None) -> Dict[str, Any]:
    """Admin button: mint regardless of age (still honours the lock, not the cooldown)."""
    rc = rc or _redis()
    rc.delete(COOL_KEY)
    ok = _mint(rc, get_settings(rc))
    # "ok" here is the mint result; status() also has an "ok" counter, so it goes last.
    return {**status(rc), "ok": ok}


def get_jar(rc=None) -> Optional[Tuple[str, Dict[str, str]]]:
    """(user_agent, cookies) to sign with, minting when missing or too old. None = skip this step."""
    rc = rc or _redis()
    settings = get_settings(rc)
    if not settings["enabled"] or rc.get(PAUSE_KEY):
        return None
    jar = _load_jar(rc)
    too_old = jar is not None and time.time() - jar[2] > settings["max_age_h"] * 3600
    if jar is None or too_old:
        if _mint(rc, settings):
            jar = _load_jar(rc)
    return (jar[0], jar[1]) if jar else None


def report(ok: bool, reason: str = "", rc=None) -> None:
    """Count the outcome of one signed call; a run of refusals throws the jar away."""
    rc = rc or _redis()
    meta = _meta(rc)
    now = int(time.time())
    if ok:
        meta["ok"] = int(meta.get("ok") or 0) + 1
        meta["fail_streak"] = 0
        meta["last_ok_at"] = now
        meta["refused_after_remint"] = False
    else:
        meta["fail"] = int(meta.get("fail") or 0) + 1
        meta["fail_streak"] = int(meta.get("fail_streak") or 0) + 1
        meta["last_fail_at"] = now
        meta["last_fail"] = (reason or "")[:120]
    if not ok and meta["fail_streak"] >= FAIL_STREAK_REMINT:
        meta["fail_streak"] = 0
        if meta.get("refused_after_remint"):
            # A fresh jar is refused as well: the IP is throttled. Sit out.
            meta["refused_after_remint"] = False
            pause = get_settings(rc)["pause_min"]
            rc.set(PAUSE_KEY, "1", ex=pause * 60)
            logger.warning("[douyin_visitor] fresh jar refused too; pausing the signed step %s min", pause)
        else:
            meta["refused_after_remint"] = True
            logger.warning("[douyin_visitor] %s refusals in a row; dropping the jar", FAIL_STREAK_REMINT)
            drop_jar(rc)
    _save_meta(rc, meta)
