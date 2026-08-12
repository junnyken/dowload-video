"""
Phase 27B — Scored Cookie Selection
=====================================
Replaces the Phase 27 basic scaffold with a production score model.

Score formula (0.0–1.0):
    base    = 0.35×success_rate + 0.25×freshness + 0.15×expiry
            + 0.15×stability   + 0.10×priority
    penalty = min(0.80, count_429_1h×0.25 + count_fail_1h×0.10)
    wm      = WARMUP_MULT if warmup_key exists else 1.0
    score   = clamp(base×wm − penalty, 0.0, 1.0)

Feature flags (all env vars, checked at import time):
    COOKIE_SCORING_ENABLED=false        master switch (default OFF)
    COOKIE_SCORING_PLATFORMS=           comma list; empty = all (when master ON)
    COOKIE_SCORING_SHADOW_LOG=false     log scores, keep LRU behavior
    COOKIE_SCORING_TOP_K=3             candidate pool for randomized pick
    COOKIE_SCORING_WARMUP_ENABLED=true
    COOKIE_SCORING_WARMUP_MULT=0.50
    COOKIE_SCORING_WARMUP_S=7200       seconds warmup lasts after hard block expires

Redis keys (p27b: namespace — never collides with existing cookie_pool: keys):
    p27b:out:{platform}:{hash}     HASH  win_1h/fail_1h/last_outcome/last_ts  TTL=7200
    p27b:pen:{platform}:{hash}     ZSET  score=ts member="429:{uid}"|"fail:{uid}"  TTL=3660
    p27b:warmup:{platform}:{hash}  STRING "1"  TTL=HARD_BLOCK_TTL+WARMUP_S
    p27b:sel:{platform}            ZSET  score=ts member={hash}  TTL=3660
    p27b:priority:{platform}:{hash} HASH field: weight (0.0–1.0)
    p27b:disabled:{platform}:{hash} STRING "1"  (no TTL — permanent until admin removes)
"""

from __future__ import annotations

import math
import os
import random
import time
import uuid
import logging
from typing import Optional, NamedTuple

from app.core.redis_client import get_redis
from app.core.cookie_pool import _hash, _COOLDOWN, _DEFAULT_COOLDOWN, HARD_BLOCK_TTL

log = logging.getLogger(__name__)

# ── Feature flags (read once at import) ──────────────────────────────────────
_ENABLED       = os.getenv("COOKIE_SCORING_ENABLED", "false").lower() in ("1", "true", "yes")
_SHADOW_LOG    = os.getenv("COOKIE_SCORING_SHADOW_LOG", "false").lower() in ("1", "true", "yes")
_WARMUP_ON     = os.getenv("COOKIE_SCORING_WARMUP_ENABLED", "true").lower() in ("1", "true", "yes")
_DEFAULT_TOP_K = int(os.getenv("COOKIE_SCORING_TOP_K", "3"))
WARMUP_MULT    = float(os.getenv("COOKIE_SCORING_WARMUP_MULT", "0.50"))
WARMUP_S       = int(os.getenv("COOKIE_SCORING_WARMUP_S", str(2 * 3600)))
SCORE_FLOOR    = float(os.getenv("COOKIE_SCORING_SCORE_FLOOR", "0.15"))

_raw_plat      = os.getenv("COOKIE_SCORING_PLATFORMS", "")
_PLATFORMS: set[str] = {p.strip() for p in _raw_plat.split(",") if p.strip()}


def is_scoring_enabled(platform: str) -> bool:
    """Return True if scored selection is active for this platform right now."""
    if not _ENABLED:
        return False
    return not _PLATFORMS or platform in _PLATFORMS


def is_shadow_mode() -> bool:
    """Shadow mode: compute + log scores but don't change cookie returned."""
    return _SHADOW_LOG


# ── Score weights ─────────────────────────────────────────────────────────────
_W_SUCCESS  = 0.35
_W_FRESH    = 0.25
_W_EXPIRY   = 0.15
_W_STABLE   = 0.15
_W_PRIO     = 0.10
_W_PEN_429  = 0.25  # penalty per 429 event in 1h
_W_PEN_FAIL = 0.10  # penalty per generic failure in 1h
_FRESH_HL   = 3 * 3600   # freshness half-life 3h
_OUTCOME_TTL = 7200      # 2h outcome hash TTL
_PEN_TTL    = 3600       # 1h penalty event window
_SEL_TTL    = 3600       # 1h selection log retention


# ── Redis key helpers ─────────────────────────────────────────────────────────
def _ok(p: str, h: str) -> str: return f"p27b:out:{p}:{h}"
def _pk(p: str, h: str) -> str: return f"p27b:pen:{p}:{h}"
def _wk(p: str, h: str) -> str: return f"p27b:warmup:{p}:{h}"
def _sk(p: str) -> str:          return f"p27b:sel:{p}"
def _dk(p: str, h: str) -> str: return f"p27b:disabled:{p}:{h}"


# ── Candidate data ────────────────────────────────────────────────────────────
class _Candidate(NamedTuple):
    cookie_b64: bytes
    h:          str
    score:      float
    debug:      dict   # for logging; never contains raw cookie bytes


# ── Score component helpers ───────────────────────────────────────────────────
def _success_rate(rc, platform: str, h: str) -> tuple[float, int]:
    raw   = rc.hgetall(_ok(platform, h))
    win   = float((raw.get(b"win_1h")  or raw.get("win_1h",  0)) or 0)
    fail  = float((raw.get(b"fail_1h") or raw.get("fail_1h", 0)) or 0)
    total = win + fail
    return (round(win / total, 4), int(total)) if total >= 5 else (0.65, int(total))


def _freshness(rc, platform: str, h: str) -> float:
    ts = float(rc.get(f"cookie_lastused:{platform}:{h}") or 0)
    if not ts:
        return 0.70  # never used — no proof yet, slightly below perfect
    return round(math.exp(-(time.time() - ts) / _FRESH_HL), 4)


def _expiry_factor(rc, platform: str, h: str) -> float:
    from app.core.cookie_pool import _get_meta
    expires_at = _get_meta(rc, platform, h).get("expires_at", 0)
    if expires_at <= 0:
        return 0.50   # session-only — unknown
    days = (expires_at - time.time()) / 86400
    return round(max(0.0, min(1.0, days / 90)), 4)


def _priority(rc, platform: str, h: str) -> float:
    try:
        raw = rc.hget(f"p27b:priority:{platform}:{h}", "weight")
        return float(raw) if raw else 0.50
    except Exception:
        return 0.50


def _penalty(rc, platform: str, h: str) -> tuple[float, int, int]:
    cutoff  = time.time() - _PEN_TTL
    try:
        members = rc.zrangebyscore(_pk(platform, h), cutoff, "+inf")
        c429    = sum(1 for m in members if b"429:" in (m if isinstance(m, bytes) else m.encode()))
        cfail   = len(members) - c429
    except Exception:
        c429 = cfail = 0
    return min(0.80, c429 * _W_PEN_429 + cfail * _W_PEN_FAIL), c429, cfail


def _in_warmup(rc, platform: str, h: str) -> bool:
    return _WARMUP_ON and bool(rc.exists(_wk(platform, h)))


def _compute(rc, platform: str, h: str) -> tuple[float, dict]:
    """Full score computation. ~6 Redis reads per cookie. Never raises."""
    try:
        sr, uses    = _success_rate(rc, platform, h)
        fresh       = _freshness(rc, platform, h)
        exp         = _expiry_factor(rc, platform, h)
        stab        = min(1.0, uses / 20)   # ramps 0→1 as cookie accumulates 20 uses
        prio        = _priority(rc, platform, h)
        pen, c4, cf = _penalty(rc, platform, h)
        warmup      = _in_warmup(rc, platform, h)

        base  = _W_SUCCESS*sr + _W_FRESH*fresh + _W_EXPIRY*exp + _W_STABLE*stab + _W_PRIO*prio
        wm    = WARMUP_MULT if warmup else 1.0
        score = max(0.0, min(1.0, round(base * wm - pen, 4)))

        debug = {
            "sr": sr, "fresh": fresh, "exp": exp, "stab": stab, "prio": prio,
            "pen": pen, "c429": c4, "cfail": cf, "warmup": warmup, "final": score,
        }
        return score, debug
    except Exception as e:
        log.debug("[CookieScore] _compute error: %s", e)
        return 0.50, {"error": str(e)}


# ── Candidate filtering ───────────────────────────────────────────────────────
def _is_disabled(rc, platform: str, h: str) -> bool:
    return bool(rc.exists(_dk(platform, h)))


def _build_candidates(platform: str, eligible: list, rc) -> list[_Candidate]:
    """
    Score eligible cookies (already pre-filtered by cookie_pool: not blocked,
    not in cooldown, not expired by cookie_health key).
    Additional exclusion: manually disabled via p27b:disabled key.
    """
    result = []
    for c in eligible:
        h = _hash(c if isinstance(c, str) else c.decode("utf-8", errors="ignore"))
        if _is_disabled(rc, platform, h):
            continue
        score, debug = _compute(rc, platform, h)
        result.append(_Candidate(cookie_b64=c, h=h, score=score, debug=debug))
    return result


# ── Top-K selection ───────────────────────────────────────────────────────────
def _top_k(pool: int) -> int:
    override = int(os.getenv("COOKIE_SCORING_TOP_K", str(_DEFAULT_TOP_K)))
    if pool <= 1:  return 1
    if pool <= 3:  return min(2, override)
    if pool <= 7:  return min(3, override)
    if pool <= 12: return min(4, override)
    return min(5, override)


def _pick(candidates: list[_Candidate]) -> _Candidate:
    """Sort by score desc, weighted-random among top-K."""
    candidates.sort(key=lambda c: c.score, reverse=True)
    k   = _top_k(len(candidates))
    top = candidates[:k]
    # weight = score² so top cookie is preferred but not monopolistic
    weights = [max(0.001, c.score ** 2) for c in top]
    return random.choices(top, weights=weights, k=1)[0]


# ── Public API ────────────────────────────────────────────────────────────────

def select_scored_cookie(
    platform: str,
    eligible_cookies: list,   # Tier-1 eligible from cookie_pool (not blocked, not cooling)
    rc,                       # Redis connection (passed in to avoid re-open)
    job_id: str = "",
) -> Optional[bytes]:
    """
    Scored entry point. Called from cookie_pool.get_cookie_from_pool() when
    scoring is enabled for this platform.

    Returns chosen cookie bytes, or None so caller falls through to LRU.
    NEVER raises — any error returns None and caller uses legacy LRU.
    """
    try:
        if not eligible_cookies:
            return None

        candidates = _build_candidates(platform, eligible_cookies, rc)
        if not candidates:
            return None

        chosen = _pick(candidates)

        # Apply per-platform cooldown (mirrors LRU path exactly)
        cooldown_s = _COOLDOWN.get(platform, _DEFAULT_COOLDOWN)
        rc.setex(f"cookie_cooldown:{platform}:{chosen.h}", cooldown_s, "1")
        rc.set(f"cookie_lastused:{platform}:{chosen.h}", time.time())

        # Selection log (admin visibility, 1h retention)
        try:
            rc.zadd(_sk(platform), {chosen.h: time.time()})
            rc.zremrangebyscore(_sk(platform), 0, time.time() - _SEL_TTL)
            rc.expire(_sk(platform), _SEL_TTL + 60)
        except Exception:
            pass

        # Diagnostic log (INFO in shadow mode so it shows without DEBUG enabled)
        _lvl = logging.INFO if _SHADOW_LOG else logging.DEBUG
        log.log(
            _lvl,
            "[CookieScore%s:%s] sel=%s score=%.3f warmup=%s pen=%.2f "
            "cands=%d top_k=%d c429=%d cfail=%d job=%s",
            "[SHADOW]" if _SHADOW_LOG else "",
            platform,
            chosen.h[:8],
            chosen.score,
            chosen.debug.get("warmup", False),
            chosen.debug.get("pen", 0.0),
            len(candidates),
            _top_k(len(candidates)),
            chosen.debug.get("c429", 0),
            chosen.debug.get("cfail", 0),
            (job_id[:8] if job_id else "-"),
        )

        return chosen.cookie_b64

    except Exception as exc:
        log.warning("[CookieScore] select_scored_cookie failed, returning None: %s", exc)
        return None


def record_outcome(
    platform: str,
    cookie_b64: bytes | str,
    success: bool,
    failure_bucket: str = "",
) -> None:
    """
    Update per-cookie score metrics after a download completes.
    Call from routes.py (or downloader.py) after _track_download().
    """
    try:
        raw = cookie_b64 if isinstance(cookie_b64, str) else cookie_b64.decode("utf-8", errors="ignore")
        h   = _hash(raw)
        rc  = get_redis()
        now = time.time()
        key = _ok(platform, h)

        if success:
            rc.hincrbyfloat(key, "win_1h", 1)
            rc.hset(key, mapping={"last_outcome": "success", "last_ts": str(now)})
            # First success after warmup → graduate the cookie
            try:
                rc.delete(_wk(platform, h))
            except Exception:
                pass
        else:
            rc.hincrbyfloat(key, "fail_1h", 1)
            rc.hset(key, mapping={"last_outcome": failure_bucket or "fail", "last_ts": str(now)})
            # Penalty event in sorted set
            is_429     = failure_bucket in ("soft_block", "rate_limited_429", "429", "hard_block")
            pen_prefix = "429" if is_429 else "fail"
            pen_member = f"{pen_prefix}:{uuid.uuid4().hex[:8]}"
            pkey       = _pk(platform, h)
            rc.zadd(pkey, {pen_member: now})
            rc.zremrangebyscore(pkey, 0, now - _PEN_TTL)
            rc.expire(pkey, _PEN_TTL + 60)

        rc.expire(key, _OUTCOME_TTL)

    except Exception as e:
        log.debug("[CookieScore] record_outcome error: %s", e)


def record_hard_block(platform: str, cookie_b64: bytes | str) -> None:
    """
    Set warmup marker so this cookie gets WARMUP_MULT after the block expires.
    Call alongside mark_cookie_blocked(hard=True).
    """
    try:
        raw = cookie_b64 if isinstance(cookie_b64, str) else cookie_b64.decode("utf-8", errors="ignore")
        h   = _hash(raw)
        rc  = get_redis()
        # Warmup key lives for: hard block duration + warmup period after
        rc.setex(_wk(platform, h), HARD_BLOCK_TTL + WARMUP_S, "1")
    except Exception as e:
        log.debug("[CookieScore] record_hard_block error: %s", e)


# ── Admin APIs ────────────────────────────────────────────────────────────────

def get_score_breakdown(platform: str) -> list[dict]:
    """Return scored summary for every cookie in the pool (admin display)."""
    try:
        rc      = get_redis()
        cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
        from app.core.cookie_pool import _get_meta
        result  = []
        for i, c in enumerate(cookies):
            raw_c = c.decode("utf-8", errors="ignore") if isinstance(c, bytes) else c
            h     = _hash(raw_c)
            score, debug = _compute(rc, platform, h)
            meta  = _get_meta(rc, platform, h)
            hr    = rc.get(f"cookie_health:{platform}:{h}")
            health = (hr.decode() if isinstance(hr, bytes) else hr) or "healthy"
            in_cd  = bool(rc.exists(f"cookie_cooldown:{platform}:{h}"))
            disabled = _is_disabled(rc, platform, h)
            result.append({
                "index":             i,
                "hash":              h[:8],    # truncated for display safety
                "label":             meta.get("label", ""),
                "score":             score,
                "tier":              "best" if score >= 0.70 else "ok" if score >= SCORE_FLOOR else "degraded",
                "health":            health,
                "in_cooldown":       in_cd,
                "disabled":          disabled,
                "warmup":            debug.get("warmup", False),
                "success_rate":      debug.get("sr", 0),
                "freshness":         debug.get("fresh", 0),
                "expiry_factor":     debug.get("exp", 0),
                "penalty":           debug.get("pen", 0),
                "c429":              debug.get("c429", 0),
                "cfail":             debug.get("cfail", 0),
                "eligibleForScoring": not in_cd and health not in ("hard", "soft", "blocked") and not disabled,
            })
        result.sort(key=lambda x: x["score"], reverse=True)
        return result
    except Exception as e:
        log.warning("[CookieScore] get_score_breakdown error: %s", e)
        return []


def get_selection_history(platform: str) -> list[str]:
    """Return hash prefixes of recently selected cookies (last 1h)."""
    try:
        rc     = get_redis()
        cutoff = time.time() - _SEL_TTL
        members = rc.zrangebyscore(_sk(platform), cutoff, "+inf")
        return [(m.decode() if isinstance(m, bytes) else m)[:8] for m in members[-20:]]
    except Exception:
        return []


def get_platform_score_summary(platform: str) -> dict:
    """Compact summary for admin lane panel."""
    bd = get_score_breakdown(platform)
    if not bd:
        return {"scoringEnabled": is_scoring_enabled(platform), "cookies": []}
    eligible  = [x for x in bd if x["eligibleForScoring"]]
    warmingUp = [x for x in bd if x["warmup"]]
    return {
        "scoringEnabled":  is_scoring_enabled(platform),
        "shadowLog":       _SHADOW_LOG,
        "candidateCookies": len(eligible),
        "warmingUpCookies": len(warmingUp),
        "topScore":        bd[0]["score"] if bd else 0,
        "avgScore":        round(sum(x["score"] for x in eligible) / len(eligible), 3) if eligible else 0,
        "cookies":         bd,
    }
