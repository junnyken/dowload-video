"""Shared fakes for the Phase 32B-1 China access layer tests. No network, no
paid calls: providers are in-memory, Redis is fakeredis."""
from __future__ import annotations

import asyncio

import fakeredis
import pytest

from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.normalized_models import (
    NormalizedMediaFormat,
    NormalizedMediaResult,
    RequestContext,
)
from app.services.china_platforms.providers.base import BaseProvider, RunRecord

DY_URL = "https://www.douyin.com/video/7300000000000000001"
DY_URL2 = "https://www.douyin.com/video/7300000000000000002"
DY_URL3 = "https://www.douyin.com/video/7300000000000000003"
SIGNED = "https://sf9-sign.douyinstatic.com/tos/abc?x-expires=1999999999&x-signature=SIGSECRETvalue%3D"

PUBLIC_CTX = RequestContext(requester_key="ip:198.51.100.7", origin="request")
ADMIN_CTX = RequestContext(requester_key="admin", is_admin=True, origin="request")

_ENV_KEYS = [
    "CHINA_ACCESS_ENABLED", "CHINA_ACCESS_GLOBAL_KILL_SWITCH", "CHINA_ACCESS_DOUYIN_ENABLED",
    "CHINA_ACCESS_DOUYIN_MANAGED_MODE", "CHINA_ACCESS_DOUYIN_PROVIDER_ORDER", "CHINA_ACCESS_APIFY_TOKEN",
    "APIFY_TOKEN", "CHINA_ACCESS_DEDUPE_WAIT_SEC", "CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT",
    "CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT", "CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD",
    "CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT", "CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD",
    "CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "CHINA_ACCESS_MANAGED_PROBES_ENABLED",
    "CHINA_ACCESS_HEALTH_FAIL_THRESHOLD", "CHINA_ACCESS_APIFY_REQUIRE_DURATION",
    "CHINA_ACCESS_BENCHMARK_DOUYIN_URLS", "CHINA_ACCESS_BENCHMARK_FIXTURES_FILE",
]


@pytest.fixture
def clean_env(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    return r


def down_redis():
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def flags_on(clean_env, rc):
    clean_env.setenv("CHINA_ACCESS_ENABLED", "true")
    clean_env.setenv("CHINA_ACCESS_DOUYIN_ENABLED", "true")
    clean_env.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "on")
    clean_env.setenv("CHINA_ACCESS_DEDUPE_WAIT_SEC", "2")
    return clean_env


def media_result(provider: str, mode: str = "native", url: str = DY_URL) -> NormalizedMediaResult:
    return NormalizedMediaResult(
        platform="douyin", canonical_url=url, title="t", provider_name=provider, provider_mode=mode,
        formats=[NormalizedMediaFormat(format_id="v", ext="mp4", label="video", has_video=True,
                                       has_audio=True, source_url=SIGNED)],
    )


class FakeProvider(BaseProvider):
    """outcome: "ok" or a failure category. Records every resolve() call."""

    def __init__(self, name: str, *, paid: bool = False, outcome: str = "ok", cost=None,
                 run_started: bool = True, delay: float = 0.0, calls: list | None = None,
                 configured: bool = True, est: float = 0.0071):
        super().__init__()
        self.name = name
        self.platform = "douyin"
        self.paid = paid
        self.mode = "managed" if paid else "native"
        self.budget_class = "apify" if paid else "none"
        self.outcome = outcome
        self.cost = cost
        self.run_started = run_started
        self.delay = delay
        self.calls = calls if calls is not None else []
        self.configured = configured
        self.est = est

    def is_configured(self) -> bool:
        return self.configured

    async def estimate_cost_usd(self, request):
        from decimal import Decimal
        return Decimal(str(self.est if self.paid else 0))

    async def resolve(self, request, context):
        self.calls.append((self.name, request.url))
        self.last_run = RunRecord(run_started=self.paid and self.run_started,
                                  actual_cost_usd=self.cost)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.outcome != "ok":
            raise ProviderFailureError(make_failure("douyin", self.name, self.outcome,
                                                    f"fake failure {SIGNED}"))
        return media_result(self.name, self.mode, request.url)


def factory(*providers):
    table = {p.name: p for p in providers}
    return lambda platform: table
