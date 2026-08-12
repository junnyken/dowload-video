"""
Smart Analysis API
==================
Routes:
  POST /api/v1/analyze-media        — submit analysis job
  GET  /api/v1/analyze/{job_id}     — get analysis results
  POST /api/v1/smart-trim/apply     — apply trim suggestion → creates real trim job
  POST /api/v1/smart-gif/apply      — apply GIF suggestion → creates real GIF job
  POST /api/v1/smart-clips/apply    — apply clip suggestion → creates real download/trim job
  POST /api/v1/smart-metadata/clean — clean filename/tags (synchronous, no job needed)
"""

import os
import uuid
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Smart Analysis"])

# ---------------------------------------------------------------------------
# Tier gating constants (env-overridable)
# ---------------------------------------------------------------------------
FREE_ANALYSES_PER_DAY = int(os.getenv("AI_FREE_ANALYSES_PER_DAY", "5"))
FREE_CLIP_SUGGESTIONS_MAX = 1   # free gets 1 highlight only
PRO_ANALYSES_PER_DAY = int(os.getenv("AI_PRO_ANALYSES_PER_DAY", "50"))
MAX_DURATION_FREE_S = 600.0     # 10 min max for free
MAX_DURATION_PRO_S = 3600.0     # 60 min max for Pro

VALID_ANALYSES = {"trim", "gif", "metadata", "clips", "summary"}

# ---------------------------------------------------------------------------
# Lazy imports (avoid circular imports at module load time)
# ---------------------------------------------------------------------------

def _get_db():
    """Return a DB connection/session. Import lazily to avoid circular deps."""
    try:
        from app.core.database import get_db_conn  # noqa: PLC0415
        return get_db_conn()
    except ImportError:
        return None


def _get_redis():
    """Return a Redis client. Import lazily."""
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        return get_redis()
    except ImportError:
        return None


def _get_analyze_task():
    """Return the analyze_media Celery task. Import lazily."""
    try:
        from app.tasks.analysis_tasks import analyze_media_task  # noqa: PLC0415
        return analyze_media_task
    except ImportError:
        return None


def _get_trim_task():
    """Return the trim Celery task. Import lazily."""
    try:
        from app.tasks.video_tasks import trim_video_task  # noqa: PLC0415
        return trim_video_task
    except ImportError:
        return None


def _get_gif_task():
    """Return the GIF Celery task. Import lazily."""
    try:
        from app.tasks.video_tasks import create_gif_task  # noqa: PLC0415
        return create_gif_task
    except ImportError:
        return None


def _get_metadata_utils():
    """Return (clean_filename, suggest_tags) callables, normalized to the
    (title, metadata=None) -> str / (title, platform="", description="") -> list[str]
    contract this module's endpoints expect. Import lazily."""
    try:
        from app.services.smart_metadata import clean_filename as _clean, suggest_tags as _tags  # noqa: PLC0415

        def clean_filename(title: str, metadata: dict | None = None) -> str:
            return _clean(title, metadata=metadata or {})["filename"]

        def suggest_tags(title: str, platform: str = "", description: str = "") -> list[str]:
            return _tags(platform=platform, title=title, description=description)["tags"]

        return clean_filename, suggest_tags
    except ImportError:
        # Provide stub implementations so the endpoint degrades gracefully
        def clean_filename(title: str, metadata: dict | None = None) -> str:
            import re
            cleaned = re.sub(r'[<>:"/\\|?*]', "", title).strip()
            return cleaned[:200] if cleaned else title

        def suggest_tags(title: str, platform: str = "", description: str = "") -> list[str]:
            words = title.lower().split()
            return [w for w in words if len(w) > 3][:10]

        return clean_filename, suggest_tags


# ---------------------------------------------------------------------------
# Auth helper (optional user)
# ---------------------------------------------------------------------------

def get_optional_user(request: Request) -> dict | None:
    """
    Extract user from session/token if present; return None for anonymous.
    Attempts to reuse existing auth dependency; degrades gracefully.
    """
    try:
        from app.core.auth import get_current_user_optional  # noqa: PLC0415
        return get_current_user_optional(request)
    except ImportError:
        pass
    # Fallback: check for a simple session cookie or header
    user_id = request.headers.get("X-User-Id") or request.cookies.get("user_id")
    if user_id:
        return {"id": user_id, "tier": "free"}
    return None


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    url: str
    media_path: str | None = None
    analyses: list[str] = ["trim", "gif", "metadata"]
    duration_hint_s: float | None = None

    @field_validator("analyses")
    @classmethod
    def validate_analyses(cls, v: list[str]) -> list[str]:
        invalid = set(v) - VALID_ANALYSES
        if invalid:
            raise ValueError(f"Invalid analysis types: {invalid}. Valid: {VALID_ANALYSES}")
        return v


class SmartTrimApplyRequest(BaseModel):
    job_id: str
    suggestion_index: int = 0
    media_url: str
    quality: str = "best"


class SmartGifApplyRequest(BaseModel):
    job_id: str
    suggestion_index: int = 0
    media_url: str
    quality: str = "480p"


class SmartClipsApplyRequest(BaseModel):
    job_id: str
    suggestion_indices: list[int] = [0]
    media_url: str
    quality: str = "best"


class MetadataCleanRequest(BaseModel):
    title: str
    platform: str = ""
    uploader: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_analysis_quota(
    user: dict | None,
    request: Request,
) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Checks Redis or DB depending on user tier.
    """
    today_str = date.today().isoformat()

    if user is None:
        # Anonymous — 2/day per IP via Redis
        ip = _get_client_ip(request)
        redis = _get_redis()
        if redis is None:
            # Can't check quota; allow but log
            logger.warning("Redis unavailable — skipping anonymous quota check")
            return True, ""
        key = f"ai_quota:anon:{ip}:{today_str}"
        try:
            count = redis.get(key)
            current = int(count) if count else 0
            if current >= 2:
                return False, "Anonymous limit: 2 analyses per day. Sign in for more."
            redis.incr(key)
            redis.expire(key, 86400)
            return True, ""
        except Exception as exc:
            logger.warning("Redis quota check failed: %s", exc)
            return True, ""

    tier = user.get("tier", "free")
    user_id = user.get("id")

    if tier == "pro":
        daily_limit = PRO_ANALYSES_PER_DAY
    else:
        daily_limit = FREE_ANALYSES_PER_DAY

    # Check Redis first (fast path)
    redis = _get_redis()
    if redis is not None:
        key = f"ai_quota:user:{user_id}:{today_str}"
        try:
            count = redis.get(key)
            current = int(count) if count else 0
            if current >= daily_limit:
                return False, f"Daily limit reached ({daily_limit}/day for {tier} tier)."
            redis.incr(key)
            redis.expire(key, 86400)
            return True, ""
        except Exception as exc:
            logger.warning("Redis user quota check failed: %s", exc)

    # Fallback: DB check
    db = _get_db()
    if db is not None:
        try:
            with db as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT analyses_count FROM analysis_usage
                        WHERE user_id = %s AND date = %s
                        """,
                        (user_id, today_str),
                    )
                    row = cur.fetchone()
                    current = row[0] if row else 0
                    if current >= daily_limit:
                        return False, f"Daily limit reached ({daily_limit}/day for {tier} tier)."
        except Exception as exc:
            logger.warning("DB quota check failed: %s", exc)

    return True, ""


def _tier_filter_results(results: dict, tier: str) -> dict:
    """
    Apply per-tier limits to analysis results.
    - 'free'      : 1 trim, 1 clip, 2 GIF suggestions; heuristic summary only
    - 'pro'       : full results
    - 'anonymous' : same as free but clip_suggestions=[]
    """
    if tier == "pro":
        return results

    filtered = dict(results)

    # Trim: keep only first suggestion
    if "trim_suggestions" in filtered and filtered["trim_suggestions"]:
        filtered["trim_suggestions"] = filtered["trim_suggestions"][:1]

    # GIF: keep first 2
    if "gif_suggestions" in filtered and filtered["gif_suggestions"]:
        filtered["gif_suggestions"] = filtered["gif_suggestions"][:2]

    # Clips: 1 for free, none for anonymous
    if tier == "anonymous":
        filtered["clip_suggestions"] = []
    elif "clip_suggestions" in filtered and filtered["clip_suggestions"]:
        filtered["clip_suggestions"] = filtered["clip_suggestions"][:FREE_CLIP_SUGGESTIONS_MAX]

    # Summary: strip LLM narrative for free/anonymous (keep heuristic fields only)
    if "summary_suggestions" in filtered and isinstance(filtered["summary_suggestions"], dict):
        summary = dict(filtered["summary_suggestions"])
        summary.pop("llm_summary", None)
        summary.pop("ai_generated_description", None)
        filtered["summary_suggestions"] = summary

    filtered["tier_filtered"] = True
    return filtered


def _load_analysis_result(job_id: str) -> dict | None:
    """Load analysis result from DB. Returns None if not found."""
    db = _get_db()
    if db is None:
        return None
    try:
        with db as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT j.status, j.progress_hint, r.result_json, j.created_at
                    FROM analysis_jobs j
                    LEFT JOIN analysis_results r ON r.job_id = j.id
                    WHERE j.id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                status, progress_hint, result_json, created_at = row
                return {
                    "status": status,
                    "progress_hint": progress_hint,
                    "result": result_json,
                    "created_at": created_at,
                }
    except Exception as exc:
        logger.error("DB load analysis result error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/analyze-media")
async def submit_analyze_media(
    body: AnalyzeRequest,
    request: Request,
    user: dict | None = Depends(get_optional_user),
):
    """
    Submit a media analysis job.
    Returns job_id immediately; poll GET /analyze/{job_id} for results.
    """
    # Tier limits
    tier = (user or {}).get("tier", "anonymous" if user is None else "free")
    max_duration = MAX_DURATION_PRO_S if tier == "pro" else MAX_DURATION_FREE_S

    if body.duration_hint_s is not None and body.duration_hint_s > max_duration:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Media duration {body.duration_hint_s:.0f}s exceeds "
                f"{tier} tier limit of {max_duration:.0f}s."
            ),
        )

    # Quota check
    allowed, reason = _check_analysis_quota(user, request)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # Check DB cache by media_path fingerprint
    if body.media_path:
        db = _get_db()
        if db is not None:
            try:
                with db as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id FROM analysis_jobs
                            WHERE media_fingerprint = %s
                              AND status = 'done'
                              AND expires_at > NOW()
                            ORDER BY created_at DESC
                            LIMIT 1
                            """,
                            (body.media_path,),
                        )
                        row = cur.fetchone()
                        if row:
                            cached_job_id = str(row[0])
                            return {
                                "job_id": cached_job_id,
                                "status": "cached",
                                "estimated_wait_s": 0,
                            }
            except Exception as exc:
                logger.warning("Cache lookup failed: %s", exc)

    # Create new job row
    job_id = str(uuid.uuid4())
    user_id = (user or {}).get("id")

    db = _get_db()
    if db is not None:
        try:
            with db as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO analysis_jobs
                            (id, url, media_fingerprint, analyses, status, user_id, created_at)
                        VALUES (%s, %s, %s, %s, 'queued', %s, NOW())
                        """,
                        (
                            job_id,
                            body.url,
                            body.media_path,
                            body.analyses,
                            user_id,
                        ),
                    )
                    conn.commit()
        except Exception as exc:
            logger.error("Failed to insert analysis_jobs row: %s", exc)
            # Don't block the user — still queue the task

    # Submit Celery task
    analyze_task = _get_analyze_task()
    if analyze_task is not None:
        try:
            analyze_task.apply_async(args=[job_id], queue="analysis")
        except Exception as exc:
            logger.error("Failed to queue analyze_media_task: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Analysis queue unavailable. Please try again later.",
            ) from exc
    else:
        logger.warning("analyze_media_task not importable — job %s queued in DB only", job_id)

    return {
        "job_id": job_id,
        "status": "queued",
        "estimated_wait_s": 10,
    }


@router.get("/analyze/{job_id}")
async def get_analysis_result(
    job_id: str,
    request: Request,
    user: dict | None = Depends(get_optional_user),
):
    """
    Poll for analysis results by job_id.
    Returns partial progress info while still processing.
    """
    row = _load_analysis_result(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Analysis job '{job_id}' not found.")

    status = row["status"]
    if status != "done":
        return {
            "job_id": job_id,
            "status": status,
            "progress_hint": row.get("progress_hint", "Processing…"),
        }

    result: dict[str, Any] = row.get("result") or {}

    # Apply tier filtering
    tier = (user or {}).get("tier", "anonymous" if user is None else "free")
    result = _tier_filter_results(result, tier)

    return {
        "job_id": job_id,
        "status": "done",
        "probe": result.get("probe", {}),
        "trim_suggestions": result.get("trim_suggestions", []),
        "clip_suggestions": result.get("clip_suggestions", []),
        "gif_suggestions": result.get("gif_suggestions", []),
        "metadata_suggestions": result.get("metadata_suggestions", {}),
        "summary_suggestions": result.get("summary_suggestions", {}),
        "signals_used": result.get("signals_used", []),
        "warnings": result.get("warnings", []),
        "fallback_used": result.get("fallback_used", False),
        "processing_time_ms": result.get("processing_time_ms", 0),
        "tier_filtered": result.get("tier_filtered", False),
    }


@router.post("/smart-trim/apply")
async def apply_smart_trim(
    body: SmartTrimApplyRequest,
    user: dict | None = Depends(get_optional_user),
):
    """
    Apply a trim suggestion from an analysis job.
    Queues an existing trim_video_task with the suggested start/end times.
    """
    row = _load_analysis_result(body.job_id)
    if row is None or row.get("status") != "done":
        raise HTTPException(status_code=404, detail="Analysis job not found or not complete.")

    result = row.get("result") or {}
    trim_suggestions = result.get("trim_suggestions", [])

    if body.suggestion_index >= len(trim_suggestions):
        raise HTTPException(
            status_code=404,
            detail=f"Trim suggestion index {body.suggestion_index} out of range "
                   f"(job has {len(trim_suggestions)} suggestions).",
        )

    suggestion = trim_suggestions[body.suggestion_index]
    suggested_start = suggestion.get("suggested_start", 0)
    suggested_end = suggestion.get("suggested_end")

    trim_task = _get_trim_task()
    if trim_task is None:
        raise HTTPException(status_code=503, detail="Trim service unavailable.")

    trim_job_id = str(uuid.uuid4())
    try:
        trim_task.apply_async(
            args=[body.media_url],
            kwargs={
                "job_id": trim_job_id,
                "start": suggested_start,
                "end": suggested_end,
                "quality": body.quality,
            },
            queue="media",
        )
    except Exception as exc:
        logger.error("Failed to queue trim job: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to queue trim job.") from exc

    return {
        "trim_job_id": trim_job_id,
        "status": "queued",
        "start": suggested_start,
        "end": suggested_end,
    }


@router.post("/smart-gif/apply")
async def apply_smart_gif(
    body: SmartGifApplyRequest,
    user: dict | None = Depends(get_optional_user),
):
    """
    Apply a GIF suggestion from an analysis job.
    Queues create_gif_task with suggested time range and quality.
    """
    row = _load_analysis_result(body.job_id)
    if row is None or row.get("status") != "done":
        raise HTTPException(status_code=404, detail="Analysis job not found or not complete.")

    result = row.get("result") or {}
    gif_suggestions = result.get("gif_suggestions", [])

    if body.suggestion_index >= len(gif_suggestions):
        raise HTTPException(
            status_code=404,
            detail=f"GIF suggestion index {body.suggestion_index} out of range "
                   f"(job has {len(gif_suggestions)} suggestions).",
        )

    suggestion = gif_suggestions[body.suggestion_index]
    start = suggestion.get("start", 0)
    end = suggestion.get("end", start + 6)
    recommended_size = suggestion.get("recommended_size", body.quality)

    gif_task = _get_gif_task()
    if gif_task is None:
        raise HTTPException(status_code=503, detail="GIF service unavailable.")

    gif_job_id = str(uuid.uuid4())
    try:
        gif_task.apply_async(
            args=[body.media_url],
            kwargs={
                "job_id": gif_job_id,
                "start": start,
                "end": end,
                "quality": recommended_size or body.quality,
            },
            queue="media",
        )
    except Exception as exc:
        logger.error("Failed to queue GIF job: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to queue GIF job.") from exc

    return {
        "gif_job_id": gif_job_id,
        "status": "queued",
    }


@router.post("/smart-clips/apply")
async def apply_smart_clips(
    body: SmartClipsApplyRequest,
    user: dict | None = Depends(get_optional_user),
):
    """
    Apply clip suggestions from an analysis job.
    Queues a trim job for each selected clip index.
    """
    row = _load_analysis_result(body.job_id)
    if row is None or row.get("status") != "done":
        raise HTTPException(status_code=404, detail="Analysis job not found or not complete.")

    result = row.get("result") or {}
    clip_suggestions = result.get("clip_suggestions", [])

    trim_task = _get_trim_task()
    if trim_task is None:
        raise HTTPException(status_code=503, detail="Trim service unavailable.")

    queued_jobs = []
    for clip_index in body.suggestion_indices:
        if clip_index >= len(clip_suggestions):
            logger.warning("Clip index %s out of range — skipping", clip_index)
            continue

        clip = clip_suggestions[clip_index]
        start = clip.get("start", 0)
        end = clip.get("end")
        trim_job_id = str(uuid.uuid4())

        try:
            trim_task.apply_async(
                args=[body.media_url],
                kwargs={
                    "job_id": trim_job_id,
                    "start": start,
                    "end": end,
                    "quality": body.quality,
                },
                queue="media",
            )
            queued_jobs.append(
                {
                    "clip_index": clip_index,
                    "trim_job_id": trim_job_id,
                    "start": start,
                    "end": end,
                }
            )
        except Exception as exc:
            logger.error("Failed to queue clip trim job index %s: %s", clip_index, exc)

    if not queued_jobs:
        raise HTTPException(status_code=422, detail="No valid clip indices could be queued.")

    return {"queued_jobs": queued_jobs}


@router.post("/smart-metadata/clean")
async def clean_metadata(body: MetadataCleanRequest):
    """
    Synchronous metadata cleaning endpoint — no auth, no metering, no Celery.
    Returns cleaned filename + suggested tags + template variants immediately.
    """
    clean_filename_fn, suggest_tags_fn = _get_metadata_utils()

    metadata = {
        "platform": body.platform,
        "uploader": body.uploader,
        "description": body.description,
    }

    try:
        filename_cleaned = clean_filename_fn(body.title, metadata=metadata)
    except TypeError:
        # Some implementations don't accept metadata kwarg
        filename_cleaned = clean_filename_fn(body.title)

    try:
        tags = suggest_tags_fn(body.title, platform=body.platform, description=body.description)
        confidence = 0.8 if tags else 0.3
    except TypeError:
        tags = suggest_tags_fn(body.title)
        confidence = 0.7 if tags else 0.3

    # Template variants for common export naming patterns
    uploader_slug = body.uploader.strip().replace(" ", "_")[:30] if body.uploader else "unknown"
    platform_slug = body.platform.lower().replace(" ", "-")[:20] if body.platform else "video"

    template_variants = {
        "default": filename_cleaned,
        "with_uploader": f"{uploader_slug} — {filename_cleaned}" if uploader_slug != "unknown" else filename_cleaned,
        "with_platform": f"[{platform_slug.upper()}] {filename_cleaned}",
        "short": filename_cleaned[:80] if len(filename_cleaned) > 80 else filename_cleaned,
        "seo_friendly": filename_cleaned.lower().replace(" ", "-"),
    }

    return {
        "filename_cleaned": filename_cleaned,
        "tags": tags,
        "confidence": confidence,
        "template_variants": template_variants,
    }
