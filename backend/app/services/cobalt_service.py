"""
Cobalt API Service — YouTube HD Format Discovery
Uses a local Cobalt instance to DISCOVER which YouTube qualities are available,
bypassing SABR restrictions. Actual downloading is done by yt-dlp on the server.

NOTE: Cobalt tunnel URLs do NOT work for proxying (Content-Length: 0 due to
Docker network isolation). So we only use Cobalt for metadata/availability
detection, and mark all formats as requires_merge=True so that the backend
downloads them via yt-dlp + ffmpeg.
"""
import os
import httpx
import re
from typing import Dict, Any, Optional

# Default local Cobalt instance
COBALT_API_URL = os.getenv("COBALT_API_URL", "http://localhost:9000")

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


def is_cobalt_available() -> bool:
    """Check if local Cobalt instance is running."""
    try:
        r = httpx.get(COBALT_API_URL, timeout=3.0)
        data = r.json()
        return "cobalt" in data and "youtube" in data.get("cobalt", {}).get("services", [])
    except Exception:
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

    try:
        r = httpx.post(
            COBALT_API_URL,
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return r.json()
    except Exception as e:
        return {"status": "error", "error": {"code": str(e)}}


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
