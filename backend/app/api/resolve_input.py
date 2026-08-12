"""
Universal Input Resolver — Phase 25 PR1 (updated)
===================================================
POST /api/v1/resolve-input
  Normalize → classify → registry lookup → typed capability response.
  Pure URL pattern matching; NO yt-dlp or network calls (<50ms).

GET /api/v1/platforms/capabilities
  Full capability matrix for PlatformsPage.

GET /api/v1/platforms/registry
  Live extractor metadata (source of truth is code).

Backward compatibility:
  All Phase 24 fields retained on ResolvedItem.
  New fields added: capability (CapabilityDescriptor), routing (RoutingInfo).
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.url_normalizer import normalize
from app.core.source_classifier import classify
from app.schemas.container import (
    CONTAINER_SOURCE_TYPES,
    CapabilityDescriptor,
    EntryFlow,
    PLATFORM_EMOJIS,
    ResolveInputItem,
    ResolveInputResult,
    RoutingInfo,
    SOURCE_TYPE_LABELS,
    SUPPORT_LEVEL_LABELS,
    SupportLevel,
)
from app.services.container_registry import get_capability

router = APIRouter(tags=["Resolve Input"])


# ── Request schema ────────────────────────────────────────────────────────────

class ResolveInputRequest(BaseModel):
    raw_input:  str   = Field(..., min_length=1, max_length=8_000,
                              description="Single URL or newline-separated URLs")
    raw_inputs: Optional[list[str]] = Field(
        default=None, description="Explicit list of URLs (alternative to raw_input)"
    )
    context:    str   = Field(default="web",
                              description="web | mobile | extension | bulk | share_target")
    hints:      dict  = Field(default_factory=dict,
                              description="Optional hints, e.g. {platform_hint: 'tiktok'}")


# ── Internal helpers ──────────────────────────────────────────────────────────

_PLATFORM_DISPLAY: dict[str, str] = {
    "tiktok": "TikTok", "douyin": "Douyin", "youtube": "YouTube",
    "instagram": "Instagram", "facebook": "Facebook", "threads": "Threads",
    "pinterest": "Pinterest", "soundcloud": "SoundCloud", "spotify": "Spotify",
    "reddit": "Reddit", "twitter": "Twitter / X", "bilibili": "Bilibili",
    "xiaohongshu": "Xiaohongshu", "lemon8": "Lemon8", "snapchat": "Snapchat",
    "vk": "VK", "twitch": "Twitch", "rumble": "Rumble", "odysee": "Odysee",
    "dailymotion": "Dailymotion", "podcast_rss": "Podcast RSS",
    "unknown": "Không xác định", "generic": "Generic",
}


def _build_routing(cap: CapabilityDescriptor, source_type: str) -> RoutingInfo:
    is_container = source_type in {st.value for st in CONTAINER_SOURCE_TYPES}
    return RoutingInfo(
        surface_flow=cap.best_entry_flow,
        recommended_endpoint=cap.recommended_endpoint,
        is_container=is_container,
        is_batch=is_container or cap.best_entry_flow in (
            EntryFlow.batch_queue, EntryFlow.profile_scrape,
        ),
    )


def _resolve_one(raw: str) -> ResolveInputItem:
    norm = normalize(raw)
    clf  = classify(norm.canonical_url)
    cap  = get_capability(clf.platform, clf.source_type)

    platform_label = _PLATFORM_DISPLAY.get(clf.platform, clf.platform.capitalize())
    source_label   = SOURCE_TYPE_LABELS.get(clf.source_type, clf.source_type)
    support_label  = SUPPORT_LEVEL_LABELS.get(cap.support_level.value, cap.support_level.value)
    emoji          = PLATFORM_EMOJIS.get(clf.platform, "🌐")

    return ResolveInputItem(
        raw_input=raw,
        normalized_input=norm.canonical_url,
        canonical_url=norm.canonical_url,
        is_short_link=norm.is_short_link,
        platform=clf.platform,
        source_type=clf.source_type,
        normalized_id=clf.normalized_id,
        capability=cap,
        routing=_build_routing(cap, clf.source_type),
        transformations=norm.transformations,
        # Flat compat fields
        platform_label=platform_label,
        platform_emoji=emoji,
        source_type_label=source_label,
        support_level=cap.support_level.value,
        support_level_label=support_label,
    )


# ── POST /resolve-input ────────────────────────────────────────────────────────

@router.post("/resolve-input", response_model=ResolveInputResult)
async def resolve_input(payload: ResolveInputRequest, request: Request):  # noqa: ARG001
    """
    Normalize → classify → capability resolve → route suggestion.
    Fast (<50 ms), no yt-dlp or network calls.

    Accepts:
      - raw_input: newline/comma-separated string
      - raw_inputs: explicit list (takes precedence)
    """
    if payload.raw_inputs is not None:
        raw_lines = [u.strip() for u in payload.raw_inputs if u.strip()]
    else:
        raw_lines = [
            line.strip()
            for line in re.split(r"[\n,]+", payload.raw_input)
            if line.strip()
        ]

    items = [_resolve_one(raw) for raw in raw_lines]

    supported    = sum(1 for i in items if i.capability.support_level in (
        SupportLevel.full, SupportLevel.partial, SupportLevel.proxy_required, SupportLevel.experimental
    ))
    cookie_req   = sum(1 for i in items if i.capability.support_level == SupportLevel.cookie_required)
    unsupported  = sum(1 for i in items if i.capability.support_level in (
        SupportLevel.unsupported, SupportLevel.temporarily_disabled
    ))

    return ResolveInputResult(
        batch_mode=len(items) > 1,
        normalized_inputs=[i.canonical_url for i in items],
        items=items,
        total=len(items),
        supported=supported,
        cookie_required=cookie_req,
        unsupported=unsupported,
        context=payload.context,
    )


# ── GET /platforms/capabilities ───────────────────────────────────────────────

@router.get("/platforms/capabilities")
async def platforms_capabilities():
    """Full capability matrix keyed by platform. Consumed by PlatformsPage."""
    from app.services.container_registry import get_full_matrix, get_all_platforms
    from app.schemas.container import PLATFORM_EMOJIS, SOURCE_TYPE_LABELS, SUPPORT_LEVEL_LABELS

    _PLATFORM_DOMAINS: dict[str, list[str]] = {
        "tiktok": ["tiktok.com"], "douyin": ["douyin.com"],
        "youtube": ["youtube.com", "youtu.be"], "instagram": ["instagram.com"],
        "facebook": ["facebook.com", "fb.watch"], "threads": ["threads.net"],
        "pinterest": ["pinterest.com"], "soundcloud": ["soundcloud.com"],
        "spotify": ["open.spotify.com"], "reddit": ["reddit.com", "redd.it"],
        "twitter": ["twitter.com", "x.com"], "bilibili": ["bilibili.com", "b23.tv"],
        "xiaohongshu": ["xiaohongshu.com", "xhslink.com"],
        "lemon8": ["lemon8-app.com"], "snapchat": ["snapchat.com"],
        "vk": ["vk.com", "vkvideo.ru"], "twitch": ["twitch.tv"],
        "rumble": ["rumble.com"], "odysee": ["odysee.com"],
        "dailymotion": ["dailymotion.com"], "podcast_rss": ["podcasts.apple.com"],
    }

    matrix = get_full_matrix()
    result = []
    for platform in sorted(matrix):
        caps = matrix[platform]
        best_level = min(
            caps, key=lambda c: [
                "full", "partial", "experimental",
                "proxy_required", "cookie_required",
                "temporarily_disabled", "unsupported",
            ].index(c.support_level.value) if c.support_level.value in [
                "full", "partial", "experimental",
                "proxy_required", "cookie_required",
                "temporarily_disabled", "unsupported",
            ] else 99
        ).support_level.value

        result.append({
            "platform": platform,
            "display_name": _PLATFORM_DISPLAY.get(platform, platform.capitalize()),
            "emoji": PLATFORM_EMOJIS.get(platform, "🌐"),
            "overall_status": best_level,
            "domains": _PLATFORM_DOMAINS.get(platform, []),
            "source_types": [
                {
                    "source_type": c.source_type,
                    "source_type_label": SOURCE_TYPE_LABELS.get(c.source_type, c.source_type),
                    "support_level": c.support_level.value,
                    "support_level_label": SUPPORT_LEVEL_LABELS.get(c.support_level.value, c.support_level.value),
                    "supported_actions": [a.value for a in c.supported_actions],
                    "warnings": [w.model_dump() for w in c.warnings],
                    "requirements": c.requirements.model_dump(),
                    "best_flow": c.best_entry_flow.value,
                    "recommended_endpoint": c.recommended_endpoint,
                    "notes": c.notes,
                }
                for c in caps
            ],
            "requires_cookie": any(c.requirements.cookie_required for c in caps),
            "proxy_required": any(c.requirements.proxy_required for c in caps),
        })

    return {"platforms": result, "total": len(result)}


# ── GET /platforms/registry ───────────────────────────────────────────────────

@router.get("/platforms/registry")
async def platforms_registry():
    """Live extractor registry metadata. Source of truth is code."""
    try:
        from app.services.extractor_registry import REGISTRY
        return {"platforms": REGISTRY.all_platforms()}
    except Exception as e:
        return {"platforms": [], "error": str(e)}
