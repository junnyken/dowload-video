"""
Phase P5.3 — Hardening Fixture Generator (`benchmarks/generate_hardening_fixtures.py`)
======================================================================================
Generates the full matrix of valid and invalid media test fixtures inside `benchmarks/hardening/`
using FFmpeg and Python:
  Valid fixtures:
    - `h264_aac.mp4`: H.264 + AAC audio (320×180, 2.0s, 15fps)
    - `hevc.mp4`: H.265/HEVC video (320×180, 2.0s, 15fps)
    - `vp9.webm`: VP9 + Opus WebM (320×180, 2.0s, 15fps)
    - `mov_clip.mov`: QuickTime MOV container (320×180, 2.0s, 15fps)
    - `no_audio.mp4`: H.264 without audio stream (320×180, 2.0s, 15fps)
    - `vertical_720x1280.mp4`: Vertical 9:16 portrait video (720×1280, 1.5s, 15fps)
    - `odd_721x405.mp4`: Odd-dimension video (721×405 encoded with `yuv444p`, 1.5s, 15fps)
    - `rotated_90.mp4`: Video with display-matrix rotation 90° metadata
    - `vfr.mp4`: Variable Frame Rate (VFR) video
  Invalid / adversarial fixtures:
    - `empty_0byte.mp4`: 0-byte empty file
    - `fake_text_as_mp4.mp4`: Non-video text/HTML file renamed to `.mp4`
    - `corrupted_truncated.mp4`: Truncated/corrupted MP4 header & payload
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict

import cv2
import numpy as np

HARDENING_DIR = Path(__file__).resolve().parent / "hardening"


def _make_base_frames(vw: int, vh: int, n_frames: int) -> list[np.ndarray]:
    frames = []
    yy, xx = np.mgrid[0:vh, 0:vw]
    for idx in range(n_frames):
        shift = idx * 2
        b = np.clip((xx * 180 // max(1, vw)) + 20 + shift, 0, 250).astype(np.uint8)
        g = np.clip((yy * 170 // max(1, vh)) + 30 + (shift // 2), 0, 250).astype(np.uint8)
        r = np.clip(((xx + yy) * 90 // max(1, vw + vh)) + 40 + shift, 0, 250).astype(np.uint8)
        frame = np.stack([b, g, r], axis=-1)
        # Draw a deterministic corner watermark box in lower-right
        wx = max(8, vw - max(48, vw // 5) - 10)
        wy = max(8, vh - max(28, vh // 6) - 10)
        ww = min(vw - wx - 4, max(40, vw // 6))
        wh = min(vh - wy - 4, max(24, vh // 7))
        cv2.rectangle(frame, (wx, wy), (wx + ww, wy + wh), (245, 245, 245), -1)
        cv2.putText(
            frame, "WM", (wx + 6, wy + wh - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 220), 2, cv2.LINE_AA,
        )
        frames.append(frame)
    return frames


def generate_all_hardening_fixtures(out_dir: Path = HARDENING_DIR) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = out_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")

    paths: Dict[str, str] = {}

    # 1. Base H.264 + AAC (320x180, 15fps, 2.0s = 30 frames)
    h264_aac = out_dir / "h264_aac.mp4"
    frames_320 = _make_base_frames(320, 180, 30)
    raw_320 = b"".join(f.tobytes() for f in frames_320)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "320x180", "-r", "15", "-i", "pipe:0",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0:sample_rate=44100",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest",
            str(h264_aac),
        ],
        input=raw_320,
        check=True,
        timeout=60,
    )
    paths["h264_aac"] = str(h264_aac)

    # 2. H.265 / HEVC
    hevc_mp4 = out_dir / "hevc.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(h264_aac),
            "-c:v", "libx265", "-crf", "22", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(hevc_mp4),
        ],
        check=True,
        timeout=60,
    )
    paths["hevc"] = str(hevc_mp4)

    # 3. VP9 / WebM
    vp9_webm = out_dir / "vp9.webm"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(h264_aac),
            "-c:v", "libvpx-vp9", "-b:v", "400k", "-deadline", "realtime", "-cpu-used", "5",
            "-c:a", "libopus", "-b:a", "96k",
            str(vp9_webm),
        ],
        check=True,
        timeout=60,
    )
    paths["vp9_webm"] = str(vp9_webm)

    # 4. QuickTime MOV
    mov_clip = out_dir / "mov_clip.mov"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(h264_aac),
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(mov_clip),
        ],
        check=True,
        timeout=60,
    )
    paths["mov"] = str(mov_clip)

    # 5. Video without audio stream
    no_audio = out_dir / "no_audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(h264_aac),
            "-an", "-c:v", "copy",
            str(no_audio),
        ],
        check=True,
        timeout=60,
    )
    paths["no_audio"] = str(no_audio)

    # 6. Vertical video 720x1280 (9:16)
    vertical_mp4 = out_dir / "vertical_720x1280.mp4"
    frames_vert = _make_base_frames(720, 1280, 18)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "720x1280", "-r", "15", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(vertical_mp4),
        ],
        input=b"".join(f.tobytes() for f in frames_vert),
        check=True,
        timeout=60,
    )
    paths["vertical_720x1280"] = str(vertical_mp4)

    # 7. Odd dimensions 721x405 (encoded with yuv444p which permits odd width/height)
    odd_mp4 = out_dir / "odd_721x405.mp4"
    frames_odd = _make_base_frames(721, 405, 18)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "721x405", "-r", "15", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv444p",
            str(odd_mp4),
        ],
        input=b"".join(f.tobytes() for f in frames_odd),
        check=True,
        timeout=60,
    )
    paths["odd_721x405"] = str(odd_mp4)

    # 8. Rotated 90deg metadata video
    rotated_mp4 = out_dir / "rotated_90.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-display_rotation:v:0", "90",
            "-i", str(h264_aac),
            "-c", "copy",
            str(rotated_mp4),
        ],
        check=True,
        timeout=60,
    )
    paths["rotated_90"] = str(rotated_mp4)

    # 9. Variable Frame Rate (VFR) video
    vfr_mp4 = out_dir / "vfr.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(h264_aac),
            "-vf", "select='not(mod(n,4))+eq(n,29)'",
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an",
            str(vfr_mp4),
        ],
        check=True,
        timeout=60,
    )
    paths["vfr"] = str(vfr_mp4)

    # 10. Invalid: 0-byte file
    empty_path = out_dir / "empty_0byte.mp4"
    empty_path.write_bytes(b"")
    paths["empty_0byte"] = str(empty_path)

    # 11. Invalid: non-video text file renamed to .mp4
    fake_path = out_dir / "fake_text_as_mp4.mp4"
    fake_path.write_bytes(b"<!DOCTYPE html><html><body>Not a video container at all</body></html>\n" * 20)
    paths["fake_text_as_mp4"] = str(fake_path)

    # 12. Invalid: corrupted / truncated video bytes
    corrupted_path = out_dir / "corrupted_truncated.mp4"
    valid_bytes = h264_aac.read_bytes()
    corrupted_path.write_bytes(valid_bytes[:180] + b"\x00\xff\x00\xff" * 30)
    paths["corrupted_truncated"] = str(corrupted_path)

    return paths


if __name__ == "__main__":
    generated = generate_all_hardening_fixtures()
    for k, v in generated.items():
        print(f"Generated {k}: {v} ({os.path.getsize(v)} bytes)")
