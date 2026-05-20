"""
API Routes
===========
POST /fetch-link       — single video instant extraction
POST /bulk-download    — batch of video URLs or channel URLs
GET  /jobs/{batch_id}  — poll job progress for a batch
GET  /proxy-download   — proxy video stream to bypass CORS
"""

import ipaddress
import uuid
from typing import List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader as _APIKeyHeader

_admin_header = _APIKeyHeader(name="X-Admin-Token", auto_error=False)
import os as _os

async def _require_admin(token: Optional[str] = Depends(_admin_header)):
    if not token or token != _os.getenv("ADMIN_PASSWORD", "matbaosupport"):
        raise HTTPException(status_code=401, detail="Unauthorized")
from pydantic import BaseModel


def _assert_safe_url(url: str) -> None:
    """Reject non-http(s) schemes and private/loopback IP ranges (SSRF guard)."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")
    hostname = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise HTTPException(status_code=400, detail="URL not allowed")
    except ValueError:
        # hostname is a domain name, not an IP — check common internal names
        blocked = ("localhost", "redis", "cobalt-api", "backend", "celery")
        if any(hostname == b or hostname.endswith(f".{b}") for b in blocked):
            raise HTTPException(status_code=400, detail="URL not allowed")

from app.services.downloader import extract_video_info, classify_url
from app.core.database import get_supabase_client
from app.core.quotas import check_user_quota
from app.tasks.video_tasks import process_video_task, scrape_channel_task
from app.main import limiter

router = APIRouter()


# ── Request / Response Models ────────────────────────────────────────

class FetchLinkRequest(BaseModel):
    url: str
    quality: Optional[str] = "video"
    remove_watermark: Optional[bool] = False
    download_subs: Optional[bool] = False


class BulkDownloadRequest(BaseModel):
    urls: List[str]
    channel_mode: Optional[bool] = False
    max_videos: Optional[int] = 100
    min_views: Optional[int] = 0
    quality: Optional[str] = "video"
    remove_watermark: Optional[bool] = False


# ── POST /fetch-spotify ──────────────────────────────────────────────

from app.services.spotify_service import (
    is_spotify_url,
    _extract_spotify_type_and_id,
    get_playlist_tracks_async,
    get_album_tracks_async,
)

class SpotifyFetchRequest(BaseModel):
    url: str

@router.post("/fetch-spotify")
@limiter.limit("30/minute")
async def fetch_spotify(payload: SpotifyFetchRequest, request: Request):
    if not is_spotify_url(payload.url):
        raise HTTPException(status_code=400, detail="Invalid Spotify URL")
    try:
        sp_type, _ = _extract_spotify_type_and_id(payload.url)

        if sp_type == "playlist":
            result = await get_playlist_tracks_async(payload.url)
            return {"success": True, "type": "playlist", **result}
        elif sp_type == "album":
            result = await get_album_tracks_async(payload.url)
            return {"success": True, "type": "album", **result}
        else:
            raise HTTPException(
                status_code=400,
                detail="Dán link track riêng lẻ vào ô nhập URL chính để tải nhạc đơn.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /fetch-link  (single, instant) ──────────────────────────────

@router.post("/fetch-link")
@limiter.limit("30/minute")
async def fetch_link(payload: FetchLinkRequest, request: Request):  # noqa: ARG001 — SlowAPI uses `request` for IP-based rate limiting
    if not payload.url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    # 1. Quotas disabled (100% Free)

    try:
        # 2. Extract info
        info = await extract_video_info(
            payload.url, 
            payload.quality, 
            payload.remove_watermark,
            download_subs=payload.download_subs
        )
        

        if info.get("local_file_path") or info.get("local_mp3_path"):
            path_to_delete = info.get("local_file_path") or info.get("local_mp3_path")
            from app.tasks.video_tasks import delete_local_file
            delete_local_file.apply_async((path_to_delete,), countdown=20 * 60)
            
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
            "is_audio_only": info.get("is_audio_only"),
            "chapters": info.get("chapters", []),
            "cached": False
        }
    except Exception as e:
        msg = str(e)
        if "video unavailable" in msg.lower() or "unavailable" in msg.lower():
            msg = "🚫 Video không khả dụng — có thể đã bị xóa, giới hạn vùng (geo-block), hoặc bị ẩn."
        elif "sign in" in msg.lower() or "bot" in msg.lower():
            msg = "🤖 YouTube yêu cầu xác thực. Đang thử kênh dự phòng... Vui lòng thử lại sau 30 giây."
        elif "private" in msg.lower():
            msg = "🔒 Video ở chế độ riêng tư, không thể tải."
        raise HTTPException(status_code=500, detail=msg)


# ── POST /bulk-download  (videos + channels) ────────────────────────

@router.post("/bulk-download")
@limiter.limit("30/minute")
async def bulk_download(payload: BulkDownloadRequest, request: Request):
    if not payload.urls or len(payload.urls) == 0:
        raise HTTPException(status_code=400, detail="No URLs provided")

    # Hard cap: 200 URLs per request.
    # Channel mode: each channel URL can expand to max_videos (capped at 500),
    # so 200 channel URLs would already mean up to 100,000 jobs — keep this low.
    MAX_URLS_PER_REQUEST = 200
    if len(payload.urls) > MAX_URLS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Tối đa {MAX_URLS_PER_REQUEST} URL mỗi lần gửi. Vui lòng chia nhỏ danh sách."
        )

    user_id = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
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
                # ── Channel / Playlist path ──────────────────────
                # Insert a placeholder "scraping" job so the frontend
                # can show "Discovering videos..." immediately.
                response = supabase.table("download_jobs").insert({
                    "batch_id": batch_id,
                    "original_url": url,
                    "status": "processing",
                    "error_message": "Đang quét kênh...",
                }).execute()
                
                channel_job_id = response.data[0]["id"]

                # Dispatch scraping to Celery background
                # Hard limit max_videos to prevent scraping abuse (max 500)
                safe_max_videos = min(payload.max_videos or 100, 500)
                scrape_channel_task.delay(url, batch_id, channel_job_id, safe_max_videos, payload.min_views, user_id)
                channel_count += 1

            else:
                # ── Single video path ────────────────────────────
                response = supabase.table("download_jobs").insert({
                    "batch_id": batch_id,
                    "original_url": url,
                    "status": "pending",
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
                    process_video_task.delay(job_id, url, user_id, payload.quality, payload.remove_watermark)
                video_count += 1

        except Exception as e:
            print(f"Error creating job for {url}: {e}")

    # Set exact 15-minute scheduled cleanup for this entire batch folder and zip
    from app.tasks.video_tasks import delete_batch_resources
    delete_batch_resources.apply_async((batch_id,), countdown=15 * 60)

    return {
        "batch_id": batch_id,
        "success": True,
        "channels_detected": channel_count,
        "videos_queued": video_count,
    }

# ── POST /bulk-zip ──────────────────────────────────────────────────

class BulkZipRequest(BaseModel):
    batch_id: str

@router.post("/bulk-zip")
@limiter.limit("30/minute")
async def bulk_zip(payload: BulkZipRequest, request: Request):
    if not payload.batch_id:
        raise HTTPException(status_code=400, detail="Batch ID required")
    
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
        
        create_zip_task.delay(payload.batch_id, zip_job_id)
        return {"success": True, "job_id": zip_job_id}
        
    response = supabase.table("download_jobs").insert({
        "batch_id": payload.batch_id,
        "original_url": "batch_zip",
        "status": "pending",
    }).execute()
    
    zip_job_id = response.data[0]["id"]
    create_zip_task.delay(payload.batch_id, zip_job_id)
    
    return {"success": True, "job_id": zip_job_id}


# ── GET /quota  (user quota info) ──────────────────────────────────

@router.get("/quota")
async def get_quota(req: Request):
    user_id = req.headers.get("x-forwarded-for", req.client.host).split(",")[0].strip()
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
async def download_local_file(filepath: str, filename: str):
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
    media_types = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4", ".zip": "application/zip"}
    media_type = media_types.get(ext, "application/octet-stream")
    
    return FileResponse(filepath, filename=filename, media_type=media_type)


# ── GET /download-thumbnail  (proxy thumbnail image) ────────────────

@router.get("/download-thumbnail")
async def download_thumbnail(url: str, filename: str = "thumbnail"):
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
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                async with client.stream("GET", url) as resp:
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
    url: str
    start_time: float  # seconds
    end_time: float    # seconds
    filename: Optional[str] = "trimmed_video"
    is_audio: Optional[bool] = False

@router.post("/trim")
@limiter.limit("10/minute")
async def trim_media(payload: TrimRequest, request: Request):
    """
    Download a video/audio, trim it using FFmpeg (-ss to -to with -c copy),
    and return the trimmed file for download.
    """
    if payload.start_time < 0 or payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="Invalid time range")
    if payload.end_time - payload.start_time > 600:
        raise HTTPException(status_code=400, detail="Maximum trim duration is 10 minutes")

    try:
        import uuid as _uuid

        ext = "mp3" if payload.is_audio else "mp4"
        download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
        os.makedirs(download_dir, exist_ok=True)

        input_path = os.path.join(download_dir, f"trim_input_{_uuid.uuid4().hex[:8]}.{ext}")
        output_path = os.path.join(download_dir, f"trim_output_{_uuid.uuid4().hex[:8]}.{ext}")

        # Download the source file
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.tiktok.com/",
        }) as client:
            async with client.stream("GET", payload.url) as resp:
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

        # Clean up input file
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

        return {
            "success": True,
            "trimmed_file_path": output_path,
            "filename": f"{slugified}_trimmed.{ext}",
            "file_size_mb": file_size_mb,
            "duration": round(payload.end_time - payload.start_time, 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        # Clean up on error
        for p in [input_path if 'input_path' in dir() else None, output_path if 'output_path' in dir() else None]:
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
            async with httpx.AsyncClient(follow_redirects=True, timeout=120.0, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.tiktok.com/",
            }) as client:
                async with client.stream("GET", payload.url) as resp:
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
    """Re-queue all failed jobs in a batch back to pending and re-dispatch."""
    user_id = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    supabase = get_supabase_client()

    failed_res = (
        supabase.table("download_jobs")
        .select("id, original_url")
        .eq("batch_id", batch_id)
        .eq("status", "failed")
        .execute()
    )
    failed_jobs = [j for j in (failed_res.data or []) if j.get("original_url") not in (None, "", "batch_zip")]

    if not failed_jobs:
        return {"success": True, "retried": 0, "message": "Không có job lỗi nào để thử lại."}

    retried = 0
    for job in failed_jobs:
        try:
            supabase.table("download_jobs").update({
                "status": "pending",
                "error_message": None,
            }).eq("id", job["id"]).execute()
            process_video_task.delay(job["id"], job["original_url"], user_id)
            retried += 1
        except Exception as e:
            print(f"[RetryFailed] Could not re-queue job {job['id']}: {e}")

    return {"success": True, "retried": retried, "message": f"Đã thêm lại {retried} job vào hàng đợi."}


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
    user_id = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
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
async def get_history(limit: int = 5):
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("download_jobs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
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
        supabase = get_supabase_client()
        response = (
            supabase.table("download_jobs")
            .select("*")
            .eq("batch_id", batch_id)
            .order("created_at", desc=False)
            .execute()
        )

        from datetime import datetime, timezone
        jobs = response.data
        total = len(jobs)
        success = sum(1 for j in jobs if j["status"] == "success")
        failed = sum(1 for j in jobs if j["status"] == "failed")
        processing = sum(1 for j in jobs if j["status"] == "processing")
        pending = sum(1 for j in jobs if j["status"] == "pending")

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
                "eta_seconds": eta_seconds,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /proxy-download  (stream video through backend) ─────────────

@router.get("/proxy-download")
async def proxy_download(url: str, filename: str = "video", ext: str = "mp4"):
    """
    Proxy the video download through the backend to bypass CORS.
    The browser calls this endpoint, and the backend fetches the actual
    CDN video and streams it back as a downloadable attachment.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    _assert_safe_url(url)

    try:
        import re
        import unicodedata

        # Remove accents
        normalized = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        # Remove punctuation, keep letters, numbers, spaces, hyphens
        cleaned = re.sub(r'[^\w\s-]', '', normalized).strip()
        # Replace spaces with hyphens
        slugified = re.sub(r'[\s]+', '-', cleaned)
        # Collapse multiple hyphens
        slugified = re.sub(r'-+', '-', slugified)
        
        if not slugified:
            slugified = "video"

        # Sanitize extension
        safe_ext = ext if ext in ("mp4", "webm", "m4a", "mp3", "ogg", "wav") else "mp4"
        media_types = {
            "mp4": "video/mp4", "webm": "video/webm",
            "m4a": "audio/mp4", "mp3": "audio/mpeg",
            "ogg": "audio/ogg", "wav": "audio/wav",
        }
        content_type = media_types.get(safe_ext, "application/octet-stream")

        safe_filename = quote(slugified, safe="")
        ascii_filename = slugified
        
        async def stream_video():
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.tiktok.com/",
                "Accept": "*/*"
            }
            async with httpx.AsyncClient(follow_redirects=True, timeout=120.0, headers=headers) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            stream_video(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{ascii_filename}.{safe_ext}"; filename*=UTF-8\'\'{safe_filename}.{safe_ext}',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy download failed: {str(e)}")
