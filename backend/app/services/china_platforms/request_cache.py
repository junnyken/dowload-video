"""
Result cache, in-flight dedupe and the once-per-paid-provider-per-URL marker
(plan §9.3).

  china:cache:{platform}:{operation}:{hash}    normalized result JSON (public context only)
  china:dedupe:{platform}:{operation}:{hash}   owner token, SET NX EX, compare-and-delete
  china:paid:{provider}:{hash}                 SET NX EX — never released on failure, so a
                                               retry storm cannot bill the same URL again
                                               (2026-10-05: one bulk run = ~175 jobs × 4 tries)
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Optional

from app.services.china_platforms import settings
from app.services.china_platforms.normalized_models import NormalizedMediaResult

logger = logging.getLogger("app.china_access")

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
)


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256((canonical_url or "").encode("utf-8")).hexdigest()[:32]


def cache_key(platform: str, operation: str, h: str) -> str:
    return f"china:cache:{platform}:{operation}:{h}"


def dedupe_key(platform: str, operation: str, h: str) -> str:
    return f"china:dedupe:{platform}:{operation}:{h}"


def paid_key(provider: str, h: str) -> str:
    return f"china:paid:{provider}:{h}"


def get_cached(platform: str, operation: str, h: str) -> Optional[NormalizedMediaResult]:
    try:
        raw = _r().get(cache_key(platform, operation, h))
        if not raw:
            return None
        res = NormalizedMediaResult.model_validate_json(raw)
        return res.model_copy(update={"cache_hit": True})
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access cache read failed: %s", type(exc).__name__)
        return None


def set_cached(result: NormalizedMediaResult, operation: str, h: str, policy_ttl: int) -> None:
    ttl = min(int(policy_ttl or 0), settings.cache_ttl_sec())
    if ttl <= 0:
        return
    try:
        payload = result.model_copy(update={"cache_hit": False}).model_dump_json()
        _r().set(cache_key(result.platform, operation, h), payload, ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access cache write failed: %s", type(exc).__name__)


def delete_cached(platform: str, operation: str, h: str) -> bool:
    return bool(_r().delete(cache_key(platform, operation, h)))


def acquire_dedupe(platform: str, operation: str, h: str) -> Optional[str]:
    """Owner token when acquired, None when another request holds it.
    Redis down → a fresh token (proceed): the paid path is separately fail-
    closed by the budget guard, so no unmetered spend can follow."""
    token = secrets.token_hex(8)
    try:
        ok = _r().set(dedupe_key(platform, operation, h), token, nx=True, ex=settings.dedupe_ttl_sec())
        return token if ok else None
    except Exception:  # noqa: BLE001
        return token


def release_dedupe(platform: str, operation: str, h: str, token: str) -> None:
    try:
        _r().eval(_RELEASE_LUA, 1, dedupe_key(platform, operation, h), token)
    except Exception:  # noqa: BLE001
        try:
            r = _r()
            key = dedupe_key(platform, operation, h)
            if r.get(key) == token:
                r.delete(key)
        except Exception:  # noqa: BLE001
            pass


def claim_paid_attempt(provider: str, h: str) -> bool:
    """True if this is the first paid attempt for (provider, URL) inside the
    dedupe TTL. Redis down → False (fail closed: no paid call)."""
    try:
        return bool(_r().set(paid_key(provider, h), "1", nx=True, ex=settings.dedupe_ttl_sec()))
    except Exception:  # noqa: BLE001
        return False


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
