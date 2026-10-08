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

from app.core.local_download import new_download_path


def _title_from_cobalt_filename(result: dict, fallback: str) -> str:
    """Display title from Cobalt's pretty filename — never used as a stored name."""
    name = os.path.basename(result.get("filename") or "")
    return name.rsplit(".", 1)[0] if name else fallback

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


def _auth_headers() -> dict:
    """Our own instance requires an API key (API_AUTH_REQUIRED=1, task #6127)
    so a public URL cannot be used by anyone else. Read at call time; the key
    itself is never logged."""
    key = (os.getenv("COBALT_API_KEY") or "").strip()
    return {"Authorization": f"Api-Key {key}"} if key else {}


# Errors that mean "this instance's IP was refused", not "this link is bad":
# another instance (other IP) may succeed. Measured 08/10: TikTok answered
# error.api.fetch.fail on every try for a while from our first instance.
_ROTATE_ON = ("error.api.fetch.fail", "error.api.fetch.rate", "error.api.fetch.critical",
              "error.api.youtube.")

# Per-platform circuit (task #6133): after this many failed Cobalt answers in
# a row for one platform, Cobalt is skipped for it for _TRIP_S, so users stop
# paying Cobalt's latency while that platform blocks us.
_TRIP_AFTER = int(os.getenv("COBALT_TRIP_AFTER", "3"))
_TRIP_S = int(os.getenv("COBALT_TRIP_SECONDS", "900"))
_STATS_TTL = 40 * 86400


def _today() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%d", _t.gmtime())


def record_cobalt_outcome(platform: str, ok: bool) -> None:
    """Never raises. ok resets the failure streak; a streak of _TRIP_AFTER
    trips the platform."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        sk = f"cobalt:stats:{_today()}"
        rc.hincrby(sk, f"{platform}|{'ok' if ok else 'fail'}", 1)
        rc.expire(sk, _STATS_TTL)
        if ok:
            rc.delete(f"cobalt:pfail:{platform}")
            return
        n = rc.incr(f"cobalt:pfail:{platform}")
        rc.expire(f"cobalt:pfail:{platform}", 600)
        if n >= _TRIP_AFTER:
            rc.setex(f"cobalt:trip:{platform}", _TRIP_S, str(n))
            rc.delete(f"cobalt:pfail:{platform}")
            print(f"[Cobalt] {platform}: {n} failures in a row — skipped for {_TRIP_S // 60} min")
    except Exception:
        pass


def cobalt_platform_tripped(platform: str) -> bool:
    try:
        from app.core.redis_client import get_redis
        return bool(get_redis().exists(f"cobalt:trip:{platform}"))
    except Exception:
        return False


def cobalt_video_quality(quality: str) -> str:
    """Our quality string → Cobalt's videoQuality. "video" (HD) = 1080,
    video_<N> = N, video_4k = max."""
    q = str(quality or "video")
    if q == "video_4k":
        return "max"
    if q.startswith("video_") and q[6:].isdigit():
        return q[6:]
    return "1080"


def instances_status(timeout: float = 5.0) -> list:
    """Admin view: each configured instance answering GET / (no API key
    needed), with its version. Host only — never the key."""
    import time as _t
    from urllib.parse import urlparse
    out = []
    for u in COBALT_API_URLS:
        t0 = _t.monotonic()
        row = {"host": urlparse(u).netloc or u, "ok": False, "ms": None, "version": None,
               "cooling_down": False}
        try:
            r = httpx.get(u, timeout=timeout)
            row["ms"] = int((_t.monotonic() - t0) * 1000)
            row["ok"] = r.status_code == 200
            try:
                row["version"] = (r.json().get("cobalt") or {}).get("version")
            except Exception:
                pass
        except Exception:
            pass
        try:
            from app.core.redis_client import get_redis
            row["cooling_down"] = bool(get_redis().exists(_cobalt_down_key(u)))
        except Exception:
            pass
        out.append(row)
    return out


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
                        audio_bitrate: str = "128",
                        extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        **(extra or {}),
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
                    **_auth_headers(),
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
        if err_code.startswith(_ROTATE_ON):
            print(f"[Cobalt] {instance}: {err_code} — trying the next instance")
            last_error = result
            continue
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

    # Cobalt's "pretty" filename is the video title — guessable by anyone who
    # knows the video, and /download-local serves by name. Never store under it.
    output_path = new_download_path(output_dir, "cobalt_", ".mp4")

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


def download_instagram_via_cobalt(url: str, output_dir: str, video_quality: str = "1080") -> "dict | None":
    """
    Download an Instagram Reel/Post via Cobalt.
    Returns minimal info dict compatible with downloader.py, or None on failure.
    """
    result = fetch_cobalt_stream(url, video_quality=video_quality, download_mode="auto")
    status = result.get("status", "error")

    if status == "error":
        print(f"[Cobalt/IG] Request failed: {result.get('error', {}).get('code', 'unknown')}")
        return None

    stream_url = result.get("url")
    if not stream_url:
        print(f"[Cobalt/IG] No stream URL (status={status})")
        return None

    title = _title_from_cobalt_filename(result, "Instagram video")
    output_path = new_download_path(output_dir, "instagram_", ".mp4")

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

        if not is_real_video(output_path):
            print("[Cobalt/IG] answer is not a video (image or broken file) — not used")
            os.remove(output_path)
            return None
        print(f"[Cobalt/IG] Downloaded {file_size/(1024*1024):.1f}MB → {output_path}")
        import uuid as _uuid2
        return {
            "url": None,
            "title": title,
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


def download_facebook_via_cobalt(url: str, output_dir: str, video_quality: str = "1080") -> "dict | None":
    """
    Download a Facebook video via Cobalt.
    Returns minimal info dict compatible with downloader.py, or None on failure.
    """
    result = fetch_cobalt_stream(url, video_quality=video_quality, download_mode="auto")
    status = result.get("status", "error")

    if status == "error":
        print(f"[Cobalt/FB] Request failed: {result.get('error', {}).get('code', 'unknown')}")
        return None

    stream_url = result.get("url")
    if not stream_url:
        print(f"[Cobalt/FB] No stream URL (status={status})")
        return None

    title = _title_from_cobalt_filename(result, "Facebook video")
    output_path = new_download_path(output_dir, "facebook_", ".mp4")

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

        if not is_real_video(output_path):
            print("[Cobalt/FB] answer is not a video (image or broken file) — not used")
            os.remove(output_path)
            return None
        print(f"[Cobalt/FB] Downloaded {file_size/(1024*1024):.1f}MB → {output_path}")
        import uuid as _uuid2
        return {
            "url": None,
            "title": title,
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


_STILL_IMAGE_CODECS = {"mjpeg", "png", "webp", "gif", "bmp", "tiff"}


def is_real_video(path: str) -> bool:
    """ffprobe finds a moving-picture video stream. Cobalt answers some posts
    (photo posts, a reel's cover) with an image, and a non-empty file is not
    a video: measured 07/10 — an Instagram "reel" came back as a 640x1026
    JPEG that the old helpers would have served as .mp4."""
    import json as _json
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
             "-of", "json", path], capture_output=True, text=True, timeout=20)
        streams = _json.loads(out.stdout or "{}").get("streams") or []
    except Exception:
        return False
    return any(s.get("codec_type") == "video" and s.get("codec_name") not in _STILL_IMAGE_CODECS
               for s in streams)


def download_social_via_cobalt(url: str, output_dir: str, platform: str,
                               quality: str = "video") -> "dict | None":
    r = _download_social_via_cobalt(url, output_dir, platform, cobalt_video_quality(quality))
    if r and not is_real_video(r.get("filepath") or ""):
        print(f"[Cobalt/{platform}] answer is not a video (image or broken file) — not used")
        try:
            os.remove(r["filepath"])
        except Exception:
            pass
        r = None
    record_cobalt_outcome(platform, bool(r))
    return r


def _download_social_via_cobalt(url: str, output_dir: str, platform: str,
                                video_quality: str = "1080") -> "dict | None":
    """Instagram / Facebook / X post via Cobalt (task #6127: tried after the
    anonymous yt-dlp attempt and BEFORE a pool cookie is spent). Same result
    shape as download_instagram_via_cobalt; None on any failure, including a
    multi-item "picker" post (no single stream)."""
    if platform == "instagram":
        return download_instagram_via_cobalt(url, output_dir, video_quality)
    if platform == "facebook":
        return download_facebook_via_cobalt(url, output_dir, video_quality)
    result = fetch_cobalt_stream(url, video_quality=video_quality, download_mode="auto")
    stream_url = result.get("url") if result.get("status") != "error" else None
    if not stream_url:
        print(f"[Cobalt/{platform}] no single stream (status={result.get('status')})")
        return None
    output_path = new_download_path(output_dir, f"{platform}_", ".mp4")
    try:
        with httpx.Client(timeout=300.0, follow_redirects=True) as client:
            with client.stream("GET", stream_url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
        import uuid as _uuid3
        return {"url": None, "title": _title_from_cobalt_filename(result, f"{platform} video"),
                "thumbnail": "", "ext": "mp4", "id": _uuid3.uuid4().hex[:8],
                "extractor": f"cobalt_{platform}", "filepath": output_path}
    except Exception as e:
        print(f"[Cobalt/{platform}] download error: {type(e).__name__}")
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        return None
