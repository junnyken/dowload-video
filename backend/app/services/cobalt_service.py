"""
Cobalt API Service — YouTube HD Download via Cobalt
Uses a local Cobalt instance to bypass YouTube SABR restrictions.

Cobalt handles YouTube's anti-bot protections (SABR, n-challenge, PO tokens)
internally. We use it for both format discovery AND server-side downloading.

Tunnel URLs returned by Cobalt (http://cobalt-api:9000/tunnel?...) are
accessible from the backend container via the Docker internal network.
"""
import os
import httpx
import re
from typing import Dict, Any, Optional

# Default local Cobalt instance (kept for backward compat / anything importing
# COBALT_API_URL directly — it's always instances[0]).
COBALT_API_URL = os.getenv("COBALT_API_URL", "http://localhost:9000")

# Multi-instance rotation: comma-separated list, self-hosted instance first.
# Only COBALT_API_URL is configured by default — no third-party public
# instance is baked in here. Cobalt's own project doesn't endorse a specific
# public list, and routing a user's requested URL through an unvetted third
# party is a real trust/privacy call the operator should make explicitly by
# setting COBALT_API_URLS, not something to default silently.
COBALT_API_URLS = [
    u.strip() for u in os.getenv("COBALT_API_URLS", COBALT_API_URL).split(",") if u.strip()
] or [COBALT_API_URL]

_COBALT_COOLDOWN_S = int(os.getenv("COBALT_INSTANCE_COOLDOWN_S", "120"))


def _cobalt_down_key(instance_url: str) -> str:
    import hashlib
    return f"cobalt:down:{hashlib.md5(instance_url.encode()).hexdigest()[:12]}"


def _healthy_cobalt_instances() -> list:
    """Configured instances not currently in cooldown from a recent failure.
    Falls back to the full list if Redis is unavailable or all are cooling
    down — never block extraction entirely because of instance bookkeeping."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        healthy = [u for u in COBALT_API_URLS if not rc.exists(_cobalt_down_key(u))]
        return healthy or list(COBALT_API_URLS)
    except Exception:
        return list(COBALT_API_URLS)


def _mark_cobalt_instance_down(instance_url: str) -> None:
    try:
        from app.core.redis_client import get_redis
        get_redis().setex(_cobalt_down_key(instance_url), _COBALT_COOLDOWN_S, "1")
    except Exception:
        pass

# Quality presets to probe
YOUTUBE_QUALITIES = [
    {"quality": "1080", "codec": "h264", "label": "Full HD", "container": "mp4"},
    {"quality": "720",  "codec": "h264", "label": "HD",      "container": "mp4"},
    {"quality": "480",  "codec": "h264", "label": "SD",      "container": "mp4"},
    {"quality": "360",  "codec": "h264", "label": "SD",      "container": "mp4"},
]

AUDIO_PRESETS = [
    {"bitrate": "128", "format": "mp3"},
    {"bitrate": "128", "format": "ogg"},
]


def _probe_cobalt_instance(instance_url: str) -> bool:
    try:
        r = httpx.get(instance_url, timeout=5.0)
        if r.status_code != 200:
            return False
        # Accept any valid JSON response from Cobalt as "available"
        # Cobalt v10+: {"cobalt": {"services": [...]}, ...}
        # Cobalt latest: may have different structure
        try:
            r.json()
        except Exception:
            pass  # non-JSON but HTTP 200 — still consider it available
        return True
    except Exception as e:
        print(f"[Cobalt] Health check failed for {instance_url}: {e}")
        return False


def is_cobalt_available() -> bool:
    """True if at least one configured Cobalt instance is running and responsive."""
    for instance in _healthy_cobalt_instances():
        if _probe_cobalt_instance(instance):
            return True
        _mark_cobalt_instance_down(instance)
    return False


def _extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL."""
    patterns = [
        r"(?:v=|\/embed\/|\/v\/|youtu\.be\/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts\/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def fetch_cobalt_stream(url: str, video_quality: str = "1080", 
                        download_mode: str = "auto",
                        youtube_codec: str = "h264",
                        audio_format: str = "best",
                        audio_bitrate: str = "128") -> Dict[str, Any]:
    """
    Request a specific quality stream from Cobalt.
    Returns dict with status, url, filename, etc.
    """
    payload = {
        "url": url,
        "videoQuality": video_quality,
        "downloadMode": download_mode,
        "youtubeVideoCodec": youtube_codec,
        "audioFormat": audio_format,
        "audioBitrate": audio_bitrate,
        "filenameStyle": "pretty",
        "alwaysProxy": False,
    }

    instances = _healthy_cobalt_instances()
    last_error: Dict[str, Any] = {"status": "error", "error": {"code": "no_cobalt_instance"}}

    for instance in instances:
        try:
            r = httpx.post(
                instance,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            result = r.json()
        except Exception as e:
            print(f"[Cobalt] {instance} request failed: {e} — trying next instance")
            _mark_cobalt_instance_down(instance)
            last_error = {"status": "error", "error": {"code": str(e)}}
            continue

        if result.get("status") != "error":
            return result
        # A structured Cobalt error (e.g. unsupported URL) isn't an instance
        # health problem — no point rotating for it, and doing so would mask
        # the real reason with a misleading "no_cobalt_instance" on retries.
        err_code = (result.get("error") or {}).get("code", "")
        if err_code.startswith("error.api."):
            return result
        last_error = result

    return last_error


def extract_youtube_formats_via_cobalt(url: str) -> Dict[str, Any]:
    """
    Probe Cobalt to discover which YouTube qualities are available.
    
    IMPORTANT: We do NOT use Cobalt tunnel URLs for actual downloading
    (they return 0 bytes due to Docker network issues). Instead, all
    formats are marked with requires_merge=True so the frontend triggers
    a backend yt-dlp download+merge when the user clicks download.
    """
    video_formats = []
    audio_formats = []
    max_video_only_height = 0
    seen_heights = set()

    # Probe video qualities
    for preset in YOUTUBE_QUALITIES:
        height = int(preset["quality"])
        if height in seen_heights:
            continue

        try:
            result = fetch_cobalt_stream(
                url=url,
                video_quality=preset["quality"],
                youtube_codec=preset["codec"],
                download_mode="auto",
            )

            status = result.get("status", "error")
            if status in ("tunnel", "redirect", "local-processing"):
                # Quality is available! Mark as requires_merge so
                # backend handles the actual download via yt-dlp
                seen_heights.add(height)

                if height > max_video_only_height:
                    max_video_only_height = height

                video_formats.append({
                    "type": "video",
                    "label": preset["label"],
                    "resolution": f"{preset['quality']}p",
                    "height": height,
                    "ext": preset["container"],
                    "filesize_mb": 0,
                    "url": "",  # No direct URL — backend will download
                    "requires_merge": True,  # Always merge via yt-dlp
                    "source": "cobalt",
                })
            else:
                error_code = result.get("error", {}).get("code", "")
                print(f"[Cobalt] {preset['quality']}p {preset['codec']}: {status} ({error_code})")
        except Exception as e:
            print(f"[Cobalt] Error probing {preset['quality']}p {preset['codec']}: {e}")
            continue

    # Probe audio formats
    for audio_preset in AUDIO_PRESETS:
        try:
            result = fetch_cobalt_stream(
                url=url,
                download_mode="audio",
                audio_format=audio_preset["format"],
                audio_bitrate=audio_preset["bitrate"],
            )

            status = result.get("status", "error")
            if status in ("tunnel", "redirect", "local-processing"):
                ext = audio_preset["format"]
                bitrate = int(audio_preset["bitrate"])

                audio_formats.append({
                    "type": "audio",
                    "label": f"{bitrate}kbps",
                    "ext": ext,
                    "filesize_mb": 0,
                    "url": "",  # No direct URL — backend will download
                    "requires_merge": True,  # Backend handles download
                    "bitrate": bitrate,
                    "source": "cobalt",
                })
        except Exception as e:
            print(f"[Cobalt] Error probing audio {audio_preset['format']}: {e}")
            continue

    # Deduplicate and sort
    video_formats.sort(key=lambda x: x["height"], reverse=True)
    audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)

    return {
        "video_formats": video_formats[:6],
        "audio_formats": audio_formats[:4],
        "max_video_only_height": max_video_only_height,
    }


def download_from_cobalt(url: str, quality: str, output_dir: str) -> Optional[str]:
    """
    Download a YouTube video via Cobalt, saving to output_dir.
    Returns local file path on success, None on failure.

    Cobalt tunnel URLs (http://cobalt-api:9000/tunnel?...) are accessible
    from the backend container within the Docker internal network.
    """
    result = fetch_cobalt_stream(url, video_quality=quality, download_mode="auto")
    status = result.get("status", "error")

    if status == "error":
        error_code = result.get("error", {}).get("code", "unknown")
        print(f"[Cobalt] Download request failed for {quality}p: {error_code}")
        return None

    stream_url = result.get("url")
    if not stream_url:
        print(f"[Cobalt] No stream URL in response for {quality}p (status={status})")
        return None

    filename = result.get("filename", f"cobalt_{quality}p.mp4")
    filename = os.path.basename(filename)
    if not filename.lower().endswith(".mp4"):
        filename = filename.rsplit(".", 1)[0] + ".mp4"

    output_path = os.path.join(output_dir, filename)

    try:
        print(f"[Cobalt] Downloading {quality}p via {status}: {stream_url[:80]}...")
        with httpx.Client(timeout=600.0, follow_redirects=True) as client:
            with client.stream("GET", stream_url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)

        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if file_size > 0:
            print(f"[Cobalt] Downloaded {quality}p: {file_size / (1024*1024):.1f}MB → {output_path}")
            return output_path

        print(f"[Cobalt] Downloaded file is empty for {quality}p")
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception as e:
        print(f"[Cobalt] Download error for {quality}p: {e}")
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    return None


def download_instagram_via_cobalt(url: str, output_dir: str) -> "dict | None":
    """
    Download an Instagram Reel/Post via Cobalt.
    Returns minimal info dict compatible with downloader.py, or None on failure.
    """
    result = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
    status = result.get("status", "error")

    if status == "error":
        print(f"[Cobalt/IG] Request failed: {result.get('error', {}).get('code', 'unknown')}")
        return None

    stream_url = result.get("url")
    if not stream_url:
        print(f"[Cobalt/IG] No stream URL (status={status})")
        return None

    import uuid as _uuid
    filename = result.get("filename") or f"instagram_{_uuid.uuid4().hex[:8]}.mp4"
    filename = os.path.basename(filename)
    if not filename.lower().endswith(".mp4"):
        filename = filename.rsplit(".", 1)[0] + ".mp4"
    output_path = os.path.join(output_dir, filename)

    try:
        print(f"[Cobalt/IG] Downloading via {status}: {stream_url[:80]}...")
        with httpx.Client(timeout=300.0, follow_redirects=True) as client:
            with client.stream("GET", stream_url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)

        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if file_size == 0:
            print("[Cobalt/IG] Downloaded file is empty")
            if os.path.exists(output_path): os.remove(output_path)
            return None

        print(f"[Cobalt/IG] Downloaded {file_size/(1024*1024):.1f}MB → {output_path}")
        import uuid as _uuid2
        return {
            "url": None,
            "title": filename.rsplit(".", 1)[0],
            "thumbnail": "",
            "ext": "mp4",
            "id": _uuid2.uuid4().hex[:8],
            "extractor": "cobalt_instagram",
            "filepath": output_path,
        }
    except Exception as e:
        print(f"[Cobalt/IG] Download error: {e}")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return None


def download_facebook_via_cobalt(url: str, output_dir: str) -> "dict | None":
    """
    Download a Facebook video via Cobalt.
    Returns minimal info dict compatible with downloader.py, or None on failure.
    """
    result = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
    status = result.get("status", "error")

    if status == "error":
        print(f"[Cobalt/FB] Request failed: {result.get('error', {}).get('code', 'unknown')}")
        return None

    stream_url = result.get("url")
    if not stream_url:
        print(f"[Cobalt/FB] No stream URL (status={status})")
        return None

    import uuid as _uuid
    filename = result.get("filename") or f"facebook_{_uuid.uuid4().hex[:8]}.mp4"
    filename = os.path.basename(filename)
    if not filename.lower().endswith(".mp4"):
        filename = filename.rsplit(".", 1)[0] + ".mp4"
    output_path = os.path.join(output_dir, filename)

    try:
        print(f"[Cobalt/FB] Downloading via {status}: {stream_url[:80]}...")
        with httpx.Client(timeout=300.0, follow_redirects=True) as client:
            with client.stream("GET", stream_url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)

        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if file_size == 0:
            print("[Cobalt/FB] Downloaded file is empty")
            if os.path.exists(output_path): os.remove(output_path)
            return None

        print(f"[Cobalt/FB] Downloaded {file_size/(1024*1024):.1f}MB → {output_path}")
        import uuid as _uuid2
        return {
            "url": None,
            "title": filename.rsplit(".", 1)[0],
            "thumbnail": "",
            "ext": "mp4",
            "id": _uuid2.uuid4().hex[:8],
            "extractor": "cobalt_facebook",
            "filepath": output_path,
        }
    except Exception as e:
        print(f"[Cobalt/FB] Download error: {e}")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return None
