"""
Channel Watch ("Theo dõi kênh") — Phase 33A core, task #6257.

A user watches a channel; the server scans it on a timetable and tells the
user (one Web Push digest per run) when new videos appear.

Data (database/migrations/038_channel_watch.sql, service role only):
  watch_sources        one row per (platform, channel) shared by all watchers
  watch_subscriptions  user ↔ source; baseline_completed_at NULL = gets no
                       deliveries yet
  watch_items_seen     ledger of items ever listed for a source; the unique
                       key (source_id, external_item_id) makes a retried or
                       overlapping scan insert an item once
  watch_deliveries     subscription ↔ item; unique (subscription_id, item_id)
                       → one notification per item per subscriber
  watch_scan_runs      one row per scan attempt (cost/latency/outcome)

Scan of a source (scan_source):
  flags/kill switch/platform enabled → Redis lock watch:scanlock:{id} →
  newest K items via the platform's FREE listing (TikTok: TikWM user/posts,
  the same discovery bulk channel downloads use; never the paid China access
  layer — those platforms are not implemented here) → diff against the ledger
  → if all K are new, up to WATCH_GAP_FOLLOWUP_MAX_PAGES bigger listings,
  possible_gap when still no overlap → insert new items (state 'baseline'
  when the source was never scanned, else 'new') → deliveries for every
  ACTIVE subscription whose baseline is complete → subscriptions still
  without a baseline get one now (they receive nothing for these items) →
  source timetable (interval = shortest entitlement of its active
  subscribers, bounded by the platform floor and WATCH_MAX_INTERVAL_SEC,
  ±12 % jitter; failures back off exponentially up to the max; degraded
  after N failures) → watch_scan_runs row.

Delivery (deliver_pending): pending deliveries grouped per subscription →
ONE push "N video mới từ <kênh>" per subscription per run, never more often
than the subscription's own delivery_interval_sec (a Free watcher of a
channel that a Pro watcher makes scan every 6 h still hears at most daily).
Rows are claimed pending→sending with a conditional update before the push,
so two overlapping delivery runs cannot notify the same row twice.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote, urlsplit

from app.core import watch_config as cfg

logger = logging.getLogger(__name__)

MODES = ("notify_only", "one_tap")
SUB_LIVE = ("active", "paused")           # statuses that occupy a slot
SCANNABLE = ("active", "degraded")

# ── Errors + user messages ───────────────────────────────────────────

_MESSAGES = {
    "login_required": "Vui lòng đăng nhập để theo dõi kênh.",  # wording: BA review
    "watch_disabled": "Tính năng theo dõi kênh đang tạm tắt.",  # wording: BA review
    "admin_only": "Tính năng theo dõi kênh đang thử nghiệm nội bộ, chưa mở cho tài khoản của bạn.",  # wording: BA review
    "source_limit_reached": "Bạn đã theo dõi tối đa {limit} kênh. Hãy bỏ theo dõi một kênh hoặc nâng cấp Pro để theo dõi thêm.",  # wording: BA review
    "replace_cooldown_active": "Bạn vừa bỏ theo dõi một kênh. Bạn có thể thêm kênh mới sau {wait}.",  # wording: BA review
    "global_free_cap_reached": "Số chỗ theo dõi kênh miễn phí tạm thời đã hết. Vui lòng thử lại sau hoặc nâng cấp Pro.",  # wording: BA review
    "ip_daily_cap_reached": "Hôm nay mạng của bạn đã thêm đủ số kênh cho phép. Vui lòng thử lại vào ngày mai.",  # wording: BA review
    "email_not_verified": "Vui lòng xác thực email trước khi theo dõi kênh.",  # wording: BA review
    "unsupported_platform": "Nền tảng này chưa hỗ trợ theo dõi kênh.",  # wording: BA review
    "platform_not_enabled": "Theo dõi kênh {platform} đang tạm tắt.",  # wording: BA review
    "unsupported_url": "Liên kết chưa đúng. Hãy dán liên kết trang kênh, ví dụ https://www.tiktok.com/@tenkenh",  # wording: BA review
    "private_or_login_required": "Không đọc được video công khai của kênh này (kênh riêng tư, không tồn tại hoặc chưa có video).",  # wording: BA review
    "not_found": "Không tìm thấy kênh đang theo dõi.",  # wording: BA review
    "invalid_mode": "Chế độ nhận tin không hợp lệ.",  # wording: BA review
    "invalid_status": "Trạng thái không hợp lệ.",  # wording: BA review
    "invalid_since": "Mốc thời gian không hợp lệ.",  # wording: BA review
}

PLATFORM_LABELS = {"tiktok": "TikTok"}


class WatchError(Exception):
    """A refusal with a machine code (error_code) and a Vietnamese message."""

    def __init__(self, code: str, status: int = 400, *, message_key: Optional[str] = None, **fmt: Any):
        self.code = code
        self.status = status
        self.extra = {k: v for k, v in fmt.items() if k == "retry_after_sec"}
        tmpl = _MESSAGES.get(message_key or code, code)
        try:
            self.message = tmpl.format(**fmt)
        except (KeyError, IndexError):
            self.message = tmpl
        super().__init__(f"{code}: {self.message}")

    def body(self) -> dict:
        return {"error_code": self.code, "message": self.message, "detail": self.message, **self.extra}


def _wait_text(seconds: float) -> str:
    seconds = max(60, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        return f"{h} giờ {m} phút"  # wording: BA review
    if h:
        return f"{h} giờ"  # wording: BA review
    return f"{m} phút"  # wording: BA review


# ── Time helpers ─────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ip_hash(ip: str) -> str:
    return hashlib.sha256(f"vidgrab-watch:{ip or ''}".encode()).hexdigest()[:32]


# ── Channel URL resolution ───────────────────────────────────────────

@dataclass
class ChannelRef:
    platform: str
    external_channel_id: str
    canonical_url: str
    display_name: str


# Hosts of platforms we recognise but cannot watch in this phase.
_KNOWN_UNSUPPORTED = (
    "youtube.com", "youtu.be", "douyin.com", "iesdouyin.com", "kuaishou.com",
    "xiaohongshu.com", "xhslink.com", "instagram.com", "facebook.com", "fb.watch",
    "x.com", "twitter.com", "threads.net", "threads.com", "bilibili.com", "b23.tv",
    "reddit.com", "pinterest.com", "pin.it", "soundcloud.com", "vimeo.com",
    "dailymotion.com", "twitch.tv", "snapchat.com", "likee.video", "weibo.com",
)
_TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com")
_TIKTOK_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")
_TIKTOK_PROFILE_RE = re.compile(r"^/@([A-Za-z0-9_.]{1,40})(?:/.*)?$")


def _host_of(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise WatchError("unsupported_url")
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise WatchError("unsupported_url")
    if parts.username or parts.password:
        raise WatchError("unsupported_url")
    return parts.hostname.lower().rstrip(".")


def platform_of_url(url: str) -> Optional[str]:
    """'tiktok' | 'other:<host>' (recognised, unsupported) | None (unknown)."""
    host = _host_of(url)
    if host in _TIKTOK_HOSTS or host in _TIKTOK_SHORT_HOSTS:
        return "tiktok"
    for h in _KNOWN_UNSUPPORTED:
        if host == h or host.endswith("." + h):
            return f"other:{h}"
    return None


def _resolve_short(url: str) -> str:
    from app.services.downloader import resolve_short_url  # network: one redirect
    return resolve_short_url(url)


def resolve_channel_url(url: str, *, resolve_short: Callable[[str], str] = None) -> ChannelRef:
    """Validate a pasted channel link. Raises WatchError(unsupported_url |
    unsupported_platform | platform_not_enabled)."""
    url = (url or "").strip()
    if not url or len(url) > 500:
        raise WatchError("unsupported_url")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    platform = platform_of_url(url)
    if platform is None:
        raise WatchError("unsupported_url")
    if platform not in cfg.IMPLEMENTED_PLATFORMS:
        raise WatchError("unsupported_platform")
    if not cfg.platform_enabled(platform):
        raise WatchError("platform_not_enabled", platform=PLATFORM_LABELS.get(platform, platform))

    # tiktok
    if _host_of(url) in _TIKTOK_SHORT_HOSTS:
        try:
            url = (resolve_short or _resolve_short)(url)
        except Exception:
            raise WatchError("unsupported_url")
        if _host_of(url) not in _TIKTOK_HOSTS:
            raise WatchError("unsupported_url")
    m = _TIKTOK_PROFILE_RE.match(urlsplit(url).path or "")
    if not m:
        raise WatchError("unsupported_url")
    username = m.group(1).rstrip(".").lower()
    if not username:
        raise WatchError("unsupported_url")
    return ChannelRef(
        platform="tiktok",
        external_channel_id=username,
        canonical_url=f"https://www.tiktok.com/@{username}",
        display_name=f"@{username}",
    )


# ── Item identity ────────────────────────────────────────────────────

_TIKTOK_VIDEO_RE = re.compile(r"/video/(\d{6,})")


def item_identity(platform: str, entry: dict) -> tuple[str, str]:
    """(external_item_id, canonical_item_url). TikTok: the numeric video id
    (entry "id" or /video/<id> in the URL); otherwise "url:<sha256>" of the
    canonical URL, so a provider without ids still dedupes."""
    from app.core.schedule_ledger import canonical_url, url_key

    url = (entry.get("url") or "").strip()
    ext = None
    if platform == "tiktok":
        raw = entry.get("id") or entry.get("video_id")
        if raw and str(raw).isdigit():
            ext = str(raw)
        else:
            m = _TIKTOK_VIDEO_RE.search(url)
            ext = m.group(1) if m else None
    if not ext:
        ext = url_key(url) if url else None
    return ext, (canonical_url(url) if url else "")


@dataclass
class Item:
    ext: str
    url: str
    title: str
    published_at: Optional[str]


def normalize_entries(platform: str, entries: list) -> list[Item]:
    out: list[Item] = []
    seen: set[str] = set()
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        ext, curl = item_identity(platform, e)
        if not ext or ext in seen:
            continue
        seen.add(ext)
        pub = e.get("published_at")
        out.append(Item(ext=ext, url=curl,  # canonical: no query (no signed params)
                        title=(e.get("title") or "")[:300],
                        published_at=pub if isinstance(pub, str) else None))
    return out


# ── Intervals ────────────────────────────────────────────────────────

def source_interval_sec(platform: str, sub_intervals: list) -> int:
    """Shortest entitlement among active subscribers (best entitled), never
    below the platform floor nor above WATCH_MAX_INTERVAL_SEC."""
    vals = [int(v) for v in sub_intervals if v]
    base = min(vals) if vals else cfg.min_interval_sec("free")
    return min(max(base, cfg.platform_floor_sec(platform)), cfg.max_interval_sec())


def jittered_delay(interval: int, rnd: Callable[[], float] = random.random) -> int:
    factor = 1.0 - cfg.JITTER + 2 * cfg.JITTER * rnd()
    return min(int(interval * factor), cfg.max_interval_sec())


def failure_delay(interval: int, failures: int, rnd: Callable[[], float] = random.random) -> int:
    """1st failure → one normal interval, then doubling, capped at max."""
    n = max(1, int(failures))
    raw = interval * (2 ** min(n - 1, 16))
    return min(jittered_delay(min(raw, cfg.max_interval_sec()), rnd), cfg.max_interval_sec())


# ── Providers (FREE listings only) ───────────────────────────────────

class ListingError(Exception):
    def __init__(self, category: str, *, permanent: bool):
        super().__init__(category)
        self.category = category
        self.permanent = permanent


def _list_tiktok(source: dict, limit: int) -> list:
    from app.services.downloader import TikWMListingError, scrape_tiktok_user_posts

    try:
        res = scrape_tiktok_user_posts(source["canonical_url"], max_videos=limit, raise_on_error=True)
    except TikWMListingError as e:
        raise ListingError("not_found_or_private" if e.permanent else "network", permanent=e.permanent)
    except Exception as e:  # httpx/JSON errors, bad URL
        logger.info("[Watch] tiktok listing error %s", type(e).__name__)
        raise ListingError("network", permanent=False)
    entries = res.get("entries") or []
    if not entries:
        raise ListingError("empty", permanent=True)
    return entries


PROVIDERS: dict[str, tuple[str, Callable[[dict, int], list]]] = {
    "tiktok": ("tikwm", _list_tiktok),
}


# ── DB helpers ───────────────────────────────────────────────────────

def _sb():
    from app.core.database import get_service_client
    return get_service_client()


def _rc():
    from app.core.redis_client import get_redis
    return get_redis()


def _one(res) -> Optional[dict]:
    data = getattr(res, "data", None) or []
    if isinstance(data, dict):
        return data
    return data[0] if data else None


def get_source(sb, source_id: str) -> Optional[dict]:
    return _one(sb.table("watch_sources").select("*").eq("id", source_id).limit(1).execute())


def _known_ids(sb, source_id: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = (sb.table("watch_items_seen").select("external_item_id")
            .eq("source_id", source_id).in_("external_item_id", ids).execute()).data or []
    return {r["external_item_id"] for r in rows}


def _active_subs(sb, source_id: str) -> list[dict]:
    return (sb.table("watch_subscriptions").select("*")
            .eq("source_id", source_id).eq("status", "active").execute()).data or []


def _scan_run(sb, row: dict) -> None:
    try:
        sb.table("watch_scan_runs").insert(row).execute()
    except Exception as e:
        logger.warning("[Watch] scan run row not written: %s", type(e).__name__)


# ── Scan ─────────────────────────────────────────────────────────────

def _lock_key(source_id: str) -> str:
    return f"watch:scanlock:{source_id}"


def scan_source(source_id: str, *, now: Optional[datetime] = None, sb=None, rc=None,
                trigger: str = "beat") -> dict:
    """Scan one source. Never raises for expected conditions; returns a dict
    with "outcome"."""
    if not cfg.scanning_allowed():
        return {"outcome": "disabled"}
    sb = sb or _sb()
    rc = rc or _rc()
    now = now or utcnow()
    src = get_source(sb, source_id)
    if not src:
        return {"outcome": "missing"}
    if src.get("status") not in SCANNABLE:
        return {"outcome": "inactive"}
    if not cfg.platform_enabled(src.get("platform") or ""):
        return {"outcome": "platform_not_enabled"}

    key, token = _lock_key(source_id), uuid.uuid4().hex
    try:
        got = rc.set(key, token, nx=True, ex=cfg.scan_lock_ttl_sec())
    except Exception as e:
        logger.warning("[Watch] scan lock unavailable (%s) — skipping %s", type(e).__name__, source_id)
        return {"outcome": "lock_unavailable"}
    if not got:
        return {"outcome": "locked"}
    try:
        return _scan_locked(sb, src, now, trigger)
    finally:
        try:
            if rc.get(key) == token:
                rc.delete(key)
        except Exception:
            pass


def _scan_locked(sb, src: dict, now: datetime, trigger: str) -> dict:
    sid = src["id"]
    platform = src["platform"]
    subs = _active_subs(sb, sid)
    if not subs:
        sb.table("watch_sources").update({"status": "idle", "subscriber_count": 0}).eq("id", sid).execute()
        return {"outcome": "no_subscribers"}

    provider_name, provider = PROVIDERS.get(platform, ("none", None))
    baseline_mode = src.get("last_scan_at") is None
    k = cfg.items_per_scan()
    interval = source_interval_sec(platform, [s.get("delivery_interval_sec") for s in subs])
    t0 = time.monotonic()
    requested = k

    try:
        if provider is None:
            raise ListingError("platform_not_supported", permanent=True)
        items = normalize_entries(platform, provider(src, k))
    except ListingError as e:
        return _record_failure(sb, src, now, interval, e, provider_name, requested, t0, subs)

    known = _known_ids(sb, sid, [i.ext for i in items])
    new = [i for i in items if i.ext not in known]

    possible_gap = False
    if not baseline_mode and items and len(new) == len(items) and len(items) >= k:
        overlap = False
        max_pages = cfg.gap_followup_max_pages()
        page = 0
        while page < max_pages:
            page += 1
            want = k * (page + 1)
            requested += want
            try:
                more = normalize_entries(platform, provider(src, want))
            except ListingError:
                break
            have = {i.ext for i in items}
            extra = [i for i in more if i.ext not in have]
            if not extra:
                overlap = True          # channel exhausted: nothing older to miss
                break
            known_extra = _known_ids(sb, sid, [i.ext for i in extra])
            items.extend(extra)
            new.extend(i for i in extra if i.ext not in known_extra)
            if known_extra or len(more) < want:
                overlap = True
                break
        possible_gap = not overlap

    inserted: list[dict] = []
    if new:
        rows = [{
            "source_id": sid,
            "external_item_id": i.ext,
            "canonical_item_url": i.url,
            "title": i.title,
            "published_at": i.published_at,
            "discovered_at": _iso(now),
            "state": "baseline" if baseline_mode else "new",
        } for i in new]
        res = (sb.table("watch_items_seen")
               .upsert(rows, on_conflict="source_id,external_item_id", ignore_duplicates=True)
               .execute())
        inserted = res.data or []

    deliveries = 0
    if inserted and not baseline_mode:
        eligible = [s for s in subs if s.get("baseline_completed_at")]
        drows = [{"subscription_id": s["id"], "item_id": it["id"], "status": "pending",
                  "created_at": _iso(now)}
                 for s in eligible for it in inserted]
        if drows:
            res = (sb.table("watch_deliveries")
                   .upsert(drows, on_conflict="subscription_id,item_id", ignore_duplicates=True)
                   .execute())
            deliveries = len(res.data or [])

    pending_baseline = [s["id"] for s in subs if not s.get("baseline_completed_at")]
    if pending_baseline:
        (sb.table("watch_subscriptions").update({"baseline_completed_at": _iso(now)})
         .in_("id", pending_baseline).execute())

    try:
        old_score = float(src.get("activity_score") or 0)
    except (TypeError, ValueError):
        old_score = 0.0
    sb.table("watch_sources").update({
        "status": "active",
        "scan_provider": provider_name,
        "last_scan_at": _iso(now),
        "next_scan_at": _iso(now + timedelta(seconds=jittered_delay(interval))),
        "scan_interval_sec": interval,
        "activity_score": round(0.7 * old_score + 0.3 * len(inserted), 3),
        "consecutive_failures": 0,
        "last_failure_category": None,
        "subscriber_count": len(subs),
    }).eq("id", sid).execute()

    outcome = "baseline" if baseline_mode else "ok"
    _scan_run(sb, {
        "source_id": sid, "provider": provider_name,
        "items_requested": requested, "items_returned": len(items), "new_items": len(inserted),
        "possible_gap": possible_gap, "estimated_cost_usd": 0, "actual_cost_usd": 0,
        "latency_ms": int((time.monotonic() - t0) * 1000), "outcome": outcome,
        "failure_category": None, "created_at": _iso(now),
    })
    logger.info("[Watch] scan %s (%s) %s: returned=%d new=%d deliveries=%d gap=%s",
                sid, trigger, outcome, len(items), len(inserted), deliveries, possible_gap)
    return {"outcome": outcome, "items_returned": len(items), "new_items": len(inserted),
            "deliveries": deliveries, "possible_gap": possible_gap,
            "baseline_completed": len(pending_baseline)}


def _record_failure(sb, src, now, interval, err: ListingError, provider_name, requested, t0, subs) -> dict:
    failures = int(src.get("consecutive_failures") or 0) + 1
    status = "degraded" if failures >= cfg.degrade_after_failures() else src.get("status", "active")
    sb.table("watch_sources").update({
        "status": status,
        "consecutive_failures": failures,
        "last_failure_category": err.category,
        "next_scan_at": _iso(now + timedelta(seconds=failure_delay(interval, failures))),
        "scan_interval_sec": interval,
        "subscriber_count": len(subs),
    }).eq("id", src["id"]).execute()
    _scan_run(sb, {
        "source_id": src["id"], "provider": provider_name,
        "items_requested": requested, "items_returned": 0, "new_items": 0,
        "possible_gap": False, "estimated_cost_usd": 0, "actual_cost_usd": 0,
        "latency_ms": int((time.monotonic() - t0) * 1000), "outcome": "failed",
        "failure_category": err.category, "created_at": _iso(now),
    })
    logger.info("[Watch] scan %s failed: %s (failures=%d)", src["id"], err.category, failures)
    return {"outcome": "failed", "failure_category": err.category, "permanent": err.permanent,
            "consecutive_failures": failures}


# ── Beat tick ────────────────────────────────────────────────────────

def due_sources(sb, now: datetime, limit: int) -> list[dict]:
    """Due, scannable, platform-enabled sources with ≥1 active subscription,
    oldest first, at most `limit`."""
    rows = (sb.table("watch_sources").select("id, platform, status, next_scan_at")
            .in_("status", list(SCANNABLE)).lte("next_scan_at", _iso(now))
            .order("next_scan_at").limit(limit).execute()).data or []
    rows = [r for r in rows if cfg.platform_enabled(r.get("platform") or "")]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    live = (sb.table("watch_subscriptions").select("source_id")
            .in_("source_id", ids).eq("status", "active").execute()).data or []
    with_subs = {r["source_id"] for r in live}
    # Due but nobody active (all paused): park as idle so these rows do not
    # crowd the due window forever; resuming a subscription reactivates it.
    orphans = [r["id"] for r in rows if r["id"] not in with_subs]
    if orphans:
        sb.table("watch_sources").update({"status": "idle", "subscriber_count": 0}).in_("id", orphans).execute()
    return [r for r in rows if r["id"] in with_subs]


def tick(*, now: Optional[datetime] = None, sb=None, rc=None,
         enqueue_scan: Callable[[str], Any], enqueue_delivery: Callable[[], Any]) -> dict:
    if not cfg.scanning_allowed():
        return {"enqueued": 0, "skipped": "disabled"}
    sb = sb or _sb()
    rc = rc or _rc()
    now = now or utcnow()
    enqueued = 0
    batch = cfg.scan_batch_per_minute()
    # Over-fetch: sources still queued from an earlier tick are skipped below
    # and must not use up this tick's batch.
    for row in due_sources(sb, now, batch * 3):
        if enqueued >= batch:
            break
        # Do not enqueue the same source again while its scan is queued.
        try:
            if not rc.set(f"watch:enqueued:{row['id']}", "1", nx=True, ex=cfg.scan_lock_ttl_sec()):
                continue
        except Exception:
            return {"enqueued": enqueued, "skipped": "redis_unavailable"}
        enqueue_scan(row["id"])
        enqueued += 1
    pending = (sb.table("watch_deliveries").select("id").eq("status", "pending").limit(1).execute()).data
    if pending:
        enqueue_delivery()
    return {"enqueued": enqueued, "deliveries_pending": bool(pending)}


def release_enqueued(rc, source_id: str) -> None:
    try:
        rc.delete(f"watch:enqueued:{source_id}")
    except Exception:
        pass


# ── Delivery ─────────────────────────────────────────────────────────

def deep_link(sub: dict, items: list[dict]) -> str:
    if sub.get("mode") == "one_tap" and len(items) == 1 and items[0].get("canonical_item_url"):
        return "/share-target?url=" + quote(items[0]["canonical_item_url"], safe="")
    return "/watch"


def build_payload(sub: dict, source: dict, items: list[dict]) -> dict:
    name = (source or {}).get("display_name") or (source or {}).get("canonical_url") or "kênh"
    n = len(items)
    body = f"{n} video mới từ {name}"  # wording: BA review
    if n == 1 and items[0].get("title"):
        body += f": {items[0]['title'][:80]}"
    return {
        "title": "VidGrab — Theo dõi kênh",  # wording: BA review
        "body": body,
        "url": deep_link(sub, items),
        "tag": f"watch-{sub['id']}",
    }


def deliver_pending(*, now: Optional[datetime] = None, sb=None, source_id: Optional[str] = None,
                    send: Optional[Callable[[str, dict], int]] = None, limit: int = 500) -> dict:
    if not cfg.scanning_allowed():
        return {"skipped": "disabled"}
    sb = sb or _sb()
    now = now or utcnow()
    if send is None:
        from app.core.push_sender import send_push as send

    q = sb.table("watch_deliveries").select("id, subscription_id, item_id, status, created_at").eq("status", "pending")
    if source_id:
        sub_ids = [r["id"] for r in (sb.table("watch_subscriptions").select("id")
                                     .eq("source_id", source_id).execute()).data or []]
        if not sub_ids:
            return {"notified": 0}
        q = q.in_("subscription_id", sub_ids)
    rows = (q.order("created_at").limit(limit).execute()).data or []
    if not rows:
        return {"notified": 0, "throttled": 0, "skipped": 0}

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["subscription_id"], []).append(r)
    subs = {s["id"]: s for s in (sb.table("watch_subscriptions").select("*")
                                 .in_("id", list(groups)).execute()).data or []}
    item_ids = list({r["item_id"] for r in rows})
    items = {i["id"]: i for i in (sb.table("watch_items_seen").select("*")
                                  .in_("id", item_ids).execute()).data or []}
    source_ids = list({s.get("source_id") for s in subs.values() if s.get("source_id")})
    sources = {s["id"]: s for s in ((sb.table("watch_sources").select("*")
                                     .in_("id", source_ids).execute()).data or [] if source_ids else [])}

    stats = {"notified": 0, "throttled": 0, "skipped": 0, "pushes": 0}
    for sub_id, dels in groups.items():
        ids = [d["id"] for d in dels]
        sub = subs.get(sub_id)
        if not sub or sub.get("status") != "active":
            (sb.table("watch_deliveries").update({"status": "skipped"})
             .in_("id", ids).eq("status", "pending").execute())
            stats["skipped"] += len(ids)
            continue
        interval = int(sub.get("delivery_interval_sec") or cfg.min_interval_sec("free"))
        last = _parse(sub.get("last_notified_at"))
        if last and (now - last).total_seconds() < interval:
            stats["throttled"] += len(ids)
            continue
        claimed = (sb.table("watch_deliveries").update({"status": "sending"})
                   .in_("id", ids).eq("status", "pending").execute()).data or []
        if not claimed:
            continue
        claimed_ids = [c["id"] for c in claimed]
        its = [items[c["item_id"]] for c in claimed if c.get("item_id") in items]
        its.sort(key=lambda i: str(i.get("published_at") or i.get("discovered_at") or ""), reverse=True)
        payload = build_payload(sub, sources.get(sub.get("source_id")), its)
        try:
            sent = int(send(sub["user_id"], payload) or 0)
        except Exception as e:
            logger.warning("[Watch] push failed for subscription %s: %s", sub_id, type(e).__name__)
            sent = 0
        final = "notified" if sent > 0 else "no_push_target"
        (sb.table("watch_deliveries").update({"status": final, "notified_at": _iso(now)})
         .in_("id", claimed_ids).execute())
        sb.table("watch_subscriptions").update({"last_notified_at": _iso(now)}).eq("id", sub_id).execute()
        stats["notified"] += len(claimed_ids)
        stats["pushes"] += 1
    return stats


# ── Subscriptions (API side) ─────────────────────────────────────────

def _count(sb, table: str, build) -> int:
    res = build(sb.table(table).select("id", count="exact")).execute()
    c = getattr(res, "count", None)
    return int(c) if c is not None else len(res.data or [])


def user_used(sb, user_id: str) -> int:
    return _count(sb, "watch_subscriptions",
                  lambda q: q.eq("user_id", user_id).in_("status", list(SUB_LIVE)))


def free_sources_total(sb) -> int:
    return _count(sb, "watch_subscriptions",
                  lambda q: q.eq("tier", "free").in_("status", list(SUB_LIVE)))


def _ip_key(iph: str, now: datetime) -> str:
    return f"watch:ipday:{iph}:{now.strftime('%Y%m%d')}"


def _ip_used(rc, iph: str, now: datetime) -> int:
    try:
        return int(rc.get(_ip_key(iph, now)) or 0)
    except Exception as e:
        # Fail open: the per-account and global caps are DB-backed.
        logger.warning("[Watch] IP cap unavailable (%s)", type(e).__name__)
        return 0


def _ip_add(rc, iph: str, now: datetime, delta: int) -> None:
    try:
        key = _ip_key(iph, now)
        rc.incrby(key, delta)
        rc.expire(key, 2 * 86400)
    except Exception:
        pass


def add_source(*, user_id: str, tier: str, is_admin: bool, email_verified: Optional[bool],
               url: str, mode: str = "one_tap", ip: str = "", now: Optional[datetime] = None,
               sb=None, rc=None, resolve_short: Callable[[str], str] = None) -> dict:
    """Validate + create (or reactivate) a subscription, then run the
    baseline scan (bounded, under the source lock). Raises WatchError."""
    sb = sb or _sb()
    rc = rc or _rc()
    now = now or utcnow()
    mode = mode or "one_tap"
    if mode not in MODES:
        raise WatchError("invalid_mode")
    eff_tier = "pro" if is_admin else (tier or "free")
    paid = cfg.is_paid(eff_tier)
    if not paid and cfg.free_requires_verified_email() and email_verified is False:
        raise WatchError("email_not_verified", 403)

    ref = resolve_channel_url(url, resolve_short=resolve_short)

    source = _one(sb.table("watch_sources").select("*").eq("platform", ref.platform)
                  .eq("external_channel_id", ref.external_channel_id).limit(1).execute())
    existing = None
    if source:
        existing = _one(sb.table("watch_subscriptions").select("*").eq("user_id", user_id)
                        .eq("source_id", source["id"]).limit(1).execute())
        if existing and existing.get("status") in SUB_LIVE:
            return {"subscription": existing, "source": source, "already": True,
                    "baseline": "done" if existing.get("baseline_completed_at") else "pending"}

    limit = cfg.max_sources(eff_tier)
    if user_used(sb, user_id) >= limit:
        raise WatchError("source_limit_reached", 403, limit=limit)

    cooldown = cfg.replace_cooldown_sec(eff_tier)
    if cooldown > 0:
        last_removed = _one(sb.table("watch_subscriptions").select("removed_at")
                            .eq("user_id", user_id).eq("status", "removed")
                            .order("removed_at", desc=True).limit(1).execute())
        removed_at = _parse((last_removed or {}).get("removed_at"))
        if removed_at:
            left = cooldown - (now - removed_at).total_seconds()
            if left > 0:
                raise WatchError("replace_cooldown_active", 429, wait=_wait_text(left),
                                 retry_after_sec=int(left))

    if not paid and free_sources_total(sb) >= cfg.global_free_cap():
        raise WatchError("global_free_cap_reached", 403)

    iph = ip_hash(ip)
    ip_cap = cfg.ip_daily_new_sources()
    if not is_admin and ip_cap >= 0 and _ip_used(rc, iph, now) >= ip_cap:
        raise WatchError("ip_daily_cap_reached", 429)

    # ── create ──
    source_created = False
    if not source:
        row = {
            "platform": ref.platform, "external_channel_id": ref.external_channel_id,
            "canonical_url": ref.canonical_url, "display_name": ref.display_name,
            "status": "active", "scan_provider": PROVIDERS.get(ref.platform, ("none",))[0],
            "next_scan_at": _iso(now), "subscriber_count": 0, "created_at": _iso(now),
        }
        try:
            source = _one(sb.table("watch_sources").insert(row).execute())
            source_created = True
        except Exception:
            # Lost a race with another request for the same channel.
            source = _one(sb.table("watch_sources").select("*").eq("platform", ref.platform)
                          .eq("external_channel_id", ref.external_channel_id).limit(1).execute())
            if not source:
                raise
    elif source.get("status") == "idle":
        sb.table("watch_sources").update({"status": "active", "next_scan_at": _iso(now)}).eq("id", source["id"]).execute()
        source["status"] = "active"

    interval = cfg.min_interval_sec(eff_tier)
    fields = {
        "mode": mode, "status": "active", "baseline_completed_at": None,
        "delivery_interval_sec": interval, "tier": eff_tier, "removed_at": None,
        "created_ip_hash": iph, "last_notified_at": None,
    }
    if existing:  # removed earlier → reactivate the same row (unique user/source)
        previous = dict(existing)
        sub = _one(sb.table("watch_subscriptions").update(fields).eq("id", existing["id"]).execute())
    else:
        previous = None
        sub = _one(sb.table("watch_subscriptions").insert(
            {"user_id": user_id, "source_id": source["id"], "created_at": _iso(now), **fields}).execute())
    _ip_add(rc, iph, now, 1)

    scan = scan_source(source["id"], now=now, sb=sb, rc=rc, trigger="baseline")
    if scan.get("outcome") == "failed" and scan.get("permanent"):
        # Undo: nothing stays behind for a channel we cannot read.
        if previous:
            sb.table("watch_subscriptions").update({
                "status": "removed", "removed_at": previous.get("removed_at"),
                "mode": previous.get("mode"), "baseline_completed_at": previous.get("baseline_completed_at"),
                "delivery_interval_sec": previous.get("delivery_interval_sec"), "tier": previous.get("tier"),
            }).eq("id", sub["id"]).execute()
        else:
            sb.table("watch_subscriptions").delete().eq("id", sub["id"]).execute()
        if source_created:
            for t in ("watch_scan_runs", "watch_items_seen"):
                sb.table(t).delete().eq("source_id", source["id"]).execute()
            sb.table("watch_sources").delete().eq("id", source["id"]).execute()
        _ip_add(rc, iph, now, -1)
        raise WatchError("private_or_login_required", 422)

    refresh_subscriber_count(sb, source["id"])
    sub = _one(sb.table("watch_subscriptions").select("*").eq("id", sub["id"]).limit(1).execute()) or sub
    return {"subscription": sub, "source": get_source(sb, source["id"]) or source, "already": False,
            "baseline": "done" if sub.get("baseline_completed_at") else "pending",
            "scan_outcome": scan.get("outcome")}


def refresh_subscriber_count(sb, source_id: str) -> int:
    n = _count(sb, "watch_subscriptions", lambda q: q.eq("source_id", source_id).eq("status", "active"))
    upd: dict = {"subscriber_count": n}
    live = _count(sb, "watch_subscriptions", lambda q: q.eq("source_id", source_id).in_("status", list(SUB_LIVE)))
    src = get_source(sb, source_id)
    if src:
        if live == 0 and src.get("status") in SCANNABLE:
            upd["status"] = "idle"
    sb.table("watch_sources").update(upd).eq("id", source_id).execute()
    return n


def own_subscription(sb, user_id: str, sub_id: str) -> dict:
    sub = _one(sb.table("watch_subscriptions").select("*").eq("id", sub_id).eq("user_id", user_id).limit(1).execute())
    if not sub or sub.get("status") == "removed":
        raise WatchError("not_found", 404)
    return sub


def update_subscription(sb, user_id: str, sub_id: str, *, status: Optional[str] = None,
                        mode: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    sub = own_subscription(sb, user_id, sub_id)
    upd: dict = {}
    if mode is not None:
        if mode not in MODES:
            raise WatchError("invalid_mode")
        upd["mode"] = mode
    if status is not None:
        if status not in ("active", "paused"):
            raise WatchError("invalid_status")
        upd["status"] = status
    if upd:
        sb.table("watch_subscriptions").update(upd).eq("id", sub_id).execute()
        if "status" in upd and sub.get("source_id"):
            if status == "active":
                src = get_source(sb, sub["source_id"])
                if src and src.get("status") == "idle":
                    sb.table("watch_sources").update({"status": "active", "next_scan_at": _iso(now or utcnow())}).eq("id", src["id"]).execute()
            refresh_subscriber_count(sb, sub["source_id"])
    return own_subscription(sb, user_id, sub_id)


def remove_subscription(sb, user_id: str, sub_id: str, *, now: Optional[datetime] = None) -> None:
    sub = own_subscription(sb, user_id, sub_id)
    now = now or utcnow()
    sb.table("watch_subscriptions").update({"status": "removed", "removed_at": _iso(now)}).eq("id", sub_id).execute()
    (sb.table("watch_deliveries").update({"status": "skipped"})
     .eq("subscription_id", sub_id).eq("status", "pending").execute())
    if sub.get("source_id"):
        refresh_subscriber_count(sb, sub["source_id"])


def list_subscriptions(sb, user_id: str, *, now: Optional[datetime] = None) -> list[dict]:
    now = now or utcnow()
    subs = [s for s in (sb.table("watch_subscriptions").select("*").eq("user_id", user_id)
                        .order("created_at", desc=True).execute()).data or []
            if s.get("status") in SUB_LIVE]
    if not subs:
        return []
    sids = list({s["source_id"] for s in subs if s.get("source_id")})
    sources = {s["id"]: s for s in (sb.table("watch_sources").select("*").in_("id", sids).execute()).data or []}
    since = _iso(now - timedelta(days=7))
    recent = (sb.table("watch_items_seen").select("id, source_id, discovered_at, state")
              .in_("source_id", sids).gte("discovered_at", since).execute()).data or []
    out = []
    for s in subs:
        src = sources.get(s.get("source_id")) or {}
        base = _parse(s.get("baseline_completed_at"))
        n_new = sum(1 for i in recent if i.get("source_id") == s.get("source_id")
                    and i.get("state") == "new" and base and (_parse(i.get("discovered_at")) or now) > base)
        out.append(subscription_view(s, src, n_new))
    return out


def subscription_view(sub: dict, src: dict, new_items_7d: int = 0) -> dict:
    return {
        "id": sub.get("id"),
        "mode": sub.get("mode"),
        "status": sub.get("status"),
        "created_at": sub.get("created_at"),
        "baseline_completed_at": sub.get("baseline_completed_at"),
        "delivery_interval_sec": sub.get("delivery_interval_sec"),
        "last_notified_at": sub.get("last_notified_at"),
        "new_items_7d": new_items_7d,
        "source": {
            "id": src.get("id"),
            "platform": src.get("platform"),
            "display_name": src.get("display_name"),
            "canonical_url": src.get("canonical_url"),
            "status": src.get("status"),
            "last_scan_at": src.get("last_scan_at"),
            "next_scan_at": src.get("next_scan_at"),
            "last_failure_category": src.get("last_failure_category"),
        },
    }


def list_items(sb, user_id: str, *, since: Optional[datetime] = None, now: Optional[datetime] = None,
               limit: int = 100) -> list[dict]:
    now = now or utcnow()
    since = since or (now - timedelta(days=7))
    subs = [s for s in (sb.table("watch_subscriptions").select("*").eq("user_id", user_id).execute()).data or []
            if s.get("status") in SUB_LIVE and s.get("baseline_completed_at")]
    if not subs:
        return []
    by_source = {s["source_id"]: s for s in subs}
    sources = {s["id"]: s for s in (sb.table("watch_sources").select("id, display_name, platform")
                                    .in_("id", list(by_source)).execute()).data or []}
    rows = (sb.table("watch_items_seen").select("*").in_("source_id", list(by_source))
            .eq("state", "new").gte("discovered_at", _iso(since))
            .order("discovered_at", desc=True).limit(limit).execute()).data or []
    out = []
    for r in rows:
        sub = by_source.get(r["source_id"])
        base = _parse(sub.get("baseline_completed_at")) if sub else None
        disc = _parse(r.get("discovered_at"))
        if not base or not disc or disc <= base:   # found by the scan that set the baseline
            continue
        src = sources.get(r["source_id"]) or {}
        out.append({
            "id": r["id"], "source_id": r["source_id"], "subscription_id": sub["id"],
            "channel": src.get("display_name"), "platform": src.get("platform"),
            "title": r.get("title"), "url": r.get("canonical_item_url"),
            "published_at": r.get("published_at"), "discovered_at": r.get("discovered_at"),
        })
    return out
