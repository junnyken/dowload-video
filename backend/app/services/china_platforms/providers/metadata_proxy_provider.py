"""
Regional metadata-proxy provider — SCAFFOLD ONLY (interface, not registered).

Plan §10: metadata only (redirects, title, formats), never media bytes; the
profile comes from platform policy, never from the frontend; proxy bytes are
metered separately from managed-provider cost. A proxy changes network
identity, it does not solve Douyin's signature requirement (2026-10-05).
"""
from __future__ import annotations

from typing import Literal

from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.providers.base import BaseProvider

ProxyProfile = Literal[
    "direct",
    "residential_global_metadata",
    "residential_cn_metadata",
    "residential_hk_metadata",
    "fallback_metadata",
]


class MetadataProxyProvider(BaseProvider):
    mode = "proxy_metadata"
    paid = True
    budget_class = "proxy_metadata"

    def __init__(self, name: str, platform: str, profile: ProxyProfile = "direct"):
        super().__init__()
        self.name = name
        self.platform = platform
        self.profile = profile
        self.allow_bytes = False   # metadata-only by default, always in wave 1

    def is_configured(self) -> bool:
        return False

    async def resolve(self, request, context):
        raise ProviderFailureError(make_failure(
            self.platform, self.name, "provider_unavailable", "metadata proxy provider not implemented (wave 2)"))
