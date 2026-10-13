"""
Redis configuration backup (task #6332, Phase 34A-2)
====================================================

The daily backup (app/tasks/backup_tasks.py) exports the Supabase database.
Redis was not backed up at all, yet Redis is where most of what the owner sets
up by hand in the admin panel lives: probe targets, known limitations, feature
switches, the desktop yt-dlp approval, takedown requests, cookie and proxy
pools, the Apify token pool, ScraperAPI keys. Losing the Redis volume means
re-entering all of it from memory.

This module exports ONLY that configuration — never caches, locks, counters,
rate limits, sessions, circuit-breaker state or queues. Those rebuild
themselves; restoring them would be wrong (a stale lock, a stale cooldown, a
counter that double-counts) rather than merely useless.

Inventory
---------
Collected by grepping the backend for Redis writes made by admin endpoints or
held as long-lived configuration (task #6332, 2026-10-13). A key is exported
only if it matches one of these patterns. SECRET = holds a credential.

  pattern                          type    what                                         secret?
  -------------------------------  ------  -------------------------------------------  -------
  admin:config:*                   str/set System Config page (value, :desc, __index__)  no
  probe:targets                    str     probe target URLs per platform (admin)        no
  probe:known_limitation           str     "known limitation" text per platform (admin)  no
  probe:min_interval               str     paid-probe interval per platform (admin)      no
  vidgrab:desktop:ytdlp            str     approved yt-dlp build for the Windows app     no
  vidgrab:youtube:enabled          str     admin YouTube on/off override                 no
  vidgrab:tuning:params            hash    auto-tuner persisted parameters               no
  takedown:requests                list    takedown requests (contain requester emails   no (PII)
                                           — personal data, not a credential)
  p27b:priority:*                  hash    admin cookie weight                           no
  p27b:disabled:*                  str     admin "disable this cookie" switch            no
  cookie_meta:*                    str     cookie metadata (label/source; NOT the cookie) no
  asr:killswitch                   str     ASR spend kill switch                         no
  china:killswitch:*               str     China layer kill switches (global/platform)   no
  china:mode:*                     str     China layer mode override per platform        no
  china:watermark:*                hash    owner watermark reviews per platform/provider no
  china:apify_pool:entries         hash    Apify pool entry metadata (never the token)   no
  china:apify_pool:seq             str     Apify pool id counter (keeps ids stable)      no
  china:secret:apify_token:meta    str     legacy Apify token metadata (last4 only)      no
  cookie_pool:*                    list    platform cookies                              SECRET
  proxy_pool:*                     list    proxy URLs with user:password                 SECRET
  scraperapi:keys                  list    ScraperAPI keys                               SECRET
  scraperapi:seeded                str     "env keys already imported" marker — grouped  SECRET
                                           with the keys: restoring it WITHOUT the keys
                                           would stop the env import on next start
  china:apify_pool:tokens          hash    Apify tokens, id -> token                     SECRET
  china:secret:apify_token         str     legacy single Apify token                     SECRET
  throwaway:accounts               str     throwaway accounts incl. passwords            SECRET

Deliberately NOT exported (examples): admin:session:*, admin:lockout:*,
admin:attempts:* (sessions/login throttle); probe:last_run:*, probe:state,
probe:alerted (runtime); cookie_health:*, cookie_cooldown:*, cookie_lastused:*
(runtime cookie state); pcircuit:*, oracle_circuit:*, cobalt:trip:*,
vidgrab:youtube:cb:* (circuit breakers); scraperapi:active_idx,
scraperapi:credits:*, scraperapi:exhausted:* (rotation state);
china:apify_pool:spend*, :calls:*, :alerted:* (counters); every *:stats:*,
*:lock*, vidcache:*, youtube:po_token, vg:*, lane:*, throttle:*, asr:spend:*,
china:cache:*, china:quota:*. Watch (channel watch) configuration lives in
env (app/core/watch_config.py) and the database, not in Redis.

Secrets
-------
Default: secret keys are exported as METADATA ONLY — group, a hash of the key
name, type, item count, TTL. No value, no key name. A backup file therefore
never contains a credential and can go to every destination, S3 included.

The admin System Config entry ``backup_redis_include_secrets`` = ``true``
(default absent = off; reversible by setting it to anything else or deleting
it) makes the run produce a SECOND payload with secret values, which is
uploaded ONLY to the private Supabase Storage bucket. S3 and the hosting
Postgres always receive the metadata-only payload. Both payloads say which one
they are (``include_secrets``).

Without that switch, after a Redis loss the secrets are re-entered in admin
(Cookies, Proxy, ScraperAPI, China/Apify pages); the metadata tells you how
many there were per group.
"""

from __future__ import annotations

import base64
import fnmatch
import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

FORMAT = "vidgrab-redis-config/1"
INCLUDE_SECRETS_CONFIG_KEY = "admin:config:backup_redis_include_secrets"
# Ceiling on keys per rule. Hitting it is reported (INCOMPLETE), never silent.
MAX_KEYS_PER_RULE = 20000


@dataclass(frozen=True)
class Rule:
    group: str
    pattern: str        # Redis glob; a pattern without * ? [ is an exact key
    secret: bool
    what: str

    @property
    def exact(self) -> bool:
        return not any(c in self.pattern for c in "*?[")


RULES: tuple[Rule, ...] = (
    Rule("admin_config", "admin:config:*", False, "System Config"),
    Rule("probe_config", "probe:targets", False, "probe targets"),
    Rule("probe_config", "probe:known_limitation", False, "known limitations"),
    Rule("probe_config", "probe:min_interval", False, "paid-probe intervals"),
    Rule("desktop_ytdlp", "vidgrab:desktop:ytdlp", False, "desktop yt-dlp approval"),
    Rule("youtube_switch", "vidgrab:youtube:enabled", False, "YouTube override"),
    Rule("auto_tuner", "vidgrab:tuning:params", False, "auto-tuner params"),
    Rule("takedown", "takedown:requests", False, "takedown requests"),
    Rule("cookie_admin", "p27b:priority:*", False, "cookie weight"),
    Rule("cookie_admin", "p27b:disabled:*", False, "cookie disabled"),
    Rule("cookie_admin", "cookie_meta:*", False, "cookie metadata"),
    Rule("killswitch", "asr:killswitch", False, "ASR kill switch"),
    Rule("killswitch", "china:killswitch:*", False, "China kill switches"),
    Rule("china_config", "china:mode:*", False, "China mode override"),
    Rule("china_config", "china:watermark:*", False, "watermark reviews"),
    Rule("china_config", "china:apify_pool:entries", False, "Apify pool metadata"),
    Rule("china_config", "china:apify_pool:seq", False, "Apify pool id counter"),
    Rule("china_config", "china:secret:apify_token:meta", False, "Apify token metadata"),
    Rule("cookie_pool", "cookie_pool:*", True, "cookies"),
    Rule("proxy_pool", "proxy_pool:*", True, "proxy URLs"),
    Rule("scraperapi_keys", "scraperapi:keys", True, "ScraperAPI keys"),
    Rule("scraperapi_keys", "scraperapi:seeded", True, "ScraperAPI seeded marker"),
    Rule("apify_tokens", "china:apify_pool:tokens", True, "Apify tokens"),
    Rule("apify_tokens", "china:secret:apify_token", True, "legacy Apify token"),
    Rule("throwaway_accounts", "throwaway:accounts", True, "throwaway accounts"),
)

SECRET_GROUPS = frozenset(r.group for r in RULES if r.secret)


def classify(key: str) -> Rule | None:
    """The rule a key falls under, or None when it is not configuration.

    Secret rules are checked first so a key can never be exported as plain
    configuration just because a broader non-secret pattern also matches.
    """
    for rule in sorted(RULES, key=lambda r: not r.secret):
        if fnmatch.fnmatchcase(key, rule.pattern):
            return rule
    return None


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ── value encoding ────────────────────────────────────────────────────────────
# The live client decodes responses; the backup task uses a raw one so bytes
# survive exactly. Text goes in as text, anything else as {"b64": ...}.

def _enc(v: Any) -> Any:
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return {"b64": base64.b64encode(v).decode("ascii")}
    return v


def _dec(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"b64"}:
        return base64.b64decode(v["b64"])
    return v


def _s(v: Any) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)


def _read_key(rc, key: str) -> tuple[str, Any, int] | None:
    """(type, value, item_count) or None if the key vanished meanwhile."""
    t = _s(rc.type(key))
    if t == "string":
        v = rc.get(key)
        return (t, _enc(v), 1) if v is not None else None
    if t == "hash":
        h = rc.hgetall(key)
        return t, {_s(f): _enc(v) for f, v in h.items()}, len(h)
    if t == "list":
        lst = rc.lrange(key, 0, -1)
        return t, [_enc(v) for v in lst], len(lst)
    if t == "set":
        members = sorted((_enc(m) for m in rc.smembers(key)), key=lambda m: json.dumps(m))
        return t, members, len(members)
    if t == "zset":
        z = rc.zrange(key, 0, -1, withscores=True)
        return t, [[_enc(m), s] for m, s in z], len(z)
    return None  # "none" (deleted) or an unsupported type (stream)


def _keys_for(rc, rule: Rule) -> tuple[list[str], bool]:
    """Keys matching one rule, via SCAN (never KEYS). (keys, truncated)."""
    if rule.exact:
        return ([rule.pattern] if rc.exists(rule.pattern) else []), False
    out: list[str] = []
    for k in rc.scan_iter(match=rule.pattern, count=500):
        k = _s(k)
        if classify(k) is not rule:
            continue  # belongs to a more specific (secret) rule
        out.append(k)
        if len(out) >= MAX_KEYS_PER_RULE:
            return sorted(out), True
    return sorted(set(out)), False


def include_secrets_setting(rc) -> bool:
    try:
        v = rc.get(INCLUDE_SECRETS_CONFIG_KEY)
    except Exception:  # noqa: BLE001
        return False
    return v is not None and _s(v).strip().lower() in ("1", "true", "yes", "on")


def export_redis_config(rc, include_secrets: bool = False) -> dict[str, Any]:
    """
    Snapshot the allow-listed configuration keys.

    Returns the payload dict (see FORMAT). ``summary`` has one row per group:
    keys, approximate bytes of the values, secret, values_included.
    ``warnings`` lists anything incomplete. Never raises on a single bad key.
    """
    keys_out: list[dict[str, Any]] = []
    secret_meta: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    seen: set[str] = set()

    for rule in RULES:
        g = summary.setdefault(rule.group, {
            "keys": 0, "bytes": 0, "secret": rule.secret,
            "values_included": (not rule.secret) or include_secrets,
        })
        keys, truncated = _keys_for(rc, rule)
        if truncated:
            warnings.append(f"redis {rule.pattern}: hit {MAX_KEYS_PER_RULE} keys — "
                            f"INCOMPLETE for this pattern")
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            try:
                got = _read_key(rc, key)
                if got is None:
                    continue
                typ, value, items = got
                pttl = rc.pttl(key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"redis {rule.group}: read failed ({type(exc).__name__})")
                continue
            ttl_ms = int(pttl) if pttl and int(pttl) > 0 else None
            size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            g["keys"] += 1
            g["bytes"] += size
            if rule.secret and not include_secrets:
                secret_meta.append({"group": rule.group, "key_sha": key_digest(key),
                                    "type": typ, "items": items, "ttl_ms": ttl_ms})
            else:
                keys_out.append({"key": key, "group": rule.group, "type": typ,
                                 "ttl_ms": ttl_ms, "value": value})

    return {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "include_secrets": bool(include_secrets),
        "note": ("Configuration only. Secret groups without values are listed in "
                 "secret_meta (counts, hashed key names) — re-enter them in admin."),
        "summary": summary,
        "keys": keys_out,
        "secret_meta": secret_meta,
        "warnings": warnings,
    }


def to_blob(payload: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def load_blob(raw: bytes) -> dict[str, Any]:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("format") != FORMAT:
        raise ValueError(f"not a Redis config backup (format={payload.get('format')!r})")
    return payload


# ── restore ───────────────────────────────────────────────────────────────────

def _write_key(pipe, entry: dict[str, Any]) -> None:
    key, typ, value = entry["key"], entry["type"], entry["value"]
    if typ == "string":
        pipe.set(key, _dec(value))
    elif typ == "hash":
        if value:
            pipe.hset(key, mapping={f: _dec(v) for f, v in value.items()})
    elif typ == "list":
        if value:
            pipe.rpush(key, *[_dec(v) for v in value])
    elif typ == "set":
        if value:
            pipe.sadd(key, *[_dec(v) for v in value])
    elif typ == "zset":
        if value:
            pipe.zadd(key, {_dec(m): float(s) for m, s in value})
    else:
        raise ValueError(f"unsupported type {typ}")
    if entry.get("ttl_ms"):
        pipe.pexpire(key, int(entry["ttl_ms"]))


def plan_restore(rc, payload: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
    """What a restore would do. Reads the target, writes nothing."""
    groups: dict[str, dict[str, int]] = {}
    actions: list[tuple[str, dict[str, Any]]] = []
    for entry in payload.get("keys", []):
        rule = classify(entry["key"])
        if rule is None:
            continue  # not configuration (tampered or older allow-list) — skip
        g = groups.setdefault(entry["group"], {"in_backup": 0, "write": 0,
                                               "conflict": 0, "overwrite": 0})
        g["in_backup"] += 1
        if rc.exists(entry["key"]):
            if overwrite:
                g["overwrite"] += 1
                actions.append(("overwrite", entry))
            else:
                g["conflict"] += 1
                actions.append(("skip", entry))
        else:
            g["write"] += 1
            actions.append(("write", entry))
    missing: dict[str, int] = {}
    for m in payload.get("secret_meta", []):
        missing[m["group"]] = missing.get(m["group"], 0) + 1
    return {"groups": groups, "actions": actions, "secrets_not_in_backup": missing}


def apply_restore(rc, plan: dict[str, Any]) -> int:
    """Execute a plan from plan_restore. Returns keys written."""
    n = 0
    pipe = rc.pipeline(transaction=False)
    for action, entry in plan["actions"]:
        if action == "skip":
            continue
        if action == "overwrite":
            pipe.delete(entry["key"])
        _write_key(pipe, entry)
        n += 1
    pipe.execute()
    return n


def display_key(entry_or_key: Any) -> str:
    """How a key may be printed: secret keys only by group + digest."""
    key = entry_or_key["key"] if isinstance(entry_or_key, dict) else entry_or_key
    rule = classify(key)
    if rule is not None and rule.secret:
        return f"<{rule.group} #{key_digest(key)}>"
    return key


def iter_groups(payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    return sorted((payload.get("summary") or {}).items())
