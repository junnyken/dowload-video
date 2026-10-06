"""
Admin-managed Apify token for the China access layer.

The owner can set or replace the token from the admin panel without editing
Vibe Host env or redeploying. Same idea as the ScraperAPI key pool
(app/core/scraperapi_pool.py: Redis-stored, env as the fallback).

  china:secret:apify_token        the token (string, no TTL; Redis runs with AOF)
  china:secret:apify_token:meta   JSON: last4, set_at, set_by_ip, validated_at,
                                  account {username, plan, is_paying},
                                  usage {monthly_usage_usd, max_monthly_usage_usd,
                                  cycle_start, cycle_end}. Never the token.

Precedence (resolve_apify_token): admin-stored > env CHINA_ACCESS_APIFY_TOKEN
> none. The legacy APIFY_TOKEN is never read or written here, so the legacy
apify_service path is unaffected.

Validation uses free Apify API calls only (no actor run, no cost):
  GET https://api.apify.com/v2/users/me          401 "invalid-token" = bad token
      (docs.apify.com/api/v2/users-me-get; we keep only username, plan.id,
      isPaying. The response also carries proxy.password, which is dropped.)
  GET https://api.apify.com/v2/users/me/limits   data.current.monthlyUsageUsd,
      data.limits.maxMonthlyUsageUsd, data.monthlyUsageCycle.{startAt,endAt}
      (docs.apify.com/api/v2/users-me-limits-get). Best effort.

The token never appears in logs, exceptions, audit metadata, responses or
alerts. Callers get `last4` at most.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger("app.china_access")

TOKEN_KEY = "china:secret:apify_token"
META_KEY = "china:secret:apify_token:meta"
ENV_NAME = "CHINA_ACCESS_APIFY_TOKEN"
APIFY_API = "https://api.apify.com/v2"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")


class TokenRejected(Exception):
    """Validation failed. `code` is safe to show; the token is never in it."""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(code)


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _s(v) -> str:
    if v is None:
        return ""
    return (v.decode() if isinstance(v, bytes) else str(v)).strip()


def admin_token() -> str:
    """The admin-stored token, or "" (also when Redis is unreachable)."""
    try:
        return _s(_r().get(TOKEN_KEY))
    except Exception:  # noqa: BLE001
        return ""


def env_token() -> str:
    return (os.getenv(ENV_NAME) or "").strip()


def resolve_apify_token() -> tuple[str, str]:
    """(token, source): source is "admin" | "env" | "none"."""
    t = admin_token()
    if t:
        return t, "admin"
    t = env_token()
    if t:
        return t, "env"
    return "", "none"


def last4(token: str) -> Optional[str]:
    return token[-4:] if token and len(token) >= 8 else None


def sanitize(raw) -> str:
    """Strip whitespace and check length/charset. Raises TokenRejected."""
    tok = "".join(str(raw or "").split())
    if not _TOKEN_RE.match(tok):
        raise TokenRejected("invalid_format", "Token không đúng định dạng (20–200 ký tự chữ, số, _ hoặc -).")
    return tok


async def validate(token: str, *, transport: Optional[httpx.AsyncBaseTransport] = None,
                   base_url: str = APIFY_API) -> dict:
    """Free calls only. Returns {"account": {...}, "usage": {...}|None}.
    Raises TokenRejected on an invalid token or when Apify cannot be reached."""
    kw: dict = {"timeout": 15.0, "headers": {"Authorization": f"Bearer {token}"}, "trust_env": False}
    if transport is not None:
        kw["transport"] = transport
    try:
        async with httpx.AsyncClient(**kw) as client:
            me = await client.get(f"{base_url}/users/me")
            if me.status_code in (401, 403):
                raise TokenRejected("invalid_token", "Apify từ chối token này (token sai hoặc đã bị thu hồi).")
            if me.status_code != 200:
                raise TokenRejected("apify_unreachable",
                                    f"Không kiểm tra được token: Apify trả HTTP {me.status_code}. Thử lại sau.", 502)
            d = (me.json() or {}).get("data") or {}
            plan = d.get("plan") or {}
            account = {"username": d.get("username"),
                       "plan": plan.get("id") if isinstance(plan, dict) else None,
                       "is_paying": d.get("isPaying")}
            usage = None
            try:
                lim = await client.get(f"{base_url}/users/me/limits")
                if lim.status_code == 200:
                    ld = (lim.json() or {}).get("data") or {}
                    cyc = ld.get("monthlyUsageCycle") or {}
                    usage = {"monthly_usage_usd": (ld.get("current") or {}).get("monthlyUsageUsd"),
                             "max_monthly_usage_usd": (ld.get("limits") or {}).get("maxMonthlyUsageUsd"),
                             "cycle_start": cyc.get("startAt"), "cycle_end": cyc.get("endAt")}
            except (httpx.HTTPError, ValueError):
                usage = None
    except TokenRejected:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        # The exception text could include the request; report the type only.
        raise TokenRejected("apify_unreachable",
                            f"Không kết nối được Apify ({type(exc).__name__}). Thử lại sau.", 502) from None
    return {"account": account, "usage": usage}


def _meta() -> dict:
    try:
        raw = _r().get(META_KEY)
        return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return {}


def store(token: str, info: dict, *, now_iso: str, ip: Optional[str]) -> None:
    meta = {"last4": last4(token), "set_at": now_iso, "set_by_ip": ip, "validated_at": now_iso,
            "account": info.get("account"), "usage": info.get("usage")}
    r = _r()
    r.set(TOKEN_KEY, token)
    r.set(META_KEY, json.dumps(meta, ensure_ascii=False))


def record_validation(info: dict, *, now_iso: str, source: str) -> None:
    """After a re-test, refresh validated_at/account/usage. The admin meta is
    used only for an admin-stored token; an env token keeps its own record."""
    key = META_KEY if source == "admin" else META_KEY + ":env"
    try:
        r = _r()
        raw = r.get(key)
        meta = json.loads(raw) if raw else {}
        meta.update({"validated_at": now_iso, "account": info.get("account"), "usage": info.get("usage"),
                     "last4": info.get("last4", meta.get("last4"))})
        r.set(key, json.dumps(meta, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def delete() -> bool:
    r = _r()
    existed = bool(r.delete(TOKEN_KEY))
    r.delete(META_KEY)
    return existed


def status() -> dict:
    """Public view. NEVER contains the token."""
    token, source = resolve_apify_token()
    out = {"configured": bool(token), "source": source, "last4": last4(token),
           "set_at": None, "set_by_ip": None, "validated_at": None, "account": None, "usage": None,
           "env_fallback_configured": bool(env_token())}
    if source == "admin":
        m = _meta()
    elif source == "env":
        try:
            raw = _r().get(META_KEY + ":env")
            m = json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            m = {}
        if m.get("last4") != last4(token):
            m = {}     # record belongs to an earlier env token
    else:
        m = {}
    for k in ("set_at", "set_by_ip", "validated_at", "account", "usage"):
        if k in m:
            out[k] = m[k]
    return out
