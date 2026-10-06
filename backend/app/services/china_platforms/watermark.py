"""
Watermark validation record (Phase 32B-2 §6, Stage A — Douyin).

No provider gives a trustworthy watermark flag, so a person reviews benchmark
items by hand. Reviews are keyed by (platform, provider route, canonical URL
hash) — never the URL itself:

  china:watermark:{platform}:{provider}          hash  url_hash → {"state","note","ts"}
  china:benchmark:items:{platform}               set   "{provider}|{url_hash}" of successful
                                                       benchmark items (reviewable)

Gate: a route's results may carry watermark_state="watermark_free" only when
it has ≥ CHINA_ACCESS_WATERMARK_MIN_REVIEWED (10) reviewed items and
≥ CHINA_ACCESS_WATERMARK_MIN_FREE_RATE (0.9) of them are watermark_free.
Otherwise "unknown". This only changes the internal state field in attempts
and reports — the user-facing wording is NOT changed in this step.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.services.china_platforms import settings
from app.services.china_platforms.errors import redact

logger = logging.getLogger("app.china_access")

REVIEW_STATES = ("watermark_free", "watermarked", "unknown")
REVIEWS_KEY = "china:watermark:{platform}:{provider}"
ITEMS_KEY = "china:benchmark:items:{platform}"
_TTL = 180 * 86400


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def register_items(platform: str, items: list[tuple[str, str]]) -> None:
    """Mark (provider, url_hash) pairs as reviewable benchmark items."""
    if not items:
        return
    try:
        r = _r()
        r.sadd(ITEMS_KEY.format(platform=platform), *[f"{p}|{h}" for p, h in items])
        r.expire(ITEMS_KEY.format(platform=platform), _TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access watermark item register failed: %s", type(exc).__name__)


def is_benchmark_item(platform: str, provider: str, url_hash: str) -> bool:
    return bool(_r().sismember(ITEMS_KEY.format(platform=platform), f"{provider}|{url_hash}"))


def record_review(platform: str, provider: str, url_hash: str, state: str, note: str,
                  ts: str) -> Optional[dict]:
    """Store (overwrite) one review. Returns the prior review, if any."""
    if state not in REVIEW_STATES:
        raise ValueError("bad state")
    r = _r()
    k = REVIEWS_KEY.format(platform=platform, provider=provider)
    prior = r.hget(k, url_hash)
    r.hset(k, url_hash, json.dumps({"state": state, "note": redact(note)[:500], "ts": ts}, ensure_ascii=False))
    r.expire(k, _TTL)
    return json.loads(prior) if prior else None


def reviews(platform: str, provider: str) -> dict[str, dict]:
    try:
        raw = _r().hgetall(REVIEWS_KEY.format(platform=platform, provider=provider)) or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in raw.items():
        k = k.decode() if isinstance(k, bytes) else k
        try:
            out[k] = json.loads(v)
        except Exception:  # noqa: BLE001
            continue
    return out


def aggregate(platform: str, provider: str, revs: Optional[dict] = None) -> dict:
    revs = reviews(platform, provider) if revs is None else revs
    dist = {s: 0 for s in REVIEW_STATES}
    for v in revs.values():
        if v.get("state") in dist:
            dist[v["state"]] += 1
    n = sum(dist.values())
    free_rate = round(dist["watermark_free"] / n, 4) if n else None
    validated = (n >= settings.watermark_min_reviewed()
                 and free_rate is not None and free_rate >= settings.watermark_min_free_rate())
    return {"provider": provider, "reviewed": n, "distribution": dist, "watermark_free_rate": free_rate,
            "min_reviewed": settings.watermark_min_reviewed(), "min_free_rate": settings.watermark_min_free_rate(),
            "gate_passed": validated, "watermark_state": "watermark_free" if validated else "unknown"}


def gated_state(platform: str, provider: str) -> str:
    """The watermark_state a result from this route may carry."""
    try:
        return aggregate(platform, provider)["watermark_state"]
    except Exception:  # noqa: BLE001
        return "unknown"
