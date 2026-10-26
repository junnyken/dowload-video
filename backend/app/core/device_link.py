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
  vidgrab:devlink:meta:<uid>        hash device -> {"kind", "label"} ("Thiết bị của tôi")
  vidgrab:devlink:man:<uid>         hash device -> signed out BY THE PERSON at (any mode)

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
DEFAULT_NET_FREE_ACCOUNTS = 3     # Free accounts per network a day before they share
DEFAULT_CLONE_NETWORKS = 4        # networks one device id may appear on within an hour
CLONE_WINDOW_S = 3600
CLONE_FLAG_S = 86400
PSEUDO_STALE_S = 86400            # a "no device id" entry stops counting after a day
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
    if "extra_enforce_at" not in s:
        # 26/10 anti-bypass rules (see "Bypass guards" below): their own 3-day watch
        s["extra_enforce_at"] = int(now + SHADOW_DAYS * 86400)
        rc.set(SETTINGS_KEY, json.dumps(s))
    mode = s.get("mode") if s.get("mode") in (MODE_OFF, MODE_SHADOW, MODE_ENFORCE) else MODE_SHADOW
    enforce_at = s.get("enforce_at")
    effective = mode
    if mode == MODE_SHADOW and enforce_at and now >= float(enforce_at):
        effective = MODE_ENFORCE
    extra_at = s.get("extra_enforce_at")
    extra = MODE_OFF if effective == MODE_OFF else (
        MODE_ENFORCE if effective == MODE_ENFORCE and (not extra_at or now >= float(extra_at)) else MODE_SHADOW)
    return {
        "mode": mode,
        "effective_mode": effective,
        "enforce_at": int(enforce_at) if enforce_at else None,
        "free_max": int(s.get("free_max") or DEFAULT_FREE_MAX),
        "paid_max": int(s.get("paid_max") or DEFAULT_PAID_MAX),
        "extra_mode": extra,
        "extra_enforce_at": int(extra_at) if extra_at else None,
        "net_free_accounts": int(s.get("net_free_accounts") or DEFAULT_NET_FREE_ACCOUNTS),
        "clone_networks": int(s.get("clone_networks") or DEFAULT_CLONE_NETWORKS),
    }


def save_settings(*, mode: Optional[str] = None, enforce_at: Any = "keep",
                  free_max: Optional[int] = None, paid_max: Optional[int] = None,
                  extra_enforce_at: Any = "keep", net_free_accounts: Optional[int] = None,
                  clone_networks: Optional[int] = None, rc=None) -> Dict[str, Any]:
    rc = rc or _rc()
    cur = get_settings(rc)
    raw = {"mode": cur["mode"], "enforce_at": cur["enforce_at"],
           "free_max": cur["free_max"], "paid_max": cur["paid_max"],
           "extra_enforce_at": cur["extra_enforce_at"], "net_free_accounts": cur["net_free_accounts"],
           "clone_networks": cur["clone_networks"]}
    if extra_enforce_at != "keep":
        raw["extra_enforce_at"] = int(extra_enforce_at) if extra_enforce_at else None
    for name, val, lo, hi in (("net_free_accounts", net_free_accounts, 1, 100), ("clone_networks", clone_networks, 2, 50)):
        if val is not None:
            if not lo <= int(val) <= hi:
                raise ValueError(f"{name} phải trong khoảng {lo}–{hi}")
            raw[name] = int(val)
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


def extra_mode(rc=None) -> str:
    try:
        return get_settings(rc)["extra_mode"]
    except Exception:  # noqa: BLE001
        return MODE_OFF


# ── Bypass guards (owner 26/10/2026) ────────────────────────────────────────
# The device id comes from the client, so on its own it can be copied to many
# machines, left out, or renewed with a private window. Server-side anchors:
#
#   copied id      one (account, device) seen on more than `clone_networks`
#                  networks within an hour is treated as several devices:
#                  "x" + hash(device, network) per network (24 h flag).
#   no id          a signed-in call without X-VG-Device counts as a device of
#                  its network: "n" + hash(network), stale after a day.
#   email aliases  name+1@gmail.com, n.a.m.e@gmail.com … share the Free
#                  allowance (family = normalised address).
#   many accounts  beyond `net_free_accounts` Free accounts on one network in
#                  a day, the extra accounts share one Free allowance.
#
# Own watch period (extra_enforce_at, 3 days from the first read): until then
# these only log; the device ids used are the clients' own.

def net_of(ip: Optional[str], wide: bool = False) -> Optional[str]:
    """16-hex hash of the client's network: the IPv4 address (wide: /16) or the
    IPv6 /64 (wide: /48). Raw addresses are never stored here."""
    import hashlib  # noqa: PLC0415
    import ipaddress  # noqa: PLC0415
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if a.version == 4:
        n = ipaddress.ip_network(f"{a}/{16 if wide else 32}", strict=False)
    else:
        n = ipaddress.ip_network(f"{a}/{48 if wide else 64}", strict=False)
    return hashlib.sha1(f"vgnet:{n}".encode()).hexdigest()[:16]


def email_family(email: Optional[str]) -> Optional[str]:
    """Same mailbox → same family: lower case, "+tag" dropped; Gmail also drops
    dots (googlemail.com = gmail.com)."""
    import hashlib  # noqa: PLC0415
    e = (email or "").strip().lower()
    if "@" not in e:
        return None
    local, domain = e.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    if not local:
        return None
    return hashlib.sha1(f"vgfam:{local}@{domain}".encode()).hexdigest()[:16]


def note_identity(user_id: str, email: Optional[str], rc=None) -> None:
    """Remember the account's email family (read by quotas). Never raises."""
    fam = email_family(email)
    if not user_id or not fam:
        return
    tag = f"fam:{user_id}"
    if time.time() - _touched.get(tag, 0) < 600:
        return
    _touched[tag] = time.time()
    try:
        rc = rc or _rc()
        rc.set(f"vidgrab:devlink:fam:{user_id}", fam, ex=STALE_S)
        rc.sadd(f"vidgrab:devlink:famusers:{fam}", str(user_id))
        rc.expire(f"vidgrab:devlink:famusers:{fam}", STALE_S)
    except Exception:  # noqa: BLE001
        pass


def family_of(user_id: str, rc=None) -> Optional[str]:
    """The family only when it has more than one account (else nothing to share)."""
    try:
        rc = rc or _rc()
        fam = rc.get(f"vidgrab:devlink:fam:{user_id}")
        fam = fam.decode() if isinstance(fam, bytes) else fam
        if fam and rc.scard(f"vidgrab:devlink:famusers:{fam}") > 1:
            return fam
    except Exception:  # noqa: BLE001
        pass
    return None


def network_extra(user_id: str, net: Optional[str], rc=None) -> bool:
    """True when this Free account is beyond the first `net_free_accounts` seen
    on this network today (the order is first use)."""
    if not user_id or not net:
        return False
    try:
        rc = rc or _rc()
        k = f"vidgrab:devlink:netusers:{net}:{_day()}"
        rc.zadd(k, {str(user_id): time.time()}, nx=True)
        rc.expire(k, 2 * 86400)
        rank = rc.zrank(k, str(user_id))
        return rank is not None and rank >= get_settings(rc)["net_free_accounts"]
    except Exception:  # noqa: BLE001
        return False


def effective_device(user_id: str, device: Optional[str], ip: Optional[str], rc=None,
                     now: Optional[float] = None) -> Optional[str]:
    """The device id the registry and the quotas use: the client's own, or —
    once the bypass guards enforce — a per-network split of a copied id, or a
    network pseudo-device when the call has no id. Shadow: logged, the
    client's id (or None) is returned. Never raises."""
    try:
        rc = rc or _rc()
        now = now or time.time()
        st = get_settings(rc, now)
        mode = st["extra_mode"]
        if mode == MODE_OFF or not user_id:
            return device
        import hashlib  # noqa: PLC0415
        uid = str(user_id)
        if not device:
            net = net_of(ip)
            if not net:
                return None
            if mode != MODE_ENFORCE:
                if _once_today(rc, f"nd:{uid}:{net}"):
                    _stat(rc, "no_device_would_count", now)
                return None
            return "n" + hashlib.sha1(f"{uid}:{net}".encode()).hexdigest()[:31]
        wide = net_of(ip, wide=True)
        if not wide:
            return device
        k = f"vidgrab:devlink:net:{uid}:{device}"
        flag = f"vidgrab:devlink:clone:{uid}:{device}"
        pipe = rc.pipeline()
        pipe.zadd(k, {wide: now})
        pipe.zremrangebyscore(k, 0, now - CLONE_WINDOW_S)
        pipe.expire(k, CLONE_WINDOW_S * 2)
        pipe.zcard(k)
        pipe.exists(flag)
        res = pipe.execute()
        nets, flagged = int(res[-2]), bool(res[-1])
        if nets > st["clone_networks"] and not flagged:
            rc.set(flag, 1, ex=CLONE_FLAG_S)
            flagged = True
            _stat(rc, "clone_detected" if mode == MODE_ENFORCE else "clone_would_split", now)
            _event(rc, "clone_detected" if mode == MODE_ENFORCE else "clone_would_split", mode,
                   uid=uid, dev=device[:8], networks=nets)
        if flagged and mode == MODE_ENFORCE:
            return "x" + hashlib.sha1(f"{device}:{wide}".encode()).hexdigest()[:31]
        return device
    except Exception:  # noqa: BLE001
        return device


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


LINKED_APP_MIN = (0, 19, 4)


def counts_as_device(request) -> bool:
    """Extensions before 5.21 send no id at all; likewise not linked.
    The Windows app before 0.19.4 cannot share its machine id with the
    browser (bridge ping without "device"): one PC would count as two devices
    and a Free account could be signed out unfairly. Such app calls are not
    linked until the app updates. Other clients always count."""
    try:
        v = (request.headers.get("X-VG-Client") or "").strip()
        origin = request.headers.get("Origin") or ""
        has_id = bool(request.headers.get("X-VG-Device"))
    except Exception:  # noqa: BLE001
        return True
    if not v:
        # extension before 5.21 sends no id: not linked until it updates (it
        # would otherwise count as a separate "no id" device of its network)
        return has_id or not origin.startswith(("chrome-extension://", "moz-extension://"))
    try:
        parts = tuple(int(x) for x in v.split("-", 1)[0].split(".")[:3])
    except ValueError:
        return False
    return parts >= LINKED_APP_MIN


def describe_request(request) -> Dict[str, str]:
    """{"kind": web|extension|app, "label": "Chrome · Windows"} from headers
    only (no fingerprinting beyond the User-Agent family)."""
    try:
        h = request.headers
        ua = (h.get("User-Agent") or "")
        if h.get("X-VG-Client"):
            kind = "app"
        elif h.get("X-VG-Ext-Version") or (h.get("Origin") or "").startswith(("chrome-extension://", "moz-extension://")):
            kind = "extension"
        else:
            kind = "web"
    except Exception:  # noqa: BLE001
        return {"kind": "web", "label": ""}
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("CocoaBrowser", "Cốc Cốc"),
                                           ("coc_coc", "Cốc Cốc"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in ua), "")
    os_name = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                           ("Windows", "Windows"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if key in ua), "")
    if kind == "app":
        browser = "App VidGrab"
    return {"kind": kind, "label": " · ".join(x for x in (browser, os_name) if x)}


def _merge_meta(old_raw, new: Dict[str, str]) -> Dict[str, Any]:
    """One PC = website + extension + app on one id: keep every kind seen, and
    the browser's label over the app's."""
    try:
        old = json.loads(old_raw) if old_raw else {}
    except (TypeError, ValueError):
        old = {}
    kinds = list(dict.fromkeys([*(old.get("kinds") or ([old["kind"]] if old.get("kind") else [])), new.get("kind") or "web"]))
    label = new.get("label") or ""
    if new.get("kind") == "app" and old.get("label") and not old.get("label", "").startswith("App"):
        label = old["label"]
    return {"kinds": [k for k in ("web", "extension", "app") if k in kinds], "label": label}


def _manual_refusal(rc, uid: str, device: str, token: Optional[str], tier: Optional[str], st) -> Optional[Dict[str, Any]]:
    """Signed out by the person from "Thiết bị của tôi": refused in every mode
    until a newer sign-in on that device."""
    man = rc.hget(f"vidgrab:devlink:man:{uid}", device)
    if man is None:
        return None
    at = session_auth_time(token)
    if at and at > float(man):
        rc.hdel(f"vidgrab:devlink:man:{uid}", device)
        _touched.pop(f"{uid}:{device}", None)
        return None
    # wording: BA review
    return {"error_code": ERR_SIGNED_OUT, "max_devices": max_devices(tier or _tier(uid), st),
            "user_message": "Thiết bị này đã được đăng xuất từ trang Thiết bị của tôi. Đăng nhập lại để dùng tiếp."}


def touch(user_id: str, device: Optional[str], token: Optional[str] = None,
          tier: Optional[str] = None, rc=None, now: Optional[float] = None,
          meta: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
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
        manual = _manual_refusal(rc, uid, device, token, tier, st)
        if manual:
            _stat(rc, "blocked_requests", now)
            return manual
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
        if meta:
            meta = _merge_meta(rc.hget(f"vidgrab:devlink:meta:{uid}", device), meta)
            pipe.hset(f"vidgrab:devlink:meta:{uid}", device, json.dumps(meta, ensure_ascii=False))
            pipe.expire(f"vidgrab:devlink:meta:{uid}", STALE_S * 2)
        pipe.zrange(ukey, 0, -1, withscores=True)
        rows = [((d.decode() if isinstance(d, bytes) else d), sc) for d, sc in pipe.execute()[-1]]
        stale_pseudo = [d for d, sc in rows if d.startswith("n") and sc < now - PSEUDO_STALE_S and d != device]
        if stale_pseudo:
            rc.zrem(ukey, *stale_pseudo)
        devices = [d for d, _ in rows if d not in stale_pseudo]
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


def note_shared_refusal(user_id: str, device: str, mode: str, platform: str, reason: str = "device") -> None:
    """The device bucket of Free accounts was full (quotas): logged once a day
    per (user, device)."""
    try:
        rc = _rc()
        if _once_today(rc, f"sh:{mode}:{user_id}:{device}"):
            suffix = "" if reason == "device" else f"_{reason}"
            name = ("shared_blocked" if mode == MODE_ENFORCE else "shared_would_block") + suffix
            _stat(rc, name)
            _event(rc, name, mode, uid=str(user_id), dev=device[:8], platform=platform)
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


def my_devices(user_id: str, current: Optional[str], rc=None) -> List[Dict[str, Any]]:
    """The person's own list: newest first, 8-char ids, which one is this device."""
    rc = rc or _rc()
    now = time.time()
    rows = rc.zrevrangebyscore(f"vidgrab:devlink:u:{user_id}", "+inf", now - STALE_S, withscores=True)
    meta = rc.hgetall(f"vidgrab:devlink:meta:{user_id}") or {}
    out = []
    for d, sc in rows:
        d = d.decode() if isinstance(d, bytes) else d
        raw = meta.get(d) or meta.get(d.encode()) if meta else None
        try:
            m = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            m = {}
        out.append({"id": d[:8], "kinds": m.get("kinds") or [m.get("kind") or "web"], "label": m.get("label") or "",
                    "last_seen": int(sc), "current": d == current})
    return out


def sign_out_device(user_id: str, short_id: str, rc=None, now: Optional[float] = None) -> Optional[str]:
    """The person signs one of their devices out. Returns the full device id,
    or None when `short_id` matches no device (or more than one)."""
    rc = rc or _rc()
    short_id = (short_id or "").lower()
    if len(short_id) < 6 or not all(c in "0123456789abcdef" for c in short_id):
        return None
    ukey = f"vidgrab:devlink:u:{user_id}"
    devs = [d.decode() if isinstance(d, bytes) else d for d in rc.zrange(ukey, 0, -1)]
    hits = [d for d in devs if d.startswith(short_id)]
    if len(hits) != 1:
        return None
    dev = hits[0]
    k = f"vidgrab:devlink:man:{user_id}"
    rc.hset(k, dev, now or time.time())
    rc.expire(k, STALE_S)
    rc.zrem(ukey, dev)
    _touched.pop(f"{user_id}:{dev}", None)
    _event(rc, "self_signed_out", effective_mode(rc), uid=str(user_id), dev=dev[:8])
    try:
        from app.core import extension_session  # noqa: PLC0415
        extension_session.revoke_device(str(user_id), dev)
    except Exception:  # noqa: BLE001
        pass
    return dev


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
