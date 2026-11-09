#!/usr/bin/env python3
"""
Benchmark generator and baseline runner for VidGrab Video Watermark Remover (Phase P0).

Structure:
  benchmarks/
    watermark-input/        videos with watermark
    watermark-reference/    clean reference videos (same name, resolution, FPS, frame count)
    labels/<stem>.json      ground-truth bounding boxes per frame
    results/RESULTS.md      markdown benchmark report
    run_benchmark.py        this script

Usage:
  python benchmarks/run_benchmark.py --generate --run
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = Path(__file__).resolve().parent
INPUT_DIR = BENCH_DIR / "watermark-input"
REF_DIR = BENCH_DIR / "watermark-reference"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"

# Ensure .tools is on PATH if present
TOOLS_DIR = ROOT_DIR / ".tools"
if TOOLS_DIR.is_dir():
    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")

sys.path.insert(0, str(ROOT_DIR / "backend"))
from app.api import flow_inpaint as fi  # noqa: E402


# ── Process peak RSS helper (Windows & POSIX) ─────────────────────────

def get_peak_rss_mb() -> float:
    """Return peak working set / RSS of the current process in MiB."""
    if sys.platform == "win32":
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return round(pmc.PeakWorkingSetSize / (1024.0 * 1024.0), 1)
    else:
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                return round(ru / (1024.0 * 1024.0), 1)
            return round(ru / 1024.0, 1)
        except Exception:
            pass
    _, peak = tracemalloc.get_traced_memory()
    return round(peak / (1024.0 * 1024.0), 1)


# ── Video I/O & probing ───────────────────────────────────────────────

def probe_video(path: Path) -> dict:
    r = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-count_frames", str(path),
        ],
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0:
        return {}
    data = json.loads(r.stdout)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            num, den = (s.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
            fps = round(int(num) / max(int(den), 1), 2)
            nb_frames = int(s.get("nb_read_frames") or s.get("nb_frames") or 0)
            dur = float(s.get("duration") or 0.0)
            if nb_frames == 0 and dur > 0:
                nb_frames = int(round(dur * fps))
            return {
                "width": int(s.get("width", 0)),
                "height": int(s.get("height", 0)),
                "fps": fps,
                "frames": nb_frames,
                "duration": round(dur, 2),
            }
    return {}


def decode_frames(path: Path, vw: int, vh: int) -> List[np.ndarray]:
    r = subprocess.run(
        [
            "ffmpeg", "-v", "quiet", "-i", str(path),
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1",
        ],
        capture_output=True,
        timeout=300,
    )
    fsz = vw * vh * 3
    n = len(r.stdout) // fsz
    return [
        np.frombuffer(r.stdout[i * fsz:(i + 1) * fsz], dtype=np.uint8)
        .reshape((vh, vw, 3))
        .copy()
        for i in range(n)
    ]


def encode_frames_lossless(frames: List[np.ndarray], out_path: Path, fps: float = 30.0) -> None:
    vh, vw = frames[0].shape[:2]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", f"{fps:.3f}", "-i", "pipe:0",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_path),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for fr in frames:
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    proc.wait(timeout=180)
    if proc.returncode != 0:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"FFmpeg encode failed for {out_path}: {err}")


# ── Image quality metrics (SSIM / PSNR / Seam / Flicker) ──────────────

def compute_ssim_patch(img1: np.ndarray, img2: np.ndarray) -> float:
    """Compute mean SSIM over a BGR patch using standard Wang et al. constants."""
    if img1.size == 0 or img2.size == 0:
        return 1.0
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    x = img1.astype(np.float64)
    y = img2.astype(np.float64)
    if x.shape[0] >= 11 and x.shape[1] >= 11:
        mu_x = cv2.GaussianBlur(x, (11, 11), 1.5)
        mu_y = cv2.GaussianBlur(y, (11, 11), 1.5)
        sigma_x2 = cv2.GaussianBlur(x * x, (11, 11), 1.5) - mu_x * mu_x
        sigma_y2 = cv2.GaussianBlur(y * y, (11, 11), 1.5) - mu_y * mu_y
        sigma_xy = cv2.GaussianBlur(x * y, (11, 11), 1.5) - mu_x * mu_y
        num = (2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)
        den = (mu_x * mu_x + mu_y * mu_y + c1) * (sigma_x2 + sigma_y2 + c2)
        return float(np.mean(num / den))
    mu_x = x.mean()
    mu_y = y.mean()
    var_x = x.var()
    var_y = y.var()
    cov_xy = np.mean((x - mu_x) * (y - mu_y))
    return float(((2 * mu_x * mu_y + c1) * (2 * cov_xy + c2)) / ((mu_x ** 2 + mu_y ** 2 + c1) * (var_x + var_y + c2)))


def compute_psnr_masked(img1: np.ndarray, img2: np.ndarray, mask_bool: np.ndarray) -> float:
    """Compute PSNR (dB) on pixels where mask_bool is True."""
    diff = img1[mask_bool].astype(np.float64) - img2[mask_bool].astype(np.float64)
    mse = float(np.mean(diff * diff))
    if mse <= 1e-10:
        return 99.0
    return round(10.0 * math.log10((255.0 ** 2) / mse), 2)


def _grad(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))


def compute_seam_and_flicker(frames: List[np.ndarray], x: int, y: int, w: int, h: int, dilate_px: int = 12) -> Tuple[float, float]:
    vh, vw = frames[0].shape[:2]
    inner = np.zeros((vh, vw), np.uint8)
    inner[y:y + h, x:x + w] = 255
    grown = fi.dilate_fill_mask(inner, dilate_px) if dilate_px > 0 else inner
    ring = cv2.dilate(grown, np.ones((9, 9), np.uint8)) - cv2.erode(grown, np.ones((9, 9), np.uint8))
    outer = cv2.dilate(grown, np.ones((81, 81), np.uint8)) - cv2.dilate(grown, np.ones((17, 17), np.uint8))

    seams = []
    for f in frames[::5]:  # sample every 5th frame for speed
        g = _grad(f)
        bg = float(g[outer > 0].mean()) or 1e-6
        seams.append(float(g[ring > 0].mean()) / bg)

    flicks = []
    for i in range(1, len(frames)):
        d = np.abs(frames[i].astype(np.int32) - frames[i - 1].astype(np.int32)).sum(axis=2)
        bg = float(d[outer > 0].mean()) or 1e-6
        flicks.append(float(d[grown > 0].mean()) / bg)

    return round(float(np.mean(seams)), 2), round(float(np.mean(flicks)) if flicks else 0.0, 2)


# ── Synthetic dataset generator (6 groups × 3 videos = 18 pairs) ──────

VW, VH = 640, 360
FPS = 30.0
N_FRAMES = 150  # 5.0 seconds @ 30fps


def _make_base_canvas(vw: int, vh: int, variant: int = 0) -> np.ndarray:
    """Create a large rich deterministic textured canvas for panning/cropping."""
    cw, ch = vw * 2, vh * 2
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    if variant % 3 == 0:
        tex = 18.0 * np.sin(xx / 9.0) * np.cos(yy / 11.0) + 10.0 * np.sin((xx + yy) / 17.0)
        b = 45 + (xx / cw) * 110 + tex
        g = 65 + (yy / ch) * 120 + tex * 0.8
        r = 85 + ((xx + yy) / (cw + ch)) * 100 - tex * 0.6
    elif variant % 3 == 1:
        tex = 20.0 * np.cos(xx / 12.0) * np.sin(yy / 8.0) + 8.0 * np.cos((xx - yy) / 13.0)
        b = 80 + (yy / ch) * 90 + tex
        g = 50 + (xx / cw) * 110 - tex * 0.5
        r = 60 + (yy / ch) * 115 + tex * 0.7
    else:
        tex = 16.0 * np.sin(xx / 7.5) * np.sin(yy / 9.5) + 12.0 * np.cos(xx / 21.0)
        b = 55 + ((cw - xx) / cw) * 105 + tex
        g = 75 + (yy / ch) * 95 + tex
        r = 50 + (xx / cw) * 125 - tex * 0.5

    canvas = np.clip(np.dstack([b, g, r]), 0, 255).astype(np.uint8)
    # Add deterministic geometric landmarks so ORB feature matching has rich keypoints
    rng = np.random.default_rng(100 + variant)
    for _ in range(90):
        px = int(rng.integers(20, cw - 20))
        py = int(rng.integers(20, ch - 20))
        rad = int(rng.integers(6, 22))
        col = tuple(int(c) for c in rng.integers(40, 220, size=3))
        cv2.circle(canvas, (px, py), rad, col, 2, lineType=cv2.LINE_AA)
        cv2.rectangle(canvas, (px - rad, py - rad), (px + rad, py + rad), col, 1)
    return canvas


def _draw_watermark_patch(frame: np.ndarray, box: List[int], text: str, alpha: float = 0.85) -> np.ndarray:
    """Draw a realistic watermark badge (rounded/filled box + crisp text) inside `box`."""
    x, y, w, h = box
    out = frame.copy()
    roi = out[y:y + h, x:x + w]
    overlay = np.full_like(roi, (235, 240, 245), dtype=np.uint8)
    # Badge background + border + text inside [x, y, w, h]
    cv2.rectangle(overlay, (2, 2), (w - 3, h - 3), (30, 160, 250), 2)
    cv2.putText(
        overlay, text, (8, int(h * 0.68)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.58, (20, 20, 25), 2, cv2.LINE_AA,
    )
    blended = roi.astype(np.float32) * (1.0 - alpha) + overlay.astype(np.float32) * alpha
    out[y:y + h, x:x + w] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


BENCHMARK_SPECS = [
    # 1. Logo cố định ở góc (3 videos)
    {"name": "static_corner_01", "group": "Logo cố định ở góc", "mode": "static_corner", "variant": 0, "box": [480, 290, 136, 48], "alpha": 0.90, "text": "VIDGRAB"},
    {"name": "static_corner_02", "group": "Logo cố định ở góc", "mode": "static_corner", "variant": 1, "box": [480, 22, 136, 48], "alpha": 0.90, "text": "DEMO TV"},
    {"name": "static_corner_03", "group": "Logo cố định ở góc", "mode": "static_corner", "variant": 2, "box": [24, 290, 136, 48], "alpha": 0.88, "text": "CLIP HD"},
    # 2. Watermark bán trong suốt (3 videos)
    {"name": "semi_transparent_01", "group": "Watermark bán trong suốt", "mode": "semi_transparent", "variant": 0, "box": [476, 286, 140, 50], "alpha": 0.35, "text": "@agency"},
    {"name": "semi_transparent_02", "group": "Watermark bán trong suốt", "mode": "semi_transparent", "variant": 1, "box": [240, 286, 160, 50], "alpha": 0.42, "text": " STUDIO "},
    {"name": "semi_transparent_03", "group": "Watermark bán trong suốt", "mode": "semi_transparent", "variant": 2, "box": [24, 24, 140, 50], "alpha": 0.32, "text": "PREVIEW"},
    # 3. Logo/chữ di chuyển (kiểu TikTok nhảy vị trí) (3 videos)
    {"name": "moving_watermark_01", "group": "Logo/chữ di chuyển", "mode": "moving_jump2", "variant": 0, "box": [480, 288, 136, 48], "box2": [24, 24, 136, 48], "alpha": 0.85, "text": "@tiktok1"},
    {"name": "moving_watermark_02", "group": "Logo/chữ di chuyển", "mode": "moving_jump3", "variant": 1, "box": [24, 288, 136, 48], "box2": [480, 24, 136, 48], "box3": [480, 288, 136, 48], "alpha": 0.85, "text": "@tiktok2"},
    {"name": "moving_watermark_03", "group": "Logo/chữ di chuyển", "mode": "moving_drift", "variant": 2, "box": [40, 288, 136, 48], "alpha": 0.85, "text": "@moving"},
    # 4. Nền chuyển động mạnh, camera rung (3 videos)
    {"name": "heavy_motion_01", "group": "Nền chuyển động mạnh, camera rung", "mode": "heavy_pan", "variant": 0, "box": [480, 288, 136, 48], "alpha": 0.88, "text": "ACTION"},
    {"name": "heavy_motion_02", "group": "Nền chuyển động mạnh, camera rung", "mode": "camera_shake", "variant": 1, "box": [480, 288, 136, 48], "alpha": 0.65, "text": "SHAKECAM"},
    {"name": "heavy_motion_03", "group": "Nền chuyển động mạnh, camera rung", "mode": "fast_diag", "variant": 2, "box": [24, 288, 136, 48], "alpha": 0.85, "text": "SPORTS"},
    # 5. Có cắt cảnh (3 videos)
    {"name": "scene_cut_01", "group": "Có cắt cảnh", "mode": "scene_cut_1", "variant": 0, "box": [480, 288, 136, 48], "alpha": 0.85, "text": "CUT 01"},
    {"name": "scene_cut_02", "group": "Có cắt cảnh", "mode": "scene_cut_2", "variant": 1, "box": [480, 24, 136, 48], "alpha": 0.85, "text": "CUT 02"},
    {"name": "scene_cut_03", "group": "Có cắt cảnh", "mode": "scene_cut_jump", "variant": 2, "box": [480, 288, 136, 48], "box2": [24, 288, 136, 48], "alpha": 0.85, "text": "CUT 03"},
    # 6. Có chữ trong nội dung (phụ đề, biển hiệu) nhưng không có watermark (3 videos)
    {"name": "content_text_neg_01", "group": "Có chữ trong nội dung (không watermark)", "mode": "neg_subtitle", "variant": 0},
    {"name": "content_text_neg_02", "group": "Có chữ trong nội dung (không watermark)", "mode": "neg_signage", "variant": 1},
    {"name": "content_text_neg_03", "group": "Có chữ trong nội dung (không watermark)", "mode": "neg_captions", "variant": 2},
]


def generate_benchmark_suite(force: bool = False) -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    REF_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    canvases = [_make_base_canvas(VW, VH, v) for v in range(3)]
    rng = np.random.default_rng(20261109)

    for spec in BENCHMARK_SPECS:
        name = spec["name"]
        in_path = INPUT_DIR / f"{name}.mp4"
        ref_path = REF_DIR / f"{name}.mp4"
        lbl_path = LABELS_DIR / f"{name}.json"

        if not force and in_path.exists() and ref_path.exists() and lbl_path.exists():
            continue

        mode = spec["mode"]
        v = spec["variant"]
        clean_frames: List[np.ndarray] = []
        dirty_frames: List[np.ndarray] = []
        boxes_by_frame: Dict[str, List[int]] = {}
        region_type = "static"

        for i in range(N_FRAMES):
            # 1. Determine background crop (camera motion / scene cut)
            if mode in ("static_corner", "semi_transparent"):
                ox = int(i * 1.2)
                oy = int(i * 0.6)
                canvas = canvases[v]
            elif mode.startswith("moving_"):
                ox = int(i * 1.5)
                oy = int(40 + 20 * math.sin(i / 15.0))
                canvas = canvases[v]
                region_type = "moving"
            elif mode == "heavy_pan":
                ox = int((i * 4) % (VW - 20))
                oy = int((i * 2) % (VH - 20))
                canvas = canvases[v]
            elif mode == "camera_shake":
                ox = int(120 + i * 2.0 + rng.integers(-14, 15))
                oy = int(80 + i * 1.0 + rng.integers(-10, 11))
                canvas = canvases[v]
            elif mode == "fast_diag":
                ox = int((i * 3.5) % (VW - 10))
                oy = int((i * 2.5) % (VH - 10))
                canvas = canvases[v]
            elif mode == "scene_cut_1":
                canvas = canvases[v] if i < 75 else canvases[(v + 1) % 3]
                ox = int((i % 75) * 2)
                oy = 40
            elif mode == "scene_cut_2":
                canvas = canvases[(v + (i // 50)) % 3]
                ox = int((i % 50) * 2)
                oy = int((i // 50) * 30)
            elif mode == "scene_cut_jump":
                canvas = canvases[v] if i < 75 else canvases[(v + 2) % 3]
                ox = int((i % 75) * 1.8)
                oy = 50
                region_type = "moving"
            else:
                # Negative samples (content text, no watermark)
                canvas = canvases[v]
                ox = int(i * 1.6)
                oy = 35

            ox = max(0, min(VW - 1, ox))
            oy = max(0, min(VH - 1, oy))
            fr = canvas[oy:oy + VH, ox:ox + VW].copy()

            # For negative samples, draw in-content text (subtitles / world signage) on BOTH clean and dirty
            if mode == "neg_subtitle":
                sub_idx = (i // 35) % 4
                subs = ["Xin chao cac ban!", "Hom nay troi rat dep", "Cung xem tiep nhe", "Cam on da theo doi!"]
                if i % 35 < 28:  # subtitle blinks on/off like real speech
                    cv2.putText(fr, subs[sub_idx], (160, 330), cv2.FONT_HERSHEY_DUPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA)
                    cv2.putText(fr, subs[sub_idx], (160, 330), cv2.FONT_HERSHEY_DUPLEX, 0.75, (255, 255, 220), 2, cv2.LINE_AA)
            elif mode == "neg_signage":
                # Signage moves WITH the scene (world-locked, not screen-locked)
                sx = max(20, 420 - int(i * 2.2))
                cv2.rectangle(fr, (sx, 110), (sx + 160, 165), (40, 50, 180), -1)
                cv2.putText(fr, "CAFE 24H", (sx + 12, 148), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
            elif mode == "neg_captions":
                if 15 <= i <= 65:
                    cv2.putText(fr, "PHAN 1: GIOI THIEU", (150, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 240, 180), 2, cv2.LINE_AA)
                elif 85 <= i <= 135:
                    cv2.putText(fr, "PHAN 2: KET QUA", (175, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (180, 255, 220), 2, cv2.LINE_AA)

            clean_frames.append(fr)

            # 2. Apply watermark for positive samples
            if mode.startswith("neg_"):
                dirty_frames.append(fr.copy())
            else:
                if mode == "moving_jump2" or mode == "scene_cut_jump":
                    box = spec["box"] if i < 75 else spec["box2"]
                elif mode == "moving_jump3":
                    box = spec["box"] if i < 50 else (spec["box2"] if i < 100 else spec["box3"])
                elif mode == "moving_drift":
                    bx0, by0, bw, bh = spec["box"]
                    bx = int(bx0 + (420 - bx0) * (i / float(N_FRAMES - 1)))
                    box = [bx, by0, bw, bh]
                else:
                    box = spec["box"]

                boxes_by_frame[str(i)] = box
                dirty_frames.append(_draw_watermark_patch(fr, box, spec["text"], alpha=spec["alpha"]))

        encode_frames_lossless(clean_frames, ref_path, fps=FPS)
        encode_frames_lossless(dirty_frames, in_path, fps=FPS)

        label_obj = {
            "width": VW,
            "height": VH,
            "fps": FPS,
            "frames": N_FRAMES,
            "group": spec["group"],
            "regions": (
                []
                if mode.startswith("neg_")
                else [
                    {
                        "id": "wm1",
                        "type": region_type,
                        "boxes": boxes_by_frame,
                    }
                ]
            ),
        }
        lbl_path.write_text(json.dumps(label_obj, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[generate] Created pair + label: {name} ({spec['group']})")

    # Also ensure the real sample video has a label file if present
    for mp4 in INPUT_DIR.glob("*.mp4"):
        lbl = LABELS_DIR / f"{mp4.stem}.json"
        if not lbl.exists():
            info = probe_video(mp4)
            if info.get("width") == 900 and info.get("height") == 720:
                n_fr = info.get("frames", 185)
                real_lbl = {
                    "width": 900,
                    "height": 720,
                    "fps": info.get("fps", 30.0),
                    "frames": n_fr,
                    "group": "Watermark bán trong suốt (video thực tế)",
                    "regions": [
                        {
                            "id": "wm1",
                            "type": "static",
                            "boxes": {str(i): [250, 480, 420, 130] for i in range(n_fr)},
                        }
                    ],
                }
                lbl.write_text(json.dumps(real_lbl, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[generate] Created label for real video: {mp4.name}")


# ── Benchmark runner ──────────────────────────────────────────────────

def run_benchmark() -> List[dict]:
    results = []
    input_files = sorted(INPUT_DIR.glob("*.mp4"))

    for in_path in input_files:
        stem = in_path.stem
        lbl_path = LABELS_DIR / f"{stem}.json"
        ref_path = REF_DIR / f"{stem}.mp4"

        in_info = probe_video(in_path)
        vw, vh = in_info.get("width", 0), in_info.get("height", 0)
        fps = in_info.get("fps", 30.0)
        n_frames = in_info.get("frames", 0)
        dur = in_info.get("duration", 0.0)

        lbl_data = {}
        if lbl_path.exists():
            lbl_data = json.loads(lbl_path.read_text(encoding="utf-8"))
        group = lbl_data.get("group", "Chưa phân nhóm")
        regions = lbl_data.get("regions", [])

        # Check if matching clean reference exists
        has_matching_ref = False
        ref_note = "Không có bản sạch khớp thông số"
        if ref_path.exists():
            ref_info = probe_video(ref_path)
            if (
                ref_info.get("width") == vw
                and ref_info.get("height") == vh
                and abs(ref_info.get("fps", 0) - fps) < 0.5
                and ref_info.get("frames") == n_frames
            ):
                has_matching_ref = True
                ref_note = "Có (khớp 100%)"
            else:
                ref_note = f"Lệch thông số ({ref_info.get('width')}x{ref_info.get('height')}, {ref_info.get('frames')}f)"

        # Negative sample (no watermark region to remove)
        if not regions:
            results.append({
                "video": in_path.name,
                "stem": stem,
                "group": group,
                "resolution": f"{vw}×{vh}",
                "fps": fps,
                "frames": n_frames,
                "has_clean": has_matching_ref,
                "ref_note": ref_note,
                "has_watermark": False,
                "ssim_in_mask": None,
                "psnr_out_mask": None,
                "seam": None,
                "flicker": None,
                "elapsed_sec": 0.0,
                "peak_ram_mb": get_peak_rss_mb(),
                "mode": "no_watermark",
                "note": "Mẫu âm tính (chống báo nhầm cho P2 Detector)",
            })
            print(f"[bench] {in_path.name}: negative sample (skipping inpaint)")
            continue

        # Positive sample: take ground-truth mask from frame 0 for P0 baseline engine
        first_region = regions[0]
        r_type = first_region.get("type", "static")
        boxes: Dict[str, List[int]] = first_region.get("boxes", {})
        box0 = boxes.get("0") or next(iter(boxes.values()))
        x, y, w, h = [int(v) for v in box0]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cleaned.mp4"
            tracemalloc.start()
            t0 = time.perf_counter()
            stats = fi.process_video_natural(
                str(in_path), str(out_path),
                x, y, w, h, vw, vh,
                fps=fps, duration=dur,
            )
            elapsed = round(time.perf_counter() - t0, 2)
            peak_ram = get_peak_rss_mb()
            tracemalloc.stop()

            cleaned_frames = decode_frames(out_path, vw, vh)
            seam, flicker = compute_seam_and_flicker(cleaned_frames, x, y, w, h)

            ssim_val: Optional[float] = None
            psnr_val: Optional[float] = None
            note = f"mode={stats['mode']}, temporal={stats['temporal_frames']}/{stats['total_frames']}"

            if has_matching_ref:
                # Re-encode reference with the exact same pipeline & encoder settings (bgr24 -> libx264, crf 18, preset fast, yuv420p)
                # so PSNR outside the mask measures only engine side-effects, not H.264 CRF-18 / swscale recompression loss.
                ref_frames = decode_frames(ref_path, vw, vh)
                ref_reencoded = Path(tmpdir) / "ref_crf18.mp4"
                ref_proc = subprocess.Popen(
                    [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "rawvideo", "-pix_fmt", "bgr24",
                        "-s", f"{vw}x{vh}", "-r", f"{fps:.3f}", "-i", "pipe:0",
                        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-pix_fmt", "yuv420p", "-an", str(ref_reencoded),
                    ],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for rfr in ref_frames:
                    ref_proc.stdin.write(rfr.tobytes())  # type: ignore[union-attr]
                ref_proc.stdin.close()  # type: ignore[union-attr]
                ref_proc.wait(timeout=180)
                ref_crf18_frames = decode_frames(ref_reencoded, vw, vh)
                in_frames = decode_frames(in_path, vw, vh)
                n = min(len(ref_frames), len(ref_crf18_frames), len(cleaned_frames), len(in_frames))

                # Verify ground-truth integrity outside all watermark boxes
                union_mask = np.zeros((vh, vw), dtype=np.uint8)
                for b in boxes.values():
                    bx, by, bw, bh = [int(v) for v in b]
                    union_mask[by:by + bh, bx:bx + bw] = 255
                dilated_union = fi.dilate_fill_mask(union_mask, 14)
                outside_bool = dilated_union == 0

                pairing_mae = float(np.mean([
                    np.abs(ref_frames[i][outside_bool].astype(np.int32) - in_frames[i][outside_bool].astype(np.int32)).mean()
                    for i in range(0, n, max(1, n // 15))
                ]))
                if pairing_mae > 1.5:
                    raise RuntimeError(f"Ground truth mismatch outside mask for {in_path.name}: MAE={pairing_mae:.2f}")

                ssims = []
                psnrs = []
                for i in range(n):
                    fb = boxes.get(str(i), box0)
                    bx, by, bw, bh = [int(v) for v in fb]
                    patch_clean = cleaned_frames[i][by:by + bh, bx:bx + bw]
                    patch_ref = ref_frames[i][by:by + bh, bx:bx + bw]
                    ssims.append(compute_ssim_patch(patch_clean, patch_ref))
                    psnrs.append(compute_psnr_masked(cleaned_frames[i], ref_crf18_frames[i], outside_bool))

                ssim_val = round(float(np.mean(ssims)), 4)
                psnr_val = round(float(np.mean(psnrs)), 2)
                if r_type == "moving":
                    note += f" (dùng bbox cố định ở frame 0: {box0} trên watermark di chuyển)"
            else:
                note += " (không tính SSIM/PSNR do thiếu bản sạch cùng thông số)"

            row = {
                "video": in_path.name,
                "stem": stem,
                "group": group,
                "resolution": f"{vw}×{vh}",
                "fps": fps,
                "frames": n_frames,
                "has_clean": has_matching_ref,
                "ref_note": ref_note,
                "has_watermark": True,
                "region_type": r_type,
                "ssim_in_mask": ssim_val,
                "psnr_out_mask": psnr_val,
                "seam": seam,
                "flicker": flicker,
                "elapsed_sec": elapsed,
                "peak_ram_mb": peak_ram,
                "mode": stats["mode"],
                "temporal_frames": stats["temporal_frames"],
                "note": note,
            }
            results.append(row)
            print(
                f"[bench] {in_path.name}: SSIM={ssim_val} PSNR_out={psnr_val} "
                f"seam={seam} flicker={flicker} time={elapsed}s RAM={peak_ram}MB"
            )

    out_json = RESULTS_DIR / "latest_metrics.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generate", action="store_true", help="Generate synthetic benchmark suite (18 video pairs + labels)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing generated benchmark videos")
    ap.add_argument("--run", action="store_true", help="Run baseline benchmark and save results/latest_metrics.json")
    args = ap.parse_args()

    if not args.generate and not args.run:
        args.generate = True
        args.run = True

    if args.generate:
        generate_benchmark_suite(force=args.force)
    if args.run:
        run_benchmark()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
