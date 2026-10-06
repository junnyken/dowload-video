"""
Xiaohongshu (RedNote) adapter (Phase 32B-3: single public video note).

native_xiaohongshu  wraps the existing app/services/xiaohongshu_extractor.py
                    (yt-dlp first, then the web API with the shared cookie pool
                    platform "xiaohongshu"). Free. Not re-implemented here.
apify_xiaohongshu   ApifyProvider over blue_puppy~rednote-video-downloader.
                    Input and output per the actor README and dataset schema,
                    read 2026-10-06 (docs/china-access/09):
                      input   urls: [{"url": ...}], maxUrls
                      output  one row per URL: url, status ("ok"|"error"),
                              downloadUrl, id, title, description, type
                              ("video"), authorName, error {code, message}
                    The actor documents NO duration and NO cover/thumbnail.
                    The parser also accepts the documented row of the
                    alternative actor agentflow~xiaohongshu-video-downloader
                    (success, type, title, desc, download_url) so the actor can
                    be swapped by env alone.

Profiles / containers are out of scope (owner decision 2026-10-06: single
video only); a profile URL is not matched, so it keeps the legacy path.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from app.services.china_platforms import settings
from app.services.china_platforms.adapters.base import PlatformAdapter
from app.services.china_platforms.adapters.short_links import first_redirect
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

PLATFORM = "xiaohongshu"

_NOTE_HOSTS = ("xiaohongshu.com",)
_SHORT_HOSTS = ("xhslink.com", "xhslink.cn")
_ID = r"([0-9a-fA-F]{24})"
_NOTE_PATHS = (
    re.compile(rf"^/explore/{_ID}/?$"),
    re.compile(rf"^/discovery/item/{_ID}/?$"),
    # A note opened from a creator's profile.
    re.compile(rf"^/user/profile/[0-9a-zA-Z]+/{_ID}/?$"),
)
_SHORT_PATH = re.compile(r"^/(?:[A-Za-z0-9]{1,4}/)?[A-Za-z0-9]{4,32}/?$")
# Kept on the canonical URL: the web page (and yt-dlp) need xsec_token for
# most notes; everything else in a share URL is tracking.
_KEEP_QUERY = ("xsec_token", "xsec_source")
CANONICAL = "https://www.xiaohongshu.com/explore/{id}"


def _host(url: str) -> tuple[str, str, str]:
    try:
        p = urlparse((url or "").strip())
        port = p.port
    except ValueError:
        return "", "", ""
    if p.scheme not in ("http", "https") or p.username or p.password or port not in (None, 80, 443):
        return "", "", ""
    return (p.hostname or "").rstrip(".").lower(), p.path or "/", p.query or ""


def _under(host: str, bases) -> bool:
    return any(host == b or host.endswith("." + b) for b in bases)


def note_id(url: str) -> Optional[str]:
    host, path, _q = _host(url)
    if not host or not _under(host, _NOTE_HOSTS):
        return None
    for rx in _NOTE_PATHS:
        m = rx.match(path)
        if m:
            return m.group(1).lower()
    return None


def is_short_link(url: str) -> bool:
    host, path, _q = _host(url)
    return bool(host) and host in _SHORT_HOSTS and bool(_SHORT_PATH.match(path))


def _http(u) -> str:
    u = u if isinstance(u, str) else ""
    if u.startswith("//"):
        u = "https:" + u
    return u if u.lower().startswith(("http://", "https://")) else ""


# ── managed actor ───────────────────────────────────────────────────────────

def parse_actor_item(item: dict) -> ParsedActorItem:
    if not isinstance(item, dict):
        return ParsedActorItem(error="item is not an object")
    err = item.get("error")
    if err or item.get("status") == "error" or item.get("success") is False:
        if isinstance(err, dict):
            err = f"{err.get('code') or ''}: {err.get('message') or ''}".strip(": ")
        return ParsedActorItem(error=str(err or "actor reported an error")[:200])
    kind = item.get("type")
    if isinstance(kind, str) and kind and kind.lower() != "video":
        return ParsedActorItem(error=f"not_video: note type {kind[:20]}")

    media = _http(item.get("downloadUrl")) or _http(item.get("download_url"))
    nid = str(item.get("id")) if item.get("id") not in (None, "") else None
    title = (item.get("title") or "").strip() if isinstance(item.get("title"), str) else ""
    if not title:
        desc = item.get("description") or item.get("desc") or ""
        title = desc.strip()[:120] if isinstance(desc, str) else ""
    if not title and media:
        title = f"Xiaohongshu {nid}" if nid else "Xiaohongshu Video"
    return ParsedActorItem(
        title=title,
        media_url=media,
        duration_sec=None,          # not in either actor's documented output
        thumbnail_url=None,         # not documented either
        uploader=item.get("authorName") if isinstance(item.get("authorName"), str) else None,
        media_id=nid,
    )


def _classify_item_error(text: str) -> str:
    t = (text or "").lower()
    if t.startswith(("invalid_url", "not_video")) or "not_video" in t:
        return "unsupported_url"
    if any(s in t for s in ("private", "login", "riêng tư")):
        return "private_or_login_required"
    if t.startswith("fetch_failed"):
        return "provider_unavailable"
    if "captcha" in t or "verify" in t:
        return "signature_or_verification_failed"
    return "parse_failed"


def _build_input(actor_id: str):
    # agentflow~ takes plain strings (its README input example); the default
    # actor takes request-list objects with a `url` key (its input schema).
    if actor_id.split("~", 1)[0] == "agentflow":
        return lambda url: {"urls": [url]}
    return lambda url: {"urls": [{"url": url}], "maxUrls": 1}


def apify_spec() -> ActorSpec:
    actor_id = settings.apify_xiaohongshu_actor_id()
    return ActorSpec(
        name="apify_xiaohongshu",
        platform=PLATFORM,
        actor_id=actor_id,
        budget_class="apify",
        build_input=_build_input(actor_id),
        parse_item=parse_actor_item,
        est_cost_usd=settings.apify_xiaohongshu_est_cost_usd(),
        max_charge_usd=settings.apify_run_max_charge_usd(),
        timeout_sec=settings.managed_timeout_sec(),
        require_duration=settings.apify_require_duration(),
        classify_item_error=_classify_item_error,
    )


# ── native (existing extractor) ─────────────────────────────────────────────

_NATIVE_CODE_CATEGORY = {
    "xhs_login_required": "cookie_required",
    "xhs_geo_restricted": "geo_restricted",
    "xhs_extract_failed": "parse_failed",
}


async def _native_resolve(request: ChinaResolveRequest, ctx: RequestContext) -> NormalizedMediaResult:
    from app.services.xiaohongshu_extractor import extract_xiaohongshu  # noqa: PLC0415
    t0 = time.monotonic()
    try:
        raw = await asyncio.to_thread(extract_xiaohongshu, request.url, request.requested_quality or "video")
    except Exception as exc:  # noqa: BLE001
        raise ProviderFailureError(make_failure(PLATFORM, "native_xiaohongshu", "unknown", type(exc).__name__))
    raw = raw if isinstance(raw, dict) else {}
    code = raw.get("error_code")
    if code:
        raise ProviderFailureError(make_failure(
            PLATFORM, "native_xiaohongshu", _NATIVE_CODE_CATEGORY.get(code, "parse_failed"),
            f"{code}: {raw.get('error_message') or ''}"))
    if (raw.get("source_type") or "video") != "video":
        # Image / carousel note. Single VIDEO only in this phase, and the
        # managed actor is video-only — not fallback-eligible on purpose.
        raise ProviderFailureError(make_failure(
            PLATFORM, "native_xiaohongshu", "unsupported_url", "image note (not a video)"))
    media = _http(raw.get("direct_url"))
    if not media:
        raise ProviderFailureError(make_failure(PLATFORM, "native_xiaohongshu", "parse_failed", "no media url"))
    dur_ms = raw.get("duration_ms")
    dur = dur_ms / 1000.0 if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
    return NormalizedMediaResult(
        platform=PLATFORM,
        canonical_url=request.url,
        media_id=note_id(request.url),
        title=(raw.get("title") or "").strip()[:500] or "Xiaohongshu Video",
        uploader=raw.get("author") or None,
        thumbnail_url=_http(raw.get("thumbnail")) or None,
        duration_sec=dur,
        formats=[NormalizedMediaFormat(format_id="native", ext="mp4", label="video",
                                       has_video=True, has_audio=True, source_url=media)],
        provider_name="native_xiaohongshu",
        provider_mode="native",
        watermark_state="unknown",
        resolution_time_ms=int((time.monotonic() - t0) * 1000),
    )


class XiaohongshuAdapter(PlatformAdapter):
    platform = PLATFORM
    host_pattern = re.compile(r"(?:^|[/.])(?:xiaohongshu\.com|xhslink\.(?:com|cn))(?:[/:?#]|$)", re.IGNORECASE)

    def matches(self, url: str) -> bool:
        """A single note or a share link. Profiles are not matched."""
        return note_id(url) is not None or is_short_link(url)

    def canonicalize(self, url: str) -> str:
        nid = note_id(url)
        if nid is None:
            if is_short_link(url):
                # Scheme kept as shared (the app copies http:// links).
                p = urlparse(url.strip())
                return f"{p.scheme.lower()}://{(p.hostname or '').lower()}{p.path.rstrip('/')}"
            return super().canonicalize(url)
        _h, _p, query = _host(url)
        qs = parse_qs(query, keep_blank_values=False)
        keep = [(k, qs[k][0]) for k in _KEEP_QUERY if qs.get(k)]
        base = CANONICAL.format(id=nid)
        return f"{base}?{urlencode(keep)}" if keep else base

    async def resolve_canonical(self, url: str) -> str:
        if not is_short_link(url) or note_id(url):
            return self.canonicalize(url)
        loc = await first_redirect(self.canonicalize(url))
        if loc and note_id(loc):
            return self.canonicalize(loc)
        return self.canonicalize(url)

    def build_providers(self) -> Dict[str, object]:
        return {
            "native_xiaohongshu": NativeProvider("native_xiaohongshu", PLATFORM, _native_resolve),
            "apify_xiaohongshu": ApifyProvider(apify_spec()),
        }
