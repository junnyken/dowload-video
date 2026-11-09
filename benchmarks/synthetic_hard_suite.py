"""
Phase P5.4 — Synthetic-Hard Benchmark Generator, Contact Sheet Exporter & Held-Out Evaluator
============================================================================================
Generates `benchmarks/synthetic-hard/` from synthetic base canvases using FFmpeg & OpenCV:
  1. `hard_faint_opacity25`: Faint watermark (opacity 25%)
  2. `hard_faint_opacity35`: Faint watermark (opacity 35%)
  3. `hard_crf33_heavy`: Heavy H.264 compression (CRF 33)
  4. `hard_scale_05x`: Downscaled 0.5x (320x180)
  5. `hard_scale_15x`: Upscaled 1.5x (960x540)
  6. `hard_motion_blur_moving`: Moving watermark with motion blur kernel
  7. `hard_vertical_720x1280`: Vertical 9:16 video (720x1280)
  8. `hard_small_wm`: Small watermark badge (64x24)
  9. `hard_large_wm`: Large watermark badge (220x74)
  10. `hard_neg_no_watermark`: Heavy-compressed video with scene text & no watermark (negative test)

Also checks `benchmarks/real-tune/` and `benchmarks/real-heldout/`. Per P5.4 rule:
if `real-tune/` or `real-heldout/` is empty, it does NOT download videos from the internet
and instead reports that the directories are empty awaiting owner-provided videos.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.api import flow_inpaint as fi
from app.services.watermark_detect import detect_watermark_regions
from app.services.watermark_track import evaluate_tracking_against_gt, track_watermark_in_video
from benchmarks.run_benchmark import (
    BENCH_DIR,
    INPUT_DIR,
    LABELS_DIR,
    REF_DIR,
    RESULTS_DIR,
    _box_iou,
    _draw_watermark_patch,
    _make_base_canvas,
    compute_psnr_masked,
    compute_ssim_patch,
    decode_frames,
)

HARD_INPUT_DIR = BENCH_DIR / "synthetic-hard"
HARD_REF_DIR = BENCH_DIR / "synthetic-hard-ref"
HARD_LABELS_DIR = BENCH_DIR / "labels-hard"
REAL_TUNE_DIR = BENCH_DIR / "real-tune"
REAL_HELDOUT_DIR = BENCH_DIR / "real-heldout"
REAL_LABELS_DIR = BENCH_DIR / "labels-real"
CONTACT_SHEETS_DIR = RESULTS_DIR / "contact-sheets"


def ensure_p5_4_directories() -> None:
    for d in (
        HARD_INPUT_DIR,
        HARD_REF_DIR,
        HARD_LABELS_DIR,
        REAL_TUNE_DIR,
        REAL_HELDOUT_DIR,
        REAL_LABELS_DIR,
        CONTACT_SHEETS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
        gk = d / ".gitkeep"
        if not gk.exists():
            gk.write_text("", encoding="utf-8")


def _encode_frames_h264(
    frames: List[np.ndarray], out_path: Path, fps: float = 30.0, crf: int = 18
) -> None:
    vh, vw = frames[0].shape[:2]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", f"{fps:.3f}", "-i", "pipe:0",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "ultrafast", "-pix_fmt", "yuv420p",
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


def _apply_motion_blur_to_patch(
    frame: np.ndarray, box: List[int], text: str, alpha: float = 0.82, ksize: int = 9
) -> np.ndarray:
    drawn = _draw_watermark_patch(frame, box, text, alpha=alpha)
    x, y, w, h = box
    out = frame.copy()
    patch = drawn[y:y + h, x:x + w].copy()
    kernel = np.zeros((ksize, ksize), dtype=np.float32)
    kernel[ksize // 2, :] = 1.0 / float(ksize)
    blurred_patch = cv2.filter2D(patch, -1, kernel)
    out[y:y + h, x:x + w] = blurred_patch
    return out


HARD_SPECS: List[Dict[str, Any]] = [
    {
        "name": "hard_faint_opacity25",
        "group": "Watermark mờ (opacity 25%)",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [476, 286, 140, 50], "alpha": 0.25, "crf": 18,
        "mode": "static", "text": "@faint25",
    },
    {
        "name": "hard_faint_opacity35",
        "group": "Watermark mờ (opacity 35%)",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [24, 286, 140, 50], "alpha": 0.35, "crf": 18,
        "mode": "static", "text": "@faint35",
    },
    {
        "name": "hard_crf34_heavy",
        "group": "Nén H.264 nặng (CRF 34)",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [480, 288, 136, 48], "alpha": 0.85, "crf": 34,
        "mode": "static", "text": "CRF34 HD",
    },
    {
        "name": "hard_scale_05x",
        "group": "Đổi tỷ lệ 0.5x (320x180)",
        "vw": 320, "vh": 180, "fps": 30.0, "n_frames": 90,
        "box": [230, 138, 76, 30], "alpha": 0.88, "crf": 18,
        "mode": "static", "text": "MINI",
    },
    {
        "name": "hard_scale_15x",
        "group": "Đổi tỷ lệ 1.5x (960x540)",
        "vw": 960, "vh": 540, "fps": 30.0, "n_frames": 90,
        "box": [720, 432, 204, 72], "alpha": 0.88, "crf": 18,
        "mode": "static", "text": "SCALE15X",
    },
    {
        "name": "hard_motion_blur_moving",
        "group": "Motion blur trên watermark di chuyển",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [40, 288, 136, 48], "alpha": 0.85, "crf": 18,
        "mode": "moving_blur", "text": "@blurmove",
    },
    {
        "name": "hard_vertical_720x1280",
        "group": "Video dọc 720x1280",
        "vw": 720, "vh": 1280, "fps": 30.0, "n_frames": 60,
        "box": [520, 1180, 168, 60], "alpha": 0.88, "crf": 18,
        "mode": "static", "text": "REELS 9:16",
    },
    {
        "name": "hard_small_wm",
        "group": "Watermark kích thước nhỏ",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [548, 312, 68, 28], "alpha": 0.88, "crf": 18,
        "mode": "static", "text": "TINY",
    },
    {
        "name": "hard_large_wm",
        "group": "Watermark kích thước lớn",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": [390, 260, 224, 76], "alpha": 0.88, "crf": 18,
        "mode": "static", "text": "LARGE LOGO",
    },
    {
        "name": "hard_neg_no_watermark",
        "group": "Không có watermark (nén CRF 33 + phụ đề)",
        "vw": 640, "vh": 360, "fps": 30.0, "n_frames": 90,
        "box": None, "alpha": 0.0, "crf": 33,
        "mode": "negative", "text": "",
    },
]


def generate_synthetic_hard_suite(force: bool = False) -> None:
    ensure_p5_4_directories()
    for idx, spec in enumerate(HARD_SPECS):
        name = spec["name"]
        in_path = HARD_INPUT_DIR / f"{name}.mp4"
        ref_path = HARD_REF_DIR / f"{name}.mp4"
        lbl_path = HARD_LABELS_DIR / f"{name}.json"
        if not force and in_path.exists() and ref_path.exists() and lbl_path.exists():
            continue

        vw, vh = int(spec["vw"]), int(spec["vh"])
        fps = float(spec["fps"])
        n_frames = int(spec["n_frames"])
        crf = int(spec["crf"])
        mode = spec["mode"]
        canvas = _make_base_canvas(vw, vh, variant=idx % 3)

        clean_frames: List[np.ndarray] = []
        dirty_frames: List[np.ndarray] = []
        boxes_by_frame: Dict[str, List[int]] = {}

        for i in range(n_frames):
            ox = int((i * 2) % max(1, vw // 2))
            oy = int((i * 1) % max(1, vh // 2))
            clean = canvas[oy:oy + vh, ox:ox + vw].copy()

            if mode == "negative":
                # Dynamic subtitle sentence changing every 30 frames (not a watermark)
                if 10 <= (i % 30) <= 24:
                    sub_txt = f"Subtitles line {i // 30 + 1}"
                    cv2.putText(
                        clean, sub_txt, (vw // 4, vh - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (250, 250, 250), 2, cv2.LINE_AA,
                    )
                dirty = clean.copy()
            elif mode == "moving_blur":
                bx0, by0, bw, bh = spec["box"]
                bx = int(bx0 + (i / float(max(1, n_frames - 1))) * (vw - bw - bx0 - 40))
                cur_box = [bx, by0, bw, bh]
                boxes_by_frame[str(i)] = cur_box
                dirty = _apply_motion_blur_to_patch(clean, cur_box, spec["text"], alpha=spec["alpha"], ksize=9)
            else:
                cur_box = list(spec["box"])
                boxes_by_frame[str(i)] = cur_box
                dirty = _draw_watermark_patch(clean, cur_box, spec["text"], alpha=spec["alpha"])

            clean_frames.append(clean)
            dirty_frames.append(dirty)

        _encode_frames_h264(dirty_frames, in_path, fps=fps, crf=crf)
        _encode_frames_h264(clean_frames, ref_path, fps=fps, crf=crf)

        lbl_data = {
            "video": f"{name}.mp4",
            "group": spec["group"],
            "width": vw,
            "height": vh,
            "fps": fps,
            "frames": n_frames,
            "crf": crf,
            "regions": (
                [
                    {
                        "id": "wm_0",
                        "type": "moving" if mode == "moving_blur" else "static",
                        "boxes": boxes_by_frame,
                    }
                ]
                if mode != "negative"
                else []
            ),
        }
        lbl_path.write_text(json.dumps(lbl_data, indent=2, ensure_ascii=False), encoding="utf-8")


def export_contact_sheet(
    before_frames: List[np.ndarray],
    after_frames: List[np.ndarray],
    out_path: Path,
    title: str = "",
    n_samples: int = 6,
) -> Optional[str]:
    """Export a 2-row x 6-column before/after contact sheet image."""
    total = min(len(before_frames), len(after_frames))
    if total <= 0:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    indices = [min(total - 1, int(round(i * (total - 1) / max(1, n_samples - 1)))) for i in range(n_samples)]
    thumb_w = 220
    vh, vw = before_frames[0].shape[:2]
    thumb_h = max(90, min(260, int(round(thumb_w * (vh / float(max(1, vw)))))))

    top_row = []
    bot_row = []
    for fidx in indices:
        b_im = cv2.resize(before_frames[fidx], (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        a_im = cv2.resize(after_frames[fidx], (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        cv2.putText(b_im, f"IN f={fidx}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (40, 220, 255), 1, cv2.LINE_AA)
        cv2.putText(a_im, f"OUT f={fidx}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (80, 255, 120), 1, cv2.LINE_AA)
        top_row.append(b_im)
        bot_row.append(a_im)

    grid = np.vstack([np.hstack(top_row), np.hstack(bot_row)])
    if title:
        banner = np.zeros((28, grid.shape[1], 3), dtype=np.uint8)
        cv2.putText(banner, title, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 1, cv2.LINE_AA)
        grid = np.vstack([banner, grid])
    cv2.imwrite(str(out_path), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return str(out_path)


def compute_temporal_flicker_in_mask(frames: List[np.ndarray], boxes_by_frame: Dict[str, List[int]]) -> float:
    """
    P5.4 definition:
      Temporal flicker inside mask = mean(|frame[t] - frame[t-1]|) on pixels inside the mask.
    """
    if len(frames) < 2:
        return 0.0
    vh, vw = frames[0].shape[:2]
    default_b = boxes_by_frame.get("0") or next(iter(boxes_by_frame.values()))
    diffs = []
    for t in range(1, len(frames)):
        b = boxes_by_frame.get(str(t)) or default_b
        x, y, w, h = [int(v) for v in b[:4]]
        x = max(0, min(vw - 2, x))
        y = max(0, min(vh - 2, y))
        w = max(2, min(vw - x, w))
        h = max(2, min(vh - y, h))
        patch_cur = frames[t][y:y + h, x:x + w].astype(np.float32)
        patch_prev = frames[t - 1][y:y + h, x:x + w].astype(np.float32)
        diffs.append(float(np.mean(np.abs(patch_cur - patch_prev))))
    return round(float(np.mean(diffs)) if diffs else 0.0, 3)


def evaluate_suite(
    suite_name: str,
    input_dir: Path,
    ref_dir: Optional[Path],
    labels_dir: Path,
) -> Dict[str, Any]:
    """
    Evaluate Detector, Tracker, and Inpaint across all `.mp4` videos in `input_dir`.
    If `input_dir` has no `.mp4` files, returns `status="empty"` without downloading anything.
    """
    ensure_p5_4_directories()
    mp4_files = sorted(input_dir.glob("*.mp4"))
    if not mp4_files:
        return {
            "suite": suite_name,
            "status": "empty",
            "video_count": 0,
            "message": f"Thư mục {input_dir.name}/ trống — chờ chủ dự án cung cấp video có quyền sử dụng (không tự tải từ Internet).",
            "videos": [],
        }

    rows: List[Dict[str, Any]] = []
    det_tp = 0
    det_fp = 0
    det_fn = 0
    neg_count = 0
    neg_fp_videos = 0

    for in_path in mp4_files:
        stem = in_path.stem
        lbl_path = labels_dir / f"{stem}.json"
        if not lbl_path.exists() and stem.startswith("253K views"):
            lbl_path = LABELS_DIR / "real_agency_01.json"
        lbl = json.loads(lbl_path.read_text(encoding="utf-8")) if lbl_path.exists() else {}
        vw = int(lbl.get("width", 640))
        vh = int(lbl.get("height", 360))
        fps = float(lbl.get("fps", 30.0))
        n_frames = int(lbl.get("frames", 90))
        dur = round(n_frames / max(1.0, fps), 2)
        regions = lbl.get("regions", [])
        group = lbl.get("group", suite_name)

        # 1. Detector
        det = detect_watermark_regions(str(in_path))
        preds = det.get("regions", [])
        det_time = float(det.get("elapsed_sec", 0.0))

        if not regions:
            neg_count += 1
            fp_c = len(preds)
            det_fp += fp_c
            if fp_c > 0:
                neg_fp_videos += 1
            rows.append({
                "video": in_path.name,
                "stem": stem,
                "group": group,
                "has_watermark": False,
                "det_precision": 1.0 if fp_c == 0 else 0.0,
                "det_recall": 1.0,
                "det_iou": None,
                "det_fp": fp_c > 0,
                "det_elapsed_sec": det_time,
                "trk_median_iou": None,
                "trk_max_drift_pct": None,
                "trk_needs_review": False,
                "ssim_in_mask": None,
                "psnr_out_mask_vs_input": None,
                "temporal_flicker_in_mask": None,
                "inpaint_elapsed_sec": None,
                "sec_per_video_sec": None,
            })
            continue

        gt_boxes = regions[0]["boxes"]
        box0 = [int(v) for v in (gt_boxes.get("0") or next(iter(gt_boxes.values())))]
        unique_gt = []
        for b in gt_boxes.values():
            tb = tuple(int(v) for v in b)
            if not any(_box_iou(tb, u) > 0.8 for u in unique_gt):
                unique_gt.append(tb)

        best_iou = 0.0
        tp_v = 0
        fp_v = 0
        for pr in preds:
            pbox = (pr["x"], pr["y"], pr["w"], pr["h"])
            iou_any = max((_box_iou(g, pbox) for g in unique_gt), default=0.0)
            best_iou = max(best_iou, iou_any)
            if iou_any >= 0.50:
                tp_v += 1
            else:
                fp_v += 1

        tp_video = 1 if tp_v >= 1 else 0
        fn_video = 0 if tp_v >= 1 else 1
        det_tp += tp_video
        det_fp += fp_v
        det_fn += fn_video

        # 2. Tracker
        tr = track_watermark_in_video(str(in_path), init_box=box0)
        ev = evaluate_tracking_against_gt(tr, gt_boxes, vw, vh)

        # 3. Inpaint
        with tempfile.TemporaryDirectory() as tmpdir:
            out_p = Path(tmpdir) / f"cleaned_{stem}.mp4"
            t0 = time.perf_counter()
            fi.process_video_natural(
                str(in_path), str(out_p),
                box0[0], box0[1], box0[2], box0[3],
                vw, vh, fps=fps, duration=dur,
                frame_boxes=tr["boxes_by_frame"],
            )
            inpaint_elapsed = round(time.perf_counter() - t0, 2)
            in_frames = decode_frames(in_path, vw, vh)
            out_frames = decode_frames(out_p, vw, vh)

        # Contact sheet (6 frames before/after)
        cs_path = CONTACT_SHEETS_DIR / f"{suite_name}_{stem}_contact.jpg"
        export_contact_sheet(in_frames, out_frames, cs_path, title=f"{suite_name}: {stem}")

        # PSNR outside mask (output vs input)
        union_mask = np.zeros((vh, vw), dtype=np.uint8)
        for b in gt_boxes.values():
            bx, by, bw, bh = [int(v) for v in b]
            union_mask[by:by + bh, bx:bx + bw] = 255
        outside_bool = fi.dilate_fill_mask(union_mask, 16) == 0
        n_common = min(len(in_frames), len(out_frames))
        psnr_list = [
            compute_psnr_masked(out_frames[i], in_frames[i], outside_bool)
            for i in range(n_common)
        ]
        psnr_out_vs_input = round(float(np.mean(psnr_list)), 2) if psnr_list else 0.0

        # SSIM inside mask (only if clean reference with matching resolution/frames exists)
        ssim_in_mask: Optional[float] = None
        if ref_dir is not None:
            ref_path = ref_dir / f"{stem}.mp4"
            if ref_path.exists():
                ref_frames = decode_frames(ref_path, vw, vh)
                if len(ref_frames) >= n_common and n_common > 0:
                    ssim_vals = []
                    for i in range(n_common):
                        bx, by, bw, bh = [int(v) for v in (gt_boxes.get(str(i)) or box0)]
                        ssim_vals.append(
                            compute_ssim_patch(
                                out_frames[i][by:by + bh, bx:bx + bw],
                                ref_frames[i][by:by + bh, bx:bx + bw],
                            )
                        )
                    ssim_in_mask = round(float(np.mean(ssim_vals)), 4)

        flicker_val = compute_temporal_flicker_in_mask(out_frames, gt_boxes)
        sec_per_vsec = round(inpaint_elapsed / max(0.1, dur), 2)

        row = {
            "video": in_path.name,
            "stem": stem,
            "group": group,
            "has_watermark": True,
            "det_precision": round(tp_video / float(max(1, tp_video + fp_v)), 4),
            "det_recall": float(tp_video),
            "det_iou": round(best_iou, 4),
            "det_fp": fp_v > 0,
            "det_elapsed_sec": det_time,
            "trk_median_iou": ev["median_iou"],
            "trk_max_drift_pct": ev["max_drift_pct"],
            "trk_needs_review": bool(tr.get("needs_review")),
            "ssim_in_mask": ssim_in_mask,
            "psnr_out_mask_vs_input": psnr_out_vs_input,
            "temporal_flicker_in_mask": flicker_val,
            "inpaint_elapsed_sec": inpaint_elapsed,
            "sec_per_video_sec": sec_per_vsec,
            "contact_sheet": str(cs_path.relative_to(BENCH_DIR)),
        }
        rows.append(row)
        print(
            f"[{suite_name}] {stem:26s} | detIoU={best_iou:.3f} | trkIoU={ev['median_iou']:.3f} "
            f"| SSIM={ssim_in_mask!s:6s} | PSNR_out={psnr_out_vs_input:.2f}dB "
            f"| flick={flicker_val:.2f} | speed={sec_per_vsec:.2f}x"
        )

    pos_rows = [r for r in rows if r["has_watermark"]]
    ssim_rows = [r["ssim_in_mask"] for r in pos_rows if r["ssim_in_mask"] is not None]
    psnr_rows = [r["psnr_out_mask_vs_input"] for r in pos_rows if r["psnr_out_mask_vs_input"] is not None]
    flick_rows = [r["temporal_flicker_in_mask"] for r in pos_rows if r["temporal_flicker_in_mask"] is not None]
    speed_rows = [r["sec_per_video_sec"] for r in pos_rows if r["sec_per_video_sec"] is not None]

    precision = round(det_tp / float(max(1, det_tp + det_fp)), 4)
    recall = round(det_tp / float(max(1, det_tp + det_fn)), 4)
    neg_fp_rate = round(neg_fp_videos / float(max(1, neg_count)), 4) if neg_count > 0 else 0.0
    trk_med_iou = round(float(np.median([r["trk_median_iou"] for r in pos_rows])), 4) if pos_rows else 0.0
    trk_max_drift = round(max((r["trk_max_drift_pct"] for r in pos_rows), default=0.0), 4)
    trk_needs_review_rate = round(
        sum(1 for r in pos_rows if r["trk_needs_review"]) / float(max(1, len(pos_rows))), 4
    )

    summary = {
        "suite": suite_name,
        "status": "completed",
        "video_count": len(rows),
        "positive_count": len(pos_rows),
        "negative_count": neg_count,
        "detector": {
            "precision": precision,
            "recall": recall,
            "no_watermark_fp_rate": neg_fp_rate,
            "mean_elapsed_sec": round(float(np.mean([r["det_elapsed_sec"] for r in rows])), 3) if rows else 0.0,
        },
        "tracker": {
            "median_iou": trk_med_iou,
            "max_drift_pct": trk_max_drift,
            "needs_review_rate": trk_needs_review_rate,
        },
        "inpaint": {
            "mean_ssim_in_mask": round(float(np.mean(ssim_rows)), 4) if ssim_rows else None,
            "min_ssim_in_mask": round(float(np.min(ssim_rows)), 4) if ssim_rows else None,
            "mean_psnr_out_mask_db": round(float(np.mean(psnr_rows)), 2) if psnr_rows else None,
            "min_psnr_out_mask_db": round(float(np.min(psnr_rows)), 2) if psnr_rows else None,
            "mean_temporal_flicker": round(float(np.mean(flick_rows)), 3) if flick_rows else None,
            "mean_sec_per_video_sec": round(float(np.mean(speed_rows)), 2) if speed_rows else None,
        },
        "videos": rows,
    }
    return summary


def run_all_p5_4_suites() -> Dict[str, Any]:
    generate_synthetic_hard_suite(force=False)
    res_hard = evaluate_suite("synthetic-hard", HARD_INPUT_DIR, HARD_REF_DIR, HARD_LABELS_DIR)
    res_real_tune = evaluate_suite("real-tune", REAL_TUNE_DIR, None, REAL_LABELS_DIR)
    res_real_heldout = evaluate_suite("real-heldout", REAL_HELDOUT_DIR, None, REAL_LABELS_DIR)

    # Also export contact sheets for the existing synthetic videos if not yet exported
    for in_p in sorted(INPUT_DIR.glob("*.mp4"))[:6]:
        cs_file = CONTACT_SHEETS_DIR / f"synthetic_{in_p.stem}_contact.jpg"
        if not cs_file.exists():
            ref_p = REF_DIR / in_p.name
            if ref_p.exists():
                in_f = decode_frames(in_p, 640, 360)
                ref_f = decode_frames(ref_p, 640, 360)
                export_contact_sheet(in_f, ref_f, cs_file, title=f"synthetic: {in_p.stem}")

    combined = {
        "synthetic_hard": res_hard,
        "real_tune": res_real_tune,
        "real_heldout": res_real_heldout,
    }
    out_p = RESULTS_DIR / "p5_4_metrics.json"
    out_p.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    return combined


if __name__ == "__main__":
    run_all_p5_4_suites()
