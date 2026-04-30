import os
import zipfile
import aiohttp
import asyncio
from typing import List, Dict, Any
from app.core.database import get_supabase_client
from app.utils.helpers import slugify
from datetime import datetime, timezone

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
MAX_ZIP_SIZE = 500 * 1024 * 1024  # 500 MB

async def download_file_to_disk(url: str, dest_path: str, max_retries: int = 3):
    """Download a remote file to local disk using asyncio/aiohttp with retry."""
    timeout = aiohttp.ClientTimeout(total=180)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True, headers=headers) as response:
                    if response.status != 200:
                        print(f"[ZIP] Download attempt {attempt+1} failed with status {response.status}: {url}")
                        continue
                    with open(dest_path, 'wb') as f:
                        while True:
                            chunk = await response.content.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                    return  # Success
        except Exception as e:
            print(f"[ZIP] Download attempt {attempt+1}/{max_retries} failed for {url}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
    print(f"[ZIP] All {max_retries} download attempts failed for {url}")

async def create_batch_zip(batch_id: str) -> Dict[str, Any]:
    """
    Gathers all successfully downloaded files (.mp3 or .mp4) for a batch,
    downloads remote URLs if needed, and checks against the size limit,
    then compresses them into a single .zip file.
    """
    supabase = get_supabase_client()
    
    # 1. Fetch all successful jobs for this batch
    response = supabase.table("download_jobs").select("*").eq("batch_id", batch_id).eq("status", "success").execute()
    jobs = response.data
    
    if not jobs:
        return {"success": False, "error": "Không có file nào thành công để nén."}

    # Prepare batch folder
    batch_dir = os.path.join(DOWNLOAD_DIR, batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    
    files_to_zip = []
    total_estimated_size = 0
    
    # 2. Process each job
    tasks = []
    for job in jobs:
        # Check size (from db if pushed as file_size_mb, otherwise assume something or just download up to limit)
        size_mb = job.get("file_size_mb") or 0
        total_estimated_size += (size_mb * 1024 * 1024)
        
        file_name = f"{job.get('slugified_name') or 'video'}"
        
        # direct_mp4_url may hold: a local file path OR a remote URL
        url_or_path = job.get("direct_mp4_url") or ""
        
        # Case 1: Local file path (starts with downloads/ or /app/downloads/)
        if url_or_path and not url_or_path.startswith("http"):
            # Normalize path
            local_path = url_or_path
            if not os.path.isabs(local_path):
                local_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), local_path)
            
            if os.path.exists(local_path):
                ext = os.path.splitext(local_path)[1] or ".mp4"
                files_to_zip.append((local_path, f"{file_name}{ext}"))
                print(f"[ZIP] Local file: {local_path}")
                continue
            else:
                print(f"[ZIP] Local path not found: {local_path}")
        
        # Case 2: Remote URL
        if url_or_path and url_or_path.startswith("http"):
            ext = ".mp3" if ".mp3" in url_or_path or ".m4a" in url_or_path else ".mp4"
            dest_file = os.path.join(batch_dir, f"{file_name}{ext}")
            if not os.path.exists(dest_file):
                tasks.append(download_file_to_disk(url_or_path, dest_file))
            files_to_zip.append((dest_file, f"{file_name}{ext}"))
            print(f"[ZIP] Remote URL queued: {url_or_path[:80]}...")
        else:
            print(f"[ZIP] Skipping job {job.get('id')}: no valid URL or path")
            
    # Check limit before downloading
    if total_estimated_size > MAX_ZIP_SIZE:
        return {"success": False, "error": f"Tổng dung lượng ({total_estimated_size/1024/1024:.2f}MB) vượt quá giới hạn 500MB."}

    # Wait for all remote downloads
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        
    # Check physical size of files downloaded
    actual_size = 0
    valid_files = []
    for fpath, arcname in files_to_zip:
        if os.path.exists(fpath):
            size = os.path.getsize(fpath)
            actual_size += size
            valid_files.append((fpath, arcname))
            
    if actual_size > MAX_ZIP_SIZE:
        return {"success": False, "error": f"Kích thước file thực tế ({actual_size/1024/1024:.2f}MB) vượt quá giới hạn."}
        
    if not valid_files:
        return {"success": False, "error": "Không thể nén vì download files thất bại."}
        
    # 3. Zip files
    zip_filename = f"batch_{batch_id}.zip"
    zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for fpath, arcname in valid_files:
            zipf.write(fpath, arcname)
            
    # File sizes
    zip_size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
    
    return {
        "success": True, 
        "zip_path": zip_path,
        "zip_size_mb": zip_size_mb,
        "total_files": len(valid_files)
    }

def create_batch_zip_sync(batch_id: str) -> Dict[str, Any]:
    """Sync wrapper to be called by Celery task."""
    return asyncio.run(create_batch_zip(batch_id))
