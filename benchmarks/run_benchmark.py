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

sys.path.insert(0, str(ROOT_DIR))
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
        GetCurProc = ctypes.windll.kernel32.GetCurrentProcess
        GetCurProc.restype = wintypes.HANDLE
        handle = GetCurProc()
        fn = getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo", None) or ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if fn(handle, ctypes.byref(pmc), pmc.cb) and pmc.PeakWorkingSetSize > 0:
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


def _box_iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = boxA
    bx, by, bw, bh = boxB
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = float(iw * ih)
    union = float(aw * ah + bw * bh) - inter
    return inter / union if union > 0 else 0.0


def run_detector_benchmark() -> Dict[str, Any]:
    """Run Phase P2 detector benchmark across all 19 videos and compute Precision, Recall (IoU >= 0.5), and False Positive rate."""
    from app.services.watermark_detect import detect_watermark_regions

    rows = []
    total_tp = 0
    total_fp = 0
    total_fn = 0
    neg_total = 0
    neg_fp_videos = 0

    for in_path in sorted(INPUT_DIR.glob("*.mp4")):
        stem = in_path.stem
        lbl_path = LABELS_DIR / f"{stem}.json"
        lbl = json.loads(lbl_path.read_text(encoding="utf-8")) if lbl_path.exists() else {}
        group = lbl.get("group", "Chưa phân nhóm")
        gt_regions = lbl.get("regions", [])

        det = detect_watermark_regions(str(in_path))
        preds = det["regions"]
        elapsed = det["elapsed_sec"]

        if not gt_regions:
            neg_total += 1
            fp_c = len(preds)
            total_fp += fp_c
            if fp_c > 0:
                neg_fp_videos += 1
            rows.append({
                "video": in_path.name,
                "stem": stem,
                "group": group,
                "has_watermark": False,
                "gt_box": None,
                "pred_box": [preds[0]["x"], preds[0]["y"], preds[0]["w"], preds[0]["h"]] if preds else None,
                "confidence": preds[0]["confidence"] if preds else None,
                "kind": preds[0]["kind"] if preds else None,
                "status": det["status"],
                "iou": None,
                "tp": 0,
                "fp": fp_c,
                "fn": 0,
                "precision": 1.0 if fp_c == 0 else 0.0,
                "recall": 1.0,
                "false_positive": fp_c > 0,
                "elapsed_sec": elapsed,
            })
            print(f"[detect] {stem:28s} | NEGATIVE | preds={len(preds)} | FP={fp_c > 0} | t={elapsed:.3f}s")
        else:
            gt_boxes_dict = gt_regions[0].get("boxes", {})
            gt_box0 = tuple(int(v) for v in (gt_boxes_dict.get("0") or next(iter(gt_boxes_dict.values()))))
            # Also collect unique GT boxes across clip (for jumping watermarks)
            unique_gt = []
            for b in gt_boxes_dict.values():
                tb = tuple(int(v) for v in b)
                if not any(_box_iou(tb, u) > 0.8 for u in unique_gt):
                    unique_gt.append(tb)

            tp_v = 0
            fp_v = 0
            best_iou = 0.0
            for pr in preds:
                pbox = (pr["x"], pr["y"], pr["w"], pr["h"])
                iou0 = _box_iou(gt_box0, pbox)
                iou_any = max((_box_iou(g, pbox) for g in unique_gt), default=0.0)
                best_iou = max(best_iou, iou0, iou_any)
                if max(iou0, iou_any) >= 0.50:
                    tp_v += 1
                else:
                    fp_v += 1

            # Ground truth count is 1 primary watermark per positive video
            tp_video = 1 if tp_v >= 1 else 0
            fn_video = 0 if tp_v >= 1 else 1
            total_tp += tp_video
            total_fp += fp_v
            total_fn += fn_video

            prec_v = tp_video / float(tp_video + fp_v) if (tp_video + fp_v) > 0 else 0.0
            rec_v = float(tp_video)
            rows.append({
                "video": in_path.name,
                "stem": stem,
                "group": group,
                "has_watermark": True,
                "gt_box": list(gt_box0),
                "pred_box": [preds[0]["x"], preds[0]["y"], preds[0]["w"], preds[0]["h"]] if preds else None,
                "confidence": preds[0]["confidence"] if preds else None,
                "kind": preds[0]["kind"] if preds else None,
                "status": det["status"],
                "iou": round(best_iou, 3),
                "tp": tp_video,
                "fp": fp_v,
                "fn": fn_video,
                "precision": round(prec_v, 3),
                "recall": round(rec_v, 3),
                "false_positive": fp_v > 0,
                "elapsed_sec": elapsed,
            })
            print(
                f"[detect] {stem[:28]:28s} | GT={list(gt_box0)!s:20s} | "
                f"Pred={([preds[0]['x'], preds[0]['y'], preds[0]['w'], preds[0]['h']] if preds else None)!s:20s} | "
                f"IoU={best_iou:.3f} | kind={preds[0]['kind'] if preds else '-':16s} | "
                f"conf={preds[0]['confidence'] if preds else 0:.2f} | t={elapsed:.3f}s"
            )

    overall_precision = round(total_tp / float(max(1, total_tp + total_fp)), 4)
    overall_recall = round(total_tp / float(max(1, total_tp + total_fn)), 4)
    neg_fp_rate = round(neg_fp_videos / float(max(1, neg_total)), 4)
    summary = {
        "total_videos": len(rows),
        "positive_videos": total_tp + total_fn,
        "negative_videos": neg_total,
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "precision": overall_precision,
        "recall": overall_recall,
        "negative_fp_rate": neg_fp_rate,
        "max_elapsed_sec": max((r["elapsed_sec"] for r in rows), default=0.0),
        "mean_elapsed_sec": round(float(np.mean([r["elapsed_sec"] for r in rows])), 3) if rows else 0.0,
        "videos": rows,
    }
    out_det = RESULTS_DIR / "detector_metrics_p2.json"
    out_det.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"\n[detect summary] Precision={overall_precision:.4f} Recall={overall_recall:.4f} "
        f"Neg_FP_Rate={neg_fp_rate * 100:.1f}% ({neg_fp_videos}/{neg_total}) "
        f"mean_time={summary['mean_elapsed_sec']}s max_time={summary['max_elapsed_sec']}s"
    )
    return summary


def run_tracker_benchmark() -> Dict[str, Any]:
    """
    Phase P3 Benchmark:
      1. Evaluates `track_watermark_in_video` across all 16 positive benchmark videos
         (per-frame IoU, median IoU, max drift %, lost rate, scene cuts, processing time).
      2. Evaluates a disappearing-watermark probe to verify lost-track & `needs_review` detection.
      3. Runs `process_video_natural` with tracked `frame_boxes` on the `moving_watermark` group
         (`moving_watermark_01..03`) and `scene_cut_03`, comparing SSIM in mask against the P0
         fixed-bbox baseline (`0.4742`).
    """
    from app.services.watermark_track import (
        evaluate_tracking_against_gt,
        retrack_with_keyframes,
        track_watermark_in_video,
    )

    expected_scene_cuts = {
        "scene_cut_01": [75],
        "scene_cut_02": [50, 100],
        "scene_cut_03": [75],
    }

    tracker_rows = []
    for in_path in sorted(INPUT_DIR.glob("*.mp4")):
        stem = in_path.stem
        lbl_path = LABELS_DIR / f"{stem}.json"
        if not lbl_path.exists():
            continue
        lbl = json.loads(lbl_path.read_text(encoding="utf-8"))
        regions = lbl.get("regions", [])
        if not regions:
            continue

        vw, vh = int(lbl["width"]), int(lbl["height"])
        gt_boxes = regions[0]["boxes"]
        box0 = gt_boxes.get("0") or next(iter(gt_boxes.values()))

        # Track starting from frame 0's bbox (keyframe 0) AND starting from P2 auto-detector
        tr_init = track_watermark_in_video(str(in_path), init_box=box0)
        ev_init = evaluate_tracking_against_gt(tr_init, gt_boxes, vw, vh)

        tr_auto = track_watermark_in_video(str(in_path), init_box=None)
        ev_auto = evaluate_tracking_against_gt(tr_auto, gt_boxes, vw, vh)

        gt_cuts = expected_scene_cuts.get(stem, [])
        pred_cuts = tr_init.get("scene_cuts", [])
        cut_match = (pred_cuts == gt_cuts)

        row = {
            "video": in_path.name,
            "stem": stem,
            "group": lbl.get("group", ""),
            "region_type": regions[0].get("type", "static"),
            "frames": int(lbl.get("frames", 150)),
            "median_iou": ev_init["median_iou"],
            "mean_iou": ev_init["mean_iou"],
            "min_iou": ev_init["min_iou"],
            "max_drift_pct": ev_init["max_drift_pct"],
            "mean_drift_pct": ev_init["mean_drift_pct"],
            "auto_init_median_iou": ev_auto["median_iou"],
            "auto_init_max_drift_pct": ev_auto["max_drift_pct"],
            "lost_rate": tr_init["lost_rate"],
            "lost_frames_count": len(tr_init["lost_frames"]),
            "gt_scene_cuts": gt_cuts,
            "pred_scene_cuts": pred_cuts,
            "scene_cut_exact": cut_match,
            "status": tr_init["status"],
            "needs_review": tr_init["needs_review"],
            "elapsed_sec": tr_init["elapsed_sec"],
        }
        tracker_rows.append(row)
        print(
            f"[track] {stem[:28]:28s} | medIoU={row['median_iou']:.4f} (auto={row['auto_init_median_iou']:.4f}) | "
            f"drift={row['max_drift_pct']:.2f}% | cuts={pred_cuts!s:10s} | lost={row['lost_rate']*100:.1f}% | t={row['elapsed_sec']:.2f}s"
        )

    # Probe lost-track & keyframe retracking on a synthetic disappearing watermark video
    canvas = _make_base_canvas(VW, VH, 0)
    lost_probe_frames = []
    for i in range(90):
        ox, oy = int(i * 1.5), 40
        fr = canvas[oy:oy + VH, ox:ox + VW].copy()
        if i < 50:
            fr = _draw_watermark_patch(fr, [480, 288, 136, 48], "TEMP WM", alpha=0.88)
        lost_probe_frames.append(fr)

    with tempfile.TemporaryDirectory() as tmpdir:
        probe_mp4 = Path(tmpdir) / "lost_probe.mp4"
        encode_frames_lossless(lost_probe_frames, probe_mp4, fps=FPS)
        tr_lost = track_watermark_in_video(str(probe_mp4), init_box=[480, 288, 136, 48])
        retracked = retrack_with_keyframes(
            str(probe_mp4),
            tr_lost,
            keyframes=[{"frame_idx": 50, "x": 480, "y": 288, "w": 136, "h": 48}],
        )
        lost_probe_report = {
            "total_frames": 90,
            "watermark_present_frames": "0..49",
            "watermark_missing_frames": "50..89",
            "detected_lost_frames_count": len(tr_lost["lost_frames"]),
            "first_lost_frame": tr_lost["lost_frames"][0] if tr_lost["lost_frames"] else None,
            "needs_review": tr_lost["needs_review"],
            "auto_erasable": tr_lost["auto_erasable"],
            "review_keyframes": tr_lost["review_keyframes"],
            "retrack_recomputed_ranges": retracked.get("recomputed_ranges", []),
        }
        print(
            f"[track lost-probe] lost_frames={len(tr_lost['lost_frames'])}/40 "
            f"(first={lost_probe_report['first_lost_frame']}) needs_review={tr_lost['needs_review']} "
            f"review_keyframes={tr_lost['review_keyframes']}"
        )

    # Re-run moving_watermark group (+ scene_cut_03) through process_video_natural with tracked frame_boxes
    p0_baseline_ssim = {
        "moving_watermark_01": 0.4832,
        "moving_watermark_02": 0.3694,
        "moving_watermark_03": 0.5699,
        "scene_cut_03": 0.4820,
    }
    moving_inpaint_rows = []
    for name in ("moving_watermark_01", "moving_watermark_02", "moving_watermark_03", "scene_cut_03"):
        in_p = INPUT_DIR / f"{name}.mp4"
        ref_p = REF_DIR / f"{name}.mp4"
        lbl_p = LABELS_DIR / f"{name}.json"
        if not (in_p.exists() and ref_p.exists() and lbl_p.exists()):
            continue
        lbl = json.loads(lbl_p.read_text(encoding="utf-8"))
        vw, vh, fps = int(lbl["width"]), int(lbl["height"]), float(lbl["fps"])
        gt_boxes = lbl["regions"][0]["boxes"]
        box0 = gt_boxes["0"]

        tr = track_watermark_in_video(str(in_p), init_box=box0)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_p = Path(tmpdir) / "cleaned_p3.mp4"
            tracemalloc.start()
            t0 = time.perf_counter()
            stats = fi.process_video_natural(
                str(in_p), str(out_p),
                box0[0], box0[1], box0[2], box0[3],
                vw, vh, fps=fps, duration=5.0,
                frame_boxes=tr["boxes_by_frame"],
            )
            elapsed = round(time.perf_counter() - t0, 2)
            peak_ram = get_peak_rss_mb()
            tracemalloc.stop()

            cleaned_frames = decode_frames(out_p, vw, vh)
            ref_frames = decode_frames(ref_p, vw, vh)

            # Re-encode reference with identical libx264 crf 18 yuv420p settings for outside-mask PSNR
            ref_reencoded = Path(tmpdir) / "ref_crf18.mp4"
            ref_proc = subprocess.Popen(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "rawvideo", "-pix_fmt", "bgr24",
                    "-s", f"{vw}x{vh}", "-r", f"{fps:.3f}", "-i", "pipe:0",
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-pix_fmt", "yuv420p", "-an", str(ref_reencoded),
                ],
                stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for rfr in ref_frames:
                ref_proc.stdin.write(rfr.tobytes())  # type: ignore[union-attr]
            ref_proc.stdin.close()  # type: ignore[union-attr]
            ref_proc.wait(timeout=180)
            ref_crf18_frames = decode_frames(ref_reencoded, vw, vh)

            union_mask = np.zeros((vh, vw), dtype=np.uint8)
            for b in gt_boxes.values():
                bx, by, bw, bh = [int(v) for v in b]
                union_mask[by:by + bh, bx:bx + bw] = 255
            outside_bool = fi.dilate_fill_mask(union_mask, 14) == 0

            ssims = []
            psnrs = []
            for i in range(min(len(cleaned_frames), len(ref_frames), len(ref_crf18_frames))):
                bx, by, bw, bh = [int(v) for v in gt_boxes[str(i)]]
                ssims.append(compute_ssim_patch(cleaned_frames[i][by:by + bh, bx:bx + bw], ref_frames[i][by:by + bh, bx:bx + bw]))
                psnrs.append(compute_psnr_masked(cleaned_frames[i], ref_crf18_frames[i], outside_bool))

            seam, flicker = compute_seam_and_flicker(cleaned_frames, box0[0], box0[1], box0[2], box0[3])
            ssim_p3 = round(float(np.mean(ssims)), 4)
            psnr_p3 = round(float(np.mean(psnrs)), 2)
            ssim_p0 = p0_baseline_ssim.get(name, 0.0)
            moving_inpaint_rows.append({
                "video": in_p.name,
                "stem": name,
                "group": lbl.get("group", ""),
                "ssim_p0_baseline": ssim_p0,
                "ssim_p3_tracked": ssim_p3,
                "ssim_gain": round(ssim_p3 - ssim_p0, 4),
                "psnr_out_mask": psnr_p3,
                "seam": seam,
                "flicker": flicker,
                "temporal_frames": stats["temporal_frames"],
                "total_frames": stats["total_frames"],
                "elapsed_sec": elapsed,
                "peak_ram_mb": peak_ram,
            })
            print(
                f"[inpaint P3 vs P0] {name:22s} | SSIM P0={ssim_p0:.4f} -> P3={ssim_p3:.4f} "
                f"(+{ssim_p3 - ssim_p0:.4f}) | PSNR_out={psnr_p3:.2f} dB | time={elapsed:.2f}s"
            )

    moving_3 = [r for r in moving_inpaint_rows if r["stem"].startswith("moving_watermark_")]
    summary = {
        "total_tracked_videos": len(tracker_rows),
        "overall_median_iou": round(float(np.median([r["median_iou"] for r in tracker_rows])), 4) if tracker_rows else 0.0,
        "overall_auto_init_median_iou": round(float(np.median([r["auto_init_median_iou"] for r in tracker_rows])), 4) if tracker_rows else 0.0,
        "max_drift_pct": max((r["max_drift_pct"] for r in tracker_rows), default=0.0),
        "auto_init_max_drift_pct": max((r["auto_init_max_drift_pct"] for r in tracker_rows), default=0.0),
        "scene_cut_exact_rate": round(sum(1 for r in tracker_rows if r["scene_cut_exact"]) / float(max(1, len(tracker_rows))), 4),
        "mean_track_elapsed_sec": round(float(np.mean([r["elapsed_sec"] for r in tracker_rows])), 3) if tracker_rows else 0.0,
        "moving_watermark_ssim_p0": 0.4742,
        "moving_watermark_ssim_p3": round(float(np.mean([r["ssim_p3_tracked"] for r in moving_3])), 4) if moving_3 else 0.0,
        "moving_watermark_psnr_out_p3": round(float(np.mean([r["psnr_out_mask"] for r in moving_3])), 2) if moving_3 else 0.0,
        "lost_track_probe": lost_probe_report,
        "tracker_videos": tracker_rows,
        "moving_inpaint_comparison": moving_inpaint_rows,
    }
    out_trk = RESULTS_DIR / "tracker_metrics_p3.json"
    out_trk.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"\n[track summary] median_IoU={summary['overall_median_iou']:.4f} (auto={summary['overall_auto_init_median_iou']:.4f}) "
        f"max_drift={summary['max_drift_pct']:.2f}% scene_cut_acc={summary['scene_cut_exact_rate']*100:.1f}% "
        f"moving_SSIM: P0={summary['moving_watermark_ssim_p0']:.4f} -> P3={summary['moving_watermark_ssim_p3']:.4f}"
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generate", action="store_true", help="Generate synthetic benchmark suite (18 video pairs + labels)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing generated benchmark videos")
    ap.add_argument("--run", action="store_true", help="Run baseline benchmark and save results/latest_metrics.json")
    ap.add_argument("--detect", action="store_true", help="Run Phase P2 watermark detector benchmark")
    ap.add_argument("--track", action="store_true", help="Run Phase P3 watermark tracker & dynamic inpaint benchmark")
    ap.add_argument("--hard", action="store_true", help="Run Phase P5.4 synthetic-hard & held-out benchmark")
    args = ap.parse_args()

    if not args.generate and not args.run and not args.detect and not args.track and not args.hard:
        args.generate = True
        args.run = True

    if args.generate:
        generate_benchmark_suite(force=args.force)
    if args.detect:
        run_detector_benchmark()
    if args.track:
        run_tracker_benchmark()
    if args.hard:
        from benchmarks.synthetic_hard_suite import run_all_p5_4_suites
        run_all_p5_4_suites()
    if args.run:
        run_benchmark()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
