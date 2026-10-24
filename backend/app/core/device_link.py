"""
Device linking — one account, a few devices (owner 24/10/2026)
==============================================================
"Liên kết những tài khoản với nhau để không bị spam hay hack lượt tải."
Owner's choices:

- An account is used on at most N devices across the website, the extension
  and the Windows app: Free 2, paid 5. A new device beyond that signs out the
  device used longest ago.
- Several Free accounts on one device SHARE one Free allowance (the device
  bucket in app.core.quotas). Paid accounts are not affected.
- Rollout: SHADOW first (only logged: "would sign out", "would refuse"), then
  ENFORCE automatically 3 days later. The admin can change the mode or the
  date (Admin → Liên kết thiết bị); config is Redis, never env.

A device is the X-VG-Device header (64 hex). The app sends its machine hash;
the extension sends the app's hash when the app is on the machine (bridge
ping) or its own install id; the extension gives that id to the website on
the same browser. So one PC is one device for all three. No header (an old
client, an API key) = not linked, nothing changes.

Redis
  vidgrab:devlink:settings          {"mode", "enforce_at", "free_max", "paid_max"}
  vidgrab:devlink:u:<uid>           zset device -> last seen (epoch)
  vidgrab:devlink:rev:<uid>         hash device -> signed out at (epoch)
  vidgrab:devlink:d:<device>        zset uid -> last seen (who uses this device)
  vidgrab:devlink:stat:<day>        hash counter -> n (admin stats, 8 days)
  vidgrab:devlink:events            list of the last 200 events (json)

A signed-out device gets 401 device_signed_out until the person signs in
again there: a session whose sign-in time is newer than the sign-out is
admitted (and becomes the newest device, signing out the next oldest).
Every Redis error fails open: nobody is ever locked out because Redis is down.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SETTINGS_KEY = "vidgrab:devlink:settings"
EVENTS_KEY = "vidgrab:devlink:events"
MODE_OFF, MODE_SHADOW, MODE_ENFORCE = "off", "shadow", "enforce"
SHADOW_DAYS = 3
DEFAULT_FREE_MAX = 2
DEFAULT_PAID_MAX = 5
STALE_S = 30 * 86400          # a device unused for 30 days no longer counts
TOUCH_EVERY_S = 60            # last-seen refresh per (user, device) per process
EVENTS_MAX = 200

ERR_SIGNED_OUT = "device_signed_out"
ERR_DEVICE_SHARED = "device_free_shared"

_touched: Dict[str, float] = {}


def _rc():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")


def short_device(device: Optional[str]) -> Optional[str]:
    """The 32-hex form used in keys (same cut as the quota 'dev:' bucket)."""
    from app.core.quotas import valid_device_hash  # noqa: PLC0415
    d = valid_device_hash(device)
    return d[:32] if d else None


def device_from_request(request) -> Optional[str]:
    try:
        return short_device(request.headers.get("X-VG-Device"))
    except Exception:  # noqa: BLE001
        return None


# ── settings ────────────────────────────────────────────────────────────────

def _raw_settings(rc) -> Dict[str, Any]:
    try:
        v = json.loads(rc.get(SETTINGS_KEY) or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def get_settings(rc=None, now: Optional[float] = None) -> Dict[str, Any]:
    """First read starts the shadow period: enforce_at = now + 3 days, saved."""
    rc = rc or _rc()
    now = now or time.time()
    s = _raw_settings(rc)
    if "mode" not in s:
        s = {"mode": MODE_SHADOW, "enforce_at": int(now + SHADOW_DAYS * 86400),
             "free_max": DEFAULT_FREE_MAX, "paid_max": DEFAULT_PAID_MAX}
        rc.set(SETTINGS_KEY, json.dumps(s))
    mode = s.get("mode") if s.get("mode") in (MODE_OFF, MODE_SHADOW, MODE_ENFORCE) else MODE_SHADOW
    enforce_at = s.get("enforce_at")
    effective = mode
    if mode == MODE_SHADOW and enforce_at and now >= float(enforce_at):
        effective = MODE_ENFORCE
    return {
        "mode": mode,
        "effective_mode": effective,
        "enforce_at": int(enforce_at) if enforce_at else None,
        "free_max": int(s.get("free_max") or DEFAULT_FREE_MAX),
        "paid_max": int(s.get("paid_max") or DEFAULT_PAID_MAX),
    }


def save_settings(*, mode: Optional[str] = None, enforce_at: Any = "keep",
                  free_max: Optional[int] = None, paid_max: Optional[int] = None,
                  rc=None) -> Dict[str, Any]:
    rc = rc or _rc()
    cur = get_settings(rc)
    raw = {"mode": cur["mode"], "enforce_at": cur["enforce_at"],
           "free_max": cur["free_max"], "paid_max": cur["paid_max"]}
    if mode is not None:
        if mode not in (MODE_OFF, MODE_SHADOW, MODE_ENFORCE):
            raise ValueError("mode phải là off, shadow hoặc enforce")
        raw["mode"] = mode
    if enforce_at != "keep":
        raw["enforce_at"] = int(enforce_at) if enforce_at else None
    for name, val in (("free_max", free_max), ("paid_max", paid_max)):
        if val is not None:
            if not 1 <= int(val) <= 50:
                raise ValueError(f"{name} phải trong khoảng 1–50")
            raw[name] = int(val)
    rc.set(SETTINGS_KEY, json.dumps(raw))
    return get_settings(rc)


def effective_mode(rc=None) -> str:
    try:
        return get_settings(rc)["effective_mode"]
    except Exception:  # noqa: BLE001
        return MODE_OFF


# ── stats / events ──────────────────────────────────────────────────────────

def _stat(rc, name: str, now: Optional[float] = None) -> None:
    try:
        k = f"vidgrab:devlink:stat:{_day(now)}"
        rc.hincrby(k, name, 1)
        rc.expire(k, 8 * 86400)
    except Exception:  # noqa: BLE001
        pass


def _event(rc, kind: str, mode: str, **fields) -> None:
    try:
        rc.lpush(EVENTS_KEY, json.dumps({"t": int(time.time()), "kind": kind, "mode": mode, **fields}))
        rc.ltrim(EVENTS_KEY, 0, EVENTS_MAX - 1)
    except Exception:  # noqa: BLE001
        pass


def _once_today(rc, tag: str, ttl: int = 86400) -> bool:
    """True the first time `tag` is seen today (shadow events are logged once)."""
    try:
        return bool(rc.set(f"vidgrab:devlink:once:{_day()}:{tag}", 1, nx=True, ex=ttl))
    except Exception:  # noqa: BLE001
        return False


# ── sign-in time of a session ───────────────────────────────────────────────

def session_auth_time(token: Optional[str]) -> Optional[float]:
    """When the person signed in for this session, read from the token without
    verifying it (the caller already did). Supabase JWT: the newest `amr`
    timestamp (stays the same across hourly refreshes), else `iat`.
    Extension vgx_ token: its `iat` (only minted from a web sign-in or renewed
    by a device that is still admitted)."""
    if not token or not isinstance(token, str):
        return None
    try:
        if token.startswith("vgx_"):
            body = token[4:].split(".", 1)[0]
            p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            return float(p.get("iat") or 0) or None
        part = token.split(".")[1]
        p = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        amr = [float(a.get("timestamp") or 0) for a in (p.get("amr") or []) if isinstance(a, dict)]
        return max(amr) if amr and max(amr) > 0 else (float(p.get("iat") or 0) or None)
    except Exception:  # noqa: BLE001
        return None


# ── the registry ────────────────────────────────────────────────────────────

def _tier(user_id: str) -> str:
    try:
        from app.core.quotas import _get_tier  # noqa: PLC0415
        return _get_tier(user_id) or "free"
    except Exception:  # noqa: BLE001
        return "free"


def max_devices(tier: str, settings: Dict[str, Any]) -> int:
    return settings["free_max"] if (tier or "free") == "free" else settings["paid_max"]


def signed_out_message(limit: int) -> str:
    # wording: BA review
    return (f"Tài khoản này đang dùng trên quá {limit} thiết bị nên thiết bị này đã bị đăng xuất. "
            "Đăng nhập lại để dùng tiếp — thiết bị dùng lâu nhất sẽ bị đăng xuất thay.")


def touch(user_id: str, device: Optional[str], token: Optional[str] = None,
          tier: Optional[str] = None, rc=None, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Register (user, device). Returns a refusal {error_code, user_message,
    max_devices} when this device is signed out and the mode is enforce;
    None otherwise. Never raises."""
    if not user_id or not device:
        return None
    try:
        rc = rc or _rc()
        now = now or time.time()
        st = get_settings(rc, now)
        mode = st["effective_mode"]
        if mode == MODE_OFF:
            return None
        uid = str(user_id)
        rev_key = f"vidgrab:devlink:rev:{uid}"
        rev = rc.hget(rev_key, device)
        if rev is not None:
            if mode != MODE_ENFORCE:
                rc.hdel(rev_key, device)   # set while enforcing, mode turned back: forget it
            else:
                at = session_auth_time(token)
                if at and at > float(rev):
                    rc.hdel(rev_key, device)   # signed in again here: admitted
                    _touched.pop(f"{uid}:{device}", None)
                    _event(rc, "readmitted", mode, uid=uid, dev=device[:8])
                else:
                    _stat(rc, "blocked_requests", now)
                    lim = max_devices(tier or _tier(uid), st)
                    return {"error_code": ERR_SIGNED_OUT, "user_message": signed_out_message(lim),
                            "max_devices": lim}
        tag = f"{uid}:{device}"
        last = _touched.get(tag, 0)
        if now - last < TOUCH_EVERY_S:
            return None
        _touched[tag] = now
        if len(_touched) > 50000:
            _touched.clear()
        ukey = f"vidgrab:devlink:u:{uid}"
        dkey = f"vidgrab:devlink:d:{device}"
        pipe = rc.pipeline()
        pipe.zadd(ukey, {device: now})
        pipe.zremrangebyscore(ukey, 0, now - STALE_S)
        pipe.expire(ukey, STALE_S * 2)
        pipe.zadd(dkey, {uid: now})
        pipe.zremrangebyscore(dkey, 0, now - STALE_S)
        pipe.expire(dkey, STALE_S * 2)
        pipe.zrange(ukey, 0, -1)
        devices = [d.decode() if isinstance(d, bytes) else d for d in pipe.execute()[-1]]
        if len(devices) <= min(st["free_max"], st["paid_max"]):
            return None
        lim = max_devices(tier or _tier(uid), st)
        extra = len(devices) - lim
        if extra <= 0:
            return None
        oldest = [d for d in devices if d != device][:extra]
        for d in oldest:
            if mode == MODE_ENFORCE:
                rc.hset(rev_key, d, now)
                rc.expire(rev_key, STALE_S)
                rc.zrem(ukey, d)
                _stat(rc, "signed_out", now)
                _event(rc, "signed_out", mode, uid=uid, dev=d[:8], by=device[:8], limit=lim)
            elif _once_today(rc, f"we:{uid}:{d}"):
                _stat(rc, "would_sign_out", now)
                _event(rc, "would_sign_out", mode, uid=uid, dev=d[:8], by=device[:8], limit=lim)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[DeviceLink] touch failed: {type(e).__name__}")
        return None


def note_shared_refusal(user_id: str, device: str, mode: str, platform: str) -> None:
    """The device bucket of Free accounts was full (quotas): logged once a day
    per (user, device)."""
    try:
        rc = _rc()
        if _once_today(rc, f"sh:{mode}:{user_id}:{device}"):
            _stat(rc, "shared_blocked" if mode == MODE_ENFORCE else "shared_would_block")
            _event(rc, "shared_blocked" if mode == MODE_ENFORCE else "shared_would_block", mode,
                   uid=str(user_id), dev=device[:8], platform=platform)
    except Exception:  # noqa: BLE001
        pass


def forget_device(user_id: str, device: str, rc=None) -> None:
    """Sign-out from a device: it stops counting toward the account."""
    try:
        rc = rc or _rc()
        rc.zrem(f"vidgrab:devlink:u:{user_id}", device)
        _touched.pop(f"{user_id}:{device}", None)
    except Exception:  # noqa: BLE001
        pass


def user_devices(user_id: str, rc=None) -> List[Dict[str, Any]]:
    rc = rc or _rc()
    rows = rc.zrange(f"vidgrab:devlink:u:{user_id}", 0, -1, withscores=True)
    revoked = rc.hgetall(f"vidgrab:devlink:rev:{user_id}") or {}
    out = [{"device": (d.decode() if isinstance(d, bytes) else d)[:8], "last_seen": int(s), "signed_out": False}
           for d, s in rows]
    out += [{"device": (d.decode() if isinstance(d, bytes) else d)[:8], "last_seen": None,
             "signed_out_at": int(float(v))} for d, v in revoked.items()]
    return out


def stats(rc=None, days: int = 7) -> Dict[str, Any]:
    rc = rc or _rc()
    now = time.time()
    per_day = []
    for i in range(days):
        day = _day(now - i * 86400)
        h = rc.hgetall(f"vidgrab:devlink:stat:{day}") or {}
        per_day.append({"day": day, **{(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in h.items()}})
    events = []
    for raw in rc.lrange(EVENTS_KEY, 0, 49):
        try:
            events.append(json.loads(raw))
        except (TypeError, ValueError):
            pass
    # Devices used by more than one account (scan is bounded).
    shared = []
    for i, key in enumerate(rc.scan_iter(match="vidgrab:devlink:d:*", count=500)):
        if i >= 5000:
            break
        n = rc.zcount(key, now - 7 * 86400, "+inf")
        if n > 1:
            k = key.decode() if isinstance(key, bytes) else key
            shared.append({"device": k.rsplit(":", 1)[-1][:8], "accounts_7d": int(n)})
    shared.sort(key=lambda r: -r["accounts_7d"])
    return {"settings": get_settings(rc), "per_day": per_day, "events": events,
            "multi_account_devices": shared[:50], "multi_account_device_count": len(shared)}
