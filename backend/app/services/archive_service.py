import os
import re
import zipfile
import aiohttp
import asyncio
import urllib.request
from datetime import datetime
from stat import S_IFREG
from typing import Dict, Any, AsyncIterator, Iterable, Optional
from stream_zip import ZIP_32, stream_zip
from app.core.database import get_supabase_client


def _safe_folder_name(name: str) -> str:
    """Sanitize a channel/uploader name to a safe directory component."""
    if not name:
        return "unknown"
    name = re.sub(r'[\\/:*?"<>|]', '_', name.strip())
    return name[:64] or "unknown"

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
MAX_ZIP_SIZE = 500 * 1024 * 1024  # 500 MB

async def download_file_to_disk(
    url: str,
    dest_path: str,
    max_retries: int = 3,
    session: "aiohttp.ClientSession | None" = None,
):
    """Download a remote file to local disk with retry.

    Pass a shared ``session`` to avoid creating one per file (preferred for
    batch downloads).  When ``session`` is None a temporary session is created.
    """
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    timeout = aiohttp.ClientTimeout(total=180)

    async def _do(sess: aiohttp.ClientSession) -> bool:
        for attempt in range(max_retries):
            try:
                async with sess.get(url, allow_redirects=True, headers=_HEADERS) as resp:
                    if resp.status != 200:
                        print(f"[ZIP] attempt {attempt+1} status {resp.status}: {url[:80]}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                        continue
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
                    return True
            except Exception as e:
                print(f"[ZIP] attempt {attempt+1}/{max_retries} error for {url[:80]}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        print(f"[ZIP] all {max_retries} attempts failed: {url[:80]}")
        return False

    if session is not None:
        await _do(session)
    else:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            await _do(sess)

async def create_batch_zip(batch_id: str, organize_by_channel: bool = False) -> Dict[str, Any]:
    """
    Gathers all successfully downloaded files (.mp3 or .mp4) for a batch,
    downloads remote URLs if needed, then compresses into a single .zip file.

    Optimizations vs original:
    - Returns early if the ZIP was already created (idempotent re-calls).
    - Uses a single shared aiohttp.ClientSession for all remote downloads in
      the batch instead of a new session per file.
    """
    zip_suffix = "_by_channel" if organize_by_channel else ""
    zip_filename = f"batch_{batch_id}{zip_suffix}.zip"
    zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)

    # Idempotency: reuse existing ZIP rather than re-creating it.
    if os.path.exists(zip_path):
        zip_size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
        return {"success": True, "zip_path": zip_path, "zip_size_mb": zip_size_mb,
                "total_files": None, "cached": True}

    supabase = get_supabase_client()

    # 1. Fetch all successful jobs for this batch
    response = (supabase.table("download_jobs")
                .select("*")
                .eq("batch_id", batch_id)
                .eq("status", "success")
                .execute())
    jobs = response.data

    if not jobs:
        return {"success": False, "error": "Không có file nào thành công để nén."}

    batch_dir = os.path.join(DOWNLOAD_DIR, batch_id)
    os.makedirs(batch_dir, exist_ok=True)

    files_to_zip: list[tuple[str, str]] = []
    total_estimated_size = 0
    remote_downloads: list[tuple[str, str]] = []   # (url, dest_path)

    # 2. Classify jobs: local file vs remote URL
    for job in jobs:
        size_mb = job.get("file_size_mb") or 0
        total_estimated_size += size_mb * 1024 * 1024

        file_name = job.get("slugified_name") or "video"
        url_or_path = job.get("direct_mp4_url") or ""

        # Channel subfolder prefix (used when organize_by_channel=True)
        channel_raw = job.get("creator_name") or job.get("creator_handle") or ""
        folder_prefix = (_safe_folder_name(channel_raw) + "/") if organize_by_channel else ""

        if url_or_path and not url_or_path.startswith("http"):
            local_path = url_or_path
            if not os.path.isabs(local_path):
                local_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    local_path,
                )
            if os.path.exists(local_path):
                ext = os.path.splitext(local_path)[1] or ".mp4"
                files_to_zip.append((local_path, f"{folder_prefix}{file_name}{ext}"))
            else:
                print(f"[ZIP] Local path not found: {local_path}")
            continue

        if url_or_path.startswith("http"):
            ext = ".mp3" if (".mp3" in url_or_path or ".m4a" in url_or_path) else ".mp4"
            dest_file = os.path.join(batch_dir, f"{file_name}{ext}")
            if not os.path.exists(dest_file):
                remote_downloads.append((url_or_path, dest_file))
            files_to_zip.append((dest_file, f"{folder_prefix}{file_name}{ext}"))
        else:
            print(f"[ZIP] Skipping job {job.get('id')}: no valid URL or path")

    if total_estimated_size > MAX_ZIP_SIZE:
        return {"success": False,
                "error": f"Tổng dung lượng ({total_estimated_size/1024/1024:.2f}MB) vượt quá giới hạn 500MB."}

    # 3. Download all remote files using ONE shared session
    if remote_downloads:
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await asyncio.gather(
                *(download_file_to_disk(url, dest, session=session)
                  for url, dest in remote_downloads),
                return_exceptions=True,
            )

    # 4. Validate collected files
    actual_size = 0
    valid_files: list[tuple[str, str]] = []
    for fpath, arcname in files_to_zip:
        if os.path.exists(fpath):
            actual_size += os.path.getsize(fpath)
            valid_files.append((fpath, arcname))

    if actual_size > MAX_ZIP_SIZE:
        return {"success": False,
                "error": f"Kích thước file thực tế ({actual_size/1024/1024:.2f}MB) vượt quá giới hạn."}

    if not valid_files:
        return {"success": False, "error": "Không thể nén vì download files thất bại."}

    # 5. Create ZIP
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fpath, arcname in valid_files:
            zipf.write(fpath, arcname)

    zip_size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
    return {
        "success": True,
        "zip_path": zip_path,
        "zip_size_mb": zip_size_mb,
        "total_files": len(valid_files),
    }

def create_batch_zip_sync(batch_id: str, organize_by_channel: bool = False) -> Dict[str, Any]:
    """Sync wrapper to be called by Celery task."""
    return asyncio.run(create_batch_zip(batch_id, organize_by_channel=organize_by_channel))


# ─────────────────────────────────────────────────────────────────────────
# Streaming ZIP — no disk writes for the zip itself (Phase: gendownload-style
# /api/zip). Each member's bytes are pulled straight from the resolved
# remote URL (or, for the rare platform that must pre-process locally, a
# transient temp file that's deleted the moment its bytes are streamed) and
# fed directly into the client's HTTP response. Nothing is ever written to
# DOWNLOAD_DIR and there is no batch/zip file left behind to clean up later.
#
# Only "video_fast" quality gives a genuine pass-through URL (see
# downloader.py: `should_download = quality != "video_fast" or is_tiktok`),
# so this path is fixed to that tier — it is not a general-purpose
# replacement for the disk-based /bulk-zip flow, which supports every
# quality because it can run FFmpeg merge/trim on the downloaded file.
# ─────────────────────────────────────────────────────────────────────────

STREAM_ZIP_QUALITY = "video_fast"
MAX_STREAM_ZIP_ITEMS = 20
_STREAM_CHUNK_SIZE = 256 * 1024


def _slugify_filename(title: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", (title or fallback).strip())
    name = re.sub(r"\s+", " ", name).strip()
    return (name[:120] or fallback)


def _iter_remote_bytes(url: str) -> Iterable[bytes]:
    """Blocking chunked read of a remote URL. Runs inside stream_zip's
    generator, which Starlette executes in a thread pool — never on the
    event loop (see iterate_in_threadpool in the route below)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=180) as resp:
        while True:
            chunk = resp.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                return
            yield chunk


def _iter_local_file_bytes(path: str) -> Iterable[bytes]:
    """Read a transient local file in chunks, deleting it once fully read."""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def resolve_stream_zip_items(urls: list[str]) -> tuple[list[dict], list[dict]]:
    """
    Extract each URL at STREAM_ZIP_QUALITY concurrently.
    Returns (resolved, failed) — resolved items carry {name, source, kind}
    where kind is "remote" (direct_mp4_url) or "local" (transient file path).
    """
    from app.services.downloader import extract_video_info

    async def _resolve_one(url: str) -> dict:
        try:
            info = await extract_video_info(url, quality=STREAM_ZIP_QUALITY)
        except Exception as e:
            return {"url": url, "error": str(e)[:200]}

        remote = info.get("direct_mp4_url") or ""
        local = info.get("local_file_path") or info.get("local_mp3_path") or ""
        if not remote and not local:
            return {"url": url, "error": "Không lấy được link tải trực tiếp."}

        ext = ".mp3" if (local.endswith(".mp3") or ".mp3" in remote or ".m4a" in remote) else ".mp4"
        name = _slugify_filename(info.get("title", ""), fallback=f"video_{abs(hash(url)) % 10_000}") + ext
        if remote.startswith("http"):
            return {"url": url, "name": name, "source": remote, "kind": "remote"}
        return {"url": url, "name": name, "source": local, "kind": "local"}

    results = await asyncio.gather(*(_resolve_one(u) for u in urls))
    resolved = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    return resolved, failed


def stream_zip_response_body(resolved_items: list[dict]):
    """Synchronous generator of zip byte chunks — pass directly to
    StreamingResponse; Starlette runs non-async iterables in a thread pool
    so this never blocks the event loop."""
    def _member_files():
        now = datetime.now()
        mode = S_IFREG | 0o644
        seen_names: dict[str, int] = {}
        for item in resolved_items:
            name = item["name"]
            if name in seen_names:
                seen_names[name] += 1
                base, ext = os.path.splitext(name)
                name = f"{base} ({seen_names[name]}){ext}"
            else:
                seen_names[name] = 0

            if item["kind"] == "remote":
                chunks = _iter_remote_bytes(item["source"])
            else:
                chunks = _iter_local_file_bytes(item["source"])
            yield (name, now, mode, ZIP_32, chunks)

    return stream_zip(_member_files())
