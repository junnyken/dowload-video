"""
Thin Apify adapter over the generic ManagedActorProvider.

Why not call apify_service.extract_douyin_apify(): that function starts a
sync run and, when it returns nothing, a SECOND async run
(apify_service.py:161-168) — two billable runs for one request, with no
budget check. Here exactly one run is started per resolve():

  POST /v2/acts/{actor}/runs?waitForFinish=..&maxItems=1&maxTotalChargeUsd=..&timeout=..
  GET  /v2/actor-runs/{id}?waitForFinish=..        (only while not finished)
  POST /v2/actor-runs/{id}/abort                   (only on our deadline)
  GET  /v2/datasets/{datasetId}/items?clean=true&limit=1

API reference: https://docs.apify.com/api/v2/act-runs-post (waitForFinish
max 60 s, maxItems, maxTotalChargeUsd) and
https://docs.apify.com/api/v2/actor-run-get (run.usageTotalUsd = "Total cost
in USD for this run. Represents what you actually pay.").

Never logs the token (it goes only in the Authorization header), response
bodies, or media URLs.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from app.services.apify_service import APIFY_BASE
from app.services.china_platforms import settings
from app.services.china_platforms.providers.managed_actor_provider import (
    ActorSpec,
    ManagedActorProvider,
)

_TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
_MAX_WAIT = 60   # Apify's cap for waitForFinish


def _cost(run: dict) -> Optional[float]:
    v = run.get("usageTotalUsd") if isinstance(run, dict) else None
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class ApifyProvider(ManagedActorProvider):

    def __init__(self, spec: ActorSpec, *, token: Optional[str] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 base_url: str = APIFY_BASE, clock=time.monotonic):
        super().__init__(spec)
        self._token = token if token is not None else settings.apify_token()
        self._transport = transport
        self._base = base_url.rstrip("/")
        self._clock = clock

    def is_configured(self) -> bool:
        return bool(self._token)

    def _client(self, timeout: float) -> httpx.AsyncClient:
        kw = {"timeout": timeout, "headers": {"Authorization": f"Bearer {self._token}"}}
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    def _http_fail(self, status: int, where: str):
        if status == 429:
            return self._fail("upstream_rate_limited", f"apify {where} HTTP 429")
        if status in (401, 403):
            return self._fail("provider_unavailable", f"apify {where} HTTP {status} (auth)")
        if status == 402:
            return self._fail("provider_unavailable", f"apify {where} HTTP 402 (account limit)")
        return self._fail("provider_unavailable", f"apify {where} HTTP {status}")

    async def _run_actor(self, actor_input: dict) -> list:
        if not self._token:
            raise self._fail("provider_unavailable", "apify token not configured")
        deadline = self._clock() + self.spec.timeout_sec

        def remaining() -> int:
            return max(0, int(deadline - self._clock()))

        async with self._client(timeout=_MAX_WAIT + 15) as client:
            # ── start: the ONLY billable call ───────────────────────────────
            try:
                resp = await client.post(
                    f"{self._base}/acts/{self.spec.actor_id}/runs",
                    params={
                        "waitForFinish": min(_MAX_WAIT, remaining()),
                        "maxItems": 1,
                        "maxTotalChargeUsd": f"{self.spec.max_charge_usd:.4f}",
                        "timeout": self.spec.timeout_sec,
                    },
                    json=actor_input,
                )
            except httpx.TimeoutException:
                # The run may or may not exist; we have no id to abort or
                # price. Counted as started so the estimate stays reserved.
                self.last_run.run_started = True
                raise self._fail("provider_timeout", "apify start request timed out")
            except httpx.HTTPError as exc:
                raise self._fail("provider_unavailable", f"apify start: {type(exc).__name__}")
            if resp.status_code not in (200, 201):
                raise self._http_fail(resp.status_code, "start")

            run = (resp.json() or {}).get("data") or {}
            self.last_run.run_started = True
            self.last_run.run_id = run.get("id")
            self.last_run.actual_cost_usd = _cost(run)
            status = run.get("status") or ""

            # ── wait (no new run, ever) ─────────────────────────────────────
            while status not in _TERMINAL and remaining() > 0 and self.last_run.run_id:
                try:
                    r2 = await client.get(
                        f"{self._base}/actor-runs/{self.last_run.run_id}",
                        params={"waitForFinish": min(_MAX_WAIT, remaining())},
                    )
                except httpx.HTTPError:
                    await asyncio.sleep(1)
                    continue
                if r2.status_code != 200:
                    await asyncio.sleep(1)
                    continue
                run = (r2.json() or {}).get("data") or run
                status = run.get("status") or status
                c = _cost(run)
                if c is not None:
                    self.last_run.actual_cost_usd = c

            if status not in _TERMINAL:
                if self.last_run.run_id:
                    try:
                        ab = await client.post(f"{self._base}/actor-runs/{self.last_run.run_id}/abort")
                        if ab.status_code in (200, 201):
                            c = _cost((ab.json() or {}).get("data") or {})
                            if c is not None:
                                self.last_run.actual_cost_usd = c
                    except httpx.HTTPError:
                        pass
                raise self._fail("provider_timeout", f"apify run not finished after {self.spec.timeout_sec}s")

            if status == "TIMED-OUT":
                raise self._fail("provider_timeout", "apify run TIMED-OUT")
            if status != "SUCCEEDED":
                raise self._fail("provider_unavailable", f"apify run {status}")

            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                raise self._fail("parse_failed", "apify run has no dataset")
            try:
                r3 = await client.get(
                    f"{self._base}/datasets/{dataset_id}/items",
                    params={"clean": "true", "limit": 1, "format": "json"},
                )
            except httpx.HTTPError as exc:
                raise self._fail("provider_unavailable", f"apify dataset: {type(exc).__name__}")
            if r3.status_code != 200:
                raise self._http_fail(r3.status_code, "dataset")
            items = r3.json()
            return items if isinstance(items, list) else []
