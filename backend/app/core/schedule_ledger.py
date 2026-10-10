"""
Scheduled channel runs — "already handled" ledger (Phase 33-0, task #6254).

Before this, every run of a daily/weekly channel schedule asked the channel
for its newest N videos and created N download jobs, whether or not the same
videos had been downloaded by the previous run. A daily schedule therefore
re-downloaded the same videos every day (the only idempotency in video_tasks
is per job_id, and every run creates new job rows).

This module keeps, per schedule, the set of channel items already handed to
the downloader, keyed by (platform, external item id) — the video id when the
discovered entry has one or it can be read from the URL, otherwise a hash of
the canonical item URL. Only scheduled channel runs use it; manual bulk and
channel downloads are unchanged.

Storage: one Redis sorted set per schedule (member = item key, score = when
it was first handled). Redis rather than a table because this phase must not
add a migration and the data is safe to lose: a missing ledger for a schedule
that has run before is re-seeded from that schedule owner's completed jobs, or
the run becomes a baseline (record, download nothing) — never a mass
re-download. Size is bounded (newest LEDGER_MAX_ITEMS kept) and the key
expires LEDGER_TTL_DAYS after the last run touched it. Production Redis runs
with appendonly + volatile-lru, so a key with a TTL can be evicted under
memory pressure; that also lands in the baseline path.

Switches
  SCHEDULE_DEDUPE_ENABLED            default true; "false" restores the old
                                     behaviour (every run downloads the newest
                                     items) except for the per-run cap below.
  SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN optional operator cap; the effective cap
                                     is min(schedule max_videos (default 10),
                                     this value, HARD_MAX_ITEMS_PER_RUN).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

HARD_MAX_ITEMS_PER_RUN = 20
DEFAULT_MAX_VIDEOS = 10
LEDGER_MAX_ITEMS = 2000
LEDGER_TTL_DAYS = 180
_KEY_PREFIX = "vidgrab:sched:seen:"

# Outcomes reported back to the caller (and written to the channel job row).
MODE_DISABLED = "disabled"      # flag off: old behaviour, capped
MODE_FIRST_RUN = "first_run"    # schedule never ran before: download newest, record
MODE_SEEDED = "seeded"          # ledger rebuilt from the owner's completed jobs
MODE_BASELINE = "baseline"      # no usable history: record everything, download nothing
MODE_LEDGER = "ledger"          # normal run: download only unseen items


def dedupe_enabled() -> bool:
    return os.getenv("SCHEDULE_DEDUPE_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def items_per_run_cap(max_videos) -> int:
    """Effective number of items a scheduled channel run may download."""
    try:
        wanted = int(max_videos) if max_videos else DEFAULT_MAX_VIDEOS
    except (TypeError, ValueError):
        wanted = DEFAULT_MAX_VIDEOS
    cap = wanted
    raw = os.getenv("SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN", "").strip()
    if raw:
        try:
            env_cap = int(raw)
            if env_cap > 0:
                cap = min(cap, env_cap)
        except ValueError:
            pass
    return max(1, min(cap, HARD_MAX_ITEMS_PER_RUN))


def ledger_key(schedule_id: str) -> str:
    return f"{_KEY_PREFIX}{schedule_id}"


# ── Item identity ────────────────────────────────────────────────────

_ID_FIELDS = ("id", "video_id", "aweme_id", "note_id", "external_id")
_URL_ID_PATTERNS = (
    re.compile(r"youtube\.com/shorts/([\w-]{6,})"),
    re.compile(r"youtu\.be/([\w-]{6,})"),
    re.compile(r"/video/(\d{6,})"),                 # tiktok, douyin
    re.compile(r"kuaishou\.com/short-video/([\w-]+)"),
    re.compile(r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{16,})"),
    re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)"),
)


def canonical_url(url: str) -> str:
    """Lower-cased scheme/host, no fragment, no trailing slash, no query —
    except YouTube's `v`, which is the identity of a watch URL."""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return (url or "").strip()
    host = (p.hostname or "").lower()
    if host.startswith("www.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    path = p.path.rstrip("/")
    query = ""
    if "youtube.com" in host and path == "/watch":
        v = parse_qs(p.query).get("v", [""])[0]
        query = f"?v={v}" if v else ""
    return f"{(p.scheme or 'https').lower()}://{host}{path}{query}"


def _external_id(entry: dict) -> Optional[str]:
    for f in _ID_FIELDS:
        v = entry.get(f)
        if v not in (None, ""):
            return str(v)
    url = entry.get("url") or ""
    host = (urlparse(url).hostname or "").lower() if url else ""
    if "youtube.com" in host:
        v = parse_qs(urlparse(url).query).get("v", [""])[0]
        if v:
            return v
    for pat in _URL_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def url_key(url: str) -> str:
    return "url:" + hashlib.sha256(canonical_url(url).encode()).hexdigest()[:32]


def item_keys(platform: str, entry: dict) -> list[str]:
    """[primary key, url-hash key]. The url-hash key is what history seeding
    can produce (stored jobs keep only the URL), so both are recorded."""
    keys = []
    ext = _external_id(entry)
    if ext:
        keys.append(f"{platform or 'unknown'}:{ext}")
    url = entry.get("url") or ""
    if url:
        uk = url_key(url)
        if uk not in keys:
            keys.append(uk)
    return keys


# ── Ledger operations ────────────────────────────────────────────────

def _touch(rc, key: str) -> None:
    # Keep only the newest LEDGER_MAX_ITEMS members and refresh the TTL.
    rc.zremrangebyrank(key, 0, -(LEDGER_MAX_ITEMS + 1))
    rc.expire(key, LEDGER_TTL_DAYS * 86400)


def _record(rc, key: str, keys: Iterable[str], now: float) -> None:
    keys = list(keys)
    if keys:
        rc.zadd(key, {k: now for k in keys}, nx=True)


def _is_known(rc, key: str, keys: list[str]) -> bool:
    return any(rc.zscore(key, k) is not None for k in keys)


def _history_urls(supabase, user_id: Optional[str], channel_url: str,
                  exclude_batch: Optional[str]) -> list[str]:
    """URLs of items this user's earlier runs of `channel_url` downloaded
    successfully. Child jobs created before task #6256 carry no user_id, only
    the batch of the channel placeholder row (which does carry user_id + the
    channel URL), so match through the batch."""
    if not user_id or not channel_url:
        return []
    res = (
        supabase.table("download_jobs")
        .select("batch_id")
        .eq("user_id", user_id)
        .eq("original_url", channel_url)
        .order("created_at", desc=True)
        .limit(60)
        .execute()
    )
    batch_ids = sorted({r.get("batch_id") for r in (res.data or [])
                        if r.get("batch_id") and r.get("batch_id") != exclude_batch})
    if not batch_ids:
        return []
    rows = (
        supabase.table("download_jobs")
        .select("original_url")
        .in_("batch_id", batch_ids)
        .eq("status", "success")
        .limit(LEDGER_MAX_ITEMS)
        .execute()
    )
    return [r["original_url"] for r in (rows.data or [])
            if r.get("original_url") and r.get("original_url") != channel_url]


def select_new_items(rc, *, schedule_id: str, platform: str, entries: list[dict],
                     max_items: int, is_first_run: bool, supabase=None,
                     user_id: Optional[str] = None, channel_url: str = "",
                     batch_id: Optional[str] = None) -> tuple[list[dict], dict]:
    """Return (entries to download now, stats) for one scheduled channel run
    and record the returned entries as handled.

    Each selected entry is claimed with ZADD NX on its primary key, so two
    overlapping runs of the same schedule cannot both download one item.
    Raises on Redis errors — the caller fails the run closed (downloads
    nothing) rather than falling back to re-downloading.
    """
    stats = {"mode": MODE_LEDGER, "discovered": len(entries), "known": 0,
             "selected": 0, "deferred": 0, "seeded": 0}
    if not dedupe_enabled():
        picked = entries[:max_items]
        stats.update(mode=MODE_DISABLED, selected=len(picked),
                     deferred=max(0, len(entries) - len(picked)))
        return picked, stats

    key = ledger_key(schedule_id)
    now = time.time()

    if not rc.exists(key):
        if is_first_run:
            stats["mode"] = MODE_FIRST_RUN
        else:
            # A schedule that ran before this ledger existed (or whose ledger
            # expired / was evicted). Rebuild it from the owner's completed
            # jobs; trust that only when it overlaps what the channel lists
            # now — a URL-form mismatch must not look like "everything new".
            urls: list[str] = []
            if supabase is not None:
                try:
                    urls = _history_urls(supabase, user_id, channel_url, batch_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[ScheduleLedger] history seed failed for %s: %s",
                                   schedule_id, type(e).__name__)
                    urls = []
            seeded = {url_key(u) for u in urls}
            overlap = any(set(item_keys(platform, e)) & seeded for e in entries)
            if seeded and overlap:
                _record(rc, key, seeded, now)
                stats.update(mode=MODE_SEEDED, seeded=len(seeded))
            else:
                # Baseline: everything listed now counts as handled; the next
                # run downloads only what appears after this one.
                for e in entries:
                    _record(rc, key, item_keys(platform, e), now)
                _touch(rc, key)
                stats.update(mode=MODE_BASELINE, known=len(entries))
                return [], stats

    picked: list[dict] = []
    for e in entries:
        keys = item_keys(platform, e)
        if not keys:
            continue
        if _is_known(rc, key, keys):
            stats["known"] += 1
            continue
        if len(picked) >= max_items:
            stats["deferred"] += 1   # not recorded → eligible next run
            continue
        if not rc.zadd(key, {keys[0]: now}, nx=True):
            stats["known"] += 1      # claimed by an overlapping run
            continue
        _record(rc, key, keys[1:], now)
        picked.append(e)
    _touch(rc, key)
    stats["selected"] = len(picked)
    return picked, stats
