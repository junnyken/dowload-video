"""
Xiaohongshu / Kuaishou whole-channel (profile) listing (task #6171).

Same shape as the Douyin channel scan (channel_listing.py, task #6055): ONE
budgeted Apify run per profile, N videos, every listed video cached under the
URL the per-video job will resolve, so the job that follows does not pay for
that video again when the listing already carried a playable media URL.

Actors (docs/plans/PLAN-6171-xhs-kuaishou-channel.md, read 2026-10-08):

  kuaishou     natanielsantos~kuaishou-scraper (the single-video actor; its
               README lists kuaishou.com/profile/<id> as a start URL)
               input   {"startUrls": [profile], "profileSortBy": "latest",
                        "maxItems": N, "maxCommentsPerVideo": 0,
                        "maxRepliesPerComment": 0} + run-level maxItems=N
               output  the same video row as a single video (id, url, text,
                        playUrl, allPlayUrls, duration, thumb, authorMeta)
  xiaohongshu  vulnv~xiaohongshu-scraper, operation "user_notes"
               input   {"operation": "user_notes", "userUrls": [profile],
                        "maxItems": N} + run-level maxItems=N
               output  record_type "note": note_id, note_type ("video" |
                        "image"), note_url, xsec_token, title, cover_url,
                        video_url, video_duration, author_nickname

Owner rules (2026-10-08): signed-in users only; at most 20 videos per scan
(CHINA_ACCESS_<P>_CHANNEL_MAX may lower it); never more than what the user
may still download today; flags OFF by default; paid calls only through the
existing guards — master/platform/channel flags, kill switches, managed mode,
the Apify token pool, the per-platform and per-vendor spend ceilings (budget
pre-check + atomic reservation of the whole estimate, then settle).

Never logs the token, media URLs or the profile URL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlencode, urlparse

from app.services.china_platforms import apify_pool, budget_guard, cost_metrics, registry, request_cache, settings
from app.services.china_platforms.channel_listing import (
    MSG_BUSY,
    MSG_UPSTREAM,
    ChannelListing,
    ChannelListingError,
    ListedVideo,
)
from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaResult,
    RequestContext,
    current_context,
)
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from app.services.china_platforms.providers.managed_actor_provider import (
    MAX_SANE_DURATION_SEC,
    ActorSpec,
    ParsedActorItem,
)

logger = logging.getLogger("app.china_access")

OPERATION = "profile_listing"
PLATFORMS = settings.CHINA_CHANNEL_PLATFORMS
_NAME = {"xiaohongshu": "Xiaohongshu", "kuaishou": "Kuaishou"}
_EXAMPLE = {"xiaohongshu": "xiaohongshu.com/user/profile/…", "kuaishou": "kuaishou.com/profile/…"}
_PROVIDER = {"xiaohongshu": "apify_xiaohongshu_profile", "kuaishou": "apify_kuaishou_profile"}

# ── user-facing text (Vietnamese) ───────────────────────────────────────────
# wording: BA review
MSG_SIGN_IN = "Bạn cần đăng nhập để tải cả kênh {name}. Đăng nhập miễn phí rồi thử lại."
# wording: BA review
MSG_NOT_AVAILABLE = ("Tải cả kênh {name} tạm thời chưa mở. "
                     "Bạn vẫn tải được từng video {name} bằng cách dán link video.")
# wording: BA review
MSG_NO_ALLOWANCE = ("Bạn đã dùng hết lượt tải hôm nay nên chưa quét được kênh {name}. "
                    "Lượt mới được cộng lại lúc {reset} (giờ Việt Nam).")
# wording: BA review
MSG_BUDGET = "Hệ thống đã dùng hết ngân sách quét kênh {name} hôm nay. Vui lòng thử lại vào ngày mai."
# wording: BA review
MSG_EMPTY = ("Không tìm thấy video nào trên kênh này. Kênh có thể để riêng tư, "
             "chưa đăng video, hoặc link không phải trang cá nhân {name}.")
# wording: BA review
MSG_BAD_URL = "Link không phải trang cá nhân {name}. Hãy dán link dạng {example}."


def _msg(template: str, platform: str, **kw) -> str:
    return template.format(name=_NAME.get(platform, platform), example=_EXAMPLE.get(platform, ""), **kw)


# ── URL recognition (no network) ────────────────────────────────────────────

_XHS_PROFILE = re.compile(r"^/user/profile/([0-9a-fA-F]{24})/?$")
_KS_PROFILE = re.compile(r"^/profile/([A-Za-z0-9_-]{4,64})/?$")
# Share pages of a Kuaishou user (the mobile representation of a profile).
# ASSUMPTION: the id in /fw/user/<id> is the same id as in /profile/<id>.
_KS_FW_USER = re.compile(r"^/fw/user/([A-Za-z0-9_-]{4,64})/?$")
_XHS_HOSTS = ("xiaohongshu.com", "rednote.com")
_XHS_SHORT = ("xhslink.com", "xhslink.cn")
_KS_HOSTS = ("kuaishou.com", "kuaishou.cn")
_KS_SHARE_HOSTS = ("chenzhongtech.com", "chenzhongtech.cn", "gifshow.com")
_KS_SHORT = ("v.kuaishou.com", "v.kuaishou.cn")


def _parts(url: str) -> tuple[str, str]:
    try:
        p = urlparse((url or "").strip())
        port = p.port
    except ValueError:
        return "", ""
    if p.scheme not in ("http", "https") or p.username or p.password or port not in (None, 80, 443):
        return "", ""
    return (p.hostname or "").rstrip(".").lower(), p.path or "/"


def _under(host: str, bases) -> bool:
    return any(host == b or host.endswith("." + b) for b in bases)


def profile_id(platform: str, url: str) -> Optional[str]:
    """The profile id of a XHS / Kuaishou PROFILE URL, else None. A note
    opened from a profile (/user/profile/<uid>/<note>) is not a profile."""
    host, path = _parts(url)
    if not host:
        return None
    if platform == "xiaohongshu" and _under(host, _XHS_HOSTS):
        m = _XHS_PROFILE.match(path)
        return m.group(1).lower() if m else None
    if platform == "kuaishou":
        if _under(host, _KS_HOSTS):
            m = _KS_PROFILE.match(path)
            return m.group(1) if m else None
        if _under(host, _KS_SHARE_HOSTS):
            m = _KS_FW_USER.match(path)
            return m.group(1) if m else None
    return None


def is_short_link(platform: str, url: str) -> bool:
    host, path = _parts(url)
    if not host or path in ("", "/"):
        return False
    if platform == "xiaohongshu":
        return host in _XHS_SHORT
    if platform == "kuaishou":
        return host in _KS_SHORT
    return False


def platform_of(url: str) -> Optional[str]:
    """xiaohongshu / kuaishou for any link on those hosts (profile, short link
    or not), else None. Used to route a CHANNEL request: such a request can
    only be served here."""
    host, _path = _parts(url)
    if not host:
        return None
    if _under(host, _XHS_HOSTS) or host in _XHS_SHORT:
        return "xiaohongshu"
    if _under(host, _KS_HOSTS) or _under(host, _KS_SHARE_HOSTS):
        return "kuaishou"
    return None


def canonical_profile_url(platform: str, pid: str) -> str:
    if platform == "xiaohongshu":
        return f"https://www.xiaohongshu.com/user/profile/{pid}"
    return f"https://www.kuaishou.com/profile/{pid}"


async def resolve_profile(platform: str, url: str) -> str:
    """Profile id from a profile URL, or from a share link whose FIRST
    redirect points at a profile (one free request to the share-link host,
    not followed — adapters/short_links.py). Raises ChannelListingError."""
    pid = profile_id(platform, url)
    if pid:
        return pid
    if is_short_link(platform, url):
        from app.services.china_platforms.adapters.short_links import first_redirect  # noqa: PLC0415
        loc = await first_redirect(url.strip())
        pid = profile_id(platform, loc or "")
        if pid:
            return pid
    raise ChannelListingError(_msg(MSG_BAD_URL, platform), "unsupported_url")


# ── who may scan, and how many ──────────────────────────────────────────────

def is_signed_in(ctx: RequestContext) -> bool:
    key = ctx.requester_key or ""
    return bool(ctx.is_admin or key == "admin" or key.startswith("user:"))


def route_open(platform: str, ctx: Optional[RequestContext] = None) -> bool:
    """Flags on, no kill switch, managed mode allows this requester, a token
    in the pool. Budget is checked separately (reserve)."""
    ctx = ctx or current_context()
    try:
        if platform not in PLATFORMS:
            return False
        if not (settings.master_enabled() and settings.platform_env_enabled(platform)):
            return False
        if not settings.china_channel_enabled(platform):
            return False
        if budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(platform):
            return False
        if not settings.apify_configured():
            return False
        return registry.managed_allowed_for(registry.effective_managed_mode(platform), ctx)
    except Exception:  # noqa: BLE001
        return False


def listing_cap(platform: str, ctx: RequestContext) -> int:
    """min(20 or the env max, what this user may still download today on
    this platform — the daily allowance is also a total across platforms)."""
    from app.core import quotas  # noqa: PLC0415
    top = settings.china_channel_max(platform)
    if ctx.is_admin or ctx.requester_key == "admin":
        return top
    req = quotas.QuotaRequester.from_key(ctx.requester_key)
    if req is None:
        return 0
    left = quotas.BatchAllowance(req).remaining(platform)
    if left == -1:
        return top
    return max(0, min(left, top))


# ── actor specs and item parsing ────────────────────────────────────────────

def _http(u) -> str:
    u = u if isinstance(u, str) else ""
    if u.startswith("//"):
        u = "https:" + u
    return u if u.lower().startswith(("http://", "https://")) else ""


def parse_xhs_note(item: dict) -> ParsedActorItem:
    """One vulnv~xiaohongshu-scraper "note" row → ParsedActorItem (dataset
    schema of the actor). Image notes are not videos."""
    if not isinstance(item, dict):
        return ParsedActorItem(error="item is not an object")
    if item.get("error"):
        return ParsedActorItem(error=str(item.get("error"))[:200])
    kind = item.get("note_type")
    if isinstance(kind, str) and kind and kind.lower() != "video":
        return ParsedActorItem(error=f"not_video: note type {kind[:20]}")
    nid = str(item.get("note_id") or "").strip() or None
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    if not title.strip():
        desc = item.get("desc") if isinstance(item.get("desc"), str) else ""
        title = desc.strip()[:120]
    if not title.strip():
        title = f"Xiaohongshu {nid}" if nid else "Xiaohongshu Video"
    dur = item.get("video_duration")
    if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur <= 0:
        dur = None
    elif dur > MAX_SANE_DURATION_SEC:
        dur = dur / 1000.0     # documented in seconds; a ms value would be huge
    return ParsedActorItem(
        title=title.strip(),
        media_url=_http(item.get("video_url")),
        duration_sec=dur,
        thumbnail_url=_http(item.get("cover_url")) or None,
        uploader=item.get("author_nickname") if isinstance(item.get("author_nickname"), str) else None,
        media_id=nid,
    )


def _xhs_build_input(n: int):
    return lambda url: {"operation": "user_notes", "userUrls": [url], "maxItems": n}


def _ks_build_input(n: int):
    # Comments are a separately billed event: keep them at 0.
    return lambda url: {"startUrls": [url], "profileSortBy": "latest", "maxItems": n,
                        "maxCommentsPerVideo": 0, "maxRepliesPerComment": 0}


def profile_spec(platform: str, n: int) -> ActorSpec:
    if platform == "xiaohongshu":
        est = settings.apify_xiaohongshu_channel_est_cost_per_video_usd() * n
        floor = settings.apify_xiaohongshu_channel_min_max_charge_usd()
        actor = settings.apify_xiaohongshu_channel_actor_id()
        build, parse = _xhs_build_input(n), parse_xhs_note
    else:
        from app.services.china_platforms.adapters.kuaishou import parse_actor_item  # noqa: PLC0415
        est = settings.apify_kuaishou_channel_est_cost_per_video_usd() * n + 0.00005
        floor = 0.0
        actor = settings.apify_kuaishou_channel_actor_id()
        build, parse = _ks_build_input(n), parse_actor_item
    return ActorSpec(
        name=_PROVIDER[platform],
        platform=platform,
        actor_id=actor,
        budget_class="apify",
        build_input=build,
        parse_item=parse,
        est_cost_usd=est,
        max_charge_usd=max(settings.apify_run_max_charge_usd(), round(est * 1.5, 4), floor),
        timeout_sec=settings.china_channel_timeout_sec(platform),
        require_duration=False,
    )


def _video_url(platform: str, item: dict) -> Optional[tuple[str, str]]:
    """(video id, the single-video URL the per-video job resolves) for one
    dataset row, or None when the row is not a listable video."""
    if not isinstance(item, dict):
        return None
    if platform == "xiaohongshu":
        if item.get("record_type") not in (None, "note"):
            return None
        kind = item.get("note_type")
        if not (isinstance(kind, str) and kind.lower() == "video"):
            return None      # image notes cannot be downloaded as a video
        from app.services.china_platforms.adapters.xiaohongshu import XiaohongshuAdapter, note_id  # noqa: PLC0415
        nid = note_id(item.get("note_url") or "") or str(item.get("note_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{24}", nid or ""):
            return None
        token = item.get("xsec_token") if isinstance(item.get("xsec_token"), str) else ""
        url = f"https://www.xiaohongshu.com/explore/{nid}"
        if token:
            url += "?" + urlencode({"xsec_token": token, "xsec_source": "pc_user"})
        return nid, XiaohongshuAdapter().canonicalize(url)
    from app.services.china_platforms.adapters.kuaishou import KuaishouAdapter  # noqa: PLC0415
    vid = str(item.get("id") or "").strip()
    raw = item.get("url") if isinstance(item.get("url"), str) else ""
    adapter = KuaishouAdapter()
    if raw and adapter.matches(raw):
        return (vid or adapter.photo_id(raw) or ""), adapter.canonicalize(raw)
    if re.fullmatch(r"[A-Za-z0-9_-]{4,64}", vid):
        return vid, f"https://www.kuaishou.com/short-video/{vid}"
    return None


def _channel_title(platform: str, item: dict) -> str:
    if platform == "xiaohongshu":
        v = item.get("author_nickname")
        return v if isinstance(v, str) else ""
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    return str(author.get("name") or author.get("username") or "")


# ── listing cache ───────────────────────────────────────────────────────────

def _listing_key(platform: str, pid: str) -> str:
    return f"china:listing:{platform}:{request_cache.url_hash(pid)}"


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _read_cached_listing(platform: str, pid: str, n: int) -> Optional[ChannelListing]:
    try:
        raw = _r().get(_listing_key(platform, pid))
        if not raw:
            return None
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:  # noqa: BLE001
        return None
    requested = int(data.get("requested", 0))
    # A cached scan covers this one if it asked for at least as many notes,
    # or if the channel turned out to have fewer than it asked for.
    if requested < n and int(data.get("rows", 0)) >= requested:
        return None
    videos = [ListedVideo(video_id=v["id"], url=v["url"], title=v.get("title") or "",
                          thumbnail_url=v.get("thumbnail_url"), duration_sec=v.get("duration_sec"))
              for v in data.get("videos", [])[:n]]
    return ChannelListing(sec_uid=pid, channel_title=data.get("channel_title") or "", videos=videos,
                          from_cache=True, cap=n)


def _write_cached_listing(platform: str, listing: ChannelListing, requested: int, rows: int, ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        payload = {"requested": requested, "rows": rows, "channel_title": listing.channel_title,
                   "videos": [v.as_dict() for v in listing.videos]}
        _r().set(_listing_key(platform, listing.sec_uid), json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access listing cache write failed: %s", type(exc).__name__)


def _attempt(platform: str, ctx: RequestContext, h: str, outcome: str, *, category=None, latency_ms=0,
             est_usd=0.0, actual_usd=None, cost_source="none", items=0, pool_entry=None) -> None:
    from datetime import datetime, timezone  # noqa: PLC0415
    from app.services.china_platforms.provider_router import record_attempt  # noqa: PLC0415
    record_attempt({
        "ts": datetime.now(timezone.utc).isoformat(),
        "platform": platform, "operation": OPERATION, "provider": _PROVIDER[platform],
        "route_mode": "managed", "canonical_url_hash": h, "origin": ctx.origin,
        "outcome": outcome, "normalized_failure_category": category,
        "latency_ms": int(latency_ms), "items": items,
        "estimated_cost_usd": round(float(est_usd or 0), 6),
        "actual_cost_usd": None if actual_usd is None else round(float(actual_usd), 6),
        "cost_source": cost_source, "apify_entry": pool_entry,
    })


# ── main entry ──────────────────────────────────────────────────────────────

def to_bulk_result(platform: str, listing: ChannelListing) -> dict:
    """The shape downloader._scrape_channel_entries_impl returns."""
    name = _NAME.get(platform, platform)
    entries = [{"url": v.url, "title": v.title or f"{name} {v.video_id[-6:]}"} for v in listing.videos]
    return {"channel_title": listing.channel_title, "entries": entries,
            "total_found": len(entries), "total_queued": len(entries)}


async def list_profile(platform: str, url: str, max_videos: int, ctx: Optional[RequestContext] = None,
                       *, provider_factory=None) -> ChannelListing:
    """List up to min(max_videos, 20, today's remaining downloads) newest
    videos of a Xiaohongshu / Kuaishou profile. Raises ChannelListingError
    (str = user-facing Vietnamese text, .code = machine code)."""
    ctx = ctx or current_context()
    if platform not in PLATFORMS:
        raise ChannelListingError(_msg(MSG_BAD_URL, platform), "unsupported_url")
    # Flags first: while the layer is off the worker has no requester bound,
    # so "not open" is the true answer, not "sign in".
    if not route_open(platform, ctx):
        raise ChannelListingError(_msg(MSG_NOT_AVAILABLE, platform), "platform_disabled")
    if not is_signed_in(ctx):
        raise ChannelListingError(_msg(MSG_SIGN_IN, platform), "login_required")

    n = max(0, min(int(max_videos or 0), listing_cap(platform, ctx)))
    if n <= 0:
        from app.core import quotas  # noqa: PLC0415
        raise ChannelListingError(_msg(MSG_NO_ALLOWANCE, platform, reset=quotas.reset_time_vn_text()),
                                  "quota_exceeded")

    # Checked after the allowance: a refused request pays nothing.
    pid = await resolve_profile(platform, url)

    cached = _read_cached_listing(platform, pid, n)
    if cached is not None:
        return cached

    if platform == "xiaohongshu" and settings.xiaohongshu_channel_free_first():
        free = await _free_xhs_listing(pid, n)
        if free is not None:
            return free

    h = request_cache.url_hash(pid)
    token = request_cache.acquire_dedupe(platform, OPERATION, h)
    if token is None:
        waited = 0.0
        while waited < settings.dedupe_wait_sec():
            await asyncio.sleep(1.0)
            waited += 1.0
            cached = _read_cached_listing(platform, pid, n)
            if cached is not None:
                return cached
        raise ChannelListingError(MSG_BUSY, "already_processing")
    try:
        return await _run_listing(platform, pid, n, ctx, h, provider_factory)
    finally:
        request_cache.release_dedupe(platform, OPERATION, h, token)


async def _free_xhs_listing(pid: str, n: int) -> Optional[ChannelListing]:
    """The existing cookie-pool scraper (free). None when it yields nothing
    (no cookie, refused, unsigned request rejected) → the paid route runs."""
    try:
        from app.services.xiaohongshu_extractor import scrape_xiaohongshu_profile  # noqa: PLC0415
        raw = await asyncio.to_thread(scrape_xiaohongshu_profile, canonical_profile_url("xiaohongshu", pid), n)
    except Exception as exc:  # noqa: BLE001
        logger.info("china_access xhs free profile listing failed: %s", type(exc).__name__)
        return None
    videos = []
    for e in (raw or {}).get("entries", [])[:n]:
        from app.services.china_platforms.adapters.xiaohongshu import note_id  # noqa: PLC0415
        nid = note_id(e.get("url") or "")
        if nid:
            videos.append(ListedVideo(video_id=nid, url=e["url"], title=(e.get("title") or "")[:300]))
    if not videos:
        return None
    return ChannelListing(sec_uid=pid, channel_title=f"Xiaohongshu {pid[:8]}…", videos=videos, cap=n)


async def _run_listing(platform: str, pid: str, n: int, ctx: RequestContext, h: str,
                       provider_factory) -> ChannelListing:
    spec = profile_spec(platform, n)
    est_micros = settings.usd_to_micros(spec.est_cost_usd)
    cand = budget_guard.PaidCandidate(spec.name, spec.budget_class, est_micros)

    def _budget_error(level: str) -> ChannelListingError:
        if level == "user_quota":
            from app.core import quotas  # noqa: PLC0415
            return ChannelListingError(_msg(MSG_NO_ALLOWANCE, platform, reset=quotas.reset_time_vn_text()),
                                       "quota_exceeded")
        return ChannelListingError(_msg(MSG_BUDGET, platform), "budget_exceeded")

    denial = budget_guard.precheck(platform, ctx.requester_key, [cand])
    if denial is not None:
        _attempt(platform, ctx, h, "skipped", category="budget_exceeded")
        raise _budget_error(denial.level)
    try:
        if budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(platform):
            raise ChannelListingError(_msg(MSG_NOT_AVAILABLE, platform), "platform_disabled")
    except ChannelListingError:
        raise
    except Exception:  # noqa: BLE001
        raise ChannelListingError(_msg(MSG_NOT_AVAILABLE, platform), "platform_disabled")
    try:
        reservation = budget_guard.reserve(platform, ctx.requester_key, cand)
    except budget_guard.BudgetDenied as bd:
        _attempt(platform, ctx, h, "skipped", category="budget_exceeded")
        raise _budget_error(bd.level)

    prov = provider_factory(spec, n) if provider_factory else ApifyProvider(spec, max_items=n)
    t0 = time.monotonic()
    items, failure = None, None
    try:
        items = await prov._run_actor(spec.build_input(canonical_profile_url(platform, pid)))
    except ProviderFailureError as exc:
        failure = exc.failure
    except Exception as exc:  # noqa: BLE001
        failure = make_failure(platform, spec.name, "unknown", type(exc).__name__)
    latency = int((time.monotonic() - t0) * 1000)

    run = getattr(prov, "last_run", None)
    started = bool(run and run.run_started)
    budget_guard.reconcile(reservation, run.actual_cost_usd if run else None, started)
    budget_guard.check_spend_alerts(spec.budget_class)
    lease = getattr(run, "pool_lease", None) if run else None
    if lease is not None:
        apify_pool.settle(lease, reservation.recorded_micros or 0, dispatched=started)
    recorded_usd = (reservation.recorded_micros or 0) / 1e6
    cost_source = "actual" if started and run.actual_cost_usd is not None else ("estimated" if started else "none")

    videos, results, title = [], [], ""
    rows = len(items or []) if isinstance(items, list) else 0
    if failure is None:
        videos, results, title = _store_items(platform, items or [], n, prov)
    cost_metrics.record_paid(platform, success=bool(videos), usable=None,
                             recorded_micros=reservation.recorded_micros or 0)
    _attempt(platform, ctx, h, "success" if videos else "failure",
             category=None if videos else (failure.category if failure else "parse_failed"),
             latency_ms=latency, est_usd=spec.est_cost_usd, actual_usd=recorded_usd if started else 0.0,
             cost_source=cost_source, items=len(videos),
             pool_entry=getattr(run, "pool_entry_id", None) if run else None)

    if failure is not None:
        logger.warning("china_access %s profile listing failed: %s", platform, failure.category)
        if failure.category in ("private_or_login_required", "parse_failed"):
            raise ChannelListingError(_msg(MSG_EMPTY, platform), "no_media_found")
        raise ChannelListingError(MSG_UPSTREAM, "provider_unavailable")
    if not videos:
        raise ChannelListingError(_msg(MSG_EMPTY, platform), "no_media_found")

    listing = ChannelListing(sec_uid=pid, channel_title=title or f"{_NAME[platform]} {pid[:12]}",
                             videos=videos, cost_usd=recorded_usd, cap=n)
    policy = registry.get_policy(platform)
    ttl = min([settings.china_channel_listing_cache_sec(platform)]
              + [request_cache.cache_ttl_for(r, policy.cache_ttl_sec) for r in results])
    _write_cached_listing(platform, listing, n, rows, ttl)
    return listing


def _store_items(platform: str, items: list, n: int, prov) -> tuple:
    """Keep video rows only; cache each one that carries a playable media URL
    under the canonical single-video URL (the per-video job's cache key), so
    that job is a cache hit. Rows without one are still listed — the job
    resolves them through the normal single-video route."""
    policy = registry.get_policy(platform)
    videos, results, seen, title = [], [], set(), ""
    for item in items:
        if len(videos) >= n:
            break
        got = _video_url(platform, item)
        if got is None:
            continue
        vid, url = got
        if url in seen:
            continue
        seen.add(url)
        if not title:
            title = _channel_title(platform, item)
        parsed = prov.spec.parse_item(item)
        result: Optional[NormalizedMediaResult] = None
        try:
            result = prov._validate([item], ChinaResolveRequest(url=url), 0)
        except Exception:  # noqa: BLE001
            result = None
        if result is not None:
            result = result.model_copy(update={"canonical_url": url, "media_id": vid or result.media_id})
            request_cache.set_cached(result, "single_media", request_cache.url_hash(url), policy.cache_ttl_sec)
            results.append(result)
        videos.append(ListedVideo(
            video_id=vid or url, url=url, title=(parsed.title or "").strip()[:300],
            thumbnail_url=parsed.thumbnail_url, duration_sec=parsed.duration_sec, cached=result is not None))
    return videos, results, title
