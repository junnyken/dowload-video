"""
Container Discovery Schemas — Phase 25 PR2
==========================================
Job lifecycle types for the async discovery framework.

These are ADDITIVE to container.py — they do not replace ContainerMeta.
Backend writes DiscoveryJobSnapshot to Redis (container_cache.py).
Frontend polls GET /discover-container/{job_id} for the snapshot.

Key design decisions:
  - job_id  : stable ID for THIS discovery run (changes on every re-discover)
  - container_id : stable ID for the discovered collection (reused on cache hits)
  - A single container_id may be served by many job_ids over time
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Status / Stage enums ──────────────────────────────────────────────────────

class DiscoveryJobStatus(str, Enum):
    queued      = "queued"
    resolving   = "resolving"
    discovering = "discovering"
    expanding   = "expanding"
    partial     = "partial"
    success     = "success"
    failed      = "failed"
    expired     = "expired"


class DiscoveryStage(str, Enum):
    normalize_input     = "normalize_input"
    resolve_capability  = "resolve_capability"
    load_cache          = "load_cache"
    fetch_summary       = "fetch_summary"
    fetch_preview       = "fetch_preview"
    assemble_sections   = "assemble_sections"
    finalize            = "finalize"
    expand_section      = "expand_section"


# Progress % landmarks per stage
STAGE_PROGRESS: dict[DiscoveryStage, int] = {
    DiscoveryStage.normalize_input:    5,
    DiscoveryStage.resolve_capability: 10,
    DiscoveryStage.load_cache:         15,
    DiscoveryStage.fetch_summary:      30,
    DiscoveryStage.fetch_preview:      60,
    DiscoveryStage.assemble_sections:  85,
    DiscoveryStage.finalize:           95,
    DiscoveryStage.expand_section:     50,
}


# ── Component models ──────────────────────────────────────────────────────────

class DiscoveryError(BaseModel):
    code:     str
    message:  str
    retryable: bool = False


class DiscoveryStats(BaseModel):
    sections_ready:   int = 0
    sections_total:   int = 0
    items_loaded:     int = 0
    items_estimated:  int = 0
    cache_hits:       int = 0
    expandables:      int = 0   # sections that still have lazy children


# ── Main snapshot — what GET /discover-container/{job_id} returns ─────────────

class DiscoveryJobSnapshot(BaseModel):
    """
    Full snapshot of a discovery job.
    Stored in Redis under container:job:{job_id}.
    Always serializes cleanly to JSON — frontend polls this directly.
    """
    job_id:       str
    container_id: str
    platform:     str
    source_type:  str           # matches SourceType enum values
    status:       DiscoveryJobStatus
    progress_pct: int           = Field(default=0, ge=0, le=100)
    stage:        DiscoveryStage = DiscoveryStage.normalize_input
    message:      str           = ""
    created_at:   float         = 0.0
    updated_at:   float         = 0.0
    expires_at:   float         = 0.0
    from_cache:   bool          = False
    partial:      bool          = False
    warnings:     list[str]     = Field(default_factory=list)

    # Inline data — filled progressively
    summary:      Optional[dict[str, Any]] = None   # ContainerMeta-shaped subset
    sections:     list[dict[str, Any]]     = Field(default_factory=list)
    stats:        DiscoveryStats           = Field(default_factory=DiscoveryStats)
    error:        Optional[DiscoveryError] = None

    # Original input (helps UI show context)
    canonical_url:      str = ""
    raw_input:          str = ""

    # PR3 hardening fields
    terminal_reason:      Optional[str] = None   # "timeout" | "cookie_required" | "proxy_required" | "no_expander" | etc.
    recovered_from_cache: bool          = False  # snapshot was lost but container data recovered
    processing_started_at: float        = 0.0   # timestamp when Celery task started


# ── Request schemas ───────────────────────────────────────────────────────────

class DiscoverContainerRequest(BaseModel):
    """
    Richer discover request (PR2 contract).
    Backward-compat: also accepts plain `url` for old callers.
    """
    # Primary inputs — at least one required
    url:           Optional[str] = Field(default=None, description="Shorthand for raw_input")
    raw_input:     Optional[str] = None
    resolved_item: Optional[dict[str, Any]] = None   # ResolveInputItem shape

    # Behavior flags
    context:          str = "web"      # web | bulk | extension
    preview_limit:    int = Field(default=20, ge=1, le=500)
    include_sections: Optional[list[str]] = None
    force_refresh:    bool = False

    def effective_url(self) -> Optional[str]:
        """Return the best input URL regardless of which field was used."""
        return self.url or self.raw_input or (
            self.resolved_item.get("canonical_url") if self.resolved_item else None
        )


class ExpandContainerRequest(BaseModel):
    """PR2 expand request — richer than the old ExpandRequest in container.py."""
    section_id:    Optional[str] = None
    ref_id:        Optional[str] = None
    limit:         Optional[int] = Field(default=None, ge=1, le=200)
    cursor:        str           = ""
    force_refresh: bool          = False

    # Legacy compat (old ExpandRequest used `section` + `child_id`)
    section:       Optional[str] = Field(default=None, exclude=True)
    child_id:      Optional[str] = Field(default=None, exclude=True)

    def effective_section(self) -> Optional[str]:
        return self.section_id or self.section

    def effective_ref(self) -> Optional[str]:
        return self.ref_id or self.child_id


# ── POST /discover-container response ────────────────────────────────────────

class DiscoverContainerResponse(BaseModel):
    """
    Immediate response from POST /discover-container.
    Frontend uses job_id to poll GET /discover-container/{job_id}.
    """
    job_id:        str
    container_id:  str
    status:        DiscoveryJobStatus
    cached:        bool
    platform:      str
    container_type: str
    title:         str  = ""
    poll_url:      str  = ""
    eta_seconds:   int  = 15


# ── Warning codes surfaced in discovery ──────────────────────────────────────

class DiscoveryWarningCode(str, Enum):
    container_partial_success      = "container_partial_success"
    container_section_expand_failed = "container_section_expand_failed"
    container_summary_only         = "container_summary_only"
    container_cache_hit            = "container_cache_hit"
    container_capability_not_ready = "container_capability_not_ready"
    container_discovery_timeout    = "container_discovery_timeout"
    container_expand_unavailable   = "container_expand_unavailable"
