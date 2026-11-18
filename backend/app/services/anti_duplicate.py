"""
Anti-Duplicate Video Fingerprint Modifier
==========================================
Modifies video files to bypass content-duplication detection systems on
TikTok, YouTube, Facebook, Instagram, and Douyin.

Techniques applied (all via FFmpeg, no external ML):
  - Strip ALL metadata (MP4 udta/meta atoms, EXIF, XMP, ID3)
  - Re-encode with micro visual changes (hue shift, crop, brightness)
  - Micro speed shift (1.005x–1.01x) to change temporal fingerprint
  - Audio fingerprint disruption (highpass, micro echo, atempo)
  - Inject unique random metadata comment + creation_time
  - Optional mirror flip (horizontal)

Three intensity levels:
  Level 1 ("light")  — metadata strip + re-mux with new comment (< 2s, no quality loss)
  Level 2 ("medium") — Level 1 + hue +2°, crop 2px, speed 1.005x (re-encode, ~1 min)
  Level 3 ("strong") — Level 2 + hue +3°, crop 4px, brightness, contrast, echo, speed 1.01x

Env vars:
  ANTI_DUP_TIMEOUT_SEC — hard timeout for FFmpeg (default 600)
  ANTI_DUP_MAX_SIZE_MB — refuse files larger than this (default 2048)
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.structured_log import get_logger

logger = get_logger(__name__)

ANTI_DUP_TIMEOUT_SEC = int(os.getenv("ANTI_DUP_TIMEOUT_SEC", "600"))
ANTI_DUP_MAX_SIZE_MB = int(os.getenv("ANTI_DUP_MAX_SIZE_MB", "2048"))

# ---------------------------------------------------------------------------
# Level presets
# ---------------------------------------------------------------------------

LEVELS = {
    "light": {
        "label": "Nhẹ — Chỉ đổi metadata & hash",
        "description": "Xóa metadata gốc, thêm comment ngẫu nhiên. Không re-encode, giữ nguyên chất lượng. Nhanh nhất (< 2 giây).",
        "re_encode": False,
        "hue_shift": 0,
        "crop_px": 0,
        "brightness": 0.0,
        "contrast": 1.0,
        "speed": 1.0,
        "highpass_hz": 0,
        "echo": False,
        "mirror": False,
    },
    "medium": {
        "label": "Trung bình — Phá fingerprint hình ảnh & âm thanh",
        "description": "Re-encode với thay đổi nhẹ: hue +2°, crop 2px, speed 1.005x, highpass 20Hz. Mắt thường không phát hiện.",
        "re_encode": True,
        "hue_shift": 2,
        "crop_px": 2,
        "brightness": 0.0,
        "contrast": 1.0,
        "speed": 1.005,
        "highpass_hz": 20,
        "echo": False,
        "mirror": False,
    },
    "strong": {
        "label": "Mạnh — Thay đổi hoàn toàn fingerprint",
        "description": "Re-encode mạnh: hue +3°, crop 4px, brightness +0.02, contrast 1.01, speed 1.01x, echo nhẹ. Hiệu quả nhất chống detect.",
        "re_encode": True,
        "hue_shift": 3,
        "crop_px": 4,
        "brightness": 0.02,
        "contrast": 1.01,
        "speed": 1.01,
        "highpass_hz": 25,
        "echo": True,
        "mirror": False,
    },
}


def get_level_info() -> list[dict]:
    """Return level descriptions for the UI."""
    return [
        {"id": k, "label": v["label"], "description": v["description"]}
        for k, v in LEVELS.items()
    ]


# ---------------------------------------------------------------------------
# FFmpeg command builder
# ---------------------------------------------------------------------------

def _build_ffmpeg_cmd(
    input_path: str,
    output_path: str,
    level: str = "medium",
    mirror: bool = False,
    custom_speed: Optional[float] = None,
    custom_hue: Optional[int] = None,
    crf: int = 18,
) -> list[str]:
    """
    Build an FFmpeg command for the given anti-duplicate level.

    Returns a list of string arguments ready for subprocess.run().
    """
    from app.core.ffmpeg_budget import thread_args

    preset = LEVELS.get(level, LEVELS["medium"])
    hue_shift = custom_hue if custom_hue is not None else preset["hue_shift"]
    speed = custom_speed if custom_speed is not None else preset["speed"]
    crop_px = preset["crop_px"]
    brightness = preset["brightness"]
    contrast = preset["contrast"]
    highpass_hz = preset["highpass_hz"]
    echo = preset["echo"]
    do_mirror = mirror or preset["mirror"]
    re_encode = preset["re_encode"] or hue_shift or crop_px or do_mirror or speed != 1.0

    # Unique metadata for every output
    unique_id = uuid.uuid4().hex
    creation_time = datetime.now(timezone.utc).isoformat()

    meta_args = [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", f"comment=pa_{unique_id}",
        "-metadata", f"creation_time={creation_time}",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
    ]

    if not re_encode:
        # Level "light": stream copy + metadata strip (instant)
        return [
            "ffmpeg", "-y", "-i", input_path,
            *meta_args,
            "-c", "copy",
            "-fflags", "+bitexact",
            "-movflags", "+faststart",
            output_path,
        ]

    # --- Build video filters ---
    vf_parts: list[str] = []

    if hue_shift:
        vf_parts.append(f"hue=h={hue_shift}")

    if crop_px > 0:
        # Crop N px from each edge, then pad back to original size (centered)
        vf_parts.append(f"crop=iw-{crop_px * 2}:ih-{crop_px * 2}")
        vf_parts.append(f"pad=iw+{crop_px * 2}:ih+{crop_px * 2}:(ow-iw)/2:(oh-ih)/2")

    if brightness != 0.0 or contrast != 1.0:
        eq_parts = []
        if brightness != 0.0:
            eq_parts.append(f"brightness={brightness}")
        if contrast != 1.0:
            eq_parts.append(f"contrast={contrast}")
        vf_parts.append(f"eq={':'.join(eq_parts)}")

    if do_mirror:
        vf_parts.append("hflip")

    if speed != 1.0:
        vf_parts.append(f"setpts=PTS/{speed}")

    # --- Build audio filters ---
    af_parts: list[str] = []

    if speed != 1.0:
        af_parts.append(f"atempo={speed}")

    if highpass_hz > 0:
        af_parts.append(f"highpass=f={highpass_hz}")

    if echo:
        # Very subtle echo: delay 6ms, decay 0.18 — inaudible but changes fingerprint
        af_parts.append("aecho=0.8:0.9:6:0.18")

    # --- Assemble command ---
    cmd = ["ffmpeg", "-y", "-i", input_path, *meta_args]

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])
    if af_parts:
        cmd.extend(["-af", ",".join(af_parts)])

    cmd.extend(thread_args())
    cmd.extend([
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ])

    return cmd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_file_hash(path: str, algorithm: str = "md5") -> str:
    """Compute hash of a file. Supports md5 and sha256."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_input(path: str) -> dict:
    """
    Validate that the input file exists and is within size limits.
    Returns {valid: bool, error: str|None, size_mb: float}.
    """
    if not os.path.isfile(path):
        return {"valid": False, "error": "File không tồn tại", "size_mb": 0}

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > ANTI_DUP_MAX_SIZE_MB:
        return {
            "valid": False,
            "error": f"File quá lớn ({size_mb:.0f}MB). Giới hạn: {ANTI_DUP_MAX_SIZE_MB}MB",
            "size_mb": round(size_mb, 2),
        }

    return {"valid": True, "error": None, "size_mb": round(size_mb, 2)}
