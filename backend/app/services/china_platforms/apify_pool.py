"""
Apify token pool for the China access layer (task #6036).

Several Apify accounts — ORGANIZATION accounts or accounts of different legal
owners, each with its own paid balance (Apify Terms §4.3 forbids several
personal accounts for one person; organizations are allowed and billed
separately: docs.apify.com/platform/collaboration/organization-account) — are
rotated per paid call.

Redis layout (no DB table, no migration):

  china:apify_pool:entries               HASH id -> meta JSON. NEVER the token.
  china:apify_pool:tokens                HASH id -> token
  china:apify_pool:seq                   id counter for admin entries ("1", "2", ...)
  china:apify_pool:spend:{id}:day:{d}    our recorded spend, micro-USD, UTC day (40-day TTL)
  china:apify_pool:spend:{id}:month:{m}  same, UTC calendar month (40-day TTL)
  china:apify_pool:spend_total:{id}      monotonic total (no TTL) — "spent since the last refresh"
  china:apify_pool:calls:{id}:day:{d}    dispatched runs per UTC day (40-day TTL)
  china:apify_pool:alerted:*             SET NX alert de-duplication

Two entries are mirrors, kept in step by sync() on every read:
  * the "legacy slot" mirrors the single admin token of secret_store
    (china:secret:apify_token). The first sync after deploy therefore turns the
    existing token into entry #1 automatically, the old /apify/token endpoints
    keep working unchanged, and a code rollback still finds the old key.
  * "env" mirrors CHINA_ACCESS_APIFY_TOKEN (fallback; priority 1000 = last).

States: active | exhausted | invalid | disabled | cooldown.
  exhausted  Apify refused to start a run for lack of credit / over the usage
             limit (HTTP 402, or one of the documented limit error types), or a
             refresh shows usage >= limit or the ACTORS feature disabled.
             Recovers automatically at `exhausted_until` (the end of Apify's
             monthlyUsageCycle from /users/me/limits, else the next UTC month)
             or when a refresh shows remaining credit.
  invalid    401/403 (token wrong, revoked, or lacking permission). Recovers
             only through a successful refresh.
  cooldown   429 or 5xx on run start. Eligible again after
             CHINA_ACCESS_APIFY_POOL_COOLDOWN_SEC.
  disabled   enabled=False (owner switch). Derived, never stored as state.

Error shapes relied on (Apify OpenAPI, https://docs.apify.com/api/openapi.json,
POST /v2/acts/{actorId}/runs = https://docs.apify.com/api/v2/act-runs-post):
  401 {"error":{"type":"invalid-token","message":"Authentication token is not valid."}}
  402 "Payment required - the user has exceeded their usage limit, does not have
      enough credits, or the request lacks authentication and payment credentials."
      (documented example type "x402-payment-required")
  403 {"error":{"type":"insufficient-permissions", ...}}
  429 {"error":{"type":"rate-limit-exceeded", ...}}  (docs.apify.com/api/v2 "Rate limiting")
  ErrorType enum members used regardless of status: not-enough-usage-to-run-paid-actor,
  platform-feature-disabled, user-disabled, apify-plan-required-to-use-paid-actor,
  concurrent-runs-limit-exceeded, actor-memory-limit-exceeded.

The token never appears in a log, exception, response, alert or audit row:
callers see `id`, `label` and `last4` only.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.china_platforms import budget_guard, settings

logger = logging.getLogger("app.china_access")

ENTRIES_KEY = "china:apify_pool:entries"
TOKENS_KEY = "china:apify_pool:tokens"
SEQ_KEY = "china:apify_pool:seq"
ENV_ID = "env"
STATES = ("active", "exhausted", "invalid", "disabled", "cooldown")
DEFAULT_PRIORITY = 100
ENV_PRIORITY = 1000
_ROLLUP_TTL = 40 * 86400

# Apify error types (ErrorType enum of the OpenAPI spec) → pool state.
_EXHAUSTED_TYPES = frozenset({
    "not-enough-usage-to-run-paid-actor", "platform-feature-disabled",
    "x402-payment-required", "monthly-usage-limit-exceeded", "limit-reached",
})
_INVALID_TYPES = frozenset({
    "invalid-token", "token-not-provided", "user-or-token-not-found", "record-or-token-not-found",
    "user-disabled", "insufficient-permissions", "apify-plan-required-to-use-paid-actor",
    "invalid-token-type",
})
_COOLDOWN_TYPES = frozenset({
    "rate-limit-exceeded", "concurrent-runs-limit-exceeded", "actor-memory-limit-exceeded",
})


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _s(v) -> str:
    if v is None:
        return ""
    return (v.decode() if isinstance(v, bytes) else str(v)).strip()


def _i(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _now() -> datetime:
    return budget_guard.utcnow()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def last4(token: str) -> Optional[str]:
    return token[-4:] if token and len(token) >= 8 else None


def _next_month_start(now: datetime) -> datetime:
    first = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


# ── keys ────────────────────────────────────────────────────────────────────

def k_spend_day(entry_id: str, day: str) -> str:
    return f"china:apify_pool:spend:{entry_id}:day:{day}"


def k_spend_month(entry_id: str, month: str) -> str:
    return f"china:apify_pool:spend:{entry_id}:month:{month}"


def k_spend_total(entry_id: str) -> str:
    return f"china:apify_pool:spend_total:{entry_id}"


def k_calls_day(entry_id: str, day: str) -> str:
    return f"china:apify_pool:calls:{entry_id}:day:{day}"


# ── storage ─────────────────────────────────────────────────────────────────

def _load_all(r) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for k, v in (r.hgetall(ENTRIES_KEY) or {}).items():
        try:
            out[_s(k)] = json.loads(_s(v))
        except ValueError:
            continue
    return out


def _save(r, meta: dict) -> None:
    r.hset(ENTRIES_KEY, meta["id"], json.dumps(meta, ensure_ascii=False))


def _new_meta(entry_id: str, token: str, *, label: str, source: str, priority: int,
              ceiling: Optional[float] = None, legacy_slot: bool = False, ip: Optional[str] = None) -> dict:
    now = _iso(_now())
    return {
        "id": entry_id, "label": label, "last4": last4(token), "fp": fingerprint(token),
        "source": source, "legacy_slot": legacy_slot, "priority": int(priority),
        "enabled": True, "monthly_ceiling_usd": ceiling,
        "state": "active", "state_reason": None, "state_since": now,
        "cooldown_until": None, "exhausted_until": None,
        "account": None, "usage": None, "refreshed_at": None, "spend_total_at_refresh_micros": 0,
        "created_at": now, "created_by_ip": ip, "last_used_at": None, "last_error_at": None,
    }


def _apply_info(meta: dict, info: dict, r) -> None:
    """Account/usage from a free validation (secret_store.validate)."""
    meta["account"] = info.get("account")
    meta["usage"] = info.get("usage")
    meta["refreshed_at"] = _iso(_now())
    meta["spend_total_at_refresh_micros"] = _i(r.get(k_spend_total(meta["id"])))


def sync(r=None) -> dict[str, dict]:
    """Bring the legacy slot and the env entry in line with their sources.
    Raises on Redis failure (callers fail closed)."""
    from app.services.china_platforms import secret_store  # noqa: PLC0415
    r = r or _r()
    entries = _load_all(r)

    # legacy slot ⇔ china:secret:apify_token
    legacy_tok = _s(r.get(secret_store.TOKEN_KEY))
    slot = next((m for m in entries.values() if m.get("legacy_slot")), None)
    if legacy_tok and (slot is None or slot.get("fp") != fingerprint(legacy_tok)):
        keep = slot or {}
        if slot is not None:
            _remove(r, slot["id"])
            entries.pop(slot["id"], None)
        new_id = str(_i(r.incr(SEQ_KEY)))
        meta = _new_meta(new_id, legacy_tok, label=keep.get("label") or "Token chính (đã lưu trước đây)",
                         source="admin", legacy_slot=True,
                         priority=keep.get("priority", DEFAULT_PRIORITY),
                         ceiling=keep.get("monthly_ceiling_usd"))
        try:
            legacy_meta = json.loads(_s(r.get(secret_store.META_KEY)) or "{}")
        except ValueError:
            legacy_meta = {}
        if legacy_meta.get("last4") == last4(legacy_tok):
            meta["account"] = legacy_meta.get("account")
            meta["usage"] = legacy_meta.get("usage")
            meta["refreshed_at"] = legacy_meta.get("validated_at")
            meta["created_at"] = legacy_meta.get("set_at") or meta["created_at"]
            meta["created_by_ip"] = legacy_meta.get("set_by_ip")
        r.hset(TOKENS_KEY, new_id, legacy_tok)
        _save(r, meta)
        entries[new_id] = meta
        logger.info("china_access apify pool: legacy token synced as entry %s (last4 %s)", new_id, meta["last4"])
    elif not legacy_tok and slot is not None:
        _remove(r, slot["id"])
        entries.pop(slot["id"], None)

    # env entry ⇔ CHINA_ACCESS_APIFY_TOKEN
    env_tok = secret_store.env_token()
    env = entries.get(ENV_ID)
    if env_tok and (env is None or env.get("fp") != fingerprint(env_tok)):
        meta = _new_meta(ENV_ID, env_tok, label="Biến môi trường", source="env",
                         priority=(env or {}).get("priority", ENV_PRIORITY),
                         ceiling=(env or {}).get("monthly_ceiling_usd"))
        meta["enabled"] = (env or {}).get("enabled", True)
        _save(r, meta)
        entries[ENV_ID] = meta
    elif not env_tok and env is not None:
        r.hdel(ENTRIES_KEY, ENV_ID)
        entries.pop(ENV_ID, None)
    return entries


def _token_for(r, meta: dict) -> str:
    if meta.get("source") == "env":
        from app.services.china_platforms import secret_store  # noqa: PLC0415
        return secret_store.env_token()
    return _s(r.hget(TOKENS_KEY, meta["id"]))


def _remove(r, entry_id: str) -> None:
    r.hdel(ENTRIES_KEY, entry_id)
    r.hdel(TOKENS_KEY, entry_id)


def all_tokens() -> list[str]:
    """For the redactor only."""
    try:
        return [_s(v) for v in (_r().hvals(TOKENS_KEY) or []) if _s(v)]
    except Exception:  # noqa: BLE001
        return []


# ── state ───────────────────────────────────────────────────────────────────

def effective_state(meta: dict, now: Optional[datetime] = None) -> str:
    now = now or _now()
    if not meta.get("enabled", True):
        return "disabled"
    st = meta.get("state") or "active"
    if st == "cooldown":
        until = _parse(meta.get("cooldown_until"))
        return "active" if until is None or now >= until else "cooldown"
    if st == "exhausted":
        until = _parse(meta.get("exhausted_until"))
        return "active" if until is not None and now >= until else "exhausted"
    return st if st in STATES else "active"


def _recover_if_due(r, meta: dict, now: datetime) -> dict:
    """Persist an automatic recovery (cooldown over / new usage cycle)."""
    if meta.get("state") in ("cooldown", "exhausted") and effective_state(meta, now) == "active":
        prev = meta["state"]
        meta.update({"state": "active", "state_reason": f"auto_recovered_from_{prev}",
                     "state_since": _iso(now), "cooldown_until": None, "exhausted_until": None})
        _save(r, meta)
        logger.info("china_access apify pool: entry %s recovered from %s", meta["id"], prev)
    return meta


def spend_figures(r, meta: dict, now: Optional[datetime] = None) -> dict:
    """Our recorded spend + the remaining credit as far as we can tell.

    apify_usage_now = Apify's monthlyUsageUsd at the last refresh + what we
    recorded on this entry since then (spend_total delta), only while that
    refresh's usage cycle is still running. usage_effective = the larger of
    that and our own month figure (the fresher/more pessimistic one).
    remaining = min(apify limit − usage_effective, owner ceiling − our month)."""
    now = now or _now()
    eid = meta["id"]
    day, month = budget_guard.day_key(now), budget_guard.month_key(now)
    ours_day = _i(r.get(k_spend_day(eid, day)))
    ours_month = _i(r.get(k_spend_month(eid, month)))
    total = _i(r.get(k_spend_total(eid)))
    usage = meta.get("usage") or {}
    limit = usage.get("max_monthly_usage_usd")
    apify_used = usage.get("monthly_usage_usd")
    cycle_end = _parse(usage.get("cycle_end"))
    apify_now = None
    if isinstance(apify_used, (int, float)) and (cycle_end is None or now < cycle_end):
        since = max(0, total - _i(meta.get("spend_total_at_refresh_micros")))
        apify_now = float(apify_used) + since / 1e6
    usage_eff = max(apify_now, ours_month / 1e6) if apify_now is not None else None
    rem = []
    if isinstance(limit, (int, float)) and usage_eff is not None:
        rem.append(float(limit) - usage_eff)
    ceiling = meta.get("monthly_ceiling_usd")
    if isinstance(ceiling, (int, float)):
        rem.append(float(ceiling) - ours_month / 1e6)
    # 7-day burn (our recorded spend, UTC days, today included)
    week = 0
    for i in range(7):
        week += _i(r.get(k_spend_day(eid, budget_guard.day_key(now - timedelta(days=i)))))
    burn = week / 7 / 1e6
    remaining = round(min(rem), 6) if rem else None
    return {
        "spend_today_usd": ours_day / 1e6,
        "spend_month_usd": ours_month / 1e6,
        "spend_7d_usd": week / 1e6,
        "calls_today": _i(r.get(k_calls_day(eid, day))),
        "apify_usage_now_usd": None if apify_now is None else round(apify_now, 6),
        "apify_limit_usd": float(limit) if isinstance(limit, (int, float)) else None,
        "remaining_usd": remaining,
        "burn_per_day_usd": round(burn, 6),
        "projected_days_left": (round(max(0.0, remaining) / burn, 1)
                                if remaining is not None and burn > 0 else None),
    }


def public_view(r, meta: dict, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    st = effective_state(meta, now)
    figs = spend_figures(r, meta, now)
    ceiling = meta.get("monthly_ceiling_usd")
    over_ceiling = isinstance(ceiling, (int, float)) and figs["spend_month_usd"] >= float(ceiling)
    view = {k: meta.get(k) for k in (
        "id", "label", "last4", "source", "legacy_slot", "priority", "enabled", "monthly_ceiling_usd",
        "state_reason", "state_since", "cooldown_until", "exhausted_until", "account", "usage",
        "refreshed_at", "created_at", "last_used_at", "last_error_at")}
    view["state"] = st
    view["eligible"] = st == "active" and not over_ceiling
    view["ineligible_reason"] = None if view["eligible"] else ("entry_ceiling" if st == "active" else st)
    view.update(figs)
    return view   # no token, no fingerprint


def _order_key(r, meta: dict, now: datetime):
    rem = spend_figures(r, meta, now)["remaining_usd"]
    return (_i(meta.get("priority")), 0 if rem is not None else 1, -(rem or 0.0), meta["id"])


# ── lease: one entry for one paid call ──────────────────────────────────────

@dataclass
class Lease:
    entry_id: str
    label: str
    last4: Optional[str]
    token: str
    reserved_micros: int
    day: str
    month: str

    def __repr__(self) -> str:   # never print the token
        return f"Lease(entry={self.entry_id!r}, last4={self.last4!r}, reserved={self.reserved_micros})"


def has_eligible() -> bool:
    """Any entry usable now? With entries configured but none eligible, the
    "no eligible token" alert fires (once) — the router skips the paid
    provider without calling pick() in that case."""
    try:
        r = _r()
        now = _now()
        entries = sync(r)
        for meta in entries.values():
            if public_view(r, _recover_if_due(r, meta, now), now)["eligible"]:
                return True
    except Exception:  # noqa: BLE001
        return False
    if entries:
        _alert_none_eligible()
    return False


def _alert_none_eligible() -> None:
    _alert_once("china:apify_pool:alerted:none_eligible", "critical",
                "Apify pool: không còn token dùng được",
                "Mọi token Apify đều hết tiền, sai, đang tạm nghỉ, bị tắt hoặc chạm trần. "
                "Lượt lấy video trả phí (Douyin/Kuaishou/Xiaohongshu) đang bị từ chối.", ttl=86400)


def first_eligible_token() -> str:
    try:
        r = _r()
        now = _now()
        metas = [m for m in sync(r).values() if public_view(r, m, now)["eligible"]]
        metas.sort(key=lambda m: _order_key(r, m, now))
        return _token_for(r, metas[0]) if metas else ""
    except Exception:  # noqa: BLE001
        return ""


def pick(est_micros: int, exclude: Optional[set] = None) -> Optional[Lease]:
    """Choose the best eligible entry and reserve `est_micros` on it (per-entry
    ceiling enforced atomically with INCRBY; rolled back when over). None when
    nothing is eligible — or Redis is unreadable (fail closed)."""
    exclude = exclude or set()
    try:
        r = _r()
        now = _now()
        entries = sync(r)
        cands = []
        for meta in entries.values():
            if meta["id"] in exclude:
                continue
            meta = _recover_if_due(r, meta, now)
            if effective_state(meta, now) != "active":
                continue
            cands.append(meta)
        cands.sort(key=lambda m: _order_key(r, m, now))
        day, month = budget_guard.day_key(now), budget_guard.month_key(now)
        for meta in cands:
            token = _token_for(r, meta)
            if not token:
                continue
            eid = meta["id"]
            new_month = _i(r.incrby(k_spend_month(eid, month), est_micros))
            r.expire(k_spend_month(eid, month), _ROLLUP_TTL)
            ceiling = meta.get("monthly_ceiling_usd")
            if isinstance(ceiling, (int, float)) and new_month > settings.usd_to_micros(float(ceiling)):
                r.decrby(k_spend_month(eid, month), est_micros)
                _alert_once(f"china:apify_pool:alerted:ceiling:{eid}:{month}", "warning",
                            "Apify pool: token chạm trần chi tiêu riêng",
                            f"Token {_who(meta)} đã chạm trần ${float(ceiling):.2f}/tháng do admin đặt. "
                            "Hệ thống chuyển sang token khác.", ttl=_ROLLUP_TTL)
                continue
            r.incrby(k_spend_day(eid, day), est_micros)
            r.expire(k_spend_day(eid, day), _ROLLUP_TTL)
            r.incrby(k_spend_total(eid), est_micros)
            try:
                r.delete("china:apify_pool:alerted:none_eligible")
            except Exception:  # noqa: BLE001
                pass
            return Lease(eid, meta.get("label") or eid, meta.get("last4"), token, est_micros, day, month)
    except Exception as exc:  # noqa: BLE001
        logger.error("china_access apify pool pick failed: %s", type(exc).__name__)
        return None
    if not exclude:
        _alert_none_eligible()
    return None


def release(lease: Lease) -> None:
    """The run never started: refund the reservation entirely."""
    settle(lease, 0, dispatched=False)


def settle(lease: Lease, recorded_micros: int, *, dispatched: bool = True) -> None:
    """Replace the reserved estimate with what budget_guard recorded for the
    call (same floor-at-estimate accounting)."""
    delta = int(recorded_micros) - lease.reserved_micros
    try:
        r = _r()
        if delta:
            r.incrby(k_spend_day(lease.entry_id, lease.day), delta)
            r.incrby(k_spend_month(lease.entry_id, lease.month), delta)
            r.incrby(k_spend_total(lease.entry_id), delta)
        if dispatched:
            k = k_calls_day(lease.entry_id, lease.day)
            r.incr(k)
            r.expire(k, _ROLLUP_TTL)
            raw = r.hget(ENTRIES_KEY, lease.entry_id)
            if raw:
                meta = json.loads(_s(raw))
                meta["last_used_at"] = _iso(_now())
                _save(r, meta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access apify pool settle failed: %s", type(exc).__name__)
    lease.reserved_micros = int(recorded_micros)
    if dispatched:
        check_pool_low()


# ── classification of Apify refusals ────────────────────────────────────────

def classify_start_error(status: int, body_text: str = "") -> tuple[Optional[str], str]:
    """(state, reason) for a NON-2xx answer to POST /v2/acts/{id}/runs.
    state None = not an account problem (bad input, 404 actor, ...)."""
    etype = ""
    try:
        err = (json.loads(body_text or "{}") or {}).get("error") or {}
        etype = str(err.get("type") or "")[:80]
    except (ValueError, AttributeError):
        etype = ""
    if etype in _EXHAUSTED_TYPES:
        return "exhausted", f"http_{status}:{etype}"
    if etype in _INVALID_TYPES:
        return "invalid", f"http_{status}:{etype}"
    if etype in _COOLDOWN_TYPES:
        return "cooldown", f"http_{status}:{etype}"
    if status == 402:
        return "exhausted", f"http_402:{etype or 'payment-required'}"
    if status in (401, 403):
        return "invalid", f"http_{status}:{etype or 'auth'}"
    if status == 429 or status >= 500:
        return "cooldown", f"http_{status}:{etype or 'unavailable'}"
    return None, f"http_{status}:{etype}"


def mark(entry_id: str, state: str, reason: str, *, exhausted_until: Optional[datetime] = None) -> None:
    """Record a transition (exhausted / invalid / cooldown / active) and send
    the matching alert once per transition."""
    try:
        r = _r()
        raw = r.hget(ENTRIES_KEY, entry_id)
        if not raw:
            return
        meta = json.loads(_s(raw))
        now = _now()
        changed = meta.get("state") != state
        meta["state"] = state
        meta["state_reason"] = reason
        if changed:
            meta["state_since"] = _iso(now)
        meta["cooldown_until"] = None
        meta["exhausted_until"] = None
        if state == "cooldown":
            meta["cooldown_until"] = _iso(now + timedelta(seconds=settings.apify_pool_cooldown_sec()))
        if state == "exhausted":
            cyc_end = _parse((meta.get("usage") or {}).get("cycle_end"))
            until = exhausted_until or (cyc_end if cyc_end and cyc_end > now else _next_month_start(now))
            meta["exhausted_until"] = _iso(until)
        if state != "active":
            meta["last_error_at"] = _iso(now)
        _save(r, meta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access apify pool mark failed: %s", type(exc).__name__)
        return
    logger.warning("china_access apify pool: entry %s (last4 %s) -> %s (%s)",
                   entry_id, meta.get("last4"), state, reason)
    if state in ("exhausted", "invalid") and changed:
        title = ("Apify pool: token hết tiền / chạm giới hạn" if state == "exhausted"
                 else "Apify pool: token không dùng được")
        tail = (f" Tự dùng lại từ {meta['exhausted_until'][:10]} (UTC) hoặc khi bấm Làm mới thấy còn tiền."
                if state == "exhausted" else " Kiểm tra token trong Apify rồi bấm Làm mới, hoặc xoá token.")
        _alert_once(f"china:apify_pool:alerted:{entry_id}:{state}:{meta['state_since']}",
                    "critical" if state == "invalid" else "warning", title,
                    f"Token {_who(meta)}: {reason}.{tail}", ttl=_ROLLUP_TTL)


# ── alerts ──────────────────────────────────────────────────────────────────

def _who(meta: dict) -> str:
    from app.services.china_platforms.errors import redact  # noqa: PLC0415
    return redact(f"\"{meta.get('label') or meta.get('id')}\" (••••{meta.get('last4') or '?'})")


def _alert_once(key: str, level: str, title: str, body: str, *, ttl: int) -> bool:
    try:
        if not _r().set(key, "1", nx=True, ex=ttl):
            return False
    except Exception:  # noqa: BLE001
        return False      # no dedupe store → no alert storm
    try:
        from app.core.alerts import send_admin_alert  # noqa: PLC0415
        send_admin_alert(level, title, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access apify pool alert failed: %s", type(exc).__name__)
    return True


def summary(r=None, now: Optional[datetime] = None, views: Optional[list] = None) -> dict:
    r = r or _r()
    now = now or _now()
    views = views if views is not None else [public_view(r, m, now) for m in sync(r).values()]
    known = [v for v in views if v["remaining_usd"] is not None and v["state"] != "disabled"]
    total_cap = 0.0
    for v in known:
        caps = []
        if v["apify_limit_usd"] is not None:
            caps.append(v["apify_limit_usd"])
        if isinstance(v.get("monthly_ceiling_usd"), (int, float)):
            caps.append(float(v["monthly_ceiling_usd"]))
        total_cap += min(caps) if caps else 0.0
    remaining = sum(max(0.0, v["remaining_usd"]) for v in known)
    burn = sum(v["burn_per_day_usd"] for v in views)
    return {
        "entries": len(views),
        "eligible": sum(1 for v in views if v["eligible"]),
        "by_state": {s: sum(1 for v in views if v["state"] == s) for s in STATES},
        "remaining_usd": round(remaining, 6) if known else None,
        "capacity_usd": round(total_cap, 6) if known else None,
        "remaining_pct": round(remaining * 100 / total_cap, 1) if known and total_cap > 0 else None,
        "spend_today_usd": round(sum(v["spend_today_usd"] for v in views), 6),
        "spend_month_usd": round(sum(v["spend_month_usd"] for v in views), 6),
        "burn_per_day_usd": round(burn, 6),
        "projected_days_left": round(remaining / burn, 1) if known and burn > 0 else None,
        "low_pct_threshold": settings.apify_pool_low_pct(),
    }


def check_pool_low() -> bool:
    """One alert per UTC month when the pool's known remaining credit drops
    below CHINA_ACCESS_APIFY_POOL_LOW_PCT % of its known capacity."""
    try:
        s = summary()
    except Exception:  # noqa: BLE001
        return False
    pct = s.get("remaining_pct")
    if pct is None or pct >= settings.apify_pool_low_pct():
        return False
    return _alert_once(f"china:apify_pool:alerted:low:{budget_guard.month_key()}", "warning",
                       "Apify pool: sắp hết tiền",
                       f"Còn khoảng ${s['remaining_usd']:.2f} / ${s['capacity_usd']:.2f} ({pct}%) trên "
                       f"{s['entries']} token. Nạp thêm hoặc thêm token tổ chức khác.", ttl=_ROLLUP_TTL)


# ── admin operations ────────────────────────────────────────────────────────

class PoolError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


def list_views() -> list[dict]:
    r = _r()
    now = _now()
    out = []
    for m in sync(r).values():
        m = _recover_if_due(r, m, now)
        out.append(public_view(r, m, now))
    out.sort(key=lambda v: (_i(v["priority"]), 0 if v["remaining_usd"] is not None else 1,
                            -(v["remaining_usd"] or 0.0), v["id"]))
    return out


def get_view(entry_id: str) -> dict:
    r = _r()
    meta = sync(r).get(entry_id)
    if meta is None:
        raise PoolError("not_found", "Không tìm thấy token này.", 404)
    return public_view(r, meta)


def add(token: str, info: dict, *, label: str, priority: Optional[int], ceiling: Optional[float],
        ip: Optional[str]) -> dict:
    r = _r()
    entries = sync(r)
    fp = fingerprint(token)
    if any(m.get("fp") == fp for m in entries.values()):
        raise PoolError("duplicate", "Token này đã có trong danh sách.", 409)
    if sum(1 for m in entries.values() if m.get("source") == "admin") >= settings.apify_pool_max_entries():
        raise PoolError("pool_full", f"Tối đa {settings.apify_pool_max_entries()} token.", 400)
    new_id = str(_i(r.incr(SEQ_KEY)))
    meta = _new_meta(new_id, token, label=label, source="admin",
                     priority=DEFAULT_PRIORITY if priority is None else priority, ceiling=ceiling, ip=ip)
    _apply_info(meta, info, r)
    _state_from_info(meta, info)
    r.hset(TOKENS_KEY, new_id, token)
    _save(r, meta)
    return public_view(r, meta)


def update(entry_id: str, fields: dict) -> tuple[dict, dict]:
    """fields ⊆ {label, priority, monthly_ceiling_usd, enabled}. Returns (prior, new) views."""
    r = _r()
    meta = sync(r).get(entry_id)
    if meta is None:
        raise PoolError("not_found", "Không tìm thấy token này.", 404)
    prior = public_view(r, meta)
    for k in ("label", "priority", "monthly_ceiling_usd", "enabled"):
        if k in fields:
            meta[k] = fields[k]
    _save(r, meta)
    return prior, public_view(r, meta)


def delete(entry_id: str) -> dict:
    from app.services.china_platforms import secret_store  # noqa: PLC0415
    r = _r()
    meta = sync(r).get(entry_id)
    if meta is None:
        raise PoolError("not_found", "Không tìm thấy token này.", 404)
    if meta.get("source") == "env":
        raise PoolError("env_entry", "Token từ biến môi trường: gỡ CHINA_ACCESS_APIFY_TOKEN trên Vibe Host, "
                                     "hoặc tắt token này.", 400)
    view = public_view(r, meta)
    if meta.get("legacy_slot"):
        secret_store.delete()        # keep the mirror consistent
    _remove(r, entry_id)
    return view


def token_of(entry_id: str) -> tuple[str, dict]:
    """(token, meta) for a refresh. Internal only."""
    r = _r()
    meta = sync(r).get(entry_id)
    if meta is None:
        raise PoolError("not_found", "Không tìm thấy token này.", 404)
    return _token_for(r, meta), meta


def _state_from_info(meta: dict, info: dict) -> None:
    acc = info.get("account") or {}
    usage = info.get("usage") or {}
    lim, used = usage.get("max_monthly_usage_usd"), usage.get("monthly_usage_usd")
    now = _now()
    if acc.get("actors_enabled") is False:
        meta.update({"state": "exhausted", "state_reason": "actors_feature_disabled",
                     "state_since": _iso(now), "cooldown_until": None})
    elif isinstance(lim, (int, float)) and isinstance(used, (int, float)) and lim > 0 and used >= lim:
        meta.update({"state": "exhausted", "state_reason": "usage_at_limit", "state_since": _iso(now),
                     "cooldown_until": None})
    else:
        if meta.get("state") != "active":
            meta["state_since"] = _iso(now)
            meta["state_reason"] = "refresh_ok"
        meta.update({"state": "active", "cooldown_until": None, "exhausted_until": None})
        return
    cyc_end = _parse(usage.get("cycle_end"))
    meta["exhausted_until"] = _iso(cyc_end if cyc_end and cyc_end > now else _next_month_start(now))


def apply_refresh(entry_id: str, info: Optional[dict], *, rejected_code: Optional[str] = None) -> dict:
    """Store a refresh result. `info` from secret_store.validate, or
    rejected_code ("invalid_token" | "apify_unreachable")."""
    r = _r()
    meta = sync(r).get(entry_id)
    if meta is None:
        raise PoolError("not_found", "Không tìm thấy token này.", 404)
    if rejected_code == "invalid_token":
        mark(entry_id, "invalid", "refresh:invalid-token")
    elif rejected_code:
        pass     # Apify unreachable: no state change
    else:
        before = meta.get("state")
        _apply_info(meta, info or {}, r)
        _state_from_info(meta, info or {})
        if meta["state"] == "exhausted" and before != "exhausted":
            reason, until = meta["state_reason"], _parse(meta.get("exhausted_until"))
            meta["state"] = before          # let mark() record the transition and alert once
            _save(r, meta)
            mark(entry_id, "exhausted", reason, exhausted_until=until)
        else:
            _save(r, meta)
        check_pool_low()
    return get_view(entry_id)
