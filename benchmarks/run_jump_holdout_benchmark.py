#!/usr/bin/env python3
"""
Phase P5.9 Mục 1 — Holdout Benchmark for Jumping & Panning Watermarks
=====================================================================
Generates `benchmarks/synthetic-jump-holdout/` (6 videos) with an independent
scene/badge generator and distinct parameters from `moving_watermark_*`:
  - Camera pan speeds: 0 px/frame, 0.5 px/frame, 3.0 px/frame
  - Jump points: frame 30, frame 60, frame 110, plus a 4-position jump video
  - Stationary logo + panning camera (0 jumps) to guard against static-logo regression
  - Clean reference videos + per-frame ground-truth bbox labels (`labels/<stem>.json`)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = Path(__file__).resolve().parent
HOLDOUT_DIR = BENCH_DIR / "synthetic-jump-holdout"
INPUT_DIR = HOLDOUT_DIR / "watermark-input"
REF_DIR = HOLDOUT_DIR / "watermark-reference"
LABELS_DIR = HOLDOUT_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"

TOOLS_DIR = ROOT_DIR / ".tools"
if TOOLS_DIR.is_dir():
    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")

sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))
sys.path.insert(0, str(BENCH_DIR))

from app.api import flow_inpaint as fi  # noqa: E402
from app.services.watermark_track import track_watermark_in_video  # noqa: E402
from run_benchmark import (  # noqa: E402
    compute_psnr_masked,
    compute_seam_and_flicker,
    compute_ssim_patch,
    decode_frames,
    encode_frames_lossless,
    get_peak_rss_mb,
)

VW, VH = 640, 360
FPS = 30.0
N_FRAMES = 150  # 5.0s @ 30fps


def _make_holdout_canvas(vw: int, vh: int, variant: int) -> np.ndarray:
    """
    Independent wide canvas generator (3x width, 2x height) so 3.0 px/frame * 150 frames
    (450 px total horizontal travel) pans continuously without clamping or wrapping.
    Uses distinct spatial frequencies, palettes, and geometric primitives from `run_benchmark.py`.
    """
    cw, ch = vw * 3, vh * 2
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)

    p = variant % 4
    if p == 0:
        wave = 22.0 * np.sin(xx / 14.0 + 0.7) * np.cos(yy / 10.5) + 14.0 * np.cos((xx - yy) / 19.0)
        b = 60 + (xx / cw) * 95 + wave
        g = 80 + (yy / ch) * 105 - wave * 0.6
        r = 70 + ((cw - xx) / cw) * 110 + wave * 0.8
    elif p == 1:
        wave = 19.0 * np.cos(xx / 9.5) * np.cos(yy / 13.5) + 11.0 * np.sin((xx + 2 * yy) / 23.0)
        b = 90 + ((ch - yy) / ch) * 85 - wave * 0.7
        g = 65 + (xx / cw) * 115 + wave
        r = 55 + (yy / ch) * 120 + wave * 0.5
    elif p == 2:
        wave = 24.0 * np.sin(xx / 11.0) * np.sin(yy / 15.0) + 9.0 * np.cos(yy / 6.5)
        b = 50 + (yy / ch) * 115 + wave * 0.9
        g = 90 + ((cw - xx) / cw) * 90 + wave * 0.4
        r = 80 + (xx / cw) * 95 - wave * 0.8
    else:
        wave = 17.0 * np.cos((xx + yy) / 10.0) + 15.0 * np.sin((xx - yy) / 16.0)
        b = 75 + (xx / cw) * 100 - wave * 0.5
        g = 70 + (yy / ch) * 100 + wave * 0.9
        r = 95 + ((cw - xx) / cw) * 85 + wave * 0.4

    canvas = np.clip(np.dstack([b, g, r]), 0, 255).astype(np.uint8)

    rng = np.random.default_rng(7000 + variant * 37)
    for _ in range(160):
        px = int(rng.integers(24, cw - 24))
        py = int(rng.integers(24, ch - 24))
        sz = int(rng.integers(8, 26))
        col = tuple(int(c) for c in rng.integers(35, 230, size=3))
        pts = np.array(
            [[px, py - sz], [px - sz, py + sz], [px + sz, py + sz]],
            dtype=np.int32,
        )
        cv2.polylines(canvas, [pts], True, col, 2, lineType=cv2.LINE_AA)
        cv2.line(canvas, (px - sz, py), (px + sz, py), col, 1, lineType=cv2.LINE_AA)
    return canvas


def _draw_holdout_watermark(frame: np.ndarray, box: List[int], text: str, alpha: float = 0.82) -> np.ndarray:
    """Independent pill/badge watermark renderer for the holdout suite."""
    x, y, w, h = [int(v) for v in box]
    out = frame.copy()
    roi = out[y : y + h, x : x + w]
    overlay = np.full_like(roi, (25, 32, 45), dtype=np.uint8)
    cv2.rectangle(overlay, (1, 1), (w - 2, h - 2), (240, 210, 90), 2)
    cv2.line(overlay, (6, h - 7), (w - 6, h - 7), (90, 200, 240), 1, lineType=cv2.LINE_AA)
    cv2.putText(
        overlay,
        text,
        (10, int(h * 0.64)),
        cv2.FONT_HERSHEY_DUPLEX,
        0.54,
        (245, 245, 250),
        1,
        cv2.LINE_AA,
    )
    blended = roi.astype(np.float32) * (1.0 - alpha) + overlay.astype(np.float32) * alpha
    out[y : y + h, x : x + w] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


HOLDOUT_SPECS: List[Dict[str, Any]] = [
    {
        "name": "holdout_jump_f30_pan0",
        "desc": "Camera pan 0 px/frame, jump at frame 30 (2 positions)",
        "variant": 0,
        "pan_x_per_frame": 0.0,
        "pan_y_amp": 0.0,
        "alpha": 0.84,
        "text": "HOLD-F30",
        "segments": [
            (0, 30, [468, 284, 144, 50]),
            (30, 150, [28, 26, 144, 50]),
        ],
    },
    {
        "name": "holdout_jump_f60_pan05",
        "desc": "Camera pan 0.5 px/frame, jump at frame 60 (2 positions)",
        "variant": 1,
        "pan_x_per_frame": 0.5,
        "pan_y_amp": 8.0,
        "alpha": 0.82,
        "text": "HOLD-F60",
        "segments": [
            (0, 60, [472, 28, 140, 48]),
            (60, 150, [30, 284, 140, 48]),
        ],
    },
    {
        "name": "holdout_jump_f110_pan3",
        "desc": "Camera pan 3.0 px/frame, late jump at frame 110 (2 positions)",
        "variant": 2,
        "pan_x_per_frame": 3.0,
        "pan_y_amp": 14.0,
        "alpha": 0.85,
        "text": "HOLD-F110",
        "segments": [
            (0, 110, [468, 284, 144, 50]),
            (110, 150, [28, 28, 144, 50]),
        ],
    },
    {
        "name": "holdout_jump4_pan05",
        "desc": "Camera pan 0.5 px/frame, jumps across 4 positions at frames 30, 60, 110",
        "variant": 3,
        "pan_x_per_frame": 0.5,
        "pan_y_amp": 10.0,
        "alpha": 0.83,
        "text": "QUAD-JMP",
        "segments": [
            (0, 30, [472, 286, 140, 48]),
            (30, 60, [28, 26, 140, 48]),
            (60, 110, [472, 26, 140, 48]),
            (110, 150, [28, 286, 140, 48]),
        ],
    },
    {
        "name": "holdout_jump_f60_pan3",
        "desc": "Camera pan 3.0 px/frame, jump at frame 60 (2 positions)",
        "variant": 4,
        "pan_x_per_frame": 3.0,
        "pan_y_amp": 12.0,
        "alpha": 0.84,
        "text": "FAST-F60",
        "segments": [
            (0, 60, [32, 284, 144, 50]),
            (60, 150, [468, 28, 144, 50]),
        ],
    },
    {
        "name": "holdout_static_logo_pan3",
        "desc": "Stationary logo + camera pan 3.0 px/frame (no jump, static-group regression guard)",
        "variant": 5,
        "pan_x_per_frame": 3.0,
        "pan_y_amp": 12.0,
        "alpha": 0.86,
        "text": "STATIC-P3",
        "segments": [
            (0, 150, [472, 284, 140, 48]),
        ],
    },
]


def generate_holdout_suite(force: bool = False) -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    REF_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for spec in HOLDOUT_SPECS:
        name = spec["name"]
        in_p = INPUT_DIR / f"{name}.mp4"
        ref_p = REF_DIR / f"{name}.mp4"
        lbl_p = LABELS_DIR / f"{name}.json"

        if not force and in_p.exists() and ref_p.exists() and lbl_p.exists():
            continue

        canvas = _make_holdout_canvas(VW, VH, spec["variant"])
        cw, ch = canvas.shape[1], canvas.shape[0]
        pan_vx = float(spec["pan_x_per_frame"])
        pan_ay = float(spec["pan_y_amp"])

        clean_frames: List[np.ndarray] = []
        dirty_frames: List[np.ndarray] = []
        boxes_by_frame: Dict[str, List[int]] = {}

        for i in range(N_FRAMES):
            ox = int(round(30 + i * pan_vx))
            oy = int(round(50 + (pan_ay * math.sin(i / 14.0) if pan_ay > 0 else 0.0)))
            ox = max(0, min(cw - VW - 1, ox))
            oy = max(0, min(ch - VH - 1, oy))

            fr = canvas[oy : oy + VH, ox : ox + VW].copy()
            clean_frames.append(fr)

            cur_box = spec["segments"][-1][2]
            for s_start, s_end, s_box in spec["segments"]:
                if s_start <= i < s_end:
                    cur_box = s_box
                    break

            boxes_by_frame[str(i)] = list(cur_box)
            dirty_frames.append(_draw_holdout_watermark(fr, cur_box, spec["text"], alpha=float(spec["alpha"])))

        encode_frames_lossless(clean_frames, ref_p, fps=FPS)
        encode_frames_lossless(dirty_frames, in_p, fps=FPS)

        is_jumping = len(spec["segments"]) > 1
        jump_frames = [seg[0] for seg in spec["segments"][1:]]
        lbl_data = {
            "video": f"{name}.mp4",
            "stem": name,
            "description": spec["desc"],
            "width": VW,
            "height": VH,
            "fps": FPS,
            "frames": N_FRAMES,
            "pan_x_per_frame": pan_vx,
            "jump_frames": jump_frames,
            "has_watermark": True,
            "regions": [
                {
                    "id": 0,
                    "type": "moving" if is_jumping else "static",
                    "alpha": spec["alpha"],
                    "text": spec["text"],
                    "boxes": boxes_by_frame,
                }
            ],
        }
        lbl_p.write_text(json.dumps(lbl_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[holdout-gen] Generated {name}.mp4 (pan={pan_vx} px/f, jumps={jump_frames})")


def run_holdout_evaluation(save_as_baseline: bool = False) -> Dict[str, Any]:
    generate_holdout_suite(force=False)
    git_head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=str(ROOT_DIR),
    ).stdout.strip()

    rows = []
    for spec in HOLDOUT_SPECS:
        name = spec["name"]
        in_p = INPUT_DIR / f"{name}.mp4"
        ref_p = REF_DIR / f"{name}.mp4"
        lbl_p = LABELS_DIR / f"{name}.json"

        lbl = json.loads(lbl_p.read_text(encoding="utf-8"))
        vw, vh, fps = int(lbl["width"]), int(lbl["height"]), float(lbl["fps"])
        gt_boxes = lbl["regions"][0]["boxes"]
        box0 = gt_boxes["0"]
        is_moving = lbl["regions"][0]["type"] == "moving"

        # Use tracker boxes for jumping watermarks (same pipeline as P3/P5 benchmark)
        if is_moving:
            tr = track_watermark_in_video(str(in_p), init_box=box0)
            frame_boxes = tr["boxes_by_frame"]
            tracker_median_iou = None
        else:
            frame_boxes = None
            tracker_median_iou = 1.0

        with tempfile.TemporaryDirectory() as tmpdir:
            out_p = Path(tmpdir) / f"cleaned_{name}.mp4"
            tracemalloc.start()
            t0 = time.perf_counter()
            stats = fi.process_video_natural(
                str(in_p),
                str(out_p),
                box0[0], box0[1], box0[2], box0[3],
                vw, vh, fps=fps, duration=5.0,
                frame_boxes=frame_boxes,
            )
            elapsed = round(time.perf_counter() - t0, 2)
            peak_ram = get_peak_rss_mb()
            tracemalloc.stop()

            cleaned_frames = decode_frames(out_p, vw, vh)
            ref_frames = decode_frames(ref_p, vw, vh)

            # Re-encode reference with libx264 crf 18 yuv420p for outside-mask PSNR
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
                union_mask[by : by + bh, bx : bx + bw] = 255
            outside_bool = fi.dilate_fill_mask(union_mask, 14) == 0

            ssims = []
            psnrs = []
            for i in range(min(len(cleaned_frames), len(ref_frames), len(ref_crf18_frames))):
                bx, by, bw, bh = [int(v) for v in gt_boxes[str(i)]]
                ssims.append(
                    compute_ssim_patch(
                        cleaned_frames[i][by : by + bh, bx : bx + bw],
                        ref_frames[i][by : by + bh, bx : bx + bw],
                    )
                )
                psnrs.append(compute_psnr_masked(cleaned_frames[i], ref_crf18_frames[i], outside_bool))

            seam, flicker = compute_seam_and_flicker(cleaned_frames, box0[0], box0[1], box0[2], box0[3])
            mean_ssim = round(float(np.mean(ssims)), 4)
            min_ssim = round(float(np.min(ssims)), 4)
            mean_psnr = round(float(np.mean(psnrs)), 2)

            row = {
                "video": f"{name}.mp4",
                "stem": name,
                "description": spec["desc"],
                "pan_x_per_frame": spec["pan_x_per_frame"],
                "jump_frames": lbl["jump_frames"],
                "region_type": lbl["regions"][0]["type"],
                "ssim_in_mask": mean_ssim,
                "min_ssim_in_mask": min_ssim,
                "psnr_outside_db": mean_psnr,
                "seam_gradient_ratio": seam,
                "flicker_ratio": flicker,
                "elapsed_sec": elapsed,
                "peak_ram_mb": peak_ram,
                "mode": stats.get("mode"),
                "temporal_frames": stats.get("temporal_frames"),
            }
            rows.append(row)
            print(
                f"[holdout] {name:26s} | SSIM={mean_ssim:.4f} (min={min_ssim:.4f}) | "
                f"PSNR_out={mean_psnr:.2f}dB | seam={seam:.2f} | flicker={flicker:.2f} | t={elapsed:.2f}s"
            )

    summary = {
        "suite": "synthetic-jump-holdout",
        "git_commit": git_head,
        "video_count": len(rows),
        "mean_ssim_in_mask": round(float(np.mean([r["ssim_in_mask"] for r in rows])), 4),
        "mean_ssim_jumping_only": round(
            float(np.mean([r["ssim_in_mask"] for r in rows if r["region_type"] == "moving"])), 4
        ),
        "static_guard_ssim": next((r["ssim_in_mask"] for r in rows if r["region_type"] == "static"), None),
        "mean_psnr_outside_db": round(float(np.mean([r["psnr_outside_db"] for r in rows])), 2),
        "mean_flicker_ratio": round(float(np.mean([r["flicker_ratio"] for r in rows])), 2),
        "mean_elapsed_sec": round(float(np.mean([r["elapsed_sec"] for r in rows])), 2),
        "videos": rows,
    }

    if save_as_baseline:
        base_path = RESULTS_DIR / "p5_9_jump_holdout_baseline_ced4bcc.json"
        base_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[holdout] Saved baseline to {base_path}")

    latest_path = RESULTS_DIR / "p5_9_jump_holdout_metrics.json"
    latest_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"[holdout summary] mean_ssim={summary['mean_ssim_in_mask']:.4f} | "
        f"jumping_only={summary['mean_ssim_jumping_only']:.4f} | "
        f"static_guard={summary['static_guard_ssim']:.4f} | mean_time={summary['mean_elapsed_sec']:.2f}s"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--regen", action="store_true")
    args = parser.parse_args()
    if args.regen:
        generate_holdout_suite(force=True)
    run_holdout_evaluation(save_as_baseline=args.save_baseline)
