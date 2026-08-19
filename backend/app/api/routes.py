"""
API Routes
===========
POST /fetch-link       — single video instant extraction
POST /bulk-download    — batch of video URLs or channel URLs
GET  /jobs/{batch_id}  — poll job progress for a batch
GET  /proxy-download   — proxy video stream to bypass CORS
"""

import ipaddress
import os
import uuid
from typing import List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse


# ── Platform download tracking (Redis counters) ──────────────────────

def _get_platform_key(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u:    return "tiktok"
    if "instagram.com" in u: return "instagram"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "douyin.com" in u:    return "douyin"
    if "threads.net" in u or "threads.com" in u: return "threads"
    if "spotify.com" in u:   return "spotify"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "linkedin.com" in u:  return "linkedin"
    return "other"

def _track_download(url: str, success: bool) -> None:
    """Increment Redis counter: vidgrab:stats:YYYY-MM-DD → platform:(ok|err)"""
    try:
        import datetime
        from app.core.redis_client import get_redis as _get_redis
        today = datetime.date.today().isoformat()
        key   = f"vidgrab:stats:{today}"
        field = f"{_get_platform_key(url)}:{'ok' if success else 'err'}"
        r = _get_redis()
        r.hincrby(key, field, 1)
        r.expire(key, 8 * 86400)   # keep 8 days of history
    except Exception:
        pass


# ── Global concurrency limiter for /fetch-link ───────────────────────
_MAX_CONCURRENT_DL = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "10"))
_ACTIVE_DL_KEY = "vidgrab:active_downloads"
_ACTIVE_DL_TTL = 600  # auto-expire 10min (crash-safety)

def _acquire_download_slot() -> tuple:
    """Atomically INCR slot counter. Returns (acquired: bool, active_count: int)."""
    try:
        from app.core.redis_client import get_redis as _get_redis
        rc = _get_redis()
        count = rc.incr(_ACTIVE_DL_KEY)
        rc.expire(_ACTIVE_DL_KEY, _ACTIVE_DL_TTL)
        if count > _MAX_CONCURRENT_DL:
            rc.decr(_ACTIVE_DL_KEY)
            return False, int(count) - 1
        return True, int(count)
    except Exception:
        return True, 0  # fail open if Redis unavailable

def _release_download_slot() -> None:
    try:
        from app.core.redis_client import get_redis as _get_redis
        rc = _get_redis()
        val = rc.decr(_ACTIVE_DL_KEY)
        if val < 0:
            rc.set(_ACTIVE_DL_KEY, 0)
    except Exception:
        pass

from pydantic import BaseModel


def _assert_safe_url(url: str) -> None:
    """
    Reject non-http(s) schemes and any URL reaching a non-public address.

    Delegates to app.core.ssrf_guard, which (unlike the previous inline
    version) resolves the hostname before deciding — a domain whose A record
    points at 127.0.0.1 / 10.x / 169.254.169.254 no longer passes.
    """
    from app.core.error_codes import make_error
    from app.core.ssrf_guard import assert_safe_url as _guard
    _guard(url, detail_factory=lambda _msg: make_error("invalid_url"))

from app.services.downloader import extract_video_info, classify_url
from app.core.database import get_supabase_client
from app.core.client_ip import get_client_ip
from app.core.quotas import (
    check_user_quota, increment_usage,
    check_quality_permission, check_batch_limit, check_feature_permission,
    check_anon_quota, increment_anon_usage, check_youtube_tier,
    ERR_QUOTA_DAILY, ERR_QUALITY, ERR_BATCH, ERR_FEATURE,
    ERR_YOUTUBE, ERR_SPOTIFY_FULL, ERR_BULK_ZIP,
)
from app.core.error_codes import make_error
from app.core.auth_middleware import get_optional_user
from app.tasks.video_tasks import process_video_task, scrape_channel_task
from app.main import limiter

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"ok": True}


# ── Request / Response Models ────────────────────────────────────────

class FetchLinkRequest(BaseModel):
    url: str
    quality: Optional[str] = "video"
    remove_watermark: Optional[bool] = False
    download_subs: Optional[bool] = False
    progress_token: Optional[str] = None
    confirmed: Optional[bool] = False   # user confirmed a costlier YouTube tier (1080p)
    subtitle_mode: Optional[str] = "off"      # off | file | burned | soft | subtitle_only
    subtitle_language: Optional[str] = "auto"  # auto | vi | en | all
    user_cookies_b64: Optional[str] = None     # base64-encoded browser cookies for private/members content
    scheduled_at: Optional[str] = None         # ISO-8601 UTC datetime to delay execution


class BulkDownloadRequest(BaseModel):
    urls: List[str]
    channel_mode: Optional[bool] = False
    max_videos: Optional[int] = 100
    min_views: Optional[int] = 0
    quality: Optional[str] = "video"
    remove_watermark: Optional[bool] = False
    download_subs: Optional[bool] = False
    subtitle_mode: Optional[str] = "off"
    subtitle_language: Optional[str] = "auto"


# ── POST /fetch-spotify ──────────────────────────────────────────────

from app.services.spotify_service import (
    is_spotify_url,
    parse_spotify_url,
    get_playlist_tracks_async,
    get_album_tracks_async,
    extract_spotify_artist_async,
    collect_artist_tracks_async,
)

# Map internal Spotify error codes → friendly Vietnamese messages for the UI.
_SPOTIFY_ERR_MSG = {
    "unsupported_spotify_url": "Link Spotify này chưa được hỗ trợ.",
    "spotify_artist_not_found": "Không tìm thấy nghệ sĩ này trên Spotify.",
    "spotify_artist_no_tracks_found": "Không tìm thấy bài hát công khai từ nghệ sĩ này.",
    "spotify_artist_partial_success": "Đã lấy được album nhưng chưa lấy đủ top tracks.",
    "spotify_artist_expand_failed": "Chưa thể lấy toàn bộ album/single của nghệ sĩ (cần cấu hình Spotify API).",
    "spotify_artist_no_downloadable_tracks": "Không có bài nào để tải từ nghệ sĩ này.",
    "spotify_artist_dedupe_empty": "Sau khi loại trùng không còn bài nào để tải.",
    "spotify_artist_track_resolution_failed": "Không tải được bài nào (nguồn nhạc tạm lỗi). Vui lòng thử lại.",
}


def _spotify_error(code_or_msg: str, status: int = 400):
    """Return an HTTPException whose detail carries a stable error code +
    friendly message so the frontend can branch on it."""
    code = code_or_msg if code_or_msg in _SPOTIFY_ERR_MSG else "unknown"
    msg = _SPOTIFY_ERR_MSG.get(code, code_or_msg)
    return HTTPException(status_code=status, detail={"error": code, "message": msg})


def _is_music_resolve(url: str) -> bool:
    """A Spotify-origin audio resolve: a track URL, or the ytsearch/scsearch
    query the artist/playlist/album ZIP buttons send. Used to attribute
    SoundCloud-fallback success/fail metrics (P5a)."""
    u = (url or "").lower()
    return ("open.spotify.com" in u) or u.startswith("ytsearch") or u.startswith("scsearch")


class SpotifyFetchRequest(BaseModel):
    url: str


class SpotifyArtistCollectRequest(BaseModel):
    url: str
    mode: str = "all_tracks"  # top_tracks | albums | singles | all_tracks
    confirmed: bool = False    # user confirmed a large catalog (P3 guard)


@router.post("/fetch-spotify")
@limiter.limit("30/minute")
async def fetch_spotify(payload: SpotifyFetchRequest, request: Request):
    if not is_spotify_url(payload.url):
        raise _spotify_error("unsupported_spotify_url")
    try:
        parsed = parse_spotify_url(payload.url)
        sp_type = parsed["source_type"]

        if sp_type == "playlist":
            result = await get_playlist_tracks_async(payload.url)
            # source_type added alongside legacy `type` (backward-compatible)
            return {"success": True, "type": "playlist", "source_type": "playlist", **result}
        elif sp_type == "album":
            result = await get_album_tracks_async(payload.url)
            return {"success": True, "type": "album", "source_type": "album", **result}
        elif sp_type == "artist":
            result = await extract_spotify_artist_async(payload.url)
            return {"success": True, "type": "artist", **result}
        else:  # track
            raise HTTPException(
                status_code=400,
                detail="Dán link track riêng lẻ vào ô nhập URL chính để tải nhạc đơn.",
            )
    except HTTPException:
        raise
    except ValueError as e:
        # ValueErrors from the service layer carry stable error codes
        raise _spotify_error(str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-spotify-artist-tracks")
@limiter.limit("15/minute")
async def fetch_spotify_artist_tracks(
    payload: SpotifyArtistCollectRequest,
    request: Request,
    user=Depends(get_optional_user),
):
    """Resolve an artist into a concrete, deduped track list for a download
    mode (top_tracks | albums | singles | all_tracks). Used by the 'Tải tất cả'
    actions — the frontend then queues each track through the normal flow."""
    if not is_spotify_url(payload.url):
        raise _spotify_error("unsupported_spotify_url")
    mode = payload.mode if payload.mode in ("top_tracks", "albums", "singles", "all_tracks") else "all_tracks"

    # Full-catalog modes (albums / singles / all_tracks) require Pro
    if mode in ("albums", "singles", "all_tracks"):
        _sp_user_id = user["id"] if user else None
        _sp_tier = check_feature_permission(_sp_user_id, "spotify_artist_full") if _sp_user_id else None
        if not _sp_user_id or (_sp_tier and not _sp_tier["allowed"]):
            raise HTTPException(
                status_code=402,
                detail={
                    "error":         ERR_SPOTIFY_FULL,
                    "feature":       "spotify_artist_full",
                    "current_tier":  _sp_tier["tier"] if _sp_tier else "free",
                    "required_tier": "pro",
                    "upgrade_url":   "/upgrade",
                    "message":       (
                        f"Chế độ '{mode}' chỉ dành cho Pro. "
                        "Free chỉ có top_tracks (10 bài). Nâng cấp để tải album/all_tracks."
                    ),
                },
            )

    try:
        result = await collect_artist_tracks_async(payload.url, mode=mode, confirmed=payload.confirmed)
        # Catalog guard (P3): when over-cap & not confirmed, `result` carries
        # needs_confirmation + real_count + message instead of a track list.
        # success=True either way so the FE branches on the field, not an error.
        return {"success": True, **result}
    except ValueError as e:
        raise _spotify_error(str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /fetch-link  (single, instant) ──────────────────────────────

def _preflight_disk_check() -> None:
    """Phase 11: Reject requests if disk is critically full. Alert at 70%, reject at 90%."""
    try:
        import shutil as _sh
        _dir = os.getenv("DOWNLOAD_DIR", "/app/downloads")
        _total, _used, _free = _sh.disk_usage(_dir)
        _pct = _used / _total * 100 if _total else 0
        if _pct > 90 or _free < 500 * 1024 * 1024:
            raise HTTPException(
                status_code=507,
                detail={
                    "error_code": "disk_full",
                    "user_message": "Hệ thống tạm hết dung lượng lưu trữ. Vui lòng thử lại sau vài phút.",
                    "retryable": True,
                    "disk_used_pct": round(_pct, 1),
                },
            )
        if _pct > 70:
            try:
                from app.core.redis_client import get_redis as _gr
                _rc = _gr()
                _ak = "vidgrab:disk:alert_sent"
                if not _rc.get(_ak):
                    _rc.set(_ak, "1", ex=3600)
                    from app.core.notifications import send_telegram_message_sync as _tg
                    _tg(
                        f"⚠️ <b>VidGrab Disk Alert</b>\n"
                        f"Disk: {_pct:.1f}% used ({_free / 1e9:.1f} GB free)\n"
                        f"Action: check cleanup or expand storage.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception:
        pass  # fail open — never block a download due to disk-check error


import subprocess as _subprocess

_SUBTITLE_LANG_MAP = {
    "vi": ["vi", "vi-VN", "vi-VIE"],
    "en": ["en", "en-US", "en-GB"],
    "auto": ["vi", "vi-VN", "vi-VIE", "en", "en-US"],
    "all": None,   # None = yt-dlp default (all available)
}

def _resolve_download_subs(mode: Optional[str], download_subs: Optional[bool]) -> bool:
    """Backward compat: honour legacy download_subs=True if subtitle_mode not set."""
    if mode and mode != "off":
        return mode != "subtitle_only"   # subtitle_only = no video download, subs handled differently
    return bool(download_subs)

def _subtitle_langs_for(subtitle_language: Optional[str]) -> list:
    lang = subtitle_language or "auto"
    langs = _SUBTITLE_LANG_MAP.get(lang, _SUBTITLE_LANG_MAP["auto"])
    return langs if langs else ["vi", "vi-VN", "vi-VIE", "en", "en-US"]

def _burn_subtitle(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """Hardcode subtitle into video using FFmpeg. Returns True on success."""
    try:
        # Escape special chars in subtitle path for FFmpeg filter
        escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        result = _subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", f"subtitles={escaped}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=1'",
             "-c:a", "copy", "-c:v", "libx264", "-crf", "23", "-preset", "fast",
             output_path],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"[Subtitle] burn failed: {e}")
        return False

def _mux_subtitle(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """Mux subtitle as a soft stream into MKV container. Returns True on success."""
    try:
        result = _subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-i", subtitle_path,
             "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
             "-metadata:s:s:0", "language=vie",
             output_path],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"[Subtitle] mux failed: {e}")
        return False


@router.post("/fetch-link")
@limiter.limit("30/minute")
async def fetch_link(
    payload: FetchLinkRequest,
    request: Request,
    user=Depends(get_optional_user),
    x_vg_source: Optional[str] = Header(None, alias="X-VG-Source"),
):
    if not payload.url:
        raise HTTPException(status_code=400, detail="URL is required")

    # ── Scheduled download: dispatch to Celery with eta and return 202 ──
    if payload.scheduled_at:
        try:
            from datetime import datetime, timezone as _tz
            _sched_dt = datetime.fromisoformat(
                payload.scheduled_at.replace("Z", "+00:00")
            ).astimezone(_tz.utc)
            if _sched_dt <= datetime.now(_tz.utc):
                raise HTTPException(status_code=400, detail="scheduled_at phải là thời điểm trong tương lai.")
            _sched_user_id = user["id"] if user else None
            _sched_job_id = str(uuid.uuid4())
            _sched_batch  = f"single_{_sched_job_id}"
            supabase = get_supabase_client()
            supabase.table("download_jobs").insert({
                "id":           _sched_job_id,
                "batch_id":     _sched_batch,
                "original_url": payload.url,
                "status":       "pending",
                "job_stage":    "scheduled",
                "user_id":      _sched_user_id,
                "platform":     _get_platform_key(payload.url),
                "selected_quality": payload.quality or "video",
                "source":       "scheduled",
            }).execute()
            process_video_task.apply_async(
                args=[_sched_job_id, payload.url, _sched_user_id,
                      payload.quality or "video",
                      bool(payload.remove_watermark), bool(payload.download_subs)],
                eta=_sched_dt,
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content={
                "success":      True,
                "scheduled":    True,
                "job_id":       _sched_job_id,
                "scheduled_at": _sched_dt.isoformat(),
                "message":      f"Đã lên lịch tải lúc {_sched_dt.strftime('%H:%M %d/%m/%Y')} UTC.",
            })
        except HTTPException:
            raise
        except Exception as _sc_err:
            raise HTTPException(status_code=400, detail=f"Lỗi lên lịch: {_sc_err}")

    # ── Phase 11: disk pre-flight check ──────────────────────────────
    _preflight_disk_check()

    # ── Tier enforcement ─────────────────────────────────────────────
    user_id = user["id"] if user else None

    # Phase 27C defaults — set early so finally block never hits NameError
    _p27c_platform  = ""
    _p27c_user_key  = ""

    # Extract client IP once — used for anon quota and YouTube yt_quota
    _req_client_ip = get_client_ip(request)

    # 1. Daily quota check (authenticated users)
    if user_id:
        quota = check_user_quota(user_id)
        if not quota["allowed"]:
            raise HTTPException(
                status_code=403,
                detail=make_error(ERR_QUOTA_DAILY, extra={
                    "message":         quota["message"],
                    "downloads_today": quota.get("downloads_today", 0),
                    "daily_limit":     quota.get("daily_limit", 30),
                }),
            )

    # 1b. Anonymous daily quota — 5 downloads/day per IP across all platforms
    if not user_id:
        _anon_q = check_anon_quota(_req_client_ip)
        if not _anon_q["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=make_error(ERR_QUOTA_DAILY, extra={
                    "message":         _anon_q["message"],
                    "downloads_today": _anon_q["downloads_today"],
                    "daily_limit":     _anon_q["daily_limit"],
                }),
            )

    # 2. Quality cap: free users cannot request 4K explicitly
    if payload.quality in ("mp4_4k", "4k") and user_id:
        q_check = check_quality_permission(user_id, payload.quality)
        if not q_check["allowed"]:
            raise HTTPException(
                status_code=403,
                detail=make_error(ERR_QUALITY, extra={
                    "message": q_check["message"],
                    "tier":    q_check["tier"],
                }),
            )

    # 2b. Phase 27C — Admission control (per-user per-platform fairness cap).
    #     Feature-flagged: ADMISSION_CONTROL_ENABLED=false by default (fail-open).
    _admission_result = None
    _p27c_platform    = _get_platform_key(payload.url)
    _p27c_user_key    = str(user_id) if user_id else _req_client_ip
    try:
        from app.core.admission_control import evaluate as _adm_eval, ADMIT_IMMEDIATE, ADMIT_DELAYED, REJECT_TEMP, REJECT_UNAVAIL
        _admission_result = _adm_eval(_p27c_platform, _p27c_user_key, is_batch=False)
        if _admission_result.decision == REJECT_UNAVAIL:
            raise HTTPException(
                status_code=503,
                detail={
                    "error":   "platform_unavailable",
                    "message": f"Nền tảng {_p27c_platform} tạm thời không khả dụng. Vui lòng thử lại sau.",
                    **_admission_result.to_dict(),
                },
            )
        if _admission_result.decision == REJECT_TEMP:
            raise HTTPException(
                status_code=429,
                detail={
                    "error":   "fairness_cap_reached",
                    "message": f"Bạn đang có quá nhiều tải đồng thời trên {_p27c_platform}. Thử lại sau {_admission_result.retry_after}s.",
                    **_admission_result.to_dict(),
                },
                headers={"Retry-After": str(_admission_result.retry_after)},
            )
        if _admission_result.decision == ADMIT_DELAYED:
            # Enqueue and return truthful 202 — NOT 200
            import uuid
            from app.core.delayed_queue import enqueue as _dq_enqueue
            from app.core.fairness_control import increment_queued
            _job_id = str(uuid.uuid4())
            increment_queued(_p27c_platform, _p27c_user_key)
            _dq_info = _dq_enqueue(_p27c_platform, _p27c_user_key, _job_id, payload.url)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=202,
                content={
                    "success": False,
                    "message": f"Yêu cầu đang xếp hàng chờ ({_p27c_platform}). Thử lại sau {_dq_info.get('estimatedWaitSec', 30)}s.",
                    **_admission_result.to_dict(),
                    **_dq_info,
                },
                headers={"Retry-After": str(_dq_info.get("estimatedWaitSec", 30))},
            )
        # ACCEPT_IMMEDIATE → acquire fairness slot (released in finally block)
        if _admission_result and not _admission_result.shadow:
            from app.core.fairness_control import acquire_platform_slot as _fc_acquire, is_fairness_enabled as _fc_en
            if _fc_en(_p27c_platform):
                _fc_acquire(_p27c_platform, _p27c_user_key)
    except HTTPException:
        raise
    except Exception as _adm_err:
        print(f"[Admission] gate error, fail-open: {_adm_err}")

    # 3. YouTube daily quota + resolution cap (YouTube alone costs proxy bandwidth).
    #    Anonymous (IP): 1/day, ≤720p, no channel. Registered: 5/day, ≤1080p.
    #    Other platforms unaffected. mp3/audio never capped (cheap).
    _yt_quota_id = None
    _yt_global = False
    _quality = payload.quality
    if _get_platform_key(payload.url) == "youtube":
        from app.core import yt_quota as _ytq
        from app.core import youtube_gate as _ytg
        _ip = _req_client_ip  # reuse already-extracted IP

        # 3a. YouTube video requires Pro — Free users are audio-only
        _yt_tier = check_youtube_tier(user_id, _quality)
        if not _yt_tier["allowed"]:
            raise HTTPException(
                status_code=402,
                detail=make_error(ERR_YOUTUBE, extra={
                    "message":           _yt_tier["message"],
                    "current_tier":      _yt_tier.get("tier", "free"),
                    "required_tier":     "pro",
                    "suggested_quality": _yt_tier.get("suggested_quality", "mp3_128"),
                    "upgrade_url":       "/upgrade",
                }),
            )

        # Phase 8 gate — feature flag → circuit breaker → daily cost ceiling →
        # quality tier. Runs BEFORE quota reserve so a blocked request neither
        # consumes a user's daily quota nor touches the proxy. Raises
        # YouTubeBlocked (mapped to the right HTTP status + friendly message).
        try:
            _gate = _ytg.preflight(_quality, confirmed=bool(payload.confirmed))
        except _ytg.YouTubeBlocked as _yb:
            raise HTTPException(status_code=_yb.http,
                                detail={"error": _yb.code, "message": _yb.message, **_yb.payload})

        # Valve: anonymous → audio-only (cheapest) when enabled.
        if (not user_id) and os.getenv("YT_ANON_AUDIO_ONLY", "0") == "1" \
                and not (_quality.startswith("mp3") or _quality.startswith("audio")):
            _quality = "mp3_128"

        _yt_quota_id = _ytq.yt_identity(user_id, _ip)
        _ok, _used, _lim = _ytq.reserve(_yt_quota_id, bool(user_id))
        if not _ok:
            _yt_quota_id = None  # nothing to refund
            _msg = (f"Bạn đã đạt giới hạn {_lim} video YouTube hôm nay."
                    if user_id else
                    "Khách chỉ tải được 1 video YouTube/ngày. Vui lòng đăng nhập để tải nhiều hơn.")
            raise HTTPException(status_code=429, detail={"error": "youtube_quota_exceeded", "message": _msg})

        # Valve: site-wide daily budget ceiling (protects proxy spend).
        _g_ok, _g_used, _g_cap = _ytq.global_reserve()
        if not _g_ok:
            _ytq.refund(_yt_quota_id)
            _yt_quota_id = None
            raise HTTPException(status_code=429, detail={
                "error": "youtube_global_cap",
                "message": "YouTube tạm hết lượt hệ thống hôm nay. Vui lòng thử lại ngày mai."})
        _yt_global = True

        # Resolution cap to control proxy bytes (audio + video_fast exempt).
        # Phase 8: also clamp to the proxy height ceiling decided by the gate
        # (default 720; 1080 only after confirm) so "best" never pulls 1080/4K
        # bytes through the paid proxy.
        _cap = int(os.getenv("YT_MAX_HEIGHT_USER", "1080") if user_id else os.getenv("YT_MAX_HEIGHT_ANON", "720"))
        _gate_h = _gate.get("effective_height")
        if _gate_h:
            _cap = min(_cap, _gate_h) if _cap > 0 else _gate_h
        if _cap > 0 and not (_quality.startswith("mp3") or _quality.startswith("audio") or _quality == "video_fast"):
            if _quality.startswith("video_") and _quality != "video":
                try:
                    _req_h = int(_quality.split("_")[1])
                except Exception:
                    _req_h = 99999
            else:  # "video" / "video_4k" / "4k" / "mp4_4k" → "best" (uncapped)
                _req_h = 99999
            if _req_h > _cap:
                _quality = f"video_{_cap}"

    # Global concurrency guard — prevent CPU/memory overload under burst traffic
    _acquired, _active = _acquire_download_slot()
    if not _acquired:
        if _yt_quota_id or _yt_global:
            from app.core import yt_quota as _ytq
            if _yt_quota_id:
                _ytq.refund(_yt_quota_id)
            if _yt_global:
                _ytq.global_refund()
        raise HTTPException(
            status_code=429,
            detail=make_error("concurrency_limit", extra={"active_downloads": _active}),
        )

    # Handle user-provided cookies (base64-encoded browser session cookies)
    user_cookie_file = None
    if payload.user_cookies_b64:
        import tempfile, base64 as _b64
        try:
            cookie_bytes = _b64.b64decode(payload.user_cookies_b64)
            cookie_text = cookie_bytes.decode('utf-8', errors='replace')
            tf = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='ug_ck_')
            tf.write(cookie_text)
            tf.flush()
            tf.close()
            user_cookie_file = tf.name
            print(f"[UserCookie] Wrote {len(cookie_text)} chars to {user_cookie_file}")
        except Exception as _ck_err:
            print(f"[UserCookie] Failed to write cookie file: {_ck_err}")
            user_cookie_file = None

    try:
        info = await extract_video_info(
            payload.url,
            _quality,
            payload.remove_watermark,
            download_subs=_resolve_download_subs(payload.subtitle_mode, payload.download_subs),
            progress_token=payload.progress_token or "",
            subtitle_language=payload.subtitle_language or "auto",
            user_cookies_file=user_cookie_file,
        )

        # Honest failure: an audio request that produced no downloadable
        # artifact is NOT a success — without this the UI shows "Xong" but
        # nothing downloads (e.g. YouTube bot-block resolves a title only).
        _q = (payload.quality or "")
        if (_q.startswith("mp3") or _q.startswith("audio")) and not (
            info.get("local_mp3_path") or info.get("local_file_path") or info.get("direct_mp4_url")
        ):
            raise ValueError("Không tạo được file nhạc từ nguồn này. Vui lòng thử lại sau.")

        _track_download(payload.url, True)
        try:
            from app.core.obs_recorder import record_success as _obs_ok
            _obs_ok(_get_platform_key(payload.url))
        except Exception:
            pass
        # Phase 27B: record per-cookie outcome for scored selection
        try:
            _p27b_ck = getattr(payload, "_selected_cookie_b64", None)
            if _p27b_ck:
                from app.core.cookie_score import record_outcome as _cs_ok
                _cs_ok(_get_platform_key(payload.url), _p27b_ck, success=True)
        except Exception:
            pass

        # P5a: a Spotify-origin resolve (track URL or ytsearch/scsearch query)
        # ultimately served audio — counts toward SoundCloud-fallback health.
        if _is_music_resolve(payload.url):
            from app.core.spotify_artist_ops import artist_metric
            artist_metric("sc_resolve_ok")

        # Phase 8: YouTube succeeded → close circuit + bill proxy bytes (only
        # when bytes actually flowed through the paid proxy) + metrics.
        if _get_platform_key(payload.url) == "youtube":
            from app.core import youtube_gate as _ytg
            _proxy_used = os.getenv("YOUTUBE_PROXY_DOWNLOAD", "0").strip().lower() in ("1", "true", "yes", "on")
            _ytg.record_result(True, info.get("file_size_mb", 0) if _proxy_used else 0)

        # YouTube CDN URLs are signed for the residential-proxy exit IP, so a
        # client (browser/extension) fetching them via proxy-download from the
        # server IP gets 403 ("proxy-download / check internet"). The bytes are
        # already on disk (combined proxy download), so for YouTube force clients
        # onto the local file: drop the proxy-bound direct URL + raw format list.
        if _get_platform_key(payload.url) == "youtube" and (
            info.get("local_file_path") or info.get("local_mp3_path")
        ):
            info["direct_mp4_url"] = ""
            info["available_formats"] = []

        # 3. Post-extraction 4K height guard (catches merge-4K scenario for free users)
        # Only applies to YouTube — other platforms (Twitter, Instagram, Reddit, TikTok…)
        # don't have meaningful 4K gating and height metadata can be misreported.
        actual_height = info.get("downloaded_height") or info.get("max_merge_height") or 0
        _is_yt_for_guard = _get_platform_key(payload.url) == "youtube"
        if actual_height > 1080 and user_id and _is_yt_for_guard:
            h_check = check_quality_permission(user_id, payload.quality, height=actual_height)
            if not h_check["allowed"]:
                raise HTTPException(
                    status_code=403,
                    detail=make_error(ERR_QUALITY, extra={
                        "message": h_check["message"],
                        "tier":    h_check["tier"],
                    }),
                )

        # 4. Increment usage counter
        if user_id:
            try:
                increment_usage(user_id)
            except Exception as _inc_err:
                print(f"[Quota] increment_usage failed silently: {_inc_err}")
            # Phase 20: record metering event (fire-and-forget)
            try:
                from app.core.metering import record_download as _record_dl
                import asyncio
                _job_id = info.get("id") or str(uuid.uuid4())
                _tier   = (user or {}).get("tier", "free") if user else "free"
                asyncio.ensure_future(_record_dl(user_id, job_id=_job_id, plan=_tier))
            except Exception:
                pass
        else:
            try:
                increment_anon_usage(_req_client_ip)
            except Exception as _anon_err:
                print(f"[Quota] increment_anon_usage failed silently: {_anon_err}")

        # Save to download_jobs for authenticated users (personal history)
        if user:
            try:
                supabase = get_supabase_client()
                supabase.table("download_jobs").insert({
                    "batch_id": f"single_{str(uuid.uuid4())}",
                    "original_url": payload.url,
                    "title": info.get("title"),
                    "thumbnail_url": info.get("thumbnail_url"),
                    "direct_mp4_url": info.get("direct_mp4_url"),
                    "file_size_mb": info.get("file_size_mb", 0),
                    "downloaded_height": info.get("downloaded_height", 0),
                    "is_audio_only": info.get("is_audio_only", False),
                    "status": "success",
                    "user_id": user["id"],
                    "source_surface": "web",
                    "selected_quality": payload.quality,
                    "source": x_vg_source or "web",
                    "platform": _get_platform_key(payload.url),
                }).execute()
            except Exception:
                pass  # History save failure must never break the download response

        from app.tasks.video_tasks import delete_local_file
        # File kept longer so the shared cache (A2) can serve repeat/hot videos
        # for hours without re-hitting YouTube. Disk stays bounded by the 10GB
        # LRU cleanup. Env-tunable.
        _EXPIRY_SECONDS = int(os.getenv("LOCAL_FILE_TTL_SEC", "7200"))  # 2h

        if info.get("local_file_path") or info.get("local_mp3_path"):
            path_to_delete = info.get("local_file_path") or info.get("local_mp3_path")
            delete_local_file.apply_async((path_to_delete,), countdown=_EXPIRY_SECONDS)

        # ── Subtitle post-processing ────────────────────────────────────
        subtitle_file_url = None
        subtitle_mode_actual = getattr(payload, 'subtitle_mode', None) or ("file" if payload.download_subs else "off")
        local_sub = info.get("local_subtitle_path")
        video_path = info.get("local_file_path")

        if subtitle_mode_actual == "burned" and local_sub and video_path and os.path.exists(local_sub) and os.path.exists(video_path):
            _burned_out = video_path.replace(".mp4", "_subbed.mp4").replace(".mkv", "_subbed.mkv")
            if not _burned_out.endswith((".mp4", ".mkv")):
                _burned_out = _burned_out + "_subbed.mp4"
            if _burn_subtitle(video_path, local_sub, _burned_out):
                info["local_file_path"] = _burned_out
                delete_local_file.apply_async((_burned_out,), countdown=_EXPIRY_SECONDS)
                video_path = _burned_out
            else:
                info["subtitle_error"] = "subtitle_embed_failed"

        elif subtitle_mode_actual == "soft" and local_sub and video_path and os.path.exists(local_sub) and os.path.exists(video_path):
            _muxed_out = os.path.splitext(video_path)[0] + "_subbed.mkv"
            if _mux_subtitle(video_path, local_sub, _muxed_out):
                info["local_file_path"] = _muxed_out
                delete_local_file.apply_async((_muxed_out,), countdown=_EXPIRY_SECONDS)
                video_path = _muxed_out
            else:
                info["subtitle_error"] = "subtitle_embed_failed"

        # Build subtitle file download URL
        if local_sub and os.path.exists(local_sub) and subtitle_mode_actual not in ("burned",):
            sub_ext = os.path.splitext(local_sub)[1].lstrip(".")
            sub_name = os.path.splitext(info.get("title") or "subtitle")[0]
            subtitle_file_url = (
                f"/api/v1/download-local"
                f"?filepath={quote(local_sub)}&filename={quote(sub_name)}.{sub_ext}"
            )
            delete_local_file.apply_async((local_sub,), countdown=_EXPIRY_SECONDS)

        return {
            "success": True,
            "title": info.get("title"),
            "thumbnail_url": info.get("thumbnail_url"),
            "direct_mp4_url": info.get("direct_mp4_url"),
            "local_mp3_path": info.get("local_mp3_path"),
            "local_file_path": info.get("local_file_path"),
            "original_url": info.get("original_url"),
            "quality": info.get("quality"),
            "duration": info.get("duration", 0),
            "file_size_mb": info.get("file_size_mb", 0),
            "available_formats": info.get("available_formats", []),
            "max_merge_height": info.get("max_merge_height", 0),
            "downloaded_height": info.get("downloaded_height", 0),
            "subtitle_url": info.get("subtitle_url"),
            "subtitle_file_url": subtitle_file_url,
            "subtitle_error": info.get("subtitle_error"),
            "is_audio_only": info.get("is_audio_only"),
            "chapters": info.get("chapters", []),
            "cached": False,
            "uploader":        info.get("uploader"),
            "channel_handle":  info.get("channel_handle"),
            "view_count":      info.get("view_count"),
            "tags":            info.get("tags", []),
            "upload_date_iso": info.get("upload_date_iso"),
            "language":        info.get("language"),
            "has_subtitles":   info.get("has_subtitles", False),
            "has_auto_captions":           info.get("has_auto_captions", False),
            "available_subtitle_languages": info.get("available_subtitle_languages", []),
            "subtitle_source":             info.get("subtitle_source", "none"),
        }
    except Exception as e:
        _track_download(payload.url, False)
        try:
            from app.core.obs_recorder import record_failure_reason as _obs_fail
            _obs_fail(_get_platform_key(payload.url), str(e))
        except Exception:
            pass
        # Phase 27B: record per-cookie outcome (failure path)
        try:
            _p27b_ck = getattr(payload, "_selected_cookie_b64", None)
            if _p27b_ck:
                from app.core.obs_recorder import _classify_to_bucket as _p27b_bk
                from app.core.cookie_score import record_outcome as _cs_fail
                _cs_fail(_get_platform_key(payload.url), _p27b_ck,
                         success=False, failure_bucket=_p27b_bk(str(e)))
        except Exception:
            pass
        if _is_music_resolve(payload.url):
            from app.core.spotify_artist_ops import artist_metric
            artist_metric("sc_resolve_fail")
        # Phase 8: YouTube proxy failure → feed the circuit breaker (5/5min→open).
        if _get_platform_key(payload.url) == "youtube":
            from app.core import youtube_gate as _ytg
            _ytg.record_result(False)
        # Refund the YouTube slots — a failed download shouldn't burn them.
        if _yt_quota_id or _yt_global:
            from app.core import yt_quota as _ytq
            if _yt_quota_id:
                _ytq.refund(_yt_quota_id)
            if _yt_global:
                _ytq.global_refund()
        msg = str(e)
        if "video unavailable" in msg.lower() or "unavailable" in msg.lower():
            msg = "🚫 Video không khả dụng — có thể đã bị xóa, giới hạn vùng (geo-block), hoặc bị ẩn."
        elif "sign in" in msg.lower() or "bot" in msg.lower():
            msg = "🤖 YouTube yêu cầu xác thực. Đang thử kênh dự phòng... Vui lòng thử lại sau 30 giây."
        elif "private" in msg.lower():
            msg = "🔒 Video ở chế độ riêng tư, không thể tải."
        elif any(kw in msg.lower() for kw in ("drm", "widevine", "encrypted", "protection", "clearkey")):
            msg = "🔐 Video được bảo vệ DRM (mã hóa bản quyền) — không thể tải nội dung này."
        elif "không đủ dung lượng" in msg:
            pass  # keep original Vietnamese disk-full message as-is
        elif "copyright" in msg.lower() or "dmca" in msg.lower():
            msg = "⚠️ Video bị giới hạn bản quyền tại khu vực này — không thể tải."
        raise HTTPException(status_code=500, detail=msg)
    finally:
        _release_download_slot()
        # Phase 27C: release per-user per-platform fairness slot + promote next delayed job
        try:
            from app.core.fairness_control import release_platform_slot as _fc_rel, is_fairness_enabled as _fc_en
            if _fc_en(_p27c_platform):
                _fc_rel(_p27c_platform, _p27c_user_key)
        except Exception:
            pass
        try:
            from app.core.delayed_queue import try_promote as _dq_promote
            _dq_promote(_p27c_platform)
        except Exception:
            pass
        # Clean up temp cookie file if one was written
        if user_cookie_file and os.path.exists(user_cookie_file):
            try:
                os.unlink(user_cookie_file)
            except Exception:
                pass


# ── POST /fetch-threads  (public Threads post or profile preview) ─────

class ThreadsFetchRequest(BaseModel):
    url: str


@router.post("/fetch-threads")
@limiter.limit("20/minute")
async def fetch_threads(
    payload: ThreadsFetchRequest,
    request: Request,
):
    """
    Inspect a PUBLIC Threads post or profile URL.

    Returns the normalized Threads schema (source_type post|profile,
    media_items, recent posts for profiles, and explicit error_code states).
    Media download itself reuses /fetch-link (single post) or the media URLs
    in `media_items` via /proxy-download.
    """
    from app.services.threads_extractor import is_threads_url, extract_threads

    url = (payload.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    _assert_safe_url(url)
    if not is_threads_url(url):
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ link Threads công khai (threads.net / threads.com).",
        )

    try:
        result = await extract_threads(url)
    except Exception as e:
        # Extractor is defensive and returns error_codes itself; this guards
        # only against truly unexpected crashes — keep Threads failures isolated.
        return {
            "success": False,
            "platform": "threads",
            "error_code": "threads_extractor_failed",
            "error_message": "Không trích xuất được nội dung Threads này.",
            "detail": str(e),
        }

    ok = not result.get("error_code")
    _track_download(url, ok)
    return {"success": ok, **result}


# ── GET /progress/{token} ────────────────────────────────────────────

@router.get("/progress/{token}")
async def get_download_progress(token: str):
    """Poll download progress for a given progress_token."""
    import json as _json
    try:
        from app.core.redis_client import get_redis
        raw = get_redis().get(f"dl_progress:{token}")
        if not raw:
            return {"status": "pending", "percent": 0, "speed_kbps": 0, "eta_seconds": 0}
        return _json.loads(raw)
    except Exception:
        return {"status": "pending", "percent": 0, "speed_kbps": 0, "eta_seconds": 0}


# ── POST /bulk-download  (videos + channels) ────────────────────────

@router.get("/progress/{token}/position")
async def get_queue_position(token: str):
    """Phase 11: Return estimated queue position and ETA for a pending job."""
    import json as _json
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        # Try to find job_id linked to this progress token
        job_id_raw = rc.get(f"dl_progress_job:{token}")
        if not job_id_raw:
            return {"queue_position": None, "eta_seconds": None, "status": "unknown"}
        # Queue depth = number of tasks before this one in the queue
        queue_depth = rc.llen("celery")
        pending_size = rc.llen("downloads")  # downloads-specific queue
        # Avg duration from queue intelligence
        avg_raw = rc.get("vidgrab:queue:avg_job_duration")
        avg_sec = float(avg_raw) if avg_raw else 45.0
        eta_seconds = int((pending_size or queue_depth or 0) * avg_sec)
        return {
            "queue_position": int(pending_size or queue_depth or 0),
            "eta_seconds": eta_seconds,
            "status": "queued",
        }
    except Exception:
        return {"queue_position": None, "eta_seconds": None, "status": "unknown"}


@router.post("/bulk-download")
@limiter.limit("30/minute")
async def bulk_download(
    payload: BulkDownloadRequest,
    request: Request,
    user=Depends(get_optional_user),
    x_vg_source: Optional[str] = Header(None, alias="X-VG-Source"),
):
    if not payload.urls or len(payload.urls) == 0:
        raise HTTPException(status_code=400, detail="No URLs provided")

    # ── Phase 11: disk pre-flight check ──────────────────────────────
    _preflight_disk_check()

    # Hard cap: 200 URLs per request.
    # Channel mode: each channel URL can expand to max_videos (capped at 500),
    # so 200 channel URLs would already mean up to 100,000 jobs — keep this low.
    MAX_URLS_PER_REQUEST = 200
    if len(payload.urls) > MAX_URLS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Tối đa {MAX_URLS_PER_REQUEST} URL mỗi lần gửi. Vui lòng chia nhỏ danh sách."
        )

    # ── Tier enforcement: batch size ────────────────────────────────
    auth_user_id = user["id"] if user else None
    batch_check = check_batch_limit(auth_user_id, len(payload.urls))
    if not batch_check["allowed"]:
        raise HTTPException(
            status_code=403,
            detail=make_error(ERR_BATCH, extra={
                "message":   batch_check["message"],
                "limit":     batch_check["limit"],
                "requested": batch_check["requested"],
                "tier":      batch_check["tier"],
            }),
        )

    # ── Daily quota gate — bulk previously skipped this entirely, letting
    # anon/free users bypass the per-day download cap that /fetch-link
    # enforces for single downloads. Gate the whole batch up front; each
    # individual job still re-checks (authenticated) inside process_video_task.
    _bulk_client_ip = get_client_ip(request)
    if auth_user_id:
        _bulk_quota = check_user_quota(auth_user_id)
        if not _bulk_quota["allowed"]:
            raise HTTPException(
                status_code=403,
                detail=make_error(ERR_QUOTA_DAILY, extra={
                    "message":         _bulk_quota["message"],
                    "downloads_today": _bulk_quota.get("downloads_today", 0),
                    "daily_limit":     _bulk_quota.get("daily_limit", 30),
                }),
            )
    else:
        _bulk_anon_q = check_anon_quota(_bulk_client_ip)
        if not _bulk_anon_q["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=make_error(ERR_QUOTA_DAILY, extra={
                    "message":         _bulk_anon_q["message"],
                    "downloads_today": _bulk_anon_q["downloads_today"],
                    "daily_limit":     _bulk_anon_q["daily_limit"],
                }),
            )

    # Also enforce max_videos per channel against tier limit
    if payload.channel_mode and auth_user_id:
        from app.core.quotas import get_tier_permissions, get_user_tier
        _tier = get_user_tier(auth_user_id)
        _perms = get_tier_permissions(_tier)
        payload.max_videos = min(payload.max_videos or 100, _perms["batch_limit"])

    batch_id = str(uuid.uuid4())
    supabase = get_supabase_client()

    channel_count = 0
    video_count = 0

    for raw_url in payload.urls:
        url = raw_url.strip()
        if not url:
            continue

        from app.utils.link_resolver import resolve_short_url
        resolved_url = resolve_short_url(url)
        url_type = classify_url(resolved_url)

        # Force channel mode from frontend toggle
        if payload.channel_mode:
            url_type = "channel"

        try:
            if url_type == "channel":
                # ── YouTube channel = registered-only (proxy cost control) ──
                if (not auth_user_id) and _get_platform_key(resolved_url) == "youtube":
                    supabase.table("download_jobs").insert({
                        "batch_id": batch_id,
                        "original_url": url,
                        "status": "failed",
                        "error_message": "Đăng nhập để tải kênh YouTube.",
                        "source_surface": "web",
                        "source": x_vg_source or "web",
                        "platform": _get_platform_key(url),
                    }).execute()
                    continue
                # ── Channel / Playlist path ──────────────────────
                # Insert a placeholder "scraping" job so the frontend
                # can show "Discovering videos..." immediately.
                response = supabase.table("download_jobs").insert({
                    "batch_id": batch_id,
                    "original_url": url,
                    "status": "processing",
                    "error_message": "Đang quét kênh...",
                    "user_id": auth_user_id,
                    "source_surface": "web",
                    "selected_quality": payload.quality,
                    "source": x_vg_source or "web",
                    "platform": _get_platform_key(url),
                }).execute()

                channel_job_id = response.data[0]["id"]

                # Dispatch scraping to Celery background
                # Hard limit max_videos to prevent scraping abuse (max 500)
                safe_max_videos = min(payload.max_videos or 100, 500)
                scrape_channel_task.apply_async(
                    args=[url, batch_id, channel_job_id, safe_max_videos, payload.min_views, auth_user_id, payload.quality, payload.remove_watermark, payload.download_subs],
                    priority=3,
                )
                channel_count += 1

            else:
                # ── Single video path ────────────────────────────
                response = supabase.table("download_jobs").insert({
                    "batch_id": batch_id,
                    "original_url": url,
                    "status": "pending",
                    "user_id": auth_user_id,
                    "source_surface": "web",
                    "selected_quality": payload.quality,
                    "source": x_vg_source or "web",
                    "platform": _get_platform_key(url),
                }).execute()

                job_id = response.data[0]["id"]

                # Dedup: if video info is already cached in Redis, resolve job immediately
                # instead of spawning a Celery task (saves worker slots for new URLs).
                _dedup_resolved = False
                if payload.quality in ("video_fast",):
                    try:
                        import hashlib as _hl, json as _js
                        from app.core.redis_client import get_redis
                        _rkey = "vidcache:" + _hl.md5(
                            f"vidinfo:{url}:{payload.quality}:{int(payload.remove_watermark)}".encode()
                        ).hexdigest()
                        _cached = get_redis().get(_rkey)
                        if _cached:
                            _ci = _js.loads(_cached)
                            supabase.table("download_jobs").update({
                                "status": "success",
                                "title": _ci.get("title", "Unknown"),
                                "direct_mp4_url": _ci.get("direct_mp4_url"),
                                "error_message": "Tải từ bộ nhớ đệm thành công",
                            }).eq("id", job_id).execute()
                            _dedup_resolved = True
                    except Exception:
                        pass

                if not _dedup_resolved:
                    process_video_task.apply_async(
                        args=[job_id, url, auth_user_id, payload.quality, payload.remove_watermark, payload.download_subs],
                        priority=5,
                    )
                # Authenticated usage is counted inside process_video_task on
                # completion (via the now-correct auth_user_id above). Anon
                # usage has no task-side hook, so count it here at dispatch —
                # matches check_anon_quota's per-IP daily gate above.
                if not auth_user_id:
                    try:
                        increment_anon_usage(_bulk_client_ip)
                    except Exception:
                        pass
                video_count += 1

        except Exception as e:
            import traceback as _tb
            print(f"Error creating job for {url}: {type(e).__name__}: {e}")
            print(_tb.format_exc())

    # Set exact 60-minute scheduled cleanup for this entire batch folder and zip
    from app.tasks.video_tasks import delete_batch_resources
    delete_batch_resources.apply_async((batch_id,), countdown=60 * 60)

    # Queue depth — let frontend show estimated wait when backlogged.
    # Check the 'downloads' queue (where process_video_task is routed).
    _queue_depth = 0
    _estimated_wait = 0
    try:
        from app.core.redis_client import get_redis as _get_redis2
        _rc2 = _get_redis2()
        _queue_depth = int(_rc2.llen("downloads") or 0)
        # Use queue_intelligence rolling avg if available; fall back to 45 s.
        _avg_raw = _rc2.get("vidgrab:queue:avg_job_duration")
        _avg_sec = float(_avg_raw) if _avg_raw else 45.0
        _estimated_wait = max(0, int((_queue_depth / max(1, 8)) * _avg_sec))
    except Exception:
        pass

    return {
        "batch_id": batch_id,
        "success": True,
        "channels_detected": channel_count,
        "videos_queued": video_count,
        "queue_depth": _queue_depth,
        "estimated_wait_seconds": _estimated_wait,
    }

# ── POST /bulk-zip ──────────────────────────────────────────────────

class BulkZipRequest(BaseModel):
    batch_id: str
    organize_by_channel: Optional[bool] = False

@router.post("/bulk-zip")
@limiter.limit("30/minute")
async def bulk_zip(
    payload: BulkZipRequest,
    request: Request,
    user=Depends(get_optional_user),
):
    if not payload.batch_id:
        raise HTTPException(status_code=400, detail="Batch ID required")

    # ZIP download is Pro-only
    _zip_user_id = user["id"] if user else None
    _zip_perm = check_feature_permission(_zip_user_id, "bulk_zip") if _zip_user_id else None
    if not _zip_user_id or (_zip_perm and not _zip_perm["allowed"]):
        raise HTTPException(
            status_code=402,
            detail={
                "error":         ERR_BULK_ZIP,
                "feature":       "bulk_zip",
                "current_tier":  _zip_perm["tier"] if _zip_perm else "free",
                "required_tier": "pro",
                "upgrade_url":   "/upgrade",
                "message":       "Tải ZIP yêu cầu gói Pro. Nâng cấp để tải toàn bộ batch về 1 file ZIP.",
            },
        )
    
    supabase = get_supabase_client()
    existing = supabase.table("download_jobs").select("id", "status").eq("batch_id", payload.batch_id).eq("original_url", "batch_zip").execute()
    
    from app.tasks.video_tasks import create_zip_task
    
    if existing.data:
        job = existing.data[0]
        zip_job_id = job["id"]
        status = job.get("status")
        
        if status == "processing":
            return {"success": True, "job_id": zip_job_id}
            
        # If pending or failed, we requeue it to ensure it runs
        supabase.table("download_jobs").update({
            "status": "pending",
            "error_message": ""
        }).eq("id", zip_job_id).execute()
        
        create_zip_task.delay(payload.batch_id, zip_job_id, payload.organize_by_channel or False)
        return {"success": True, "job_id": zip_job_id}

    response = supabase.table("download_jobs").insert({
        "batch_id": payload.batch_id,
        "original_url": "batch_zip",
        "status": "pending",
    }).execute()

    zip_job_id = response.data[0]["id"]
    create_zip_task.delay(payload.batch_id, zip_job_id, payload.organize_by_channel or False)

    return {"success": True, "job_id": zip_job_id}


# ── POST /zip-stream  (bulk zip, no disk writes — gendownload-style) ──

class ZipStreamRequest(BaseModel):
    urls: list[str]

@router.post("/zip-stream")
@limiter.limit("10/minute")
async def zip_stream(
    payload: ZipStreamRequest,
    request: Request,
    user=Depends(get_optional_user),
):
    """
    Bundle several videos into one streamed .zip — resolved and forwarded
    directly to the client with no local file ever written for the archive
    itself. Fixed to "video_fast" quality (the only tier that yields a true
    pass-through URL); for trim/GIF/watermark-removal or other qualities
    that need FFmpeg, use /bulk-zip instead.
    """
    from app.services.archive_service import (
        resolve_stream_zip_items, stream_zip_response_body, MAX_STREAM_ZIP_ITEMS,
    )

    urls = [u.strip() for u in (payload.urls or []) if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="Cần ít nhất 1 URL.")
    if len(urls) > MAX_STREAM_ZIP_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Tối đa {MAX_STREAM_ZIP_ITEMS} video/lần cho zip streaming.",
        )

    # Same monetization gate as the existing disk-based /bulk-zip.
    _zip_user_id = user["id"] if user else None
    _zip_perm = check_feature_permission(_zip_user_id, "bulk_zip") if _zip_user_id else None
    if not _zip_user_id or (_zip_perm and not _zip_perm["allowed"]):
        raise HTTPException(
            status_code=402,
            detail={
                "error":         ERR_BULK_ZIP,
                "feature":       "bulk_zip",
                "current_tier":  _zip_perm["tier"] if _zip_perm else "free",
                "required_tier": "pro",
                "upgrade_url":   "/upgrade",
                "message":       "Tải ZIP yêu cầu gói Pro. Nâng cấp để tải toàn bộ batch về 1 file ZIP.",
            },
        )

    resolved, failed = await resolve_stream_zip_items(urls)
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail={"error": "extraction_failed", "message": "Không lấy được video nào.", "failed": failed},
        )

    headers = {
        "Content-Disposition": 'attachment; filename="vidgrab_batch.zip"',
        "Access-Control-Expose-Headers": "Content-Disposition, X-Failed-Count",
        "X-Failed-Count": str(len(failed)),
    }
    return StreamingResponse(
        stream_zip_response_body(resolved),
        media_type="application/zip",
        headers=headers,
    )


# ── GET /quota  (user quota info) ──────────────────────────────────

@router.get("/quota")
async def get_quota(req: Request):
    user_id = get_client_ip(req)
    try:
        info = check_user_quota(user_id)
        return {
            "success": True,
            "quota": info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /download-local ──────────────────────────────────────────────

from fastapi.responses import FileResponse
import os

@router.get("/download-local")
@limiter.limit("120/minute")
async def download_local_file(request: Request, filepath: str, filename: str):
    # Resolve relative paths (e.g. "downloads/xxx.mp3") to absolute
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not os.path.isabs(filepath):
        filepath = os.path.join(base_dir, filepath)

    # Prevent path traversal: ensure the file is inside the downloads directory
    allowed_dir = os.path.realpath(os.path.join(base_dir, "downloads"))
    real_path = os.path.realpath(filepath)
    if not real_path.startswith(allowed_dir + os.sep) and real_path != allowed_dir:
        raise HTTPException(status_code=403, detail="Access denied.")

    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File expired or not found.")
    filepath = real_path
    
    # Detect media type from extension
    ext = os.path.splitext(filepath)[1].lower()
    media_types = {
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4", ".zip": "application/zip",
        ".srt": "text/plain; charset=utf-8", ".vtt": "text/vtt; charset=utf-8",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    
    return FileResponse(filepath, filename=filename, media_type=media_type)


# ── GET /download-thumbnail  (proxy thumbnail image) ────────────────

@router.get("/download-thumbnail")
@limiter.limit("120/minute")
async def download_thumbnail(request: Request, url: str, filename: str = "thumbnail"):
    """
    Proxy-download a video thumbnail image through the backend.
    This bypasses CORS restrictions so the browser can save the image.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    _assert_safe_url(url)

    try:
        import re
        import unicodedata

        # Sanitize filename
        normalized = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        cleaned = re.sub(r'[^\w\s-]', '', normalized).strip()
        slugified = re.sub(r'[\s]+', '-', cleaned)
        slugified = re.sub(r'-+', '-', slugified)
        if not slugified:
            slugified = "thumbnail"

        # Detect image extension from URL
        ext = "jpg"
        url_lower = url.lower()
        if ".png" in url_lower:
            ext = "png"
        elif ".webp" in url_lower:
            ext = "webp"

        content_types = {
            "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
        }
        content_type = content_types.get(ext, "image/jpeg")

        async def stream_image():
            # follow_redirects stays OFF — safe_stream re-runs the SSRF guard on
            # every hop, so a public URL cannot 302 us onto 169.254.169.254.
            from app.core.ssrf_guard import safe_stream
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with safe_stream(client, "GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            stream_image(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{slugified}.{ext}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Thumbnail download failed: {str(e)}")


# ── POST /trim  (cut video/audio segment with FFmpeg) ────────────────

import subprocess

class TrimRequest(BaseModel):
    url: Optional[str] = None
    local_path: Optional[str] = None  # Phase 22: server-side local file path
    start_time: float  # seconds
    end_time: float    # seconds
    filename: Optional[str] = "trimmed_video"
    is_audio: Optional[bool] = False

@router.post("/trim")
@limiter.limit("10/minute")
async def trim_media(payload: TrimRequest, request: Request):
    """
    Download a video/audio (or use a local file), trim it using FFmpeg (-ss to -to with -c copy),
    and return the trimmed file for download.
    Accepts either `url` (remote) or `local_path` (server-side file already in downloads/).
    """
    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="url or local_path is required")
    if payload.start_time < 0 or payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="Invalid time range")
    if payload.end_time - payload.start_time > 600:
        raise HTTPException(status_code=400, detail="Maximum trim duration is 10 minutes")

    try:
        import uuid as _uuid

        ext = "mp3" if payload.is_audio else "mp4"
        download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
        os.makedirs(download_dir, exist_ok=True)

        output_path = os.path.join(download_dir, f"trim_output_{_uuid.uuid4().hex[:8]}.{ext}")
        _trim_downloaded = False

        # Phase 22: support local_path (skip download when file is already on server)
        if payload.local_path:
            abs_path = os.path.realpath(payload.local_path)
            abs_dl   = os.path.realpath(download_dir)
            if not abs_path.startswith(abs_dl):
                raise HTTPException(status_code=400, detail="Invalid local_path: path traversal not allowed")
            if not os.path.exists(abs_path):
                raise HTTPException(status_code=404, detail="Local file not found or expired")
            input_path = abs_path
        else:
            input_path = os.path.join(download_dir, f"trim_input_{_uuid.uuid4().hex[:8]}.{ext}")
            _trim_downloaded = True
            # Download the source file. This path had NO SSRF guard at all —
            # any caller could point /trim at an internal host.
            _assert_safe_url(payload.url)
            from app.core.ssrf_guard import safe_stream
            async with httpx.AsyncClient(timeout=120.0, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.tiktok.com/",
            }) as client:
                async with safe_stream(client, "GET", payload.url) as resp:
                    resp.raise_for_status()
                    with open(input_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)

        # FFmpeg trim with copy mode (fast, lossless)
        start_str = f"{payload.start_time:.2f}"
        end_str = f"{payload.end_time:.2f}"

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", start_str,
            "-to", end_str,
            "-i", input_path,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ]

        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            # Fallback: re-encode if copy mode fails (some formats need it)
            if payload.is_audio:
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-ss", start_str, "-to", end_str,
                    "-i", input_path,
                    "-acodec", "libmp3lame", "-b:a", "320k",
                    output_path,
                ]
            else:
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-ss", start_str, "-to", end_str,
                    "-i", input_path,
                    "-c:v", "libx264", "-c:a", "aac", "-preset", "fast",
                    output_path,
                ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                raise ValueError(f"FFmpeg error: {result.stderr.decode()[-200:]}")

        if not os.path.exists(output_path):
            raise ValueError("Trimmed file was not created")

        # Only clean up input file when it was downloaded (not a pre-existing local_path)
        if _trim_downloaded:
            try:
                os.remove(input_path)
            except:
                pass

        # Schedule cleanup of output file after 15 minutes
        from app.tasks.video_tasks import delete_local_file
        delete_local_file.apply_async((output_path,), countdown=15 * 60)

        # Sanitize filename
        import re
        import unicodedata
        normalized = unicodedata.normalize('NFKD', payload.filename).encode('ascii', 'ignore').decode('ascii')
        cleaned = re.sub(r'[^\w\s-]', '', normalized).strip()
        slugified = re.sub(r'[\s]+', '-', cleaned) or "trimmed"

        file_size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)
        output_size_ratio = round(file_size_mb / max(os.path.getsize(input_path) / (1024 * 1024), 0.001), 3)

        return {
            "success": True,
            "trimmed_file_path": output_path,
            "filename": f"{slugified}_trimmed.{ext}",
            "file_size_mb": file_size_mb,
            "duration": round(payload.end_time - payload.start_time, 2),
            "output_size_ratio": output_size_ratio,
        }

    except HTTPException:
        raise
    except Exception as e:
        # Clean up on error — never remove a pre-existing local_path
        _downloaded_input = locals().get("input_path") if locals().get("_trim_downloaded") else None
        for p in [_downloaded_input, locals().get("output_path")]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass
        raise HTTPException(status_code=500, detail=f"Trim failed: {str(e)}")


# ── POST /to-gif  (convert video segment to animated GIF via FFmpeg) ──

class ToGifRequest(BaseModel):
    url: Optional[str] = None        # remote video URL (http/https)
    local_path: Optional[str] = None # server-side local file path (already downloaded)
    start_time: float = 0
    end_time: float = 10
    width: int = 480      # output width px (height auto-scaled); max 1080
    fps: int = 15         # frames per second; max 30
    filename: Optional[str] = "animation"

@router.post("/to-gif")
@limiter.limit("5/minute")
async def convert_to_gif(payload: ToGifRequest, request: Request):
    """
    Download a video clip and convert it to a high-quality animated GIF.
    Uses FFmpeg two-pass palette approach: palettegen → paletteuse.
    Limits: 30s max duration, 1080px max width, 30fps max.
    Accepts either a remote `url` OR a server-side `local_path`.
    """
    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="url or local_path is required")

    duration = payload.end_time - payload.start_time
    if payload.start_time < 0 or duration <= 0:
        raise HTTPException(status_code=400, detail="Invalid time range")
    if duration > 30:
        raise HTTPException(status_code=400, detail="Giới hạn 30 giây cho GIF")
    width = max(64, min(payload.width, 1080))
    fps = max(1, min(payload.fps, 30))

    try:
        import uuid as _uuid

        download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
        os.makedirs(download_dir, exist_ok=True)

        uid = _uuid.uuid4().hex[:8]
        palette_path = os.path.join(download_dir, f"gif_pal_{uid}.png")
        output_path = os.path.join(download_dir, f"gif_out_{uid}.gif")

        # ── Step 1: Resolve input file ───────────────────────
        if payload.local_path:
            # Sanitise: must be inside downloads dir (path traversal guard)
            abs_path = os.path.realpath(payload.local_path)
            abs_dl   = os.path.realpath(download_dir)
            if not abs_path.startswith(abs_dl):
                raise HTTPException(status_code=400, detail="Invalid local_path")
            if not os.path.exists(abs_path):
                raise HTTPException(status_code=404, detail="Local file not found")
            input_path = abs_path
        else:
            input_path = os.path.join(download_dir, f"gif_src_{uid}.mp4")
            _assert_safe_url(payload.url)
            from app.core.ssrf_guard import safe_stream
            async with httpx.AsyncClient(timeout=120.0, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.tiktok.com/",
            }) as client:
                async with safe_stream(client, "GET", payload.url) as resp:
                    resp.raise_for_status()
                    with open(input_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)

        start_str = f"{payload.start_time:.2f}"
        dur_str   = f"{duration:.2f}"
        vf_base   = f"fps={fps},scale={width}:-1:flags=lanczos"

        # ── Step 2: Generate palette (FFmpeg pass 1) ─────────
        palette_cmd = [
            "ffmpeg", "-y",
            "-ss", start_str, "-t", dur_str,
            "-i", input_path,
            "-vf", f"{vf_base},palettegen=stats_mode=diff",
            palette_path,
        ]
        r1 = subprocess.run(palette_cmd, capture_output=True, timeout=60)
        if r1.returncode != 0 or not os.path.exists(palette_path):
            raise ValueError(f"palettegen failed: {r1.stderr.decode()[-200:]}")

        # ── Step 3: Convert to GIF using palette (pass 2) ────
        gif_cmd = [
            "ffmpeg", "-y",
            "-ss", start_str, "-t", dur_str,
            "-i", input_path,
            "-i", palette_path,
            "-lavfi", f"{vf_base} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            output_path,
        ]
        r2 = subprocess.run(gif_cmd, capture_output=True, timeout=120)
        if r2.returncode != 0 or not os.path.exists(output_path):
            raise ValueError(f"GIF conversion failed: {r2.stderr.decode()[-200:]}")

        # Clean up temp files
        for p in (input_path, palette_path):
            try: os.remove(p)
            except: pass

        # Schedule GIF cleanup after 20 minutes
        from app.tasks.video_tasks import delete_local_file
        delete_local_file.apply_async((output_path,), countdown=20 * 60)

        import re as _re, unicodedata as _uni
        norm = _uni.normalize("NFKD", payload.filename).encode("ascii", "ignore").decode("ascii")
        slug = _re.sub(r"[\s]+", "-", _re.sub(r"[^\w\s-]", "", norm).strip()) or "animation"

        gif_size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)

        return {
            "success": True,
            "gif_path": output_path,
            "filename": f"{slug}.gif",
            "file_size_mb": gif_size_mb,
            "width": width,
            "fps": fps,
            "duration": round(duration, 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        for p in [locals().get("input_path"), locals().get("palette_path"), locals().get("output_path")]:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass
        raise HTTPException(status_code=500, detail=f"GIF conversion failed: {str(e)}")


# ── POST /retry-failed/{batch_id}  (re-queue all failed jobs) ─────────

@router.post("/retry-failed/{batch_id}")
@limiter.limit("10/minute")
async def retry_failed_jobs(batch_id: str, request: Request):
    """
    Re-queue failed jobs in a batch.
    Skips jobs that have already reached MAX_MANUAL_RETRIES attempts.
    """
    from app.core.state_model import MAX_MANUAL_RETRIES, RETRYABLE_STATES
    user_id = get_client_ip(request)
    supabase = get_supabase_client()

    failed_res = (
        supabase.table("download_jobs")
        .select("id, original_url, status, recovery_attempts")
        .eq("batch_id", batch_id)
        .in_("status", list(RETRYABLE_STATES))
        .execute()
    )
    candidates = [
        j for j in (failed_res.data or [])
        if j.get("original_url") not in (None, "", "batch_zip")
    ]

    if not candidates:
        return {"success": True, "retried": 0, "skipped": 0,
                "message": "Không có job nào có thể thử lại."}

    retried = 0
    skipped = 0
    for job in candidates:
        attempts = (job.get("recovery_attempts") or 0)
        if attempts >= MAX_MANUAL_RETRIES:
            skipped += 1
            continue
        try:
            supabase.table("download_jobs").update({
                "status":             "pending",
                "error_message":      None,
                "recovery_attempts":  attempts + 1,
            }).eq("id", job["id"]).execute()
            process_video_task.delay(job["id"], job["original_url"], user_id)
            retried += 1
        except Exception as e:
            print(f"[RetryFailed] Could not re-queue job {job['id']}: {e}")
            skipped += 1

    msg = f"Đã thêm lại {retried} job."
    if skipped:
        msg += f" Bỏ qua {skipped} job (đã đạt giới hạn thử lại hoặc lỗi)."
    return {"success": True, "retried": retried, "skipped": skipped, "message": msg}


# ── POST /resume-batch/{batch_id}  (resume interrupted batches) ────────

@router.post("/resume-batch/{batch_id}")
@limiter.limit("10/minute")
async def resume_batch(batch_id: str, request: Request):
    """
    Resume a batch where jobs got stuck:
    - pending: never dispatched (e.g. worker restart before Celery picked them up)
    - processing: stuck > 10 min (worker died mid-task)
    """
    from datetime import datetime, timezone, timedelta
    user_id = get_client_ip(request)
    supabase = get_supabase_client()

    stuck_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)

    candidates_res = (
        supabase.table("download_jobs")
        .select("id, original_url, status, created_at")
        .eq("batch_id", batch_id)
        .in_("status", ["pending", "processing"])
        .execute()
    )

    stuck_jobs = []
    for job in (candidates_res.data or []):
        url = job.get("original_url", "")
        if url in (None, "", "batch_zip"):
            continue
        if job["status"] == "pending":
            stuck_jobs.append(job)
        elif job["status"] == "processing":
            try:
                created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
                if created < stuck_cutoff:
                    stuck_jobs.append(job)
            except Exception:
                stuck_jobs.append(job)

    if not stuck_jobs:
        return {"success": True, "resumed": 0, "message": "Không có job nào bị kẹt."}

    resumed = 0
    for job in stuck_jobs:
        try:
            supabase.table("download_jobs").update({
                "status": "pending",
                "error_message": "♻ Đang khởi động lại...",
            }).eq("id", job["id"]).execute()
            process_video_task.delay(job["id"], job["original_url"], user_id)
            resumed += 1
        except Exception as e:
            print(f"[ResumeBatch] Could not re-queue job {job['id']}: {e}")

    return {"success": True, "resumed": resumed, "message": f"Đã khởi động lại {resumed} job."}


# ── GET /jobs/{batch_id}  (polling) ──────────────────────────────────

@router.get("/history")
async def get_history(
    limit: int = 5,
    offset: int = 0,
    platform: str = None,
    status: str = None,
):
    try:
        supabase = get_supabase_client()
        q = (
            supabase.table("download_jobs")
            .select("*")
            .order("created_at", desc=True)
        )
        if platform:
            q = q.eq("platform", platform)
        if status in ("success", "failed", "processing"):
            q = q.eq("status", status)
        q = q.range(offset, offset + limit - 1)
        response = q.execute()
        return {
            "success": True,
            "jobs": response.data if response.data else []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/history/all")
async def delete_all_history():
    try:
        supabase = get_supabase_client()
        supabase.table("download_jobs").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/history/{job_id}")
async def delete_history_job(job_id: str):
    try:
        supabase = get_supabase_client()
        supabase.table("download_jobs").delete().eq("id", job_id).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/jobs/{batch_id}")
async def get_jobs_by_batch(batch_id: str):
    try:
        from datetime import datetime, timezone
        supabase = get_supabase_client()
        response = (
            supabase.table("download_jobs")
            .select("*")
            .eq("batch_id", batch_id)
            .order("created_at", desc=False)
            .execute()
        )

        now_utc = datetime.now(timezone.utc)
        jobs = response.data
        total = len(jobs)
        success = sum(1 for j in jobs if j["status"] == "success")
        failed = sum(1 for j in jobs if j["status"] == "failed")
        processing = sum(1 for j in jobs if j["status"] == "processing")
        pending = sum(1 for j in jobs if j["status"] == "pending")

        # Annotate each job with a computed recovery_state for the frontend
        for job in jobs:
            job["recovery_state"] = _compute_recovery_state(job, now_utc)

        recoverable = sum(1 for j in jobs if j["recovery_state"] in ("expired_refreshable", "recoverable"))

        # ETA estimate: (elapsed / completed) * remaining
        eta_seconds = None
        completed = success + failed
        remaining = pending + processing
        if completed > 0 and remaining > 0:
            try:
                timestamps = [j["created_at"] for j in jobs if j.get("created_at")]
                if timestamps:
                    oldest = min(timestamps)
                    batch_start = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - batch_start).total_seconds()
                    avg_per_video = elapsed / completed
                    eta_seconds = int(avg_per_video * remaining)
            except Exception:
                pass

        return {
            "jobs": jobs,
            "summary": {
                "total": total,
                "success": success,
                "failed": failed,
                "processing": processing,
                "pending": pending,
                "recoverable": recoverable,
                "eta_seconds": eta_seconds,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _compute_recovery_state(job: dict, now_utc) -> str:
    """
    Compute a human-readable recovery state for a job.
    Values: ok | processing | expired_refreshable | expired_gone | recoverable | stuck | abandoned
    """
    status    = job.get("status", "")
    job_stage = job.get("job_stage", "")
    url       = job.get("original_url", "")

    if url == "batch_zip" or not url:
        return "ok"

    if job_stage == "abandoned":
        return "abandoned"

    if status in ("pending", "processing"):
        # Detect stuck: updated_at > 15min ago
        ts_str = job.get("updated_at") or job.get("created_at")
        if ts_str:
            try:
                from datetime import timedelta
                ts = __import__("datetime").datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now_utc - ts).total_seconds() > 15 * 60:
                    return "stuck"
            except Exception:
                pass
        return "processing"

    if status == "success":
        exp_str = job.get("link_expires_at")
        if exp_str:
            try:
                exp = __import__("datetime").datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                if now_utc >= exp:
                    return "expired_refreshable"
            except Exception:
                pass
        return "ok"

    if status == "failed":
        attempts = job.get("recovery_attempts") or 0
        if attempts < 3:
            return "recoverable"
        return "abandoned"

    return "ok"


@router.post("/jobs/{job_id}/refresh")
@limiter.limit("20/minute")
async def refresh_job_link(job_id: str, request: Request):
    """Re-extract a job whose download link has expired or failed."""
    from app.core.recovery import refresh_job_link as _refresh
    from app.core.auth_middleware import get_optional_user
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi import Depends

    user_id = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from app.core.auth_middleware import get_optional_user as _gu
            # Minimal inline token extraction (avoids circular Depends wiring)
            token = auth_header.split(" ", 1)[1]
            sb = get_supabase_client()
            r = sb.auth.get_user(token)
            if r and r.user:
                user_id = str(r.user.id)
        except Exception:
            pass

    result = _refresh(job_id, user_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── GET /proxy-download  (stream video through backend) ─────────────

@router.get("/proxy-download")
@limiter.limit("60/minute")
async def proxy_download(request: Request, url: str, filename: str = "video", ext: str = "mp4"):
    """
    Proxy the video download through the backend to bypass CORS.
    Peeks at the upstream Content-Type so audio URLs mis-labeled as .mp4
    (common with TikWM) get the correct extension and MIME type.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    _assert_safe_url(url)

    try:
        import re
        import unicodedata

        # Slugify filename
        normalized = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        cleaned = re.sub(r'[^\w\s-]', '', normalized).strip()
        slugified = re.sub(r'[\s]+', '-', cleaned)
        slugified = re.sub(r'-+', '-', slugified) or "video"

        safe_ext = ext if ext in ("mp4", "webm", "m4a", "mp3", "ogg", "wav") else "mp4"

        _is_yt_cdn = "googlevideo.com" in url
        _req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.youtube.com/" if _is_yt_cdn else "https://www.tiktok.com/",
            "Accept": "*/*",
        }

        # ── Peek at actual Content-Type to catch audio-disguised-as-mp4 ──────
        # TikWM sometimes returns music URLs in the hdplay/play slots.
        # We do a HEAD first (8s timeout); if that fails we proceed with declared ext.
        if safe_ext == "mp4" and not _is_yt_cdn:
            try:
                from app.core.ssrf_guard import safe_head
                async with httpx.AsyncClient(timeout=8.0) as peek:
                    head = await safe_head(peek, url, headers=_req_headers)
                    upstream_ct = head.headers.get("content-type", "").split(";")[0].strip().lower()
                    if upstream_ct in ("audio/mpeg", "audio/mp3", "audio/x-mpeg"):
                        safe_ext = "mp3"
                    elif upstream_ct in ("audio/mp4", "audio/aac", "audio/x-m4a"):
                        safe_ext = "m4a"
                    # video/mp4 or anything else → keep safe_ext as-is
            except Exception:
                pass  # HEAD not supported by CDN → proceed with declared ext

        media_types = {
            "mp4": "video/mp4", "webm": "video/webm",
            "m4a": "audio/mp4", "mp3": "audio/mpeg",
            "ogg": "audio/ogg", "wav": "audio/wav",
        }
        content_type = media_types.get(safe_ext, "application/octet-stream")
        safe_filename = quote(slugified, safe="")

        async def stream_video():
            import re as _re
            _proxy = None
            if _is_yt_cdn:
                from app.core.proxy_manager import IPROYAL_PROXY
                if IPROYAL_PROXY:
                    _ip_m = _re.search(r'[?&]ip=([0-9.]+)', url)
                    if _ip_m:
                        _cdn_ip = _ip_m.group(1)
                        _server_ip = os.getenv("SERVER_OUTBOUND_IP", "")
                        if not _server_ip or _cdn_ip != _server_ip:
                            _proxy = IPROYAL_PROXY

            from app.core.ssrf_guard import safe_stream
            async with httpx.AsyncClient(
                timeout=300.0, headers=_req_headers,
                proxy=_proxy if _proxy else None,
            ) as client:
                async with safe_stream(client, "GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            stream_video(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{slugified}.{safe_ext}"; filename*=UTF-8\'\'{safe_filename}.{safe_ext}',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy download failed: {str(e)}")


# ── GET /extension/download  (serve Chrome extension zip) ────────────

@router.get("/extension/download")
async def download_extension():
    """Serve the latest VidGrab Chrome extension zip for direct download."""
    import glob as _glob
    # Check mounted path first (set via docker-compose volume)
    _mounted = "/app/extension/VidGrab-extension.zip"
    if os.path.exists(_mounted):
        return FileResponse(_mounted, filename="VidGrab-extension.zip", media_type="application/zip")
    # Fallback: glob versioned zips next to the app root
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    zips = sorted(_glob.glob(os.path.join(base, "chrome_extension_v*_store.zip")), reverse=True)
    if not zips:
        zips = sorted(_glob.glob(os.path.join(base, "chrome_extension_v*.zip")), reverse=True)
    if not zips:
        raise HTTPException(status_code=404, detail="Extension file not found.")
    return FileResponse(zips[0], filename="VidGrab-extension.zip", media_type="application/zip")


# ── GET /platforms  (Platform Discovery — public) ─────────────────────────

@router.get("/platforms")
async def list_supported_platforms():
    """
    Return list of all registered extractors and their capabilities.
    Used by the frontend Platform Discovery page.
    No authentication required.
    """
    try:
        from app.services.extractor_registry import REGISTRY
        platforms = REGISTRY.all_platforms()
    except Exception as e:
        # Registry may not be available in minimal environments
        platforms = []

    # Merge with static metadata (status, logo, description) from a curated list
    _STATIC_META = {
        "Bilibili":    {"status": "full",  "isNew": True,  "logo": "📺"},
        "Xiaohongshu": {"status": "full",  "isNew": True,  "logo": "🌸"},
        "Lemon8":      {"status": "basic", "isNew": True,  "logo": "🍋"},
        "Snapchat":    {"status": "basic", "isNew": True,  "logo": "👻"},
        "VK":          {"status": "basic", "isNew": True,  "logo": "💙"},
        "Twitch":      {"status": "basic", "isNew": True,  "logo": "🎮"},
        "Rumble":      {"status": "basic", "isNew": True,  "logo": "📹"},
        "Odysee":      {"status": "basic", "isNew": True,  "logo": "🌊"},
        "Dailymotion": {"status": "basic", "isNew": True,  "logo": "📽️"},
        "Podcast":     {"status": "basic", "isNew": True,  "logo": "🎙️"},
        "Instagram":   {"status": "full",  "isNew": True,  "logo": "📸"},
        "Twitter":     {"status": "full",  "isNew": True,  "logo": "🐦"},
        "Reddit":      {"status": "full",  "isNew": True,  "logo": "🤖"},
    }

    enriched = []
    for p in platforms:
        meta = _STATIC_META.get(p["platform"], {})
        enriched.append({**p, **meta})

    return {"success": True, "platforms": enriched, "total": len(enriched)}


# ── POST /validate-urls  (Batch URL validation) ───────────────────────────

@router.post("/validate-urls")
async def validate_urls(body: dict):
    """
    Validate a list of URLs before bulk download.
    Returns: {total, counts, summary, urls: [{url, status, platform, message}]}

    Body: {"urls": ["https://...", ...]}
    Authentication: optional (same quota/tier as bulk-download).
    """
    urls = body.get("urls", [])
    if not isinstance(urls, list):
        raise HTTPException(status_code=422, detail="urls must be a list")
    if len(urls) > 500:
        raise HTTPException(status_code=422, detail="Maximum 500 URLs per validation request")

    try:
        from app.services.extractor_registry import REGISTRY
        result = REGISTRY.validate_batch(urls)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


# ── GET /platform-status  (Public platform health for users) ─────────────────

@router.get("/platform-status")
async def get_platform_status():
    """
    Returns a simplified health status for major platforms.
    Only exposes user-facing state (healthy/degraded/constrained) — no internal metrics.
    No authentication required; cached 60s.
    """
    _STATUS_MSG = {
        "healthy":     "Hoạt động bình thường",
        "constrained": "Tốc độ có thể chậm hơn",
        "degraded":    "Đang gặp sự cố",
    }
    try:
        from app.core.lane_observer import observe_all_platforms
        observations = observe_all_platforms()
        platforms = []
        for obs in observations:
            lane = obs.get("laneState", "healthy")
            platforms.append({
                "platform": obs["platform"],
                "status":   lane,
                "message":  _STATUS_MSG.get(lane, "Hoạt động bình thường"),
                "reason":   obs.get("constrainedReason") if lane != "healthy" else None,
            })
        degraded_count = sum(1 for p in platforms if p["status"] == "degraded")
        return {
            "success":        True,
            "platforms":      platforms,
            "degraded_count": degraded_count,
            "all_healthy":    degraded_count == 0,
        }
    except Exception as e:
        return {"success": True, "platforms": [], "degraded_count": 0, "all_healthy": True}

    return {"success": True, **result}
