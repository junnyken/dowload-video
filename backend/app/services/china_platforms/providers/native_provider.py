"""Generic native provider: wraps an existing extractor coroutine. Free."""
from __future__ import annotations

from typing import Awaitable, Callable

from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaResult,
    RequestContext,
)
from app.services.china_platforms.providers.base import BaseProvider, RunRecord

NativeFn = Callable[[ChinaResolveRequest, RequestContext], Awaitable[NormalizedMediaResult]]


class NativeProvider(BaseProvider):
    mode = "native"
    paid = False
    budget_class = "none"

    def __init__(self, name: str, platform: str, fn: NativeFn):
        super().__init__()
        self.name = name
        self.platform = platform
        self._fn = fn

    async def resolve(self, request: ChinaResolveRequest, context: RequestContext) -> NormalizedMediaResult:
        self.last_run = RunRecord()
        return await self._fn(request, context)
