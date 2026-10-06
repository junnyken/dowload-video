"""
Kuaishou adapter (Phase 32B-3: single public video, managed only).

apify_kuaishou  ApifyProvider over natanielsantos~kuaishou-scraper (same
                vendor as the Douyin actor that runs in production). Input and
                output per the actor README and input schema, read 2026-10-06
                (docs/china-access/09-XHS-KUAISHOU-PROVIDERS.md):
                  input   startUrls: [url]  (video, /f/ share and
                          chenzhongtech /fw/photo/ links are the documented forms)
                  output  id, text (caption), thumb, cover, duration,
                          width, height, playUrl, allPlayUrls[{url,
                          qualityType, videoCodec, avgBitrate}],
                          authorMeta.name, musicMeta.audioUrl
                The README shows `duration: 332` next to millisecond
                `createTime`, so duration is read as seconds.

There is NO native provider here: www.kuaishou.com does not answer from the
workspace or production (connect/read timeouts, 2026-10-05) and yt-dlp
2026.08.19 has no Kuaishou extractor. The scaffold in
app/services/kuaishou_extractor.py stays behind KUAISHOU_ENABLED, untouched;
this adapter only reuses its pure URL parser.

musicMeta.audioUrl is NOT offered as the audio track: it is the music entry
of the post, which is not guaranteed to be the video's own sound.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from app.services.china_platforms import settings
from app.services.china_platforms.adapters.base import PlatformAdapter
from app.services.china_platforms.adapters.short_links import first_redirect
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from app.services.china_platforms.providers.managed_actor_provider import (
    MAX_SANE_DURATION_SEC,
    ActorSpec,
    ParsedActorItem,
)

PLATFORM = "kuaishou"

# Canonical forms are the ones the actor README lists as inputs.
_SHORT_VIDEO = "https://www.kuaishou.com/short-video/{id}"
_FW_PHOTO = "https://v.m.chenzhongtech.com/fw/photo/{id}"
_SHARE_F = "https://www.kuaishou.com/f/{id}"


def _http(u) -> str:
    u = u if isinstance(u, str) else ""
    if u.startswith("//"):
        u = "https:" + u
    return u if u.lower().startswith(("http://", "https://")) else ""


def _parse(url: str):
    from app.services.kuaishou_extractor import parse_kuaishou_url  # noqa: PLC0415
    return parse_kuaishou_url((url or "").strip())


def _best_play_url(item: dict) -> str:
    media = _http(item.get("playUrl"))
    if media:
        return media
    best, best_rate = "", -1
    for entry in item.get("allPlayUrls") or []:
        if not isinstance(entry, dict):
            continue
        u = _http(entry.get("url"))
        rate = entry.get("avgBitrate") if isinstance(entry.get("avgBitrate"), (int, float)) else 0
        if u and rate > best_rate:
            best, best_rate = u, rate
    return best


def parse_actor_item(item: dict) -> ParsedActorItem:
    """Map one natanielsantos~kuaishou-scraper dataset item (README example).
    Defensive: an `error` field is not documented but is honoured if present."""
    if not isinstance(item, dict):
        return ParsedActorItem(error="item is not an object")
    if item.get("error"):
        err = item.get("error")
        if isinstance(err, dict):
            err = f"{err.get('code') or ''} {err.get('message') or ''}".strip()
        return ParsedActorItem(error=str(err)[:200])

    media = _best_play_url(item)
    vid = str(item.get("id")) if item.get("id") not in (None, "") else None
    title = item.get("text") or item.get("caption") or ""
    if not str(title).strip() and media:
        title = f"Kuaishou {vid}" if vid else "Kuaishou Video"
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}

    duration = item.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        duration = None
    elif duration > MAX_SANE_DURATION_SEC:
        duration = duration / 1000.0   # a millisecond value; the README shows seconds
    width = item.get("width") if isinstance(item.get("width"), int) else None
    return ParsedActorItem(
        title=str(title or ""),
        media_url=media,
        duration_sec=duration,
        thumbnail_url=_http(item.get("thumb")) or _http(item.get("cover")) or None,
        uploader=(author.get("name") or author.get("username") or None),
        media_id=vid,
        audio_url=None,
        width=width,
    )


def _classify_item_error(text: str) -> str:
    t = (text or "").lower()
    if any(s in t for s in ("private", "login", "riêng tư")):
        return "private_or_login_required"
    if "captcha" in t or "verify" in t:
        return "signature_or_verification_failed"
    return "parse_failed"


def apify_spec() -> ActorSpec:
    return ActorSpec(
        name="apify_kuaishou",
        platform=PLATFORM,
        actor_id=settings.apify_kuaishou_actor_id(),
        budget_class="apify",
        # Comments are a separately billed event ($0.0015 each): keep them at 0.
        # `maxItems` in this actor's schema is per search term / hashtag; the
        # run-level maxItems=1 that ApifyProvider sends caps the dataset.
        build_input=lambda url: {"startUrls": [url], "maxCommentsPerVideo": 0, "maxRepliesPerComment": 0},
        parse_item=parse_actor_item,
        est_cost_usd=settings.apify_kuaishou_est_cost_usd(),
        max_charge_usd=settings.apify_run_max_charge_usd(),
        timeout_sec=settings.managed_timeout_sec(),
        require_duration=settings.apify_require_duration(),
        classify_item_error=_classify_item_error,
    )


class KuaishouAdapter(PlatformAdapter):
    platform = PLATFORM
    host_pattern = re.compile(
        r"(?:^|[/.])(?:kuaishou\.(?:com|cn)|gifshow\.com|chenzhongtech\.(?:com|cn))(?:[/:?#]|$)",
        re.IGNORECASE,
    )

    def matches(self, url: str) -> bool:
        """Exact host check via the extractor's parser: a single video or a
        share link. Profiles, search and lookalike hosts are not matched."""
        return _parse(url) is not None

    @staticmethod
    def photo_id(url: str) -> Optional[str]:
        link = _parse(url)
        return link.photo_id if link else None

    def canonicalize(self, url: str) -> str:
        link = _parse(url)
        if link is None:
            return super().canonicalize(url)
        if link.kind == "short_video":
            return _SHORT_VIDEO.format(id=link.code)
        if link.kind == "fw_photo":
            return _FW_PHOTO.format(id=link.code)
        if link.kind == "share_f":
            return _SHARE_F.format(id=link.code)
        # short_link (v.kuaishou.com/<code>) and the unverified /video/<id>
        # form: https, no query, no fragment.
        return link.url.split("?", 1)[0]

    async def resolve_canonical(self, url: str) -> str:
        link = _parse(url)
        if link is None or link.kind != "short_link":
            return self.canonicalize(url)
        loc = await first_redirect(link.url)
        target = _parse(loc) if loc else None
        if target is not None and not target.needs_redirect:
            return self.canonicalize(loc)
        return self.canonicalize(url)

    def build_providers(self) -> Dict[str, object]:
        return {"apify_kuaishou": ApifyProvider(apify_spec())}
