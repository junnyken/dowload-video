"""
Normalized contracts for the China access layer (plan §5).

`url` is a plain validated string rather than pydantic HttpUrl: HttpUrl
re-serialises URLs (adds trailing slashes, re-encodes), which would change the
canonical form we hash for cache/dedupe.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Operation = Literal["single_media", "container_discovery", "container_expand", "metadata_only"]
ProviderMode = Literal["native", "managed", "cookie_session", "proxy_metadata"]
WatermarkState = Literal["watermark_free", "watermarked", "unknown"]
HealthState = Literal["healthy", "constrained", "degraded", "paused", "recovery"]


class ChinaResolveRequest(BaseModel):
    url: str
    operation: Operation = "single_media"
    user_id: Optional[str] = None
    anonymous_key: Optional[str] = None
    requested_quality: Optional[str] = None
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must be http(s)")
        return v


class NormalizedMediaFormat(BaseModel):
    format_id: str
    ext: str
    label: str
    height: Optional[int] = None
    width: Optional[int] = None
    codec: Optional[str] = None
    has_audio: bool = False
    has_video: bool = False
    estimated_size_bytes: Optional[int] = None
    requires_merge: bool = False
    source_url: Optional[str] = None
    expires_at: Optional[datetime] = None


class NormalizedMediaResult(BaseModel):
    platform: str
    canonical_url: str
    media_id: Optional[str] = None
    title: str
    uploader: Optional[str] = None
    # Platform id of the author's profile when the provider gives one
    # (Douyin: authorMeta.secUid → douyin.com/user/<id>; task #6055).
    uploader_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_sec: Optional[float] = None
    formats: list[NormalizedMediaFormat] = []
    subtitles: list[dict] = []
    container: Optional[dict] = None
    provider_name: str
    provider_mode: ProviderMode
    watermark_state: WatermarkState = "unknown"
    resolution_time_ms: int = 0
    cache_hit: bool = False

    def primary_video_url(self) -> str:
        for f in self.formats:
            if f.has_video and f.source_url:
                return f.source_url
        for f in self.formats:
            if f.source_url:
                return f.source_url
        return ""

    def audio_url(self) -> str:
        for f in self.formats:
            if f.has_audio and not f.has_video and f.source_url:
                return f.source_url
        return ""


class ProviderHealthSnapshot(BaseModel):
    provider_name: str
    platform: str
    state: HealthState = "healthy"
    consecutive_failures: int = 0
    last_failure_category: Optional[str] = None
    last_failure_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    eligible: bool = True


# ── request context ─────────────────────────────────────────────────────────

Origin = Literal["request", "worker", "probe", "benchmark"]


@dataclass(frozen=True)
class RequestContext:
    """Who is asking. Bound per request; never contains secrets.

    requester_key: "user:<id>" | "ip:<ip>" | "admin" | "unknown"
    private:       True when the caller supplied its own cookie — such a result
                   is user-specific and is neither cached nor sent to a managed
                   provider's cache key space.
    """
    requester_key: str = "unknown"
    is_admin: bool = False
    origin: Origin = "worker"
    private: bool = False
    allow_managed_benchmark: bool = False
    user_cookies_file: Optional[str] = None

    def with_(self, **kw) -> "RequestContext":
        return replace(self, **kw)


_CURRENT: ContextVar[Optional[RequestContext]] = ContextVar("china_access_ctx", default=None)


def current_context() -> RequestContext:
    return _CURRENT.get() or RequestContext()


def set_context(ctx: RequestContext):
    return _CURRENT.set(ctx)


def reset_context(token) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, LookupError):
        pass
