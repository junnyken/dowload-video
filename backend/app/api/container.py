"""
Container API — Phase 26-A (YouTube proxy-safe)
=================================================
New endpoints for container-source deep workflows.

POST /api/v1/resolve-input               — classify URL + return capability snapshot
POST /api/v1/discover-container          — start async discovery, return job_id + container_id
GET  /api/v1/discover-container/{job_id} — poll DiscoveryJobSnapshot (PR2 contract)
GET  /api/v1/container/{id}              — poll discovery status + sections (legacy compat)
POST /api/v1/container/{id}/expand       — expand a lazy section or child
POST /api/v1/container/{id}/queue        — queue selected items → existing batch flow
POST /api/v1/container/{id}/refresh      — invalidate cache + re-discover
GET  /api/v1/container/{id}/manifest     — CSV export of all discovered items
GET  /api/v1/platforms/capabilities      — runtime capability matrix (proxy/cookie aware)
GET  /api/v1/admin/container-stats       — admin stats + capability snapshot

Phase 26-A changes (YouTube):
  - YouTube single_video always returns 503 youtube_single_temporarily_disabled
  - YouTube channel/playlist gates on YTDL_PROXY health (3 states: no_proxy/unhealthy/healthy)
  - Expand re-checks proxy health so stale sessions don't silently fail
  - Queue blocked for YouTube (youtube_container_queue_not_available) — downloads not ready
  - /platforms/capabilities now includes 5 YouTube-specific truthfulness fields

All single-download flows (POST /fetch-link, POST /bulk-download) are
UNCHANGED — these endpoints are additive only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import time
import uuid
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.container_discovery import (
    DiscoveryJobStatus,
    DiscoveryJobSnapshot,
    DiscoveryStage,
    STAGE_PROGRESS,
)
from app.services.container_cache import (
    acquire_discovery_lock,
    check_queue_dedup,
    get_active_job_for_url,
    get_job,
    get_job_meta,
    invalidate_all_for_container,
    save_batch_source_meta,
    save_job,
    save_job_meta,
    set_queue_dedup,
    set_active_job_for_url,
)
from app.services.container_discovery import (
    ContainerItem, ContainerMeta, ContainerSection,
    cache_container, get_cached_container, get_url_for_container_id,
    invalidate_container, make_container_id, track_container_metric,
)
from app.services.container_dedupe import DedupeLayer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Container"])

# ── Container source types that support the deep workflow ─────────────────────
_CONTAINER_SOURCE_TYPES = {
    "profile", "channel", "playlist", "album", "artist",
    "board", "feed", "subreddit", "media_tab",
}

# ── Platform → expander support ───────────────────────────────────────────────
_SUPPORTED_PLATFORMS = {
    "spotify":    "full",
    "soundcloud": "full",
    "threads":    "full",
    "pinterest":  "full",
    "tiktok":     "full",
    "douyin":     "full",
    "facebook":   "partial",
    "instagram":  "cookie_required",
    "twitter":    "cookie_required",
    "reddit":     "partial",
    "youtube":    "proxy_required",  # PR2: demoted from temporarily_disabled
}


# ── Request / response models ─────────────────────────────────────────────────

class ResolveInputRequest(BaseModel):
    url: str = Field(..., min_length=4)


class DiscoverRequest(BaseModel):
    url: str = Field(..., min_length=4)
    max_items: int = Field(default=100, ge=1, le=500)
    include_sections: Optional[list[str]] = None


class ExpandRequest(BaseModel):
    section: str
    child_id: str = ""
    cursor: str = ""


class QueueRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    queue_mode: str = "selected"    # selected | latest_n | top_n | section | all
    n: Optional[int] = None
    section_key: Optional[str] = None
    apply_dedupe: bool = True
    quality: str = "mp3_320"


def _queue_request_hash(container_id: str, req: QueueRequest) -> str:
    """Stable hash of a queue request for idempotency dedup within QUEUE_DEDUP_TTL."""
    payload = {
        "cid":     container_id,
        "ids":     sorted(req.item_ids or []),
        "mode":    req.queue_mode,
        "n":       req.n,
        "section": req.section_key,
        "quality": req.quality,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_container_or_404(container_id: str) -> tuple[str, ContainerMeta]:
    url = get_url_for_container_id(container_id)
    if not url:
        raise HTTPException(404, detail="Container không tồn tại hoặc đã hết hạn.")
    meta = get_cached_container(url)
    if not meta:
        raise HTTPException(404, detail="Container đã hết hạn. Vui lòng discover lại.")
    return url, meta


def _meta_to_response(meta: ContainerMeta) -> dict:
    d = asdict(meta)
    # Sections: serialize items + children
    for s in d.get("sections", []):
        s["items"] = s.get("items", [])
        s["children"] = s.get("children", [])
    return d


def _collect_items_by_mode(
    meta: ContainerMeta,
    request: QueueRequest,
) -> list[ContainerItem]:
    """Select items from container based on queue_mode."""
    all_items: list[ContainerItem] = []
    for section in meta.sections:
        if request.section_key and section.key != request.section_key:
            continue
        all_items.extend(section.items)

    if request.queue_mode == "selected" and request.item_ids:
        id_set = set(request.item_ids)
        return [i for i in all_items if i.id in id_set or i.url in id_set]

    if request.queue_mode == "latest_n" and request.n:
        return all_items[:request.n]

    if request.queue_mode == "top_n" and request.n:
        return sorted(all_items, key=lambda x: x.views, reverse=True)[:request.n]

    if request.queue_mode == "section":
        return all_items

    # "all" or fallback
    return all_items


async def _create_bulk_jobs(
    urls: list[str],
    batch_id: str,
    quality: str,
    user_id: Optional[str],
) -> int:
    """
    Insert download_jobs rows and dispatch process_video_task for each URL.
    Mirrors the logic in POST /bulk-download (routes.py) without repeating
    quota enforcement (container workflow assumes session is already authenticated).
    """
    from app.core.database import get_supabase_client
    from app.tasks.video_tasks import process_video_task

    supabase = get_supabase_client()
    queued = 0
    for url in urls:
        job_id = str(uuid.uuid4())
        try:
            supabase.table("download_jobs").insert({
                "id": job_id,
                "batch_id": batch_id,
                "original_url": url,
                "status": "pending",
                "job_stage": "queued",
                "selected_quality": quality,
                "user_id": user_id,
                "job_type": "bulk",
            }).execute()
            process_video_task.apply_async(
                args=[job_id, url, user_id, quality],
                queue="downloads",
            )
            queued += 1
        except Exception:
            pass
    return queued


# ── POST /resolve-input ───────────────────────────────────────────────────────

@router.post("/resolve-input")
async def resolve_input(req: ResolveInputRequest):
    """
    Classify a URL and return its capability snapshot.

    Phase 26-A: performs a live proxy health probe for YouTube so the UI can
    show an accurate pre-discovery gate (not a spinner that ends in an error).

    Returns:
      platform, source_type, normalized_id, canonical_url, is_container,
      capability, can_discover, message
    """
    from app.core.url_normalizer import normalize
    from app.core.source_classifier import classify
    from app.services.youtube_proxy_health import get_youtube_proxy_health_async

    norm = normalize(req.url)
    url  = norm.canonical_url
    clf  = classify(url)

    is_container = clf.source_type in _CONTAINER_SOURCE_TYPES
    cap          = _runtime_capability(clf.platform)

    # ── YouTube-specific capability (live probe) ───────────────────────────────
    can_discover     = False
    discovery_message = ""

    if clf.platform == "youtube":
        if clf.source_type == "single_video":
            can_discover = False
            discovery_message = "youtube_single_temporarily_disabled"
        elif clf.source_type in ("channel", "playlist"):
            health = await get_youtube_proxy_health_async()
            logger.info(
                "resolve_input_yt url=%.60s source_type=%s proxy_state=%s",
                url, clf.source_type, health.state,
            )
            if health.state == "no_proxy":
                can_discover = False
                discovery_message = "youtube_proxy_required"
            elif health.state == "unhealthy":
                can_discover = False
                discovery_message = "youtube_proxy_unhealthy"
            else:
                can_discover = True
            # Expose live proxy state in the capability block
            cap = {**cap, "proxy_state": health.state}
        else:
            can_discover = False
            discovery_message = f"youtube_source_type_unsupported:{clf.source_type}"
    elif is_container:
        can_discover = cap.get("can_preview", False)

    return {
        "platform":       clf.platform,
        "source_type":    clf.source_type,
        "normalized_id":  clf.normalized_id,
        "canonical_url":  url,
        "is_container":   is_container,
        "capability":     cap,
        "can_discover":   can_discover,
        "message":        discovery_message,
    }


# ── POST /discover-container ──────────────────────────────────────────────────

@router.post("/discover-container")
async def discover_container(req: DiscoverRequest):  # noqa: ARG001 request removed; use req.url
    """
    Start async container discovery.

    PR2 contract: returns job_id + container_id.
    Frontend polls GET /discover-container/{job_id} for DiscoveryJobSnapshot.
    Legacy polling via GET /container/{container_id} still works.

    - Cache hit (status=ready/partial) → returns snapshot instantly, from_cache=True.
    - In-flight duplicate → returns the existing job_id (no double work).
    - Cache miss → spawns PR2 discover_container_task.
    """
    from app.core.url_normalizer import normalize
    from app.core.source_classifier import classify

    norm = normalize(req.url)
    url = norm.canonical_url

    clf = classify(url)

    # ── Phase 26-A: YouTube pre-flight ────────────────────────────────────────
    if clf.platform == "youtube":
        if clf.source_type == "single_video":
            logger.info(
                "discover_blocked_single_yt url=%.60s source_type=%s",
                url, clf.source_type,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code":    "youtube_single_temporarily_disabled",
                    "message": (
                        "YouTube single video tạm thời không khả dụng. "
                        "Dùng channel hoặc playlist để dùng tính năng Bulk Browse."
                    ),
                },
            )
        if clf.source_type not in ("channel", "playlist"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code":    "youtube_source_type_unsupported",
                    "message": (
                        f"YouTube source_type '{clf.source_type}' chưa được hỗ trợ. "
                        "Chỉ channel và playlist."
                    ),
                },
            )
        # Live proxy health gate (cache TTL 45s → only one probe per 45s)
        from app.services.youtube_proxy_health import get_youtube_proxy_health_async
        _yt_health = await get_youtube_proxy_health_async()
        logger.info(
            "discover_yt_preflight url=%.60s source_type=%s proxy_state=%s",
            url, clf.source_type, _yt_health.state,
        )
        if _yt_health.state == "no_proxy":
            raise HTTPException(
                status_code=503,
                detail={
                    "code":    "youtube_proxy_required",
                    "message": (
                        "YouTube container discovery cần YTDL_PROXY residential. "
                        "Liên hệ admin để cấu hình biến môi trường."
                    ),
                },
            )
        if _yt_health.state == "unhealthy":
            raise HTTPException(
                status_code=503,
                detail={
                    "code":    "youtube_proxy_unhealthy",
                    "message": (
                        "YouTube proxy hiện không hoạt động. "
                        "Thử lại sau ít phút hoặc liên hệ admin."
                    ),
                },
            )

    # ── Phase 26-B: Cookie pre-flight for Instagram / Twitter ────────────────
    if clf.platform in ("instagram", "twitter"):
        from app.services.cookie_capability import evaluate as _eval_cookie
        _ck = _eval_cookie(clf.platform)
        if not _ck["can_discover_container"]:
            state = _ck.get("cookie_state", "cookie_unavailable")
            logger.warning(
                "discover_blocked_cookie platform=%s state=%s url=%.60s",
                clf.platform, state, url,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code":               f"{clf.platform}_{state}",
                    "message":            _ck.get("message") or "Cookie không hợp lệ.",
                    "availability_reason": state,
                },
            )
        logger.info(
            "discover_cookie_preflight platform=%s state=%s url=%.60s",
            clf.platform, _ck.get("cookie_state"), url,
        )

    if clf.source_type not in _CONTAINER_SOURCE_TYPES:
        raise HTTPException(
            400,
            detail=(
                f"URL này là '{clf.source_type}' — không phải container source. "
                "Dùng POST /fetch-link cho video đơn."
            ),
        )

    support = _SUPPORTED_PLATFORMS.get(clf.platform, "partial")
    if support == "temporarily_disabled":
        raise HTTPException(503, detail=f"{clf.platform} container tạm thời không khả dụng.")

    now = time.time()

    # ── Cache hit ─────────────────────────────────────────────────────────────
    cached = get_cached_container(url)
    if cached and cached.status in ("ready", "partial"):
        track_container_metric(clf.platform, "cache_hit")
        # Reuse or create a snapshot for this cache hit
        existing_job_id = get_active_job_for_url(url)
        if existing_job_id:
            job = get_job(existing_job_id)
            if job and job.status in (DiscoveryJobStatus.success, DiscoveryJobStatus.partial):
                return {
                    "job_id": existing_job_id,
                    "container_id": cached.container_id,
                    "status": cached.status,
                    "cached": True,
                    "platform": cached.platform,
                    "container_type": cached.container_type,
                    "title": cached.title,
                    "poll_url": f"/api/v1/discover-container/{existing_job_id}",
                    "eta_seconds": 0,
                }
        # No job snapshot yet (pre-PR2 cache) — return minimal response
        return {
            "job_id": "",
            "container_id": cached.container_id,
            "status": cached.status,
            "cached": True,
            "platform": cached.platform,
            "container_type": cached.container_type,
            "title": cached.title,
            "poll_url": f"/api/v1/container/{cached.container_id}",
            "eta_seconds": 0,
        }

    # ── Duplicate detection (in-flight lock) ──────────────────────────────────
    existing_job_id = get_active_job_for_url(url)
    if existing_job_id:
        existing_job = get_job(existing_job_id)
        if existing_job and existing_job.status not in (
            DiscoveryJobStatus.failed, DiscoveryJobStatus.expired
        ):
            # Another job is already running — return it
            return {
                "job_id": existing_job_id,
                "container_id": existing_job.container_id,
                "status": existing_job.status,
                "cached": False,
                "platform": existing_job.platform,
                "container_type": existing_job.source_type,
                "title": "",
                "poll_url": f"/api/v1/discover-container/{existing_job_id}",
                "eta_seconds": 12,
            }

    # ── New discovery job ─────────────────────────────────────────────────────
    container_id = make_container_id()
    job_id = "job_" + uuid.uuid4().hex[:12]

    # Acquire in-flight lock (NX, 5 min TTL)
    acquire_discovery_lock(url, job_id)

    # Seed ContainerMeta (backward compat for GET /container/{id})
    meta = ContainerMeta(
        container_id=container_id,
        platform=clf.platform,
        container_type=clf.source_type,
        url=url,
        title="",
        status="discovering",
        support_level=support,
    )
    cache_container(meta)

    # Create DiscoveryJobSnapshot (PR2 polling contract)
    snapshot = DiscoveryJobSnapshot(
        job_id=job_id,
        container_id=container_id,
        platform=clf.platform,
        source_type=clf.source_type,
        status=DiscoveryJobStatus.queued,
        stage=DiscoveryStage.normalize_input,
        progress_pct=STAGE_PROGRESS[DiscoveryStage.normalize_input],
        message="Đang chuẩn hóa link…",
        created_at=now,
        updated_at=now,
        expires_at=now + 1800,
        canonical_url=url,
        raw_input=req.url,
    )
    save_job(snapshot)
    save_job_meta(job_id, container_id, url)   # long-lived recovery mapping
    set_active_job_for_url(url, job_id)
    track_container_metric(clf.platform, "discover_start")
    logger.info("discover_queued job_id=%s platform=%s url=%.80s", job_id, clf.platform, url)

    # Dispatch PR2 Celery task
    from app.tasks.container_tasks import discover_container_task as _task
    _task.apply_async(
        args=[job_id, url, container_id],
        kwargs={
            "max_items": req.max_items,
            "include_sections": req.include_sections,
            "raw_input": req.url,
        },
        queue="bulk",
    )

    return {
        "job_id": job_id,
        "container_id": container_id,
        "status": "queued",
        "cached": False,
        "platform": clf.platform,
        "container_type": clf.source_type,
        "title": "",
        "poll_url": f"/api/v1/discover-container/{job_id}",
        "eta_seconds": 15,
    }


# ── GET /discover-container/{job_id} — PR2 polling endpoint ──────────────────

@router.get("/discover-container/{job_id}")
async def get_discovery_job(job_id: str):
    """
    Poll a DiscoveryJobSnapshot by job_id.

    Recovery behavior (PR3):
    - If snapshot is missing but job_meta + container data exist → return recovered success/partial
    - If both are gone → return expired with retryable=True so frontend shows "Re-discover" CTA
    """
    job = get_job(job_id)

    if job is not None:
        return job.model_dump()

    # ── Snapshot missing — try recovery via job_meta ──────────────────────────
    meta_info = get_job_meta(job_id)
    if meta_info:
        cached = get_cached_container(meta_info["url"])
        if cached and cached.status in ("ready", "partial"):
            now = time.time()
            recovered_status = (
                DiscoveryJobStatus.success if cached.status == "ready"
                else DiscoveryJobStatus.partial
            )
            logger.info(
                "discover_recovered job_id=%s container_id=%s status=%s",
                job_id, cached.container_id, recovered_status,
            )
            return {
                "job_id":              job_id,
                "container_id":        cached.container_id,
                "platform":            cached.platform,
                "source_type":         cached.container_type,
                "status":              recovered_status,
                "progress_pct":        100,
                "stage":               DiscoveryStage.finalize,
                "message":             "Đã khôi phục từ cache.",
                "from_cache":          True,
                "recovered_from_cache": True,
                "partial":             cached.status == "partial",
                "warnings":            ["container_cache_hit"],
                "summary":             {
                    "container_id": cached.container_id,
                    "platform":     cached.platform,
                    "container_type": cached.container_type,
                    "title":        cached.title,
                    "item_count":   cached.item_count,
                    "status":       cached.status,
                },
                "sections":  [],
                "stats":     {},
                "error":     None,
                "canonical_url": meta_info["url"],
                "raw_input":     "",
                "terminal_reason": None,
                "created_at":  now,
                "updated_at":  now,
                "expires_at":  now + 1800,
                "processing_started_at": 0.0,
            }

    # ── Both snapshot and container data are gone — return expired ────────────
    return {
        "job_id":        job_id,
        "container_id":  "",
        "platform":      "unknown",
        "source_type":   "unknown",
        "status":        DiscoveryJobStatus.expired,
        "progress_pct":  0,
        "stage":         DiscoveryStage.normalize_input,
        "message":       "Job không tồn tại hoặc đã hết hạn. Vui lòng khám phá lại.",
        "from_cache":    False,
        "recovered_from_cache": False,
        "partial":       False,
        "warnings":      [],
        "summary":       None,
        "sections":      [],
        "stats":         {},
        "error":         {"code": "JOB_EXPIRED", "message": "Snapshot đã hết hạn.", "retryable": True},
        "terminal_reason": "expired",
        "canonical_url": "",
        "raw_input":     "",
        "created_at":    0.0,
        "updated_at":    0.0,
        "expires_at":    0.0,
        "processing_started_at": 0.0,
    }


# ── GET /container/{container_id} ─────────────────────────────────────────────

@router.get("/container/{container_id}")
async def get_container(container_id: str):
    """Poll container discovery status + sections."""
    _, meta = _get_container_or_404(container_id)
    return _meta_to_response(meta)


# ── POST /container/{container_id}/expand ─────────────────────────────────────

@router.post("/container/{container_id}/expand")
async def expand_section(container_id: str, req: ExpandRequest):
    """
    Expand a lazy section (items_loaded=False) or a specific child.

    Examples:
      {"section": "albums"}              → load all album children's tracks
      {"section": "albums", "child_id": "sp_album:abc123"} → expand one album
    """
    url, meta = _get_container_or_404(container_id)

    # Find the target section
    target_section: Optional[ContainerSection] = None
    for s in meta.sections:
        if s.key == req.section:
            target_section = s
            break

    if target_section is None:
        raise HTTPException(404, detail=f"Section '{req.section}' không tìm thấy.")

    # ── Phase 26-A: YouTube expand proxy re-check ─────────────────────────────
    # Re-check health in case the proxy went down after the initial discovery.
    # This prevents a "partial expand" where some sections succeed and others
    # silently return [] without explanation.
    if meta.platform == "youtube":
        from app.services.youtube_proxy_health import get_youtube_proxy_health_async
        _expand_health = await get_youtube_proxy_health_async()
        logger.info(
            "expand_yt_proxy_check container_id=%s section=%s proxy_state=%s",
            container_id[:12], req.section, _expand_health.state,
        )
        if _expand_health.state == "no_proxy":
            raise HTTPException(
                status_code=503,
                detail={
                    "code":    "youtube_proxy_required",
                    "message": "YouTube expand cần YTDL_PROXY. YTDL_PROXY không được cấu hình.",
                },
            )
        if _expand_health.state == "unhealthy":
            raise HTTPException(
                status_code=503,
                detail={
                    "code":    "youtube_proxy_unhealthy",
                    "message": "YouTube proxy không hoạt động. Expand tạm thời không khả dụng.",
                },
            )

    # ── Phase 26-B: Cookie re-check for Instagram / Twitter expand ───────────
    if meta.platform in ("instagram", "twitter"):
        from app.services.cookie_capability import evaluate as _eval_cookie
        _ck = _eval_cookie(meta.platform)
        if not _ck["can_expand"]:
            state = _ck.get("cookie_state", "cookie_unavailable")
            logger.warning(
                "expand_blocked_cookie platform=%s state=%s container_id=%s",
                meta.platform, state, container_id[:12],
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code":               f"{meta.platform}_{state}",
                    "message":            _ck.get("message") or "Cookie không hợp lệ.",
                    "availability_reason": state,
                },
            )

    from app.services.container_expanders import get_expander
    expander = get_expander(meta.platform)

    if expander is None:
        raise HTTPException(400, detail=f"Platform '{meta.platform}' chưa hỗ trợ expand.")

    # Call platform-specific expand
    try:
        if hasattr(expander, "expand_section"):
            new_items = expander.expand_section(url, req.section, req.child_id)
        else:
            raise HTTPException(501, detail="Expander chưa implement expand_section.")
    except HTTPException:
        raise
    except Exception as e:
        track_container_metric(meta.platform, "expand_fail")
        raise HTTPException(500, detail=f"Expand thất bại: {str(e)[:200]}")

    # Update the section in cached meta
    target_section.items = new_items
    target_section.items_loaded = True
    target_section.item_count = len(new_items)
    cache_container(meta)
    track_container_metric(meta.platform, "expand_ok")

    return {
        "section": req.section,
        "child_id": req.child_id,
        "items_loaded": len(new_items),
        "items": [asdict(i) for i in new_items],
    }


# ── POST /container/{container_id}/queue ──────────────────────────────────────

@router.post("/container/{container_id}/queue")
async def queue_container(container_id: str, req: QueueRequest, request: Request):
    """
    Queue selected container items as a batch download job.

    PR3: idempotent — same request within 60s returns the existing batch_id.
    Returns {batch_id, jobs_queued, dedupe_summary, source_container_id}.
    """
    url, meta = _get_container_or_404(container_id)

    if meta.status not in ("ready", "partial"):
        raise HTTPException(400, detail="Container chưa sẵn sàng. Đợi discover hoàn thành.")

    # ── Phase 26-A: YouTube queue policy block ────────────────────────────────
    # YouTube single-video downloads are disabled (Oracle IP bot-blocked) so
    # creating a batch that fans out to process_video_task would generate 100%
    # failure rates. Reject explicitly so no phantom batch is created.
    if meta.platform == "youtube":
        logger.info(
            "queue_blocked_by_policy platform=youtube container_id=%s", container_id[:12],
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code":    "youtube_container_queue_not_available",
                "message": (
                    "YouTube download hiện chưa khả dụng (Oracle IP bot-block). "
                    "Queue không được tạo để tránh batch ảo."
                ),
                "retryable": False,
            },
        )

    # ── Phase 26-B: Cookie re-check before Instagram/Twitter queue ──────────
    if meta.platform in ("instagram", "twitter"):
        from app.services.cookie_capability import evaluate as _eval_cookie
        _ck = _eval_cookie(meta.platform)
        if not _ck["can_queue_container_items"]:
            state = _ck.get("cookie_state", "cookie_unavailable")
            logger.warning(
                "queue_blocked_cookie platform=%s state=%s container_id=%s",
                meta.platform, state, container_id[:12],
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code":               f"{meta.platform}_{state}",
                    "message":            _ck.get("message") or "Cookie không hợp lệ.",
                    "availability_reason": state,
                    "retryable":          state in ("cookie_blocked",),
                },
            )

    # ── Idempotency check ─────────────────────────────────────────────────────
    req_hash = _queue_request_hash(container_id, req)
    existing_batch = check_queue_dedup(req_hash)
    if existing_batch:
        logger.info("queue_dedup container_id=%s batch_id=%s", container_id[:12], existing_batch)
        return {
            "batch_id":           existing_batch,
            "jobs_queued":        0,
            "duplicate_request":  True,
            "dedupe_summary": {
                "accepted_count": 0,
                "dropped_count":  0,
                "reasons":        ["duplicate_request"],
            },
            "source_container_id": container_id,
            "poll_url": f"/api/v1/jobs/{existing_batch}",
        }

    items = _collect_items_by_mode(meta, req)
    if not items:
        raise HTTPException(400, detail="Không có mục nào được chọn.")

    # ── Dedupe ────────────────────────────────────────────────────────────────
    total_before = len(items)
    dedupe_summary: dict = {
        "accepted_count": total_before,
        "dropped_count":  0,
        "reasons":        [],
    }
    if req.apply_dedupe:
        deduper = DedupeLayer()
        result = deduper.filter(items)
        items = result.kept
        dropped = total_before - len(items)
        dedupe_summary = {
            "accepted_count": len(items),
            "dropped_count":  dropped,
            "reasons":        result.reasons_list if dropped else [],
        }

    if not items:
        raise HTTPException(400, detail="Tất cả mục đã bị loại do trùng lặp.")

    # ── Get optional user_id ──────────────────────────────────────────────────
    # get_optional_user takes (request, credentials, x_api_key) and the last two
    # default to Depends(...) objects. Calling it with just `request` left
    # x_api_key holding a raw Depends — truthy, so it tried .startswith on it
    # and raised, and the except below swallowed that. Every container queue
    # ran as anonymous: the jobs were never attributed to the signed-in user,
    # so they never appeared in that account's history and never counted
    # against its quota. It also assigned the whole user dict to a variable
    # typed Optional[str] and passed it on as the job's user_id.
    user_id: Optional[str] = None
    try:
        from fastapi.security import HTTPBearer
        from app.core.auth_middleware import get_optional_user

        cred = await HTTPBearer(auto_error=False)(request)
        user = await get_optional_user(
            request, cred, request.headers.get("X-API-Key"),
        )
        user_id = user.get("id") if user else None
    except Exception:
        pass

    batch_id = str(uuid.uuid4())
    urls = [i.url for i in items if i.url]
    item_ids = [i.id for i in items if i.id]

    jobs_queued = await _create_bulk_jobs(urls, batch_id, req.quality, user_id)

    # ── Persist source provenance + idempotency record ────────────────────────
    save_batch_source_meta(batch_id, container_id, meta.platform, item_ids, req.section_key or "")
    set_queue_dedup(req_hash, batch_id)

    track_container_metric(meta.platform, "queued", jobs_queued)
    logger.info(
        "queue_created container_id=%s batch_id=%s jobs=%d platform=%s",
        container_id[:12], batch_id, jobs_queued, meta.platform,
    )

    # Phase 26-B: Reddit partial queue annotation
    # Reddit containers only contain public items (the expander already filtered
    # gated content), so accepted_count == jobs_queued and rejected_count == 0.
    reddit_partial = meta.platform == "reddit"

    return {
        "batch_id":             batch_id,
        "jobs_queued":          jobs_queued,
        "dedupe_summary":       dedupe_summary,
        "source_container_id":  container_id,
        "poll_url":             f"/api/v1/jobs/{batch_id}",
        # Partial queue fields (Reddit only for now)
        "partial_accepted":     reddit_partial,
        "accepted_count":       jobs_queued if reddit_partial else None,
        "rejected_count":       0            if reddit_partial else None,
        "rejected_reason":      "reddit_login_required" if reddit_partial else None,
    }


# ── POST /container/{container_id}/refresh ────────────────────────────────────

@router.post("/container/{container_id}/refresh")
async def refresh_container(container_id: str):
    """
    Invalidate all cache keys for this container and queue a fresh discovery.
    PR3: properly invalidates PR2 keys (expand cache, summary, url_job mapping)
    in addition to the legacy PR1 vidgrab:container:* key.
    """
    url = get_url_for_container_id(container_id)
    if not url:
        raise HTTPException(404, detail="Container không tồn tại.")

    # Invalidate PR1 legacy cache
    meta = get_cached_container(url)
    platform = meta.platform if meta else "unknown"
    invalidate_container(url)

    # Invalidate all PR2 keys
    invalidate_all_for_container(url, platform, container_id)

    logger.info("container_refresh container_id=%s platform=%s", container_id[:12], platform)

    # Re-discover via the same logic (no circular import — just call the function directly)
    return await discover_container(DiscoverRequest(url=url))


# ── GET /container/{container_id}/manifest ────────────────────────────────────

def _runtime_capability(platform: str) -> dict:
    """
    Returns actual runtime support flags — not just what the code can do,
    but what the production environment allows right now.

    Phase 26-A: YouTube gets a 5-field truthfulness matrix. The proxy health
    probe is NOT run here (this is called from sync admin endpoints); the
    capability reflects proxy configured status only. Use get_youtube_proxy_health
    for live state in the discover/expand/resolve paths.
    """
    level = _SUPPORTED_PLATFORMS.get(platform, "unsupported")

    if level == "cookie_required":
        # Phase 26-B: use actual Redis pool state, not just env-var presence
        from app.services.cookie_capability import evaluate as _eval_cookie
        _ck = _eval_cookie(platform)
        return {
            "support_level":             level,
            "can_discover_container":    _ck["can_discover_container"],
            "can_expand":                _ck["can_expand"],
            "can_queue_container_items": _ck["can_queue_container_items"],
            "can_single_download":       _ck["can_single_download"],
            "can_preview":               _ck["can_discover_container"],
            "can_queue":                 _ck["can_queue_container_items"],
            "requires_cookie":           True,
            "availability_reason":       _ck.get("availability_reason"),
            "cookie_state":              _ck.get("cookie_state"),
        }

    if level == "proxy_required":
        proxy_configured = bool(os.environ.get("YTDL_PROXY") or os.environ.get("RESIDENTIAL_PROXY_URL"))

        if platform == "youtube":
            # Phase 26-A YouTube-specific truth matrix
            return {
                "support_level":             level,
                # Container (channel / playlist) — needs healthy proxy
                "can_discover_container":    proxy_configured,
                "can_expand":                proxy_configured,
                # Queue is disabled until Oracle-IP block is resolved
                "can_queue_container_items": False,
                # Single video always off — Oracle datacenter IP bot-blocked
                "can_single_download":       False,
                # Legacy fields kept for backward compat
                "can_preview":               proxy_configured,
                "can_queue":                 False,
                "requires_proxy":            True,
                "proxy_configured":          proxy_configured,
                "availability_reason": (
                    "proxy_configured_check_health" if proxy_configured
                    else "youtube_proxy_not_configured"
                ),
                "note": (
                    "PR26-A: channel/playlist enabled when proxy healthy. "
                    "Single video disabled. Queue blocked."
                ),
            }

        has_proxy = proxy_configured
        return {
            "support_level":  level,
            "can_preview":    has_proxy,
            "can_queue":      has_proxy,
            "gating_reason":  None if has_proxy else "YTDL_PROXY not configured",
        }

    can = level in ("full", "partial")
    return {
        "support_level": level,
        "can_preview":   can,
        "can_queue":     level == "full",
        "gating_reason": None,
    }


@router.get("/platforms/capabilities")
async def platforms_capabilities():
    """
    Returns runtime capability matrix for all supported platforms.
    PR3: includes can_preview / can_queue based on actual env (proxy/cookie).
    """
    return {
        platform: _runtime_capability(platform)
        for platform in _SUPPORTED_PLATFORMS
    }


@router.get("/admin/container-stats")
async def container_admin_stats():
    """
    Admin overview of Phase 25 container discovery subsystem.
    Reads counters written by track_container_metric() in container_tasks.py.
    """
    from app.core.redis_client import get_redis
    import datetime

    try:
        rc = get_redis()
        today = datetime.date.today().isoformat()
        pattern = f"container:metric:{today}:*"
        raw_keys = rc.keys(pattern)

        stats: dict = {}
        for key in raw_keys:
            k = key.decode() if isinstance(key, bytes) else key
            # key format: container:metric:{date}:{platform}:{metric}
            parts = k.split(":")
            if len(parts) < 5:
                continue
            platform = parts[3]
            metric   = parts[4]
            val_raw  = rc.get(key)
            val      = int(val_raw) if val_raw else 0
            stats.setdefault(platform, {})[metric] = val

        # Aggregate totals across platforms
        totals: dict[str, int] = {}
        for platform_data in stats.values():
            for metric, count in platform_data.items():
                totals[metric] = totals.get(metric, 0) + count

        # Runtime capability snapshot
        capabilities = {
            platform: _runtime_capability(platform)
            for platform in _SUPPORTED_PLATFORMS
        }

        return {
            "date":          today,
            "by_platform":   stats,
            "totals":        totals,
            "capabilities":  capabilities,
        }
    except Exception as exc:
        logger.error("admin_container_stats_failed exc=%.200s", str(exc))
        raise HTTPException(500, detail=f"Stats unavailable: {str(exc)[:100]}")


def _iter_manifest_csv(meta):
    """Generator that yields CSV rows one at a time — O(1) memory per row."""
    hdr = io.StringIO()
    csv.writer(hdr).writerow(["id", "url", "title", "author", "duration_ms",
                               "media_type", "views", "published_at", "section"])
    yield hdr.getvalue()
    for section in meta.sections:
        for item in section.items:
            row = io.StringIO()
            csv.writer(row).writerow([
                item.id, item.url, item.title, item.author,
                item.duration_ms, item.media_type,
                item.views, item.published_at, section.key,
            ])
            yield row.getvalue()


@router.get("/container/{container_id}/manifest")
async def container_manifest(container_id: str):
    """Export all discovered items as CSV (streaming — no full-load in memory)."""
    _, meta = _get_container_or_404(container_id)
    filename = f"vidgrab_container_{container_id[:12]}.csv"
    return StreamingResponse(
        _iter_manifest_csv(meta),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
