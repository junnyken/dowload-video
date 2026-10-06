"""
Douyin adapter (wave 1: single media).

native_douyin  wraps the existing chain in douyin_extractor.extract_douyin_video
               with skip_scraperapi=True: ScraperAPI is a PAID call (owner
               correction #2) and the access layer does not budget it in wave 1,
               so it is skipped rather than spent unmetered.
apify_douyin   ApifyProvider over the actor natanielsantos~douyin-scraper.
               Output fields per the actor README (read 2026-10-06): id, text,
               url, thumb, authorMeta.name, videoMeta{playUrl, cover, width},
               musicMeta{duration = MUSIC length}, error. No video duration is
               documented; videoMeta.duration is used only if present.

Why a managed route at all: native Douyin is measured broken (0/1245 on prod,
2026-10-05/06) because Douyin requires signature cookies even for public
videos, and yt-dlp's DouyinIE still has "TODO: Run verification challenge code
to generate signature cookies".
"""
from __future__ import annotations

import re
import time
from typing import Dict

from app.services.china_platforms import settings
from app.services.china_platforms.adapters.base import PlatformAdapter
from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaFormat,
    NormalizedMediaResult,
    RequestContext,
)
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from app.services.china_platforms.providers.managed_actor_provider import ActorSpec, ParsedActorItem
from app.services.china_platforms.providers.native_provider import NativeProvider

PLATFORM = "douyin"


def _http(u) -> str:
    u = u if isinstance(u, str) else ""
    if u.startswith("//"):
        u = "https:" + u
    return u if u.lower().startswith(("http://", "https://")) else ""


def parse_actor_item(item: dict) -> ParsedActorItem:
    """Map one natanielsantos~douyin-scraper dataset item. Falls back to the
    legacy apify_service parser's field names for other shapes."""
    if not isinstance(item, dict):
        return ParsedActorItem(error="item is not an object")
    if item.get("error"):
        return ParsedActorItem(error=str(item.get("error"))[:200])

    vm = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    media = _http(vm.get("playUrl")) or _http(vm.get("downloadAddr"))
    thumb = _http(item.get("thumb")) or _http(vm.get("cover")) or _http(vm.get("originCover"))
    title = item.get("text") or item.get("desc") or item.get("title") or ""
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    mm = item.get("musicMeta") if isinstance(item.get("musicMeta"), dict) else {}
    audio = _http(mm.get("playUrl")) or _http(mm.get("musicUrl"))

    if not media:
        # Other actor shapes (the fields apify_service._parse_apify_video_item knows).
        try:
            from app.services.apify_service import _parse_apify_video_item  # noqa: PLC0415
            legacy = _parse_apify_video_item(item, "video") or {}
        except Exception:  # noqa: BLE001
            legacy = {}
        media = _http(legacy.get("direct_mp4_url"))
        thumb = thumb or _http(legacy.get("thumbnail_url"))
        audio = audio or _http(legacy.get("audio_url"))
        if not title and legacy.get("title") not in (None, "", "Douyin Video"):
            title = legacy.get("title")

    duration = vm.get("duration")
    if isinstance(duration, (int, float)) and duration > 1000:
        duration = duration / 1000.0   # ms, as in Douyin's own JSON
    width = vm.get("width") if isinstance(vm.get("width"), int) else None
    return ParsedActorItem(
        title=str(title or ""),
        media_url=media,
        duration_sec=duration if isinstance(duration, (int, float)) else None,
        thumbnail_url=thumb or None,
        uploader=(author.get("name") or author.get("nickname") or None),
        media_id=str(item.get("id")) if item.get("id") else None,
        audio_url=audio or None,
        width=width,
    )


def _classify_item_error(text: str) -> str:
    t = (text or "").lower()
    if any(s in t for s in ("private", "login", "riêng tư")):
        return "private_or_login_required"
    if any(s in t for s in ("not found", "deleted", "removed", "unavailable")):
        return "parse_failed"
    if "captcha" in t or "verify" in t:
        return "signature_or_verification_failed"
    return "parse_failed"


def apify_spec() -> ActorSpec:
    return ActorSpec(
        name="apify_douyin",
        platform=PLATFORM,
        actor_id=settings.apify_douyin_actor_id(),
        budget_class="apify",
        # postUrls only: `maxItems` is NOT an input field of this actor (it
        # is the run-level query param, set by ApifyProvider).
        build_input=lambda url: {"postUrls": [url]},
        parse_item=parse_actor_item,
        est_cost_usd=settings.apify_douyin_est_cost_usd(),
        max_charge_usd=settings.apify_run_max_charge_usd(),
        timeout_sec=settings.managed_timeout_sec(),
        require_duration=settings.apify_require_duration(),
        classify_item_error=_classify_item_error,
    )


def _native_failure_category(msg: str) -> str:
    from app.services.douyin_extractor import DOUYIN_COOKIE_REQUIRED_MSG  # noqa: PLC0415
    m = (msg or "")
    if DOUYIN_COOKIE_REQUIRED_MSG in m:
        return "cookie_required"
    low = m.lower()
    if "private" in low or "riêng tư" in low:
        return "private_or_login_required"
    if "timeout" in low or "quá thời gian" in low:
        return "provider_timeout"
    return "unknown"


async def _native_resolve(request: ChinaResolveRequest, ctx: RequestContext) -> NormalizedMediaResult:
    from app.services.douyin_extractor import extract_douyin_video  # noqa: PLC0415
    t0 = time.monotonic()
    try:
        info = await extract_douyin_video(
            request.url,
            request.requested_quality or "video",
            ctx.user_cookies_file,
            skip_scraperapi=True,
        )
    except ValueError as exc:
        raise ProviderFailureError(make_failure(
            PLATFORM, "native_douyin", _native_failure_category(str(exc)), str(exc)))
    media = _http(info.get("direct_mp4_url"))
    if not media:
        raise ProviderFailureError(make_failure(PLATFORM, "native_douyin", "parse_failed", "no media url"))
    formats = [NormalizedMediaFormat(format_id="native", ext="mp4", label="video",
                                     has_video=True, has_audio=True, source_url=media)]
    audio = _http(info.get("audio_url"))
    if audio:
        formats.append(NormalizedMediaFormat(format_id="native_audio", ext="mp3", label="audio",
                                             has_audio=True, source_url=audio))
    dur = info.get("duration")
    return NormalizedMediaResult(
        platform=PLATFORM,
        canonical_url=request.url,
        media_id=DouyinAdapter.video_id(request.url),
        title=info.get("title") or "Douyin Video",
        thumbnail_url=info.get("thumbnail_url") or None,
        duration_sec=float(dur) if isinstance(dur, (int, float)) and dur > 0 else None,
        formats=formats,
        provider_name="native_douyin",
        provider_mode="native",
        # iesdouyin swaps /playwm/ → /play/, but nothing validates the bytes.
        watermark_state="unknown",
        resolution_time_ms=int((time.monotonic() - t0) * 1000),
    )


class DouyinAdapter(PlatformAdapter):
    platform = PLATFORM
    host_pattern = re.compile(r"(?:^|[/.])(?:douyin\.com|iesdouyin\.com)(?:[/:?#]|$)", re.IGNORECASE)

    @staticmethod
    def video_id(url: str):
        from app.services.douyin_extractor import _extract_video_id  # noqa: PLC0415
        return _extract_video_id(url or "")

    def canonicalize(self, url: str) -> str:
        from app.services.douyin_extractor import _canonical_douyin_url  # noqa: PLC0415
        vid = self.video_id(url)
        return _canonical_douyin_url(vid) if vid else super().canonicalize(url)

    async def resolve_canonical(self, url: str) -> str:
        if self.video_id(url):
            return self.canonicalize(url)
        from app.services.douyin_extractor import _resolve_short_url  # noqa: PLC0415
        return self.canonicalize(await _resolve_short_url(url))

    def build_providers(self) -> Dict[str, object]:
        return {
            "native_douyin": NativeProvider("native_douyin", PLATFORM, _native_resolve),
            "apify_douyin": ApifyProvider(apify_spec()),
        }
