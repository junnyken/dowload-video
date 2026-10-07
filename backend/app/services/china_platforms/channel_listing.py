"""
Douyin channel (profile) listing through the managed Apify route (task #6055).

One profile = ONE Apify run of natanielsantos~douyin-scraper with
{"profileUrls": [url], "maxItemsPerUrl": N} (fields per the actor README,
read 2026-10-07). The actor is pay-per-result, so the cost is N × the per-video
estimate — N is therefore never more than what the requester may still download
today (owner 2026-10-07: guest ≤ 5, signed-in ≤ 20, admin ≤ 100 per scan).

Every listed item already carries the video's play URL. Each one is written to
the router's result cache under the canonical /video/<id> URL, so the per-video
jobs that follow are cache hits — the same video is not paid for twice.

Same guards as a single-video managed call (provider_router.py): master and
platform flags, kill switches, managed mode for this requester, a configured
token pool, the budget pre-check and an atomic reservation of the whole
estimate, then settle against the run's reported cost (floored at the
estimate). Never logs the token, media URLs or the profile URL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.china_platforms import apify_pool, budget_guard, cost_metrics, registry, request_cache, settings
from app.services.china_platforms.adapters.douyin import parse_actor_item
from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaResult,
    RequestContext,
    current_context,
)
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from app.services.china_platforms.providers.managed_actor_provider import ActorSpec

logger = logging.getLogger("app.china_access")

PLATFORM = "douyin"
OPERATION = "profile_listing"
PROVIDER_NAME = "apify_douyin_profile"
_SEC_UID = re.compile(r"/(?:share/)?user/([A-Za-z0-9_.-]{6,})")

# Vietnamese text shown on the channel job / returned to the app.
MSG_NOT_AVAILABLE = ("Tải cả kênh Douyin tạm thời chưa mở. "
                     "Bạn vẫn tải được từng video Douyin bằng cách dán link video.")
MSG_NO_ALLOWANCE = ("Bạn đã dùng hết lượt tải hôm nay nên chưa quét được kênh. "
                    "Lượt mới được cộng lại lúc {reset} (giờ Việt Nam).")
MSG_NO_ALLOWANCE_GUEST = ("Bạn đã dùng hết lượt tải của khách hôm nay nên chưa quét được kênh. "
                          "Đăng nhập để có {user_limit} lượt/ngày.")
MSG_BUSY = "Kênh này đang được quét. Vui lòng đợi 1–2 phút rồi thử lại."
MSG_BUDGET = "Hệ thống đã dùng hết ngân sách quét Douyin hôm nay. Vui lòng thử lại vào ngày mai."
MSG_UPSTREAM = "Nền tảng nguồn tạm thời không phản hồi. Thử lại sau vài phút."
MSG_EMPTY = ("Không tìm thấy video nào trên kênh này. Kênh có thể để riêng tư, "
             "chưa đăng video, hoặc link không phải trang cá nhân Douyin.")
MSG_BAD_URL = ("Link không phải kênh hay video Douyin. Hãy dán link trang cá nhân (douyin.com/user/…) "
               "hoặc link một video của kênh đó.")
MSG_NO_AUTHOR = ("Không tìm được kênh của tác giả video này. Hãy mở trang cá nhân tác giả trong app Douyin, "
                 "bấm … → Chia sẻ → Sao chép liên kết rồi dán link đó.")


class ChannelListingError(ValueError):
    """A listing refusal/failure whose str() is the user-facing text."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class ListedVideo:
    video_id: str
    url: str
    title: str = ""
    thumbnail_url: Optional[str] = None
    duration_sec: Optional[float] = None
    cached: bool = False           # a playable result was cached for this video

    def as_dict(self) -> dict:
        return {"id": self.video_id, "url": self.url, "title": self.title,
                "thumbnail_url": self.thumbnail_url, "duration_sec": self.duration_sec}


@dataclass
class ChannelListing:
    sec_uid: str
    channel_title: str
    videos: list = field(default_factory=list)
    from_cache: bool = False
    cost_usd: float = 0.0
    cap: int = 0

    def to_bulk_result(self) -> dict:
        """The shape downloader._scrape_channel_entries_impl returns."""
        entries = [{"url": v.url, "title": v.title or f"Douyin Video {v.video_id[-6:]}"} for v in self.videos]
        return {"channel_title": self.channel_title, "entries": entries,
                "total_found": len(entries), "total_queued": len(entries)}


# ── helpers ─────────────────────────────────────────────────────────────────

def sec_uid_of(url: str) -> Optional[str]:
    m = _SEC_UID.search(url or "")
    return m.group(1) if m else None


def _canonical_video_url(video_id: str) -> str:
    from app.services.douyin_extractor import _canonical_douyin_url  # noqa: PLC0415
    return _canonical_douyin_url(video_id)


def _listing_key(sec_uid: str) -> str:
    return f"china:listing:{PLATFORM}:{request_cache.url_hash(sec_uid)}"


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def listing_cap(ctx: Optional[RequestContext] = None) -> int:
    """How many videos one scan may list for this requester: what they can
    still download today (total across platforms), admin / unlimited tiers up
    to CHINA_ACCESS_DOUYIN_CHANNEL_ADMIN_MAX. A requester we cannot identify
    gets the guest limit."""
    from app.core import quotas  # noqa: PLC0415
    ctx = ctx or current_context()
    admin_max = settings.douyin_channel_admin_max()
    if ctx.is_admin or ctx.requester_key == "admin":
        return admin_max
    req = quotas.QuotaRequester.from_key(ctx.requester_key)
    if req is None:
        lim = quotas.platform_limit_anon()
        return admin_max if lim == -1 else max(0, min(lim, admin_max))
    left = quotas.BatchAllowance(req).remaining(PLATFORM)
    if left == -1:
        return admin_max
    return max(0, min(left, admin_max))


def no_allowance_message(ctx: Optional[RequestContext] = None) -> str:
    from app.core import quotas  # noqa: PLC0415
    ctx = ctx or current_context()
    if (ctx.requester_key or "").startswith("user:"):
        return MSG_NO_ALLOWANCE.format(reset=quotas.reset_time_vn_text())
    return MSG_NO_ALLOWANCE_GUEST.format(user_limit=quotas.platform_limit_user())


def route_open(ctx: Optional[RequestContext] = None) -> bool:
    """Flags on, no kill switch, managed mode allows this requester, a token
    in the pool. Budget is checked separately (reserve)."""
    ctx = ctx or current_context()
    try:
        if not (settings.master_enabled() and settings.platform_env_enabled(PLATFORM)):
            return False
        if not settings.douyin_channel_enabled():
            return False
        if budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(PLATFORM):
            return False
        if not settings.apify_configured():
            return False
        return registry.managed_allowed_for(registry.effective_managed_mode(PLATFORM), ctx)
    except Exception:  # noqa: BLE001
        return False


def profile_spec(n: int) -> ActorSpec:
    est = settings.apify_douyin_est_cost_usd() * n
    return ActorSpec(
        name=PROVIDER_NAME,
        platform=PLATFORM,
        actor_id=settings.apify_douyin_actor_id(),
        budget_class="apify",
        build_input=lambda url: {
            "profileUrls": [url],
            "maxItemsPerUrl": n,
            "profileSortFilter": "latest",
        },
        parse_item=parse_actor_item,
        est_cost_usd=est,
        # Room for the start fee + rounding; never below the single-run cap.
        max_charge_usd=max(settings.apify_run_max_charge_usd(), round(est * 1.5, 4)),
        timeout_sec=settings.douyin_channel_timeout_sec(),
        require_duration=False,
    )


def _read_cached_listing(sec_uid: str, n: int) -> Optional[ChannelListing]:
    try:
        raw = _r().get(_listing_key(sec_uid))
        if not raw:
            return None
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:  # noqa: BLE001
        return None
    # A cached scan covers this one if it asked for at least as many videos,
    # or if the channel turned out to have fewer than it asked for.
    if int(data.get("requested", 0)) < n and len(data.get("videos", [])) >= int(data.get("requested", 0)):
        return None
    videos = []
    for v in data.get("videos", [])[:n]:
        h = request_cache.url_hash(v["url"])
        hit = request_cache.get_cached(PLATFORM, "single_media", h)
        videos.append(ListedVideo(video_id=v["id"], url=v["url"], title=v.get("title") or "",
                                  thumbnail_url=v.get("thumbnail_url"), duration_sec=v.get("duration_sec"),
                                  cached=hit is not None))
    return ChannelListing(sec_uid=sec_uid, channel_title=data.get("channel_title") or "", videos=videos,
                          from_cache=True, cap=n)


def _write_cached_listing(listing: ChannelListing, requested: int, ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        payload = {"requested": requested, "channel_title": listing.channel_title,
                   "videos": [v.as_dict() for v in listing.videos]}
        _r().set(_listing_key(listing.sec_uid), json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access listing cache write failed: %s", type(exc).__name__)


def _attempt(ctx: RequestContext, h: str, outcome: str, *, category=None, latency_ms=0, est_usd=0.0,
             actual_usd=None, cost_source="none", items=0, pool_entry=None) -> None:
    from datetime import datetime, timezone  # noqa: PLC0415
    from app.services.china_platforms.provider_router import record_attempt  # noqa: PLC0415
    record_attempt({
        "ts": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM, "operation": OPERATION, "provider": PROVIDER_NAME,
        "route_mode": "managed", "canonical_url_hash": h, "origin": ctx.origin,
        "outcome": outcome, "normalized_failure_category": category,
        "latency_ms": int(latency_ms), "items": items,
        "estimated_cost_usd": round(float(est_usd or 0), 6),
        "actual_cost_usd": None if actual_usd is None else round(float(actual_usd), 6),
        "cost_source": cost_source, "apify_entry": pool_entry,
    })


# ── main entry ──────────────────────────────────────────────────────────────

async def profile_url_from_video(url: str, ctx: RequestContext) -> str:
    """A Douyin VIDEO link (douyin.com/video/<id>, jingxuan?modal_id=<id>,
    v.douyin.com/<code>) → its author's profile URL (task #6055: people paste
    a video of the channel they want). The video is resolved through the
    managed route (a cache hit after a scan, else one budgeted call ≈ one
    video's price) and authorMeta.secUid gives the profile. Raises
    ChannelListingError."""
    from app.services.china_platforms.adapters.douyin import DouyinAdapter  # noqa: PLC0415
    from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure  # noqa: PLC0415
    from app.services.china_platforms.provider_router import ProviderRouter  # noqa: PLC0415

    vid = DouyinAdapter.video_id(url)
    if not vid and "v.douyin.com" in (url or "").lower():
        from app.services.douyin_extractor import _resolve_short_url  # noqa: PLC0415
        target = await _resolve_short_url(url)
        if sec_uid_of(target):
            return target
        vid = DouyinAdapter.video_id(target)
    if not vid:
        raise ChannelListingError(MSG_BAD_URL, "unsupported_url")
    req = ChinaResolveRequest(url=_canonical_video_url(vid), operation="single_media")
    try:
        # Managed route only: the free native chain never returns the author id.
        result = await ProviderRouter().resolve(req, ctx, only_provider="apify_douyin")
    except AlreadyProcessing:
        raise ChannelListingError(MSG_BUSY, "already_processing") from None
    except ChinaAccessFailure as exc:
        if exc.category == "budget_exceeded":
            raise ChannelListingError(MSG_BUDGET, "budget_exceeded") from None
        if exc.category in ("parse_failed", "unsupported_url", "private_or_login_required"):
            raise ChannelListingError(MSG_NO_AUTHOR, "no_media_found") from None
        raise ChannelListingError(MSG_UPSTREAM, "provider_unavailable") from None
    sec = (result.uploader_id or "").strip()
    if not _SEC_UID.search(f"/user/{sec}"):
        raise ChannelListingError(MSG_NO_AUTHOR, "no_media_found")
    return f"https://www.douyin.com/user/{sec}"


async def list_douyin_profile(profile_url: str, max_videos: int, ctx: Optional[RequestContext] = None,
                              *, provider_factory=None, cap_key: Optional[str] = None) -> ChannelListing:
    """List up to min(max_videos, listing_cap) newest videos of a Douyin
    profile — or of the author of a Douyin video link. Raises
    ChannelListingError (str = user-facing text).

    cap_key (task #6125): the allowance the cap is read from when it differs
    from ctx.requester_key — the app's guest machine (dev:<32 hex>). Only the
    cap uses it; budgets stay on ctx.requester_key (the IP), so a made-up
    machine id cannot mint provider budget."""
    ctx = ctx or current_context()
    if not route_open(ctx):
        raise ChannelListingError(MSG_NOT_AVAILABLE, "platform_disabled")

    cap_ctx = ctx.with_(requester_key=cap_key) if cap_key else ctx
    cap = listing_cap(cap_ctx)
    n = max(0, min(int(max_videos or 0), cap))
    if n <= 0:
        raise ChannelListingError(no_allowance_message(cap_ctx), "quota_exceeded")

    sec_uid = sec_uid_of(profile_url)
    if not sec_uid:
        # Checked after the allowance: a refused request pays nothing.
        sec_uid = sec_uid_of(await profile_url_from_video(profile_url, ctx))

    cached = _read_cached_listing(sec_uid, n)
    if cached is not None:
        return cached

    h = request_cache.url_hash(sec_uid)
    token = request_cache.acquire_dedupe(PLATFORM, OPERATION, h)
    if token is None:
        waited = 0.0
        while waited < settings.dedupe_wait_sec():
            await asyncio.sleep(1.0)
            waited += 1.0
            cached = _read_cached_listing(sec_uid, n)
            if cached is not None:
                return cached
        raise ChannelListingError(MSG_BUSY, "already_processing")
    try:
        return await _run_listing(sec_uid, n, ctx, h, provider_factory)
    finally:
        request_cache.release_dedupe(PLATFORM, OPERATION, h, token)


async def _run_listing(sec_uid: str, n: int, ctx: RequestContext, h: str, provider_factory) -> ChannelListing:
    spec = profile_spec(n)
    est_micros = settings.usd_to_micros(spec.est_cost_usd)
    cand = budget_guard.PaidCandidate(PROVIDER_NAME, spec.budget_class, est_micros)
    denial = budget_guard.precheck(PLATFORM, ctx.requester_key, [cand])
    if denial is not None:
        _attempt(ctx, h, "skipped", category="budget_exceeded")
        raise ChannelListingError(MSG_BUDGET if denial.level != "user_quota" else no_allowance_message(ctx),
                                  "budget_exceeded")
    try:
        if budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(PLATFORM):
            raise ChannelListingError(MSG_NOT_AVAILABLE, "platform_disabled")
    except ChannelListingError:
        raise
    except Exception:  # noqa: BLE001
        raise ChannelListingError(MSG_NOT_AVAILABLE, "platform_disabled")
    try:
        reservation = budget_guard.reserve(PLATFORM, ctx.requester_key, cand)
    except budget_guard.BudgetDenied as bd:
        _attempt(ctx, h, "skipped", category="budget_exceeded")
        raise ChannelListingError(MSG_BUDGET if bd.level != "user_quota" else no_allowance_message(ctx),
                                  "budget_exceeded")

    prov = provider_factory(spec, n) if provider_factory else ApifyProvider(spec, max_items=n)
    profile_url = f"https://www.douyin.com/user/{sec_uid}"
    t0 = time.monotonic()
    items, failure = None, None
    try:
        items = await prov._run_actor(spec.build_input(profile_url))
    except ProviderFailureError as exc:
        failure = exc.failure
    except Exception as exc:  # noqa: BLE001
        failure = make_failure(PLATFORM, PROVIDER_NAME, "unknown", type(exc).__name__)
    latency = int((time.monotonic() - t0) * 1000)

    run = getattr(prov, "last_run", None)
    started = bool(run and run.run_started)
    cost = budget_guard.reconcile(reservation, run.actual_cost_usd if run else None, started)
    budget_guard.check_spend_alerts(spec.budget_class)
    lease = getattr(run, "pool_lease", None) if run else None
    if lease is not None:
        apify_pool.settle(lease, reservation.recorded_micros or 0, dispatched=started)
    recorded_usd = (reservation.recorded_micros or 0) / 1e6
    cost_source = "actual" if started and run.actual_cost_usd is not None else ("estimated" if started else "none")

    videos: list = []
    results: list = []
    channel_title = ""
    if failure is None:
        videos, results, channel_title = _store_items(items or [], n, prov)
    cost_metrics.record_paid(PLATFORM, success=bool(videos), usable=None,
                             recorded_micros=reservation.recorded_micros or 0)
    _attempt(ctx, h, "success" if videos else "failure",
             category=None if videos else (failure.category if failure else "parse_failed"),
             latency_ms=latency, est_usd=spec.est_cost_usd, actual_usd=recorded_usd if started else 0.0,
             cost_source=cost_source, items=len(videos),
             pool_entry=getattr(run, "pool_entry_id", None) if run else None)

    if failure is not None:
        logger.warning("china_access douyin profile listing failed: %s", failure.category)
        if failure.category in ("private_or_login_required", "parse_failed"):
            raise ChannelListingError(MSG_EMPTY, "no_media_found")
        raise ChannelListingError(MSG_UPSTREAM, "provider_unavailable")
    if not videos:
        raise ChannelListingError(MSG_EMPTY, "no_media_found")

    listing = ChannelListing(sec_uid=sec_uid, channel_title=channel_title or f"Douyin {sec_uid[:12]}…",
                             videos=videos, cost_usd=recorded_usd, cap=n)
    ttl = min([settings.douyin_channel_listing_cache_sec()]
              + [request_cache.cache_ttl_for(r, registry.get_policy(PLATFORM).cache_ttl_sec)
                 for r in results])
    _write_cached_listing(listing, n, ttl)
    return listing


def _store_items(items: list, n: int, prov) -> tuple:
    """Validate each dataset item like a single-video result and cache it
    under its canonical /video/<id> URL. Items without an id are dropped;
    items without a usable play URL are still listed (the per-video job
    resolves them normally). Returns (videos, cached results, channel title)."""
    results: list = []
    policy = registry.get_policy(PLATFORM)
    videos, seen, title = [], set(), ""
    for item in items:
        if len(videos) >= n:
            break
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()
        if not vid.isdigit() or vid in seen:
            continue
        seen.add(vid)
        url = _canonical_video_url(vid)
        parsed = parse_actor_item(item)
        if not title:
            author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
            title = str(author.get("nickName") or author.get("name") or author.get("nickname") or "")
        cached = False
        result: Optional[NormalizedMediaResult] = None
        try:
            result = prov._validate([item], ChinaResolveRequest(url=url), 0)
        except Exception:  # noqa: BLE001
            result = None
        if result is not None:
            result = result.model_copy(update={"canonical_url": url, "media_id": vid})
            request_cache.set_cached(result, "single_media", request_cache.url_hash(url), policy.cache_ttl_sec)
            results.append(result)
            cached = True
        videos.append(ListedVideo(
            video_id=vid, url=url, title=(parsed.title or "").strip()[:300],
            thumbnail_url=parsed.thumbnail_url, duration_sec=parsed.duration_sec, cached=cached))
    return videos, results, title
