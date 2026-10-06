"""
China platform policies (plan §7) — a policy skeleton, NOT a claim that any
route works. Named ChinaPlatformPolicy because app/core/platform_policy.py
already defines PlatformPolicy (Phase 28 throughput policy).

Wave 1 (owner decisions 2026-10-06): Douyin single media is routable.
Phase 32B-3 (owner decisions 2026-10-06): Kuaishou and Xiaohongshu single
video are routable too — each still needs its own CHINA_ACCESS_<P>_ENABLED.
Lemon8 is a registry entry; Bilibili is registered but NOT routed (native
works; plan §21.12). Weibo is not registered at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Failure categories after which the NEXT provider may run. Douyin native fails
# with the cookie/signature message for every public video (0/1245 on prod,
# 2026-10-05/06; yt-dlp DouyinIE: "TODO: Run verification challenge code to
# generate signature cookies"), which is exactly what a managed actor can
# solve — so those categories are eligible. A private/login-only item or a bad
# link is not: paying an actor would not change the answer.
_DOUYIN_FALLBACK = frozenset({
    "cookie_required", "cookie_invalid", "signature_or_verification_failed",
    "upstream_rate_limited", "provider_timeout", "provider_unavailable",
    "parse_failed", "unknown",
})


# Xiaohongshu native (xiaohongshu_extractor) fails with "cookie required" when
# the pool has no XHS cookie and with "API did not answer" when the cookie is
# stale or the web API blocks us — both are what a managed actor can solve.
# Not eligible: private/login-only, an unsupported link, or an image-only note
# (reported as unsupported_url: the chosen actor is video-only, paying it
# would buy a `not_video` row).
_XHS_FALLBACK = frozenset({
    "cookie_required", "cookie_invalid", "signature_or_verification_failed", "geo_restricted",
    "upstream_rate_limited", "provider_timeout", "provider_unavailable",
    "parse_failed", "unknown",
})


@dataclass(frozen=True)
class ChinaPlatformPolicy:
    platform: str
    routable_in_wave1: bool
    supported_operations: frozenset
    provider_order: dict
    allowed_providers: frozenset
    managed_provider_allowed: bool
    metadata_proxy_profile: str = "direct"
    requires_cookie_for: dict = field(default_factory=dict)
    cache_ttl_sec: int = 1800
    max_container_items: int = 50
    delivery_mode: str = "server_proxy_default"
    fallback_categories: frozenset = frozenset()
    budget_class: str = "none"     # platform-level paid budget bucket name

    def to_public_dict(self) -> dict:
        return {
            "platform": self.platform,
            "routable_in_wave1": self.routable_in_wave1,
            "supported_operations": sorted(self.supported_operations),
            "provider_order": {k: list(v) for k, v in self.provider_order.items()},
            "managed_provider_allowed": self.managed_provider_allowed,
            "metadata_proxy_profile": self.metadata_proxy_profile,
            "cache_ttl_sec": self.cache_ttl_sec,
            "max_container_items": self.max_container_items,
            "delivery_mode": self.delivery_mode,
            "fallback_categories": sorted(self.fallback_categories),
        }


CHINA_PLATFORM_POLICIES: dict[str, ChinaPlatformPolicy] = {
    "douyin": ChinaPlatformPolicy(
        platform="douyin",
        routable_in_wave1=True,
        supported_operations=frozenset({"single_media"}),
        provider_order={"single_media": ("native_douyin", "apify_douyin")},
        allowed_providers=frozenset({"native_douyin", "apify_douyin"}),
        managed_provider_allowed=True,
        metadata_proxy_profile="china_or_hk_optional",
        requires_cookie_for={"container_discovery": "conditional"},
        cache_ttl_sec=1800,
        max_container_items=50,
        fallback_categories=_DOUYIN_FALLBACK,
        budget_class="douyin",
    ),
    # Managed only: www.kuaishou.com does not answer from the workspace or
    # production (connect/read timeouts, 2026-10-05) and yt-dlp has no
    # Kuaishou extractor. The scaffold in kuaishou_extractor.py stays behind
    # its own KUAISHOU_ENABLED flag, outside this layer (its downloader hook
    # runs first, so with both flags on the old path wins).
    "kuaishou": ChinaPlatformPolicy(
        platform="kuaishou",
        routable_in_wave1=True,
        supported_operations=frozenset({"single_media"}),
        provider_order={"single_media": ("apify_kuaishou",)},
        allowed_providers=frozenset({"apify_kuaishou"}),
        managed_provider_allowed=True,
        metadata_proxy_profile="china_or_hk_optional",
        cache_ttl_sec=1800,
        max_container_items=30,
        fallback_categories=frozenset(),
        budget_class="kuaishou",
    ),
    # Free route first (owner decision 2026-10-06): the existing extractor
    # (yt-dlp, then the web API with the shared cookie pool), then the actor.
    "xiaohongshu": ChinaPlatformPolicy(
        platform="xiaohongshu",
        routable_in_wave1=True,
        supported_operations=frozenset({"single_media"}),
        provider_order={"single_media": ("native_xiaohongshu", "apify_xiaohongshu")},
        allowed_providers=frozenset({"native_xiaohongshu", "apify_xiaohongshu"}),
        managed_provider_allowed=True,
        metadata_proxy_profile="china_or_hk_optional",
        requires_cookie_for={"container_discovery": True},
        cache_ttl_sec=900,
        max_container_items=20,
        fallback_categories=_XHS_FALLBACK,
        budget_class="xiaohongshu",
    ),
    "bilibili": ChinaPlatformPolicy(
        platform="bilibili",
        routable_in_wave1=False,   # correction #5: registry only, native flow untouched
        supported_operations=frozenset({"single_media", "container_discovery", "container_expand"}),
        provider_order={"single_media": ("native_bilibili",)},
        allowed_providers=frozenset({"native_bilibili"}),
        managed_provider_allowed=False,
        metadata_proxy_profile="direct_then_china_or_hk_for_geo_locked",
        requires_cookie_for={"single_media": "conditional"},
        cache_ttl_sec=1800,
        max_container_items=100,
    ),
    "lemon8": ChinaPlatformPolicy(
        platform="lemon8",
        routable_in_wave1=False,
        supported_operations=frozenset({"single_media"}),
        provider_order={"single_media": ("native_lemon8",)},
        allowed_providers=frozenset({"native_lemon8"}),
        managed_provider_allowed=False,
        cache_ttl_sec=1800,
        max_container_items=20,
    ),
}
