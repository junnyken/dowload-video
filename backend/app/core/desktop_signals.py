"""
Windows app — raw facts for the admin "Dấu hiệu bất thường" list
(task #6126, PLAN-32E §5.3, P2). Write side only; admin_desktop.py reads.
=====================================================================
Display only: nothing here refuses a download or locks anyone out — the
owner / an admin decides by hand (Cộng lượt / Đặt lại / Đặt lại IP).

Keys (one set per UTC day, kept SIGNAL_TTL_SEC = 40 days like the route
stats). <req> is the quota requester key (dev:<32 hex> / user:<id> / ip:…),
<dev> the first 32 hex of the machine hash (the same id as a dev: key):

  vidgrab:sig:app_dl:<day>           HASH <req> → downloads the app was let
                                     through (claims incl. offline reports,
                                     server route, Douyin); denominator for
                                     refunds and "downloads without a claim"
  vidgrab:sig:ips:<day>              SET  IPs seen (index, avoids SCAN)
  vidgrab:sig:ipdev:<ip>:<day>       SET  machines (<dev>) behind that IP
  vidgrab:sig:iplimit:<day>          HASH <ip> → times the IP cap was hit
                                     (shadow mode too)
  vidgrab:sig:devs:<day>             SET  machines used signed in (index)
  vidgrab:sig:devusers:<dev>:<day>   SET  user ids signed in on that machine
  vidgrab:sig:retro:<day>            HASH <req> → offline downloads reported
  vidgrab:sig:refund:<day>           HASH <req> → refunds granted
  vidgrab:sig:ver:<day>              HASH <version|none> → app calls

Every function swallows Redis errors: a statistic never blocks a download.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

SIGNAL_TTL_SEC = 40 * 86400
_DEV = re.compile(r"^[0-9a-f]{32,64}$")
_VER = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,6}$")


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _day() -> str:
    from app.core.quotas import _utc_day  # noqa: PLC0415
    return _utc_day()


def _dev_id(req, device_header: Optional[str]) -> Optional[str]:
    """The machine behind this request: a guest machine's own requester id,
    else the X-VG-Device header (signed-in machines)."""
    from app.core import quotas  # noqa: PLC0415
    if req is not None and req.kind == quotas.REQ_DEVICE:
        return str(req.ident)[:32]
    d = (device_header or "").strip().lower()
    return d[:32] if _DEV.match(d) else None


def note_app_download(req, ip: str, device_header: Optional[str] = None) -> None:
    """The server let one app download through (any route)."""
    from app.core import quotas  # noqa: PLC0415
    if req is None or req.kind == quotas.REQ_ADMIN:
        return
    try:
        r, day = _r(), _day()
        dev = _dev_id(req, device_header)
        keys = [f"vidgrab:sig:app_dl:{day}"]
        r.hincrby(keys[0], req.key, 1)
        if dev and ip:
            r.sadd(f"vidgrab:sig:ips:{day}", ip)
            r.sadd(f"vidgrab:sig:ipdev:{ip}:{day}", dev)
            keys += [f"vidgrab:sig:ips:{day}", f"vidgrab:sig:ipdev:{ip}:{day}"]
        if dev and req.kind == quotas.REQ_USER:
            r.sadd(f"vidgrab:sig:devs:{day}", dev)
            r.sadd(f"vidgrab:sig:devusers:{dev}:{day}", str(req.ident))
            keys += [f"vidgrab:sig:devs:{day}", f"vidgrab:sig:devusers:{dev}:{day}"]
        for k in keys:
            r.expire(k, SIGNAL_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.debug("desktop_signals app_dl skipped: %s", type(exc).__name__)


def _hincr(name: str, field: str) -> None:
    try:
        r = _r()
        k = f"vidgrab:sig:{name}:{_day()}"
        r.hincrby(k, field, 1)
        r.expire(k, SIGNAL_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.debug("desktop_signals %s skipped: %s", name, type(exc).__name__)


def note_ip_limit(ip: str) -> None:
    """A guest machine went over its IP's shared cap (refused or, in shadow
    mode, only reported)."""
    _hincr("iplimit", ip or "unknown")


def note_retro(req) -> None:
    _hincr("retro", req.key)


def note_refund(req) -> None:
    _hincr("refund", req.key)


def note_version(raw: Optional[str]) -> None:
    """One app call to /client/* or /fetch-link, by the app's own version."""
    v = (raw or "").strip()
    _hincr("ver", v if _VER.match(v) else "none")
