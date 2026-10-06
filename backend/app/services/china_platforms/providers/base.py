"""
Provider protocol (plan §6) and a small base class.

A provider resolves ONE request ONE time. It raises ProviderFailureError with a
normalized failure; anything else it raises is wrapped by the router as
`unknown` (and redacted). Retrying is the router's decision, never the
provider's — and in wave 1 the router never retries a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaResult,
    ProviderHealthSnapshot,
    RequestContext,
)


@dataclass
class RunRecord:
    """Cost/diagnostic info a paid provider leaves after a call (scrubbed)."""
    run_id: Optional[str] = None
    run_started: bool = False          # False → nothing was billed
    actual_cost_usd: Optional[float] = None
    elapsed_ms: int = 0
    duration_missing: bool = False
    media_url_expiry: Optional[str] = None


@runtime_checkable
class ChinaPlatformProvider(Protocol):
    name: str
    platform: str
    mode: str            # "native" | "managed" | "cookie_session" | "proxy_metadata"
    paid: bool
    budget_class: str    # provider budget bucket ("apify"); "none" for free

    async def can_handle(self, request: ChinaResolveRequest, policy) -> bool: ...

    async def resolve(self, request: ChinaResolveRequest, context: RequestContext) -> NormalizedMediaResult: ...

    async def estimate_cost_usd(self, request: ChinaResolveRequest) -> Decimal: ...

    async def health(self) -> ProviderHealthSnapshot: ...


class BaseProvider:
    name: str = "base"
    platform: str = ""
    mode: str = "native"
    paid: bool = False
    budget_class: str = "none"

    def __init__(self) -> None:
        self.last_run: RunRecord = RunRecord()

    async def can_handle(self, request: ChinaResolveRequest, policy) -> bool:
        return request.operation in policy.supported_operations

    async def estimate_cost_usd(self, request: ChinaResolveRequest) -> Decimal:
        return Decimal("0")

    def is_configured(self) -> bool:
        return True

    async def health(self) -> ProviderHealthSnapshot:
        from app.services.china_platforms import provider_health  # noqa: PLC0415
        return provider_health.snapshot(self.name, self.platform)

    async def resolve(self, request: ChinaResolveRequest, context: RequestContext) -> NormalizedMediaResult:
        raise NotImplementedError
