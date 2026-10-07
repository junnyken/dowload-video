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


def media_expiry_ts(result: NormalizedMediaResult) -> Optional[int]:
    """Earliest expiry (unix) among the result's signed media URLs, if any
    URL says (x-expires / expires / TikTok-style hex path)."""
    from app.services.format_probe import url_expiry  # noqa: PLC0415
    ts = [url_expiry(f.source_url) for f in result.formats if f.source_url]
    ts = [t for t in ts if t]
    return min(ts) if ts else None


def _now_ts() -> float:
    from app.services.china_platforms.budget_guard import utcnow  # noqa: PLC0415
    return utcnow().timestamp()


def get_cached(platform: str, operation: str, h: str) -> Optional[NormalizedMediaResult]:
    """A cached result whose media URL expires within the margin is dropped
    (task #6036): a cache hit must never hand out an expired signed URL."""
    try:
        r = _r()
        raw = r.get(cache_key(platform, operation, h))
        if not raw:
            return None
        res = NormalizedMediaResult.model_validate_json(raw)
        exp = media_expiry_ts(res)
        if exp is not None and exp - settings.cache_expiry_margin_sec() <= _now_ts():
            r.delete(cache_key(platform, operation, h))
            return None
        return res.model_copy(update={"cache_hit": True})
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access cache read failed: %s", type(exc).__name__)
        return None


def cache_ttl_for(result: NormalizedMediaResult, policy_ttl: int) -> int:
    """Normal TTL = min(policy, CHINA_ACCESS_CACHE_TTL_SEC). With
    CHINA_ACCESS_CACHE_EXTENDED_TTL_SEC > 0 and a known media expiry the
    result may stay longer, up to that value. Either way never beyond the
    media URL's expiry minus CHINA_ACCESS_CACHE_EXPIRY_MARGIN_SEC."""
    ttl = min(int(policy_ttl or 0), settings.cache_ttl_sec())
    if ttl <= 0:
        return 0
    exp = media_expiry_ts(result)
    if exp is None:
        return ttl
    extended = settings.cache_extended_ttl_sec()
    if extended > ttl:
        ttl = extended
    left = int(exp - settings.cache_expiry_margin_sec() - _now_ts())
    return max(0, min(ttl, left))


def set_cached(result: NormalizedMediaResult, operation: str, h: str, policy_ttl: int) -> None:
    ttl = cache_ttl_for(result, policy_ttl)
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


def release_paid_attempt(provider: str, h: str) -> None:
    """Undo claim_paid_attempt when NO run was started (the vendor refused
    before billing: 401/402/403 on every token, no eligible token, start
    request failed). Owner incident 2026-10-07: while the Apify tokens were
    broken, every Douyin video tried got the marker and stayed blocked for the
    whole dedupe TTL after the tokens were fixed. Never raises."""
    try:
        _r().delete(paid_key(provider, h))
    except Exception:  # noqa: BLE001
        pass


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
