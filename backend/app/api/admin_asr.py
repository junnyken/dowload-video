"""
Admin — Transcript ASR (Phase 32A)
====================================
All routes require verify_admin (Bearer session or X-Admin-Token), mounted
under /api/v1/admin.

  GET  /admin/asr/summary      — today's (UTC day) jobs, minutes, spend, ceiling,
                                 kill switch, provider, failure rate, timings
  POST /admin/asr/killswitch   — {"on": bool}; audited
  POST /admin/asr/selftest     — {"file_id": str, "diarize"?: bool, "language"?: str}
                                 runs the configured provider on the first
                                 ≤60 s of an existing download; counted against
                                 the daily ceiling; audited. Works while
                                 ASR_ENABLED is off — this is how the provider is
                                 verified on production without opening ASR to users.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core.audit import log_admin_action
from app.services.asr import budget as asr_budget
from app.services.asr import config as asr_config
from app.services.asr.jobs import parse_error
from app.services.asr.types import ERROR_MESSAGES_VI, AsrError

logger = logging.getLogger(__name__)

router = APIRouter()

_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)


def _get_db():
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


def _parse_ts(v) -> float | None:
    if not v:
        return None
    from datetime import datetime  # noqa: PLC0415
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@router.get("/asr/summary")
async def asr_summary(_=Depends(verify_admin)) -> dict[str, Any]:
    now = asr_budget.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ledger = asr_budget.snapshot(asr_budget.day_key(now))

    rows: list[dict] = []
    db_ok = True
    try:
        resp = (
            _get_db().table("transcript_asr_jobs")
            .select("id, status, duration_sec, created_at, updated_at, error_message")
            .gte("created_at", day_start.isoformat())
            .limit(2000)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.error("asr summary query failed: %s", exc)
        db_ok = False

    metas = asr_budget.meta_get_many([r["id"] for r in rows])
    by_status: dict[str, int] = {}
    error_codes: dict[str, int] = {}
    per_provider: dict[str, dict[str, Any]] = {}
    proc_secs: list[float] = []
    turnaround_secs: list[float] = []
    minutes_jobs = 0.0

    for r in rows:
        st = r.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        try:
            minutes_jobs += float(r.get("duration_sec") or 0) / 60.0
        except (TypeError, ValueError):
            pass
        if st == "failed":
            code, _msg = parse_error(r.get("error_message"))
            error_codes[code or "unknown"] = error_codes.get(code or "unknown", 0) + 1
        meta = metas.get(r["id"], {})
        prov = meta.get("provider") or "unknown"
        p = per_provider.setdefault(prov, {"jobs": 0, "done": 0, "failed": 0,
                                           "est_cost_usd": 0.0, "actual_cost_usd": 0.0})
        p["jobs"] += 1
        if st in ("done", "failed"):
            p[st] += 1
        try:
            p["est_cost_usd"] += float(meta.get("est_cost") or 0)
            p["actual_cost_usd"] += float(meta.get("actual_cost") or 0)
        except ValueError:
            pass
        if st == "done":
            try:
                s0, s1 = float(meta.get("started_ts") or 0), float(meta.get("finished_ts") or 0)
                if s0 and s1 >= s0:
                    proc_secs.append(s1 - s0)
            except ValueError:
                pass
            c0, c1 = _parse_ts(r.get("created_at")), _parse_ts(r.get("updated_at"))
            if c0 and c1 and c1 >= c0:
                turnaround_secs.append(c1 - c0)

    for p in per_provider.values():
        p["est_cost_usd"] = round(p["est_cost_usd"], 6)
        p["actual_cost_usd"] = round(p["actual_cost_usd"], 6)

    finished = by_status.get("done", 0) + by_status.get("failed", 0)
    provider = asr_config.provider_name()
    return {
        "day_utc": ledger["day_utc"],
        "enabled": asr_config.asr_enabled(),
        "provider": provider,
        "model": asr_config.gemini_model() if provider == "gemini" else "whisper-1",
        "price_per_min_usd": asr_config.price_per_min(provider),
        "price_verified": False,
        "killswitch": ledger["killswitch"],
        "spend_today_usd": ledger["spend_usd"],
        "spend_ceiling_usd": ledger["ceiling_usd"],
        "minutes_reserved_today": ledger["minutes"],
        "minutes_cap_today": ledger["minutes_cap"],
        "minutes_in_jobs_today": round(minutes_jobs, 2),
        "per_user_daily_minutes_limit": int(os.environ.get("TRANSCRIPT_ASR_DAILY_MINUTES_LIMIT", "120")),
        "jobs_today": len(rows),
        "jobs_by_status": by_status,
        "failure_rate": round(by_status.get("failed", 0) / finished, 4) if finished else None,
        "failures_by_code": error_codes,
        "avg_processing_sec": round(sum(proc_secs) / len(proc_secs), 1) if proc_secs else None,
        "avg_turnaround_sec": round(sum(turnaround_secs) / len(turnaround_secs), 1) if turnaround_secs else None,
        "per_provider": per_provider,
        "redis_ok": ledger["redis_ok"],
        "db_ok": db_ok,
    }


class KillswitchRequest(BaseModel):
    on: bool


@router.post("/asr/killswitch")
async def asr_killswitch(req: KillswitchRequest, request: Request, _=Depends(verify_admin)):
    try:
        asr_budget.set_killswitch(req.on)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Không ghi được trạng thái (Redis): {exc}") from exc
    log_admin_action(request, "admin.asr.killswitch", resource_type="asr", metadata={"on": req.on})
    return {"success": True, "killswitch": req.on}


class SelftestRequest(BaseModel):
    file_id: str
    diarize: bool = False
    language: str | None = None


def _run_selftest(video_path: str, clip_sec: float, provider, diarize: bool, language: str | None) -> dict:
    from app.core.local_download import new_download_path  # noqa: PLC0415
    from app.services.asr.chunking import extract_audio  # noqa: PLC0415

    audio_path = new_download_path(_DOWNLOADS_DIR, "asr_selftest_", ".mp3")
    timings: dict[str, int] = {}
    t0 = time.monotonic()
    try:
        extract_audio(video_path, audio_path, max_sec=clip_sec, timeout=120)
        timings["extract_ms"] = int((time.monotonic() - t0) * 1000)
        t1 = time.monotonic()
        try:
            result = provider.transcribe(audio_path, language=language, diarize=diarize, duration_sec=clip_sec)
        finally:
            timings["provider_ms"] = int((time.monotonic() - t1) * 1000)
        return {"result": result, "timings": timings}
    finally:
        try:
            if os.path.isfile(audio_path):
                os.remove(audio_path)
        except OSError:
            pass


@router.post("/asr/selftest")
async def asr_selftest(req: SelftestRequest, request: Request, _=Depends(verify_admin)):
    from app.api.processing import _guard_local_path  # noqa: PLC0415
    from app.api.routes import _preflight_disk_check  # noqa: PLC0415
    from app.services.asr import get_provider  # noqa: PLC0415
    from app.services.asr.chunking import probe_duration_sec  # noqa: PLC0415

    def _err(status: int, code: str, message: str | None = None, **extra):
        return JSONResponse(status_code=status, content={
            "ok": False, "error_code": code,
            "detail": message or ERROR_MESSAGES_VI.get(code, code), **extra,
        })

    video_path = _guard_local_path(req.file_id)
    try:
        provider = get_provider()
    except AsrError as exc:
        return _err(503, "provider_unavailable", exc.message)

    try:
        duration = await run_in_threadpool(probe_duration_sec, video_path)
    except AsrError as exc:
        return _err(400, exc.code, exc.message)
    clip_sec = round(min(duration, float(asr_config.selftest_max_sec())), 3)
    if clip_sec <= 0:
        return _err(400, "too_long", "Video rỗng.")
    est = provider.estimate_cost_usd(clip_sec, req.diarize)

    # Same gates as a user job (minus ASR_ENABLED): kill switch + ceiling, disk.
    try:
        day = asr_budget.reserve(est, clip_sec / 60.0)
    except AsrError as exc:
        return _err(503, exc.code)
    try:
        _preflight_disk_check()
    except HTTPException:
        asr_budget.release(day, est, clip_sec / 60.0)
        raise

    t0 = time.monotonic()
    audit = {"provider": provider.name, "clip_sec": clip_sec, "cost_estimate_usd": est}
    try:
        out = await run_in_threadpool(_run_selftest, video_path, clip_sec, provider, req.diarize, req.language)
    except AsrError as exc:
        called = exc.code != "audio_extract_failed"
        if not called:
            asr_budget.release(day, est, clip_sec / 60.0)
        log_admin_action(request, "admin.asr.selftest", resource_type="asr",
                         metadata={**audit, "ok": False, "error_code": exc.code})
        return _err(502 if called else 400, exc.code, exc.message,
                    provider=provider.name, clip_sec=clip_sec,
                    cost_estimate_usd=est if called else 0.0, raw_output=exc.raw)
    except Exception as exc:  # noqa: BLE001 — counted as spent: the call may have happened
        log_admin_action(request, "admin.asr.selftest", resource_type="asr",
                         metadata={**audit, "ok": False, "error_code": "internal_error"})
        return _err(500, "internal_error", str(exc)[:300], provider=provider.name)

    result = out["result"]
    from app.services.asr_service import segments_to_cues  # noqa: PLC0415
    from app.services.subtitle_format import serialize_srt  # noqa: PLC0415

    log_admin_action(request, "admin.asr.selftest", resource_type="asr",
                     metadata={**audit, "ok": True, "segments": len(result.segments)})
    return {
        "ok": True,
        "provider": provider.name,
        "model": getattr(provider, "model", None),
        "clip_sec": clip_sec,
        "source_duration_sec": duration,
        "language": result.language,
        "segment_count": len(result.segments),
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text, **({"speaker": s.speaker} if s.speaker else {})}
            for s in result.segments
        ],
        "srt_preview": serialize_srt(segments_to_cues(result.segments, with_speaker=req.diarize))[:4000],
        "timings_ms": {**out["timings"], "total_ms": int((time.monotonic() - t0) * 1000)},
        "cost_estimate_usd": est,
        "counted_against_ceiling": True,
    }
