"""
API Routes
===========
POST /fetch-link       — single video instant extraction
POST /bulk-download    — batch of video URLs or channel URLs
GET  /jobs/{batch_id}  — poll job progress for a batch
GET  /proxy-download   — proxy video stream to bypass CORS
"""

import uuid
from typing import List, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.downloader import extract_video_info, classify_url
from app.core.database import get_supabase_client
from app.core.cache import get_cached_result
from app.core.quotas import check_user_quota, increment_usage
from app.tasks.video_tasks import process_video_task, scrape_channel_task
from app.main import limiter

router = APIRouter()


# ── Request / Response Models ────────────────────────────────────────

class FetchLinkRequest(BaseModel):
    url: str
    quality: Optional[str] = "video"
    remove_watermark: Optional[bool] = False


class BulkDownloadRequest(BaseModel):
    urls: List[str]
    channel_mode: Optional[bool] = False
    max_videos: Optional[int] = 20
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
async def fetch_link(payload: FetchLinkRequest, request: Request):
    if not payload.url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    user_id = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    
    # 1. Check Quotas
    quota_info = check_user_quota(user_id)
    if not quota_info["allowed"]:
        raise HTTPException(status_code=403, detail="QUOTA_EXCEEDED")

    try:
        # 2. Check Cache
        cached = get_cached_result(payload.url)
        if cached:
            print(f"[Cache Hit] API - URL: {payload.url}")
            increment_usage(user_id)
            return {
                "success": True,
                "title": cached.get("title"),
                "thumbnail_url": cached.get("thumbnail_url"),
                "direct_mp4_url": cached.get("direct_mp4_url"),
                "cached": True
            }

        print(f"[Cache Miss] API - URL: {payload.url} - Fetching fresh link")
        # 3. Extract info
        info = await extract_video_info(payload.url, payload.quality, payload.remove_watermark)
        
        # 4. Increment usage
        increment_usage(user_id)
        
        if info.get("local_file_path") or info.get("local_mp3_path"):
            path_to_delete = info.get("local_file_path") or info.get("local_mp3_path")
            from app.tasks.video_tasks import delete_local_file
            delete_local_file.apply_async((path_to_delete,), countdown=15 * 60)
            
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
            "cached": False
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /bulk-download  (videos + channels) ────────────────────────

@router.post("/bulk-download")
@limiter.limit("30/minute")
async def bulk_download(payload: BulkDownloadRequest, request: Request):
    if not payload.urls or len(payload.urls) == 0:
        raise HTTPException(status_code=400, detail="No URLs provided")
        
    # LIMIT TEMPORARILY DISABLED
    # if len(payload.urls) > 50:
    #     raise HTTPException(status_code=400, detail="Maximum 50 URLs allowed per batch to prevent abuse.")

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
                # Hard limit max_videos to prevent scraping abuse (max 100)
                safe_max_videos = min(payload.max_videos or 20, 100)
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
    # Check if a zip job already exists to avoid duplicates
    existing = supabase.table("download_jobs").select("id").eq("batch_id", payload.batch_id).eq("original_url", "batch_zip").execute()
    if existing.data:
        return {"success": True, "job_id": existing.data[0]["id"]}
        
    response = supabase.table("download_jobs").insert({
        "batch_id": payload.batch_id,
        "original_url": "batch_zip",
        "status": "pending",
    }).execute()
    
    zip_job_id = response.data[0]["id"]
    from app.tasks.video_tasks import create_zip_task
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
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File expired or not found.")
    return FileResponse(filepath, filename=filename, media_type="audio/mpeg")


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

        jobs = response.data
        total = len(jobs)
        success = sum(1 for j in jobs if j["status"] == "success")
        failed = sum(1 for j in jobs if j["status"] == "failed")
        processing = sum(1 for j in jobs if j["status"] == "processing")
        pending = sum(1 for j in jobs if j["status"] == "pending")

        return {
            "jobs": jobs,
            "summary": {
                "total": total,
                "success": success,
                "failed": failed,
                "processing": processing,
                "pending": pending,
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
