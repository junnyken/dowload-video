"""
Generic managed-actor provider (plan §12.1).

The router never sees an actor ID: actor id, input mapping, output mapping,
timeout, max charge and cost estimate all come from an ActorSpec built from
settings by the platform adapter. Subclasses implement only the vendor API
call (`_run_actor`), which must start AT MOST ONE billable run.

A run counts as success only after the output is validated: title, an
http(s) media URL, and (when present / when required) a sane duration.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from app.services.china_platforms.errors import ProviderFailureError, make_failure, redact
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaFormat,
    NormalizedMediaResult,
    RequestContext,
)
from app.services.china_platforms.providers.base import BaseProvider, RunRecord

MAX_SANE_DURATION_SEC = 6 * 3600


@dataclass
class ParsedActorItem:
    title: str = ""
    media_url: str = ""
    duration_sec: Optional[float] = None
    thumbnail_url: Optional[str] = None
    uploader: Optional[str] = None
    uploader_id: Optional[str] = None
    media_id: Optional[str] = None
    audio_url: Optional[str] = None
    width: Optional[int] = None
    error: Optional[str] = None


@dataclass
class ActorSpec:
    name: str
    platform: str
    actor_id: str
    budget_class: str
    build_input: Callable[[str], dict]
    parse_item: Callable[[dict], ParsedActorItem]
    est_cost_usd: float
    max_charge_usd: float
    timeout_sec: int
    require_duration: bool = False
    # Optional: map an item-level `error` text to a failure category.
    classify_item_error: Optional[Callable[[str], str]] = None


def _expiry_from_url(url: str) -> Optional[str]:
    """Douyin CDN URLs carry x-expires=<unix>; report it, never the URL."""
    try:
        qs = parse_qs(urlparse(url).query)
        raw = (qs.get("x-expires") or qs.get("expires") or [None])[0]
        if raw and str(raw).isdigit():
            from datetime import datetime, timezone  # noqa: PLC0415
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        pass
    return None


class ManagedActorProvider(BaseProvider):
    mode = "managed"
    paid = True

    def __init__(self, spec: ActorSpec):
        super().__init__()
        self.spec = spec
        self.name = spec.name
        self.platform = spec.platform
        self.budget_class = spec.budget_class

    async def estimate_cost_usd(self, request: ChinaResolveRequest) -> Decimal:
        return Decimal(str(self.spec.est_cost_usd))

    def _fail(self, category: str, detail: str | None = None) -> ProviderFailureError:
        return ProviderFailureError(make_failure(self.platform, self.name, category, detail))

    async def _run_actor(self, actor_input: dict) -> list:
        """Start one run, wait for it, return dataset items. Must set
        self.last_run.run_started / run_id / actual_cost_usd as it goes."""
        raise NotImplementedError

    async def resolve(self, request: ChinaResolveRequest, context: RequestContext) -> NormalizedMediaResult:
        self.last_run = RunRecord()
        t0 = time.monotonic()
        try:
            items = await self._run_actor(self.spec.build_input(request.url))
        finally:
            self.last_run.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return self._validate(items, request, self.last_run.elapsed_ms)

    def _validate(self, items, request: ChinaResolveRequest, elapsed_ms: int) -> NormalizedMediaResult:
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise self._fail("parse_failed", "empty dataset")
        parsed = self.spec.parse_item(items[0])
        if parsed.error:
            cat = "parse_failed"
            if self.spec.classify_item_error:
                cat = self.spec.classify_item_error(parsed.error) or "parse_failed"
            raise self._fail(cat, f"item error: {parsed.error}")
        if not (parsed.title or "").strip():
            raise self._fail("parse_failed", "missing title")
        if not parsed.media_url or not parsed.media_url.lower().startswith(("http://", "https://")):
            raise self._fail("parse_failed", "missing media url")
        if parsed.duration_sec is not None:
            try:
                d = float(parsed.duration_sec)
            except (TypeError, ValueError):
                raise self._fail("parse_failed", "duration not numeric")
            if not (0 < d <= MAX_SANE_DURATION_SEC):
                raise self._fail("parse_failed", f"duration out of range: {d}")
            parsed.duration_sec = d
        else:
            self.last_run.duration_missing = True
            if self.spec.require_duration:
                raise self._fail("parse_failed", "missing duration")

        self.last_run.media_url_expiry = _expiry_from_url(parsed.media_url)
        formats = [NormalizedMediaFormat(
            format_id="managed_video", ext="mp4", label="video",
            width=parsed.width, has_video=True, has_audio=True,
            source_url=parsed.media_url,
        )]
        if parsed.audio_url and parsed.audio_url.lower().startswith(("http://", "https://")):
            formats.append(NormalizedMediaFormat(
                format_id="managed_audio", ext="mp3", label="audio",
                has_audio=True, has_video=False, source_url=parsed.audio_url,
            ))
        return NormalizedMediaResult(
            platform=self.platform,
            canonical_url=request.url,
            media_id=parsed.media_id,
            title=parsed.title.strip()[:500],
            uploader=parsed.uploader,
            uploader_id=parsed.uploader_id,
            thumbnail_url=parsed.thumbnail_url,
            duration_sec=parsed.duration_sec,
            formats=formats,
            provider_name=self.name,
            provider_mode="managed",
            # No provider gives a trustworthy watermark flag (plan §12.2).
            watermark_state="unknown",
            resolution_time_ms=elapsed_ms,
        )

    @staticmethod
    def scrub(text) -> str:
        return redact(text)
