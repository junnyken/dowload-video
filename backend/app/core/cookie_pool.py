"""
Cookie Pool Manager
===================
Redis-backed rotating cookie pool per platform.

Rotation strategy:
  - LRU (Least Recently Used): picks the cookie unused for the longest time
  - Per-cookie cooldown: after use, cookie enters a cooldown window
    so it can't be hammered back-to-back — load distributes evenly
  - Smart block TTL: 429 soft-rate-limit = 15 min, hard block = 6 h
  - All-blocked fallback: return least-recently-blocked as last resort

Cookie health states (stored in cookie_health:{platform}:{hash}):
  "soft"     — 429 / temporary rate limit    (15 min TTL, auto-clears)
  "hard"     — account challenge / suspended  (6 h TTL, auto-clears)
  "expired"  — cookie's expires_at is past   (no TTL, manual remove required)
  "disabled" — admin-disabled manually       (no TTL, re-enable via API)
  absent     — healthy / in-cooldown check separate key

Redis keys:
  cookie_pool:{platform}              LIST of base64-encoded cookie strings
  cookie_health:{platform}:{hash}     "soft"|"hard"|"expired"|"disabled" (TTL varies)
  cookie_cooldown:{platform}:{hash}   exists while cookie is in cooldown window
  cookie_lastused:{platform}:{hash}   Unix timestamp of last use (for LRU)
  cookie_meta:{platform}:{hash}       JSON: {label, expires_at, account_hint, added_at}

Per-platform cooldown (seconds between reuses of same cookie):
  instagram     20s  — 4 accounts × 20s cooldown ≈ 12 req/min effective cap
  tiktok        10s
  facebook      15s
  youtube        5s
  twitter       30s  — X.com aggressive rate-limiter
  reddit        15s
  bilibili      10s
  xiaohongshu   25s  — RedNote strict anti-bot
  default       10s
"""

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional

from app.core.redis_client import get_redis

# Block TTLs
SOFT_BLOCK_TTL = 15 * 60   # 15 min — 429 / temporary rate limit
HARD_BLOCK_TTL =  6 * 60 * 60  # 6 h  — account suspended / challenge
_META_TTL = 400 * 24 * 3600

# Per-cookie reuse cooldown (seconds) — prevents same cookie from being
# called back-to-back when multiple workers fire simultaneously
_COOLDOWN: dict[str, int] = {
    "instagram":    20,
    "facebook":     15,
    "tiktok":       10,
    "youtube":       5,
    "twitter":      30,
    "reddit":       15,
    "bilibili":     10,
    "xiaohongshu":  25,
    "lemon8":       20,
}
_DEFAULT_COOLDOWN = 10

_AUTH_COOKIES: dict[str, set[str]] = {
    "youtube": {
        "SID", "SSID", "APISID", "SAPISID",
        "__Secure-1PAPISID", "__Secure-3PAPISID",
        "__Secure-1PSID", "__Secure-3PSID",
        "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
        "LOGIN_INFO",
    },
    "tiktok": {
        "sessionid", "sessionid_ss",
        "tt_webid", "tt_webid_v2",
        "passport_auth_status",
    },
    "instagram": {
        "sessionid", "ds_user_id", "csrftoken",
    },
    "facebook": {
        "c_user", "xs", "fr",
    },
    "twitter": {
        "auth_token", "ct0", "twid",
    },
    "reddit": {
        # token_v2 is a short-lived OAuth JWT (~1h, auto-refreshed) — exclude from expiry tracking
        "reddit_session", "loid",
    },
    "bilibili": {
        "SESSDATA", "bili_jct", "DedeUserID",
    },
    "xiaohongshu": {
        "a1", "web_session", "webId",
    },
    "lemon8": {
        "sessionid", "install_id",
    },
}


def _hash(cookie_b64: str) -> str:
    return hashlib.md5(cookie_b64.encode()).hexdigest()[:16]


def _parse_cookie_info(cookie_b64: str, platform: str) -> dict:
    try:
        decoded = base64.b64decode(cookie_b64).decode("utf-8", errors="ignore")
    except Exception:
        return {"expires_at": 0, "account_hint": ""}

    auth_names = _AUTH_COOKIES.get(platform, set())
    min_expiry = 0
    account_hint = ""
    hint_names = {"SID", "sessionid", "c_user", "ds_user_id"}
    now_ts = int(time.time())

    for line in decoded.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name = parts[5]
        value = parts[6] if len(parts) > 6 else ""
        try:
            expiry = int(parts[4])
        except (ValueError, IndexError):
            continue
        # Only consider future expiry timestamps — past ones are short-lived tokens
        # (e.g. Reddit token_v2 / OAuth JWTs) that refresh automatically and should
        # not poison the session expiry calculation.
        if expiry > 0 and expiry > now_ts and name in auth_names:
            if min_expiry == 0 or expiry < min_expiry:
                min_expiry = expiry
        if name in hint_names and not account_hint:
            account_hint = (value[:24] + "…") if len(value) > 24 else value

    return {"expires_at": min_expiry, "account_hint": account_hint}


def _get_meta(rc, platform: str, h: str) -> dict:
    try:
        raw = rc.get(f"cookie_meta:{platform}:{h}")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _set_meta(rc, platform: str, h: str, meta: dict) -> None:
    try:
        rc.setex(f"cookie_meta:{platform}:{h}", _META_TTL, json.dumps(meta))
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────────

def get_cookie_from_pool(platform: str) -> Optional[str]:
    """
    LRU selection with cooldown:
    1. Skip cookies that are hard/soft-blocked or in cooldown
    2. Among eligible cookies, pick the one unused the longest (LRU)
    3. If ALL are in cooldown (not blocked), pick the one whose cooldown
       expires soonest (rather than returning None)
    4. If ALL are blocked, return the one with least remaining block TTL
       as last resort (better than nothing)
    """
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    cookies = rc.lrange(pool_key, 0, -1)
    if not cookies:
        return None

    cooldown_s = _COOLDOWN.get(platform, _DEFAULT_COOLDOWN)
    now = time.time()

    eligible = []       # (last_used_ts, cookie_b64)
    in_cooldown = []    # (cooldown_ttl, cookie_b64)
    blocked = []        # (block_ttl, cookie_b64)

    for c in cookies:
        h = _hash(c)
        health = rc.get(f"cookie_health:{platform}:{h}")
        # expired/disabled — permanently excluded, never used as last-resort
        if health in ("expired", "disabled"):
            continue
        if health in ("soft", "hard", "blocked"):
            ttl = rc.ttl(f"cookie_health:{platform}:{h}") or 0
            blocked.append((ttl, c))
            continue

        # Auto-detect expired cookie at selection time (lazy check)
        meta = _get_meta(rc, platform, h)
        exp = meta.get("expires_at", 0)
        if exp and exp < int(time.time()):
            mark_cookie_expired(platform, c)
            continue

        in_cd = rc.exists(f"cookie_cooldown:{platform}:{h}")
        if in_cd:
            cd_ttl = rc.ttl(f"cookie_cooldown:{platform}:{h}") or 0
            in_cooldown.append((cd_ttl, c))
            continue

        last_used = float(rc.get(f"cookie_lastused:{platform}:{h}") or 0)
        eligible.append((last_used, c))

    if eligible:
        # Phase 27B — scored selection (feature-flag gated, falls back to LRU on any error)
        _scored_chosen = None
        try:
            from app.core.cookie_score import select_scored_cookie, is_scoring_enabled, is_shadow_mode
            if is_scoring_enabled(platform):
                _scored_chosen = select_scored_cookie(platform, [c for _, c in eligible], rc)
        except Exception as _sc_err:
            print(f"[CookiePool] scored selection error ({platform}), using LRU: {_sc_err}")

        if _scored_chosen is not None and not is_shadow_mode():
            return _scored_chosen

        # Legacy LRU (default path AND shadow-mode fallback)
        eligible.sort(key=lambda x: x[0])
        chosen = eligible[0][1]
        h = _hash(chosen)
        rc.setex(f"cookie_cooldown:{platform}:{h}", cooldown_s, "1")
        rc.set(f"cookie_lastused:{platform}:{h}", now)
        return chosen

    if in_cooldown:
        # All healthy cookies are cooling down — pick the one that expires soonest
        in_cooldown.sort(key=lambda x: x[0])
        chosen = in_cooldown[0][1]
        h = _hash(chosen)
        rc.set(f"cookie_lastused:{platform}:{h}", now)
        print(f"[CookiePool] {platform}: all cookies in cooldown, using soonest ({in_cooldown[0][0]:.0f}s remaining)")
        return chosen

    if blocked:
        # All blocked — return least-blocked as last resort
        blocked.sort(key=lambda x: x[0])
        chosen = blocked[0][1]
        print(f"[CookiePool] {platform}: all cookies blocked, using least-blocked (TTL {blocked[0][0]}s)")
        return chosen

    return None


def mark_cookie_blocked(platform: str, cookie_b64: str, hard: bool = False) -> None:
    """
    Mark cookie as blocked.
    hard=False → soft block (15 min) — for 429 / temporary rate limit
    hard=True  → hard block (6 h)   — for account challenge / suspended
    """
    h = _hash(cookie_b64)
    rc = get_redis()
    ttl = HARD_BLOCK_TTL if hard else SOFT_BLOCK_TTL
    label = "hard" if hard else "soft"
    rc.setex(f"cookie_health:{platform}:{h}", ttl, label)
    rc.delete(f"cookie_cooldown:{platform}:{h}")
    print(f"[CookiePool] {platform} cookie {h} {label}-blocked (retry in {ttl//60}min)")


def mark_cookie_soft_blocked(platform: str, cookie_b64: str) -> None:
    """Convenience: soft block (429 / rate limit)."""
    mark_cookie_blocked(platform, cookie_b64, hard=False)


def mark_cookie_hard_blocked(platform: str, cookie_b64: str) -> None:
    """Convenience: hard block (challenge / suspended)."""
    mark_cookie_blocked(platform, cookie_b64, hard=True)


def mark_cookie_expired(platform: str, cookie_b64: str) -> None:
    """
    Mark cookie as expired (expires_at is in the past).
    No TTL — stays excluded until cookie is removed or re-added.
    Admin sees reason = 'expired' vs 'hard' block.
    """
    h = _hash(cookie_b64)
    rc = get_redis()
    rc.set(f"cookie_health:{platform}:{h}", "expired")  # no TTL — permanent
    rc.delete(f"cookie_cooldown:{platform}:{h}")
    print(f"[CookiePool] {platform} cookie {h} marked EXPIRED (remove and re-add with fresh session)")


def mark_cookie_disabled(platform: str, cookie_b64: str, reason: str = "") -> None:
    """
    Admin-disable a cookie without removing it from the pool.
    No TTL — stays disabled until unmark_cookie_disabled() is called.
    Distinct from hard_block (temporary) and expired (stale session).
    """
    h = _hash(cookie_b64)
    rc = get_redis()
    rc.set(f"cookie_health:{platform}:{h}", "disabled")  # no TTL — admin must re-enable
    rc.delete(f"cookie_cooldown:{platform}:{h}")
    if reason:
        meta = _get_meta(rc, platform, h)
        meta["disabled_reason"] = reason
        meta["disabled_at"] = int(time.time())
        _set_meta(rc, platform, h, meta)
    print(f"[CookiePool] {platform} cookie {h} manually DISABLED" + (f": {reason}" if reason else ""))


def unmark_cookie_disabled(platform: str, cookie_b64: str) -> None:
    """Re-enable a manually-disabled cookie."""
    h = _hash(cookie_b64)
    rc = get_redis()
    health = rc.get(f"cookie_health:{platform}:{h}")
    if health == "disabled":
        rc.delete(f"cookie_health:{platform}:{h}")
        meta = _get_meta(rc, platform, h)
        meta.pop("disabled_reason", None)
        meta.pop("disabled_at", None)
        _set_meta(rc, platform, h, meta)
        print(f"[CookiePool] {platform} cookie {h} RE-ENABLED by admin")


def add_cookie(platform: str, cookie_b64: str, label: str = "") -> int:
    """
    Add cookie to pool. Skips duplicates. Parses and stores expiry metadata.
    Returns new pool size.
    """
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    h = _hash(cookie_b64)

    existing = rc.lrange(pool_key, 0, -1)
    if cookie_b64 not in existing:
        rc.rpush(pool_key, cookie_b64)
        rc.delete(f"cookie_health:{platform}:{h}")
        rc.delete(f"cookie_cooldown:{platform}:{h}")

    info = _parse_cookie_info(cookie_b64, platform)
    meta = _get_meta(rc, platform, h)
    meta.update({
        "label": label or meta.get("label", ""),
        "expires_at": info["expires_at"],
        "account_hint": info["account_hint"],
        "added_at": meta.get("added_at") or int(time.time()),
    })
    _set_meta(rc, platform, h, meta)
    return rc.llen(pool_key)


def remove_cookie(platform: str, index: int) -> int:
    """Remove cookie at index from pool. Returns remaining pool size."""
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    cookies = rc.lrange(pool_key, 0, -1)
    if 0 <= index < len(cookies):
        h = _hash(cookies[index])
        rc.delete(f"cookie_meta:{platform}:{h}")
        rc.delete(f"cookie_health:{platform}:{h}")
        rc.delete(f"cookie_cooldown:{platform}:{h}")
        rc.delete(f"cookie_lastused:{platform}:{h}")

    sentinel = f"__del_{index}__"
    rc.lset(pool_key, index, sentinel)
    rc.lrem(pool_key, 0, sentinel)
    return rc.llen(pool_key)


def get_expiry_report(platform: str) -> list[dict]:
    rc = get_redis()
    cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
    now = int(time.time())
    result = []

    for i, c in enumerate(cookies):
        h = _hash(c)
        meta = _get_meta(rc, platform, h)
        expires_at = meta.get("expires_at", 0)

        if expires_at == 0 and meta.get("added_at") is None:
            info = _parse_cookie_info(c, platform)
            expires_at = info["expires_at"]
            meta.update(info)
            meta.setdefault("added_at", 0)
            _set_meta(rc, platform, h, meta)

        # Self-heal: if stored expires_at is already past AND it was a short-lived token
        # (expired within 24h of being added, e.g. Reddit token_v2 JWT), re-parse now
        # that token_v2 is excluded from auth_names, so we get a better expiry estimate.
        added_at = meta.get("added_at", 0) or 0
        if expires_at > 0 and expires_at < now and added_at and (expires_at - added_at) < 86400:
            info = _parse_cookie_info(c, platform)
            expires_at = info["expires_at"]
            meta["expires_at"] = expires_at
            _set_meta(rc, platform, h, meta)

        days_left: Optional[int] = None
        expiry_status = "unknown"
        if expires_at > 0:
            days_left = max(0, (expires_at - now) // 86400)
            if days_left == 0:
                expiry_status = "expired"
            elif days_left <= 7:
                expiry_status = "critical"
            elif days_left <= 30:
                expiry_status = "expiring_soon"
            else:
                expiry_status = "healthy"
        else:
            expiry_status = "session_only"

        health_val = rc.get(f"cookie_health:{platform}:{h}")
        blocked = health_val in ("soft", "hard", "blocked")
        blocked_ttl = rc.ttl(f"cookie_health:{platform}:{h}") if blocked else None
        cooldown_ttl = rc.ttl(f"cookie_cooldown:{platform}:{h}") if rc.exists(f"cookie_cooldown:{platform}:{h}") else None
        last_used = float(rc.get(f"cookie_lastused:{platform}:{h}") or 0)

        # Resolve effective health label
        effective_health = health_val or "healthy"
        if health_val == "disabled":
            effective_health = "disabled"
        elif health_val == "expired":
            effective_health = "expired"
        elif expiry_status == "expired" and not health_val:
            # cookie not yet auto-marked but visually expired
            effective_health = "expired"

        disabled_reason = meta.get("disabled_reason", "") if health_val == "disabled" else ""

        result.append({
            "index":           i,
            "hash":            h,
            "label":           meta.get("label", ""),
            "account_hint":    meta.get("account_hint", ""),
            "expires_at":      expires_at,
            "expires_at_str":  (
                datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime("%Y-%m-%d")
                if expires_at else None
            ),
            "days_left":       days_left,
            "expiry_status":   expiry_status,
            "health_status":   effective_health,
            "blocked":         blocked,
            "blocked_ttl_s":   blocked_ttl,
            "cooldown_ttl_s":  cooldown_ttl,
            "last_used":       last_used,
            "disabled_reason": disabled_reason,
            "usable":          effective_health not in ("expired", "disabled", "hard", "soft", "blocked"),
        })

    return result


def get_pool_status() -> dict:
    """Return health + expiry summary for all platforms."""
    rc = get_redis()
    result = {}
    now = int(time.time())

    # Discover all platforms dynamically from Redis keys
    known_keys = rc.keys("cookie_pool:*")
    all_platforms = sorted({k.split(":", 1)[1] for k in (known_keys or [])})
    if not all_platforms:
        all_platforms = ["youtube", "tiktok", "facebook", "instagram"]

    for platform in all_platforms:
        pool_key = f"cookie_pool:{platform}"
        cookies = rc.lrange(pool_key, 0, -1)
        hard_blocked = soft_blocked = in_cooldown = expired_count = disabled_count = 0
        expiry_warnings = 0
        min_days_left: Optional[int] = None

        for c in cookies:
            h = _hash(c)
            health = rc.get(f"cookie_health:{platform}:{h}")
            if health == "hard":
                hard_blocked += 1
            elif health in ("soft", "blocked"):
                soft_blocked += 1
            elif health == "expired":
                expired_count += 1
            elif health == "disabled":
                disabled_count += 1
            elif rc.exists(f"cookie_cooldown:{platform}:{h}"):
                in_cooldown += 1

            meta = _get_meta(rc, platform, h)
            expires_at = meta.get("expires_at", 0)
            if expires_at > 0:
                days = max(0, (expires_at - now) // 86400)
                if min_days_left is None or days < min_days_left:
                    min_days_left = days
                if days <= 30:
                    expiry_warnings += 1

        total = len(cookies)
        unusable = hard_blocked + soft_blocked + expired_count + disabled_count
        result[platform] = {
            "total":           total,
            "healthy":         max(0, total - unusable),
            "in_cooldown":     in_cooldown,
            "soft_blocked":    soft_blocked,
            "hard_blocked":    hard_blocked,
            "expired":         expired_count,
            "disabled":        disabled_count,
            "expiry_warnings": expiry_warnings,
            "min_days_left":   min_days_left,
            "cooldown_s":      _COOLDOWN.get(platform, _DEFAULT_COOLDOWN),
        }

    return result
