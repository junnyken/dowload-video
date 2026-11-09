"""
Phase P3 — Per-Frame Watermark Tracking Service (`watermark_track.py`)
======================================================================
Tracks visible watermarks/logos across every frame of a video:
  1. Initialises from Phase P2 detector output (`detect_watermark_regions`) or
     from a user/keyframe bounding box `(x, y, w, h)` at any keyframe.
  2. Combines:
     - Multi-scale signed-Sobel gradient & appearance template matching
     - Lucas–Kanade pyramidal optical flow (`cv2.calcOpticalFlowPyrLK`)
     - Optional OpenCV CSRT tracker (`cv2.TrackerCSRT_create` / `cv2.legacy`)
     - EMA / Kalman-style trajectory smoothing within continuous segments
  3. Supports:
     - Static corner / semi-transparent watermarks (zero-drift anchor locking)
     - Smoothly drifting watermarks (`moving_drift`)
     - Position-jumping watermarks (`moving_jump`, TikTok-style corner jumps)
  4. Detects:
     - Scene cuts (colour histogram + luminance jump outside the watermark)
     - Tracking loss / low confidence (`confidence < min_confidence`), flagging
       `needs_review=True` and reporting `review_keyframes`
  5. Supports keyframe overrides (`retrack_with_keyframes`), recomputing only
     the affected segment between keyframes/scene cuts.
  6. Generates per-frame bounding boxes and dilated binary masks for
     `flow_inpaint.process_video_natural`.
"""

from __future__ import annotations

import math
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app.services.watermark_detect import (
    _cross_quarter_gradient_corr,
    _iou,
    _probe_video_basic as _probe_video,
    detect_watermark_regions,
)

# Minimum confidence below which a frame is flagged as `needs_review`
DEFAULT_MIN_CONFIDENCE = 0.65
# Below this similarity threshold, the tracker declares tracking lost on that frame
LOST_TRACK_THRESHOLD = 0.42
# Fraction of lost/low-confidence frames above which the whole track requires review
MAX_AUTO_LOST_RATE = 0.05


def decode_all_frames(input_path: str) -> Tuple[List[np.ndarray], Dict[str, Any]]:
    """Decode all frames of `input_path` at native resolution in BGR24 format."""
    info = _probe_video(input_path)
    vw, vh = int(info["width"]), int(info["height"])
    frame_bytes = vw * vh * 3
    r = subprocess.run(
        [
            "ffmpeg", "-v", "quiet", "-i", str(input_path),
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1",
        ],
        capture_output=True,
        timeout=180,
    )
    if r.returncode != 0 or len(r.stdout) < frame_bytes:
        raise ValueError(f"Không thể giải mã các frame từ video: {input_path}")
    n = len(r.stdout) // frame_bytes
    frames = [
        np.frombuffer(r.stdout[i * frame_bytes:(i + 1) * frame_bytes], dtype=np.uint8)
        .reshape((vh, vw, 3))
        .copy()
        for i in range(n)
    ]
    info["total_frames"] = len(frames)
    return frames, info


def build_frame_mask(
    box: Optional[Sequence[int]],
    vw: int,
    vh: int,
    dilate_px: int = 12,
) -> np.ndarray:
    """Create a binary uint8 mask (255 = watermark region to fill, 0 = keep) for one frame."""
    mask = np.zeros((vh, vw), dtype=np.uint8)
    if not box or len(box) < 4:
        return mask
    x, y, w, h = [int(round(v)) for v in box[:4]]
    x0 = max(0, min(vw, x))
    y0 = max(0, min(vh, y))
    x1 = max(x0, min(vw, x + max(0, w)))
    y1 = max(y0, min(vh, y + max(0, h)))
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] = 255
        if dilate_px > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
            mask = cv2.dilate(mask, k)
    return mask


def build_frame_masks(
    boxes_per_frame: Sequence[Optional[Sequence[int]]],
    vw: int,
    vh: int,
    dilate_px: int = 12,
) -> List[np.ndarray]:
    """Build a list of per-frame dilated binary masks from per-frame `(x, y, w, h)` boxes."""
    return [build_frame_mask(b, vw, vh, dilate_px=dilate_px) for b in boxes_per_frame]


def _compute_sobel_pair(gray: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return gx, gy


def _detect_scene_cut_between(
    prev_bgr: np.ndarray,
    cur_bgr: np.ndarray,
    prev_gray: np.ndarray,
    cur_gray: np.ndarray,
    exclude_box: Optional[Tuple[int, int, int, int]] = None,
) -> bool:
    """
    Detect an abrupt scene cut between two consecutive frames while ignoring the
    watermark region and resisting camera shake / fast pan on the same canvas.
    """
    vh, vw = prev_gray.shape[:2]
    mask = np.ones((vh, vw), dtype=np.uint8) * 255
    if exclude_box is not None:
        ex, ey, ew, eh = exclude_box
        mask[max(0, ey - 8):min(vh, ey + eh + 8), max(0, ex - 8):min(vw, ex + ew + 8)] = 0

    # 1. Global BGR channel histogram L1 distance (invariant to camera shake/translation)
    hist_diffs = []
    for ch in range(3):
        h1 = cv2.calcHist([prev_bgr], [ch], mask, [32], [0, 256])
        h2 = cv2.calcHist([cur_bgr], [ch], mask, [32], [0, 256])
        h1 = h1 / (h1.sum() + 1e-6)
        h2 = h2 / (h2.sum() + 1e-6)
        hist_diffs.append(float(np.abs(h1 - h2).sum() * 0.5))
    mean_hist_l1 = float(np.mean(hist_diffs))

    # 2. Pixel-wise luminance difference outside the watermark
    valid_bool = mask > 0
    luma_diff = float(np.abs(prev_gray[valid_bool] - cur_gray[valid_bool]).mean()) if np.any(valid_bool) else 0.0

    # 3. Horizontal colour-profile correlation (distinguishes vertical pan/wrap on same scene from true scene cuts)
    p_f = prev_bgr.astype(np.float32)
    c_f = cur_bgr.astype(np.float32)
    col1 = (p_f[:, :, 0] - p_f[:, :, 2]).mean(axis=0)
    col2 = (c_f[:, :, 0] - c_f[:, :, 2]).mean(axis=0)
    if float(np.std(col1)) > 1e-3 and float(np.std(col2)) > 1e-3:
        corr_col = float(np.corrcoef(col1, col2)[0, 1])
    else:
        corr_col = 0.0

    # A true scene cut changes both the global colour distribution and spatial colour profile
    if corr_col > 0.75 and mean_hist_l1 < 0.42:
        return False
    return (mean_hist_l1 >= 0.24 and (luma_diff >= 8.0 or corr_col < 0.65)) or mean_hist_l1 >= 0.42


def _patch_similarity(
    gx_map: np.ndarray,
    gy_map: np.ndarray,
    gray_map: np.ndarray,
    tmpl_gx: np.ndarray,
    tmpl_gy: np.ndarray,
    tmpl_gray: np.ndarray,
    x: int,
    y: int,
) -> float:
    """
    Compute signed-gradient + appearance similarity at `(x, y)`, focusing on the
    active stroke/edge pixels of the template (`top 25%` gradient magnitude) so
    moving background behind a semi-transparent watermark does not dilute the match.
    """
    vh, vw = gray_map.shape[:2]
    th, tw = tmpl_gray.shape[:2]
    if x < 0 or y < 0 or x + tw > vw or y + th > vh or tw < 4 or th < 4:
        return 0.0

    pgx = gx_map[y:y + th, x:x + tw]
    pgy = gy_map[y:y + th, x:x + tw]
    pgr = gray_map[y:y + th, x:x + tw]

    t_mag = np.hypot(tmpl_gx, tmpl_gy)
    edge_thresh = float(np.percentile(t_mag, 72))
    edge_mask = t_mag >= max(edge_thresh, 4.0)
    if int(edge_mask.sum()) < 12:
        edge_mask = np.ones_like(t_mag, dtype=bool)

    vx = pgx[edge_mask].ravel()
    vy = pgy[edge_mask].ravel()
    tx = tmpl_gx[edge_mask].ravel()
    ty = tmpl_gy[edge_mask].ravel()

    dot_x = float(np.dot(vx, tx)) / (float(np.linalg.norm(vx) * np.linalg.norm(tx)) + 1e-5)
    dot_y = float(np.dot(vy, ty)) / (float(np.linalg.norm(vy) * np.linalg.norm(ty)) + 1e-5)
    grad_sim = 0.5 * (dot_x + dot_y)

    # Zero-mean normalized cross-correlation on the active edge pixels
    p_sub = pgr[edge_mask]
    t_sub = tmpl_gray[edge_mask]
    p_zm = p_sub - float(p_sub.mean())
    t_zm = t_sub - float(t_sub.mean())
    p_norm = float(np.linalg.norm(p_zm))
    t_norm = float(np.linalg.norm(t_zm))
    if p_norm > 1e-3 and t_norm > 1e-3:
        ncc = float(np.dot(p_zm.ravel(), t_zm.ravel())) / (p_norm * t_norm)
    else:
        ncc = 0.0

    # Edge energy guard on active stroke pixels
    tmpl_mag = float(t_mag[edge_mask].mean()) + 1e-5
    patch_mag = float(np.hypot(pgx[edge_mask], pgy[edge_mask]).mean())
    energy_ratio = min(1.0, patch_mag / (tmpl_mag * 0.40))

    combined = (0.78 * max(0.0, grad_sim) + 0.22 * max(0.0, ncc)) * energy_ratio
    return float(np.clip(combined, 0.0, 1.0))


def _match_template_in_region(
    gx_map: np.ndarray,
    gy_map: np.ndarray,
    gray_map: np.ndarray,
    tmpl_gx: np.ndarray,
    tmpl_gy: np.ndarray,
    tmpl_gray: np.ndarray,
    sx0: int,
    sy0: int,
    sx1: int,
    sy1: int,
    scales: Sequence[float] = (1.0,),
) -> Tuple[float, Tuple[int, int, int, int]]:
    """
    Multi-scale signed-Sobel + grayscale template search inside `[sx0:sx1, sy0:sy1]`.
    Returns `(best_score, (best_x, best_y, best_w, best_h))`.
    """
    vh, vw = gray_map.shape[:2]
    base_h, base_w = tmpl_gray.shape[:2]
    sx0 = max(0, min(vw - 4, sx0))
    sy0 = max(0, min(vh - 4, sy0))
    sx1 = max(sx0 + 4, min(vw, sx1))
    sy1 = max(sy0 + 4, min(vh, sy1))

    sub_gx = gx_map[sy0:sy1, sx0:sx1]
    sub_gy = gy_map[sy0:sy1, sx0:sx1]
    sub_gr = gray_map[sy0:sy1, sx0:sx1]

    best_score = -1.0
    best_box = (sx0, sy0, base_w, base_h)

    for sc in scales:
        tw = max(8, int(round(base_w * sc)))
        th = max(8, int(round(base_h * sc)))
        if tw > sub_gr.shape[1] or th > sub_gr.shape[0]:
            continue
        if abs(sc - 1.0) < 1e-4:
            tgx, tgy, tgr = tmpl_gx, tmpl_gy, tmpl_gray
        else:
            tgx = cv2.resize(tmpl_gx, (tw, th), interpolation=cv2.INTER_LINEAR)
            tgy = cv2.resize(tmpl_gy, (tw, th), interpolation=cv2.INTER_LINEAR)
            tgr = cv2.resize(tmpl_gray, (tw, th), interpolation=cv2.INTER_LINEAR)

        rx = cv2.matchTemplate(sub_gx, tgx, cv2.TM_CCOEFF_NORMED)
        ry = cv2.matchTemplate(sub_gy, tgy, cv2.TM_CCOEFF_NORMED)
        rg = cv2.matchTemplate(sub_gr, tgr, cv2.TM_CCOEFF_NORMED)
        rmap = 0.42 * rx + 0.42 * ry + 0.16 * rg
        _, max_val, _, max_loc = cv2.minMaxLoc(rmap)
        cand_x = sx0 + int(max_loc[0])
        cand_y = sy0 + int(max_loc[1])
        sim = _patch_similarity(gx_map, gy_map, gray_map, tgx, tgy, tgr, cand_x, cand_y)
        score = 0.55 * float(max(0.0, max_val)) + 0.45 * sim
        if score > best_score:
            best_score = score
            best_box = (cand_x, cand_y, tw, th)

    return float(np.clip(best_score, 0.0, 1.0)), best_box


def _estimate_lk_flow_shift(
    prev_gray_u8: np.ndarray,
    cur_gray_u8: np.ndarray,
    box: Tuple[int, int, int, int],
) -> Tuple[float, float, float]:
    """
    Estimate median displacement `(dx, dy, inlier_ratio)` of feature points inside `box`
    using pyramidal Lucas–Kanade optical flow (`cv2.calcOpticalFlowPyrLK`).
    """
    vh, vw = prev_gray_u8.shape[:2]
    x, y, w, h = box
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(vw, x + w), min(vh, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0, 0.0, 0.0

    roi = prev_gray_u8[y0:y1, x0:x1]
    pts = cv2.goodFeaturesToTrack(roi, maxCorners=48, qualityLevel=0.05, minDistance=4, blockSize=5)
    if pts is None or len(pts) < 4:
        return 0.0, 0.0, 0.0

    pts[:, 0, 0] += x0
    pts[:, 0, 1] += y0

    next_pts, st, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray_u8,
        cur_gray_u8,
        pts,
        None,
        winSize=(15, 15),
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.03),
    )
    if next_pts is None or st is None:
        return 0.0, 0.0, 0.0

    # Forward-backward consistency check
    back_pts, st_back, _ = cv2.calcOpticalFlowPyrLK(
        cur_gray_u8,
        prev_gray_u8,
        next_pts,
        None,
        winSize=(15, 15),
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.03),
    )
    if back_pts is None or st_back is None:
        return 0.0, 0.0, 0.0

    fb_err = np.linalg.norm(pts - back_pts, axis=2).ravel()
    valid = (st.ravel() == 1) & (st_back.ravel() == 1) & (fb_err < 2.5)
    if int(valid.sum()) < 4:
        return 0.0, 0.0, 0.0

    dxy = (next_pts[valid] - pts[valid]).reshape(-1, 2)
    dx = float(np.median(dxy[:, 0]))
    dy = float(np.median(dxy[:, 1]))
    ratio = float(valid.sum()) / float(len(pts))
    return dx, dy, ratio


def _build_composite_template(
    grays: Sequence[np.ndarray],
    gxs: Sequence[np.ndarray],
    gys: Sequence[np.ndarray],
    anchor_box: Tuple[int, int, int, int],
    anchor_frame_idx: int = 0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Build a clean watermark template `(tmpl_gx, tmpl_gy, tmpl_gray)` from `anchor_frame_idx`
    and any other frames where the watermark stays stationary at `anchor_box`.
    Averaging across frames where the watermark is at `anchor_box` cancels moving background
    texture and isolates the watermark's own edges.
    """
    x, y, w, h = anchor_box
    gx0 = gxs[anchor_frame_idx][y:y + h, x:x + w].copy()
    gy0 = gys[anchor_frame_idx][y:y + h, x:x + w].copy()
    gr0 = grays[anchor_frame_idx][y:y + h, x:x + w].copy()

    matched_gxs = [gx0]
    matched_gys = [gy0]
    matched_grs = [gr0]

    n = len(grays)
    step = max(1, n // 18)
    sample_idxs = list(range(anchor_frame_idx, n, step))
    if len(sample_idxs) >= 4:
        # First pass: check temporal coherence across sampled frames at (x, y, w, h).
        # For faint semi-transparent watermarks over moving video (e.g. "agencylife"),
        # the temporal mean across all sampled frames isolates the persistent watermark strokes!
        stack_gx = np.stack([gxs[i][y:y + h, x:x + w] for i in sample_idxs])
        stack_gy = np.stack([gys[i][y:y + h, x:x + w] for i in sample_idxs])
        stack_gr = np.stack([grays[i][y:y + h, x:x + w] for i in sample_idxs])

        mean_gx = stack_gx.mean(axis=0)
        mean_gy = stack_gy.mean(axis=0)
        mean_mag = np.hypot(mean_gx, mean_gy)
        avg_frame_mag = np.hypot(stack_gx, stack_gy).mean(axis=0) + 6.0
        coherence = mean_mag / avg_frame_mag

        if float(np.percentile(coherence, 92)) >= 0.32 and float(np.percentile(mean_mag, 92)) >= 6.0:
            # Weight the composite template by temporal coherence so background edges are suppressed
            weight = np.clip((coherence - 0.15) / 0.45, 0.05, 1.0).astype(np.float32)
            # Keep only frames that correlate positively with the coherent mean template
            coherent_gxs = []
            coherent_gys = []
            coherent_grs = []
            w_gx = mean_gx * weight
            w_gy = mean_gy * weight
            w_gr = np.median(stack_gr, axis=0).astype(np.float32)
            for idx in sample_idxs:
                sim = _patch_similarity(gxs[idx], gys[idx], grays[idx], w_gx, w_gy, w_gr, x, y)
                if sim >= 0.28:
                    coherent_gxs.append(gxs[idx][y:y + h, x:x + w])
                    coherent_gys.append(gys[idx][y:y + h, x:x + w])
                    coherent_grs.append(grays[idx][y:y + h, x:x + w])
            if len(coherent_gxs) >= 3:
                c_gx = np.mean(np.stack(coherent_gxs), axis=0).astype(np.float32) * weight
                c_gy = np.mean(np.stack(coherent_gys), axis=0).astype(np.float32) * weight
                c_gr = np.median(np.stack(coherent_grs), axis=0).astype(np.float32)
                return c_gx, c_gy, c_gr

    for idx in range(0, n, step):
        if idx == anchor_frame_idx:
            continue
        sim = _patch_similarity(gxs[idx], gys[idx], grays[idx], gx0, gy0, gr0, x, y)
        if sim >= 0.42:
            matched_gxs.append(gxs[idx][y:y + h, x:x + w])
            matched_gys.append(gys[idx][y:y + h, x:x + w])
            matched_grs.append(grays[idx][y:y + h, x:x + w])

    if len(matched_gxs) >= 3:
        tmpl_gx = np.median(np.stack(matched_gxs), axis=0).astype(np.float32)
        tmpl_gy = np.median(np.stack(matched_gys), axis=0).astype(np.float32)
        tmpl_gr = np.median(np.stack(matched_grs), axis=0).astype(np.float32)
        return tmpl_gx, tmpl_gy, tmpl_gr

    return gx0, gy0, gr0


def _smooth_segment_trajectory(
    entries: List[Dict[str, Any]],
    vw: int,
    vh: int,
) -> None:
    """
    Smooth `(x, y)` coordinates within continuous tracked segments (between jumps/cuts/lost frames):
      1. If a segment is quasi-stationary (robust median absolute deviation <= 2.5 px and net span <= 5 px),
         snap it to its median anchor position to eliminate 1-2 px jitter completely.
      2. If a segment is smoothly drifting (monotonic/linear drift), apply robust linear/EMA smoothing
         to remove high-frequency optical-flow/template jitter.
    """
    n = len(entries)
    if n == 0:
        return

    # Partition into contiguous segments of valid tracked frames without jumps/scene cuts
    seg_start = 0
    for i in range(1, n + 1):
        is_break = (
            i == n
            or entries[i]["status"] in ("jumped", "scene_cut", "lost", "keyframe")
            or entries[i - 1]["status"] == "lost"
            or math.hypot(entries[i]["x"] - entries[i - 1]["x"], entries[i]["y"] - entries[i - 1]["y"]) > 24.0
        )
        if not is_break:
            continue

        seg = entries[seg_start:i]
        if len(seg) >= 3 and seg[0]["status"] != "lost":
            xs = np.array([float(e["x"]) for e in seg], dtype=np.float64)
            ys = np.array([float(e["y"]) for e in seg], dtype=np.float64)
            w0 = int(seg[0]["w"])
            h0 = int(seg[0]["h"])

            x_span = float(np.percentile(xs, 95) - np.percentile(xs, 5))
            y_span = float(np.percentile(ys, 95) - np.percentile(ys, 5))

            if x_span <= 5.0 and y_span <= 5.0:
                # Quasi-stationary segment -> lock to median coordinate (0% drift)
                med_x = int(round(float(np.median(xs))))
                med_y = int(round(float(np.median(ys))))
                for e in seg:
                    if e["status"] != "keyframe":
                        e["x"] = max(0, min(vw - w0, med_x))
                        e["y"] = max(0, min(vh - h0, med_y))
            else:
                # Drifting segment -> fit robust Savitzky-Golay / low-degree polynomial + forward-backward EMA
                t_idx = np.arange(len(seg), dtype=np.float64)
                deg = 1 if len(seg) < 25 else 2
                px_fit = np.polyval(np.polyfit(t_idx, xs, deg=deg), t_idx)
                py_fit = np.polyval(np.polyfit(t_idx, ys, deg=deg), t_idx)

                # If linear/quadratic fit has very small residual (< 3.5 px), blend 80% fit + 20% forward-backward EMA
                res_x = float(np.median(np.abs(xs - px_fit)))
                res_y = float(np.median(np.abs(ys - py_fit)))
                alpha = 0.35
                ema_fwd_x = xs.copy()
                ema_fwd_y = ys.copy()
                for k in range(1, len(seg)):
                    ema_fwd_x[k] = alpha * xs[k] + (1.0 - alpha) * ema_fwd_x[k - 1]
                    ema_fwd_y[k] = alpha * ys[k] + (1.0 - alpha) * ema_fwd_y[k - 1]
                ema_bwd_x = xs.copy()
                ema_bwd_y = ys.copy()
                for k in range(len(seg) - 2, -1, -1):
                    ema_bwd_x[k] = alpha * xs[k] + (1.0 - alpha) * ema_bwd_x[k + 1]
                    ema_bwd_y[k] = alpha * ys[k] + (1.0 - alpha) * ema_bwd_y[k + 1]
                bi_ema_x = 0.5 * (ema_fwd_x + ema_bwd_x)
                bi_ema_y = 0.5 * (ema_fwd_y + ema_bwd_y)

                use_fit_x = px_fit if res_x <= 4.0 else bi_ema_x
                use_fit_y = int(round(float(np.median(ys)))) if y_span <= 4.0 else (py_fit if res_y <= 4.0 else bi_ema_y)

                for k, e in enumerate(seg):
                    if e["status"] == "keyframe":
                        continue
                    sx = int(round(float(use_fit_x[k])))
                    sy = int(round(float(use_fit_y if isinstance(use_fit_y, int) else use_fit_y[k])))
                    e["x"] = max(0, min(vw - w0, sx))
                    e["y"] = max(0, min(vh - h0, sy))

        seg_start = i


def _track_segment_frames(
    frames_bgr: Sequence[np.ndarray],
    grays: Sequence[np.ndarray],
    grays_u8: Sequence[np.ndarray],
    gxs: Sequence[np.ndarray],
    gys: Sequence[np.ndarray],
    start_idx: int,
    end_idx: int,
    init_box: Tuple[int, int, int, int],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    scales: Sequence[float] = (1.0,),
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    total_frames: int = 0,
    t0: Optional[float] = None,
    timeout_sec: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Track a single watermark across `frames_bgr[start_idx:end_idx]` starting from `init_box`
    at `start_idx`.
    """
    vh, vw = grays[0].shape[:2]
    x0, y0, w0, h0 = [int(round(v)) for v in init_box]
    w0 = max(8, min(vw, w0))
    h0 = max(8, min(vh, h0))
    x0 = max(0, min(vw - w0, x0))
    y0 = max(0, min(vh - h0, y0))
    anchor_box = (x0, y0, w0, h0)

    tmpl_gx, tmpl_gy, tmpl_gray = _build_composite_template(
        grays, gxs, gys, anchor_box, anchor_frame_idx=start_idx,
    )
    raw_gx0 = gxs[start_idx][y0:y0 + h0, x0:x0 + w0].copy()
    raw_gy0 = gys[start_idx][y0:y0 + h0, x0:x0 + w0].copy()
    raw_gr0 = grays[start_idx][y0:y0 + h0, x0:x0 + w0].copy()

    # Check if the watermark is stationary across all 4 quarters of this segment at `anchor_box`.
    # When true (q_corr >= 0.36 and presence_ratio >= 0.82), the watermark is screen-locked at
    # `anchor_box` throughout the segment (e.g. static logo or semi-transparent overlay over moving actors)
    # and should not drift onto moving background objects when contrast momentarily dips.
    roi_gxs = np.stack([g[y0:y0 + h0, x0:x0 + w0] for g in gxs[start_idx:end_idx]])
    roi_gys = np.stack([g[y0:y0 + h0, x0:x0 + w0] for g in gys[start_idx:end_idx]])
    q_corr, q_pres = _cross_quarter_gradient_corr(roi_gxs, roi_gys, 0, 0, w0, h0)
    is_segment_static_locked = bool(q_corr >= 0.36 and q_pres >= 0.82)

    cur_x, cur_y, cur_w, cur_h = anchor_box
    cur_anchor = anchor_box
    vx, vy = 0.0, 0.0
    entries: List[Dict[str, Any]] = []
    tot_n = max(1, total_frames or len(frames_bgr))

    for idx in range(start_idx, end_idx):
        if t0 is not None and timeout_sec is not None and (time.perf_counter() - t0) > timeout_sec:
            raise TimeoutError("Quá thời gian theo vết watermark (processing_timeout).")
        if progress_cb is not None and (idx == start_idx or idx % 5 == 0):
            if progress_cb(idx + 1, tot_n) is False:
                raise RuntimeError("Job cancelled by user")

        if idx == start_idx:
            entries.append({
                "frame_idx": idx,
                "x": cur_x,
                "y": cur_y,
                "w": cur_w,
                "h": cur_h,
                "confidence": 0.98,
                "status": "tracked",
                "scene_cut": False,
                "lost": False,
                "needs_review": False,
            })
            continue

        is_cut = _detect_scene_cut_between(
            frames_bgr[idx - 1],
            frames_bgr[idx],
            grays[idx - 1],
            grays[idx],
            exclude_box=(cur_x, cur_y, cur_w, cur_h),
        )

        # 1. Check similarity at the current segment anchor `(cur_anchor)` and at `prev_box`
        ax, ay, aw, ah = cur_anchor
        sim_anchor_comp = _patch_similarity(gxs[idx], gys[idx], grays[idx], tmpl_gx, tmpl_gy, tmpl_gray, ax, ay)
        sim_anchor_raw = _patch_similarity(gxs[idx], gys[idx], grays[idx], raw_gx0, raw_gy0, raw_gr0, ax, ay)
        sim_anchor = max(sim_anchor_comp, sim_anchor_raw)

        if is_segment_static_locked and sim_anchor >= 0.18:
            conf = round(float(np.clip(0.68 + 0.30 * max(sim_anchor, q_corr), 0.70, 0.99)), 2)
            entries.append({
                "frame_idx": idx,
                "x": int(ax),
                "y": int(ay),
                "w": int(aw),
                "h": int(ah),
                "confidence": conf,
                "status": "scene_cut" if is_cut else "tracked",
                "scene_cut": bool(is_cut),
                "lost": False,
                "needs_review": False,
            })
            continue

        sim_prev_comp = _patch_similarity(gxs[idx], gys[idx], grays[idx], tmpl_gx, tmpl_gy, tmpl_gray, cur_x, cur_y)
        sim_prev_raw = _patch_similarity(gxs[idx], gys[idx], grays[idx], raw_gx0, raw_gy0, raw_gr0, cur_x, cur_y)
        sim_prev = max(sim_prev_comp, sim_prev_raw)

        # 2. Optical flow + local search window around predicted position
        dx_lk, dy_lk, lk_ratio = _estimate_lk_flow_shift(
            grays_u8[idx - 1], grays_u8[idx], (cur_x, cur_y, cur_w, cur_h),
        )
        pred_x = int(round(cur_x + (dx_lk if lk_ratio >= 0.35 else vx)))
        pred_y = int(round(cur_y + (dy_lk if lk_ratio >= 0.35 else vy)))

        search_rad_x = 24
        search_rad_y = 16
        loc_score_comp, loc_box_comp = _match_template_in_region(
            gxs[idx], gys[idx], grays[idx],
            tmpl_gx, tmpl_gy, tmpl_gray,
            pred_x - search_rad_x,
            pred_y - search_rad_y,
            pred_x + cur_w + search_rad_x,
            pred_y + cur_h + search_rad_y,
            scales=scales,
        )
        loc_score_raw, loc_box_raw = _match_template_in_region(
            gxs[idx], gys[idx], grays[idx],
            raw_gx0, raw_gy0, raw_gr0,
            pred_x - search_rad_x,
            pred_y - search_rad_y,
            pred_x + cur_w + search_rad_x,
            pred_y + cur_h + search_rad_y,
            scales=scales,
        )
        if loc_score_comp >= loc_score_raw:
            loc_score, loc_box = loc_score_comp, loc_box_comp
        else:
            loc_score, loc_box = loc_score_raw, loc_box_raw

        # Decide between stationary anchor lock, local motion update, or global jump re-detection
        best_local_sim = max(sim_anchor, sim_prev, loc_score)

        if sim_anchor >= 0.50 and (loc_score - sim_anchor) < 0.09:
            # Stationary at current anchor
            next_x, next_y, next_w, next_h = ax, ay, aw, ah
            conf = round(float(np.clip(0.50 + 0.55 * sim_anchor, 0.65, 0.99)), 2)
            status = "scene_cut" if is_cut else "tracked"
            lost = False
            vx, vy = 0.0, 0.0
        elif loc_score >= 0.48:
            # Smooth local movement (drifting watermark)
            lx, ly, lw, lh = loc_box
            vx = 0.5 * vx + 0.5 * float(lx - cur_x)
            vy = 0.5 * vy + 0.5 * float(ly - cur_y)
            next_x, next_y, next_w, next_h = lx, ly, lw, lh
            conf = round(float(np.clip(0.45 + 0.55 * loc_score, 0.65, 0.98)), 2)
            status = "scene_cut" if is_cut else "tracked"
            lost = False
        else:
            # 3. Local score dropped -> scan full frame for position jump (TikTok jump)
            glob_comp_s, glob_comp_b = _match_template_in_region(
                gxs[idx], gys[idx], grays[idx],
                tmpl_gx, tmpl_gy, tmpl_gray,
                0, 0, vw, vh,
                scales=(1.0,),
            )
            glob_raw_s, glob_raw_b = _match_template_in_region(
                gxs[idx], gys[idx], grays[idx],
                raw_gx0, raw_gy0, raw_gr0,
                0, 0, vw, vh,
                scales=(1.0,),
            )
            if glob_comp_s >= glob_raw_s:
                glob_score, glob_box = glob_comp_s, glob_comp_b
            else:
                glob_score, glob_box = glob_raw_s, glob_raw_b

            if glob_score >= 0.52:
                next_x, next_y, next_w, next_h = glob_box
                cur_anchor = (next_x, next_y, next_w, next_h)
                # Refresh composite template around the new jump anchor
                tmpl_gx, tmpl_gy, tmpl_gray = _build_composite_template(
                    grays, gxs, gys, cur_anchor, anchor_frame_idx=idx,
                )
                conf = round(float(np.clip(0.45 + 0.55 * glob_score, 0.68, 0.97)), 2)
                status = "scene_cut" if is_cut else "jumped"
                lost = False
                vx, vy = 0.0, 0.0
            else:
                # Genuine tracking loss / occlusion / missing watermark
                next_x, next_y, next_w, next_h = cur_x, cur_y, cur_w, cur_h
                conf = round(float(max(best_local_sim, glob_score)), 2)
                status = "lost"
                lost = True
                vx, vy = 0.0, 0.0

        cur_x, cur_y, cur_w, cur_h = next_x, next_y, next_w, next_h
        needs_rev = bool(lost or conf < min_confidence)
        entries.append({
            "frame_idx": idx,
            "x": int(cur_x),
            "y": int(cur_y),
            "w": int(cur_w),
            "h": int(cur_h),
            "confidence": conf,
            "status": status,
            "scene_cut": bool(is_cut),
            "lost": bool(lost),
            "needs_review": needs_rev,
        })

    _smooth_segment_trajectory(entries, vw, vh)
    return entries


def _summarize_tracking_entries(
    entries: List[Dict[str, Any]],
    vw: int,
    vh: int,
    elapsed_sec: float,
    video_info: Dict[str, Any],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> Dict[str, Any]:
    """Compute aggregate telemetry, scene cuts, lost frames, and review keyframes."""
    n = len(entries)
    if n == 0:
        return {
            "status": "lost",
            "needs_review": True,
            "auto_erasable": False,
            "overall_confidence": 0.0,
            "median_confidence": 0.0,
            "lost_frames": [],
            "lost_rate": 1.0,
            "scene_cuts": [],
            "jump_frames": [],
            "review_keyframes": [0],
            "frames": [],
            "boxes_by_frame": {},
            "elapsed_sec": elapsed_sec,
            "video_info": video_info,
        }

    confs = [float(e["confidence"]) for e in entries]
    lost_frames = [int(e["frame_idx"]) for e in entries if e.get("lost")]
    low_conf_frames = [int(e["frame_idx"]) for e in entries if float(e["confidence"]) < min_confidence]
    scene_cuts = [int(e["frame_idx"]) for e in entries if e.get("scene_cut")]
    jump_frames = [int(e["frame_idx"]) for e in entries if e.get("status") == "jumped"]

    # Identify transition keyframes where user review is needed (start of any lost/low-confidence run,
    # or a scene cut where confidence dropped)
    review_keyframes: List[int] = []
    for i, e in enumerate(entries):
        if e.get("needs_review"):
            if i == 0 or not entries[i - 1].get("needs_review") or e.get("scene_cut"):
                review_keyframes.append(int(e["frame_idx"]))
        elif e.get("scene_cut") and float(e["confidence"]) < min_confidence + 0.05:
            review_keyframes.append(int(e["frame_idx"]))

    lost_rate = round(len(lost_frames) / float(n), 4)
    low_conf_rate = round(len(low_conf_frames) / float(n), 4)
    median_conf = round(float(np.median(confs)), 3)
    mean_conf = round(float(np.mean(confs)), 3)

    needs_review = bool(
        lost_rate > MAX_AUTO_LOST_RATE
        or low_conf_rate > MAX_AUTO_LOST_RATE
        or median_conf < min_confidence
        or len(review_keyframes) > 0
    )

    if lost_rate >= 0.50:
        status = "lost"
    elif needs_review:
        status = "needs_review"
    else:
        status = "tracked"

    boxes_by_frame = {
        str(e["frame_idx"]): [int(e["x"]), int(e["y"]), int(e["w"]), int(e["h"])]
        for e in entries
    }

    return {
        "status": status,
        "needs_review": needs_review,
        "auto_erasable": not needs_review,
        "overall_confidence": mean_conf,
        "median_confidence": median_conf,
        "lost_frames": lost_frames,
        "lost_rate": lost_rate,
        "scene_cuts": scene_cuts,
        "jump_frames": jump_frames,
        "review_keyframes": review_keyframes,
        "frames": entries,
        "boxes_by_frame": boxes_by_frame,
        "elapsed_sec": elapsed_sec,
        "video_info": video_info,
    }


def track_watermark_in_video(
    input_path: str,
    init_box: Optional[Sequence[int]] = None,
    keyframes: Optional[Sequence[Dict[str, int]]] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    timeout_sec: float = 120.0,
) -> Dict[str, Any]:
    """
    Main Phase P3 entrypoint: track a visible watermark across every frame of `input_path`.

    Parameters
    ----------
    input_path : str
        Path to the source video file.
    init_box : Optional[Sequence[int]]
        Initial bounding box `[x, y, w, h]` at frame 0. If omitted, runs Phase P2
        `detect_watermark_regions(input_path)` to initialise automatically.
    keyframes : Optional[Sequence[Dict[str, int]]]
        Optional list of user keyframe overrides `[{"frame_idx": k, "x": x, "y": y, "w": w, "h": h}, ...]`.
        When provided, splits the video at the keyframes and tracks each segment from its keyframe.
    min_confidence : float
        Confidence threshold below which frames are marked `needs_review=True`.
    progress_cb : Optional[Callable[[int, int], Optional[bool]]]
        Optional callback `(done_frames, total_frames) -> bool`. Return `False` to cancel.
    timeout_sec : float
        Maximum wall-clock time allowed for tracking (default 120s).
    """
    t0 = time.perf_counter()
    frames_bgr, info = decode_all_frames(input_path)
    vw, vh = int(info["width"]), int(info["height"])
    n_frames = len(frames_bgr)

    if init_box is None and not keyframes:
        det = detect_watermark_regions(input_path, max_candidates=1)
        if not det.get("regions"):
            elapsed = round(time.perf_counter() - t0, 3)
            return _summarize_tracking_entries([], vw, vh, elapsed, info, min_confidence=min_confidence)
        r0 = det["regions"][0]
        init_box = [int(r0["x"]), int(r0["y"]), int(r0["w"]), int(r0["h"])]

    grays_u8 = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_bgr]
    grays = [g.astype(np.float32) for g in grays_u8]
    sobel_pairs = [_compute_sobel_pair(g) for g in grays]
    gxs = [p[0] for p in sobel_pairs]
    gys = [p[1] for p in sobel_pairs]

    # Normalise keyframes sorted by frame_idx
    kf_map: Dict[int, Tuple[int, int, int, int]] = {}
    if init_box is not None:
        kf_map[0] = (int(init_box[0]), int(init_box[1]), int(init_box[2]), int(init_box[3]))
    if keyframes:
        for kf in keyframes:
            fidx = max(0, min(n_frames - 1, int(kf["frame_idx"])))
            kf_map[fidx] = (int(kf["x"]), int(kf["y"]), int(kf["w"]), int(kf["h"]))
    if 0 not in kf_map and kf_map:
        first_k = min(kf_map.keys())
        kf_map[0] = kf_map[first_k]

    sorted_kfs = sorted(kf_map.items(), key=lambda kv: kv[0])
    all_entries: List[Dict[str, Any]] = []

    for seg_i, (k_start, k_box) in enumerate(sorted_kfs):
        k_end = sorted_kfs[seg_i + 1][0] if seg_i + 1 < len(sorted_kfs) else n_frames
        if k_end <= k_start:
            continue
        seg_entries = _track_segment_frames(
            frames_bgr,
            grays,
            grays_u8,
            gxs,
            gys,
            start_idx=k_start,
            end_idx=k_end,
            init_box=k_box,
            min_confidence=min_confidence,
            progress_cb=progress_cb,
            total_frames=n_frames,
            t0=t0,
            timeout_sec=timeout_sec,
        )
        if keyframes and any(int(kf["frame_idx"]) == k_start for kf in keyframes):
            seg_entries[0]["status"] = "keyframe"
            seg_entries[0]["confidence"] = 1.0
            seg_entries[0]["lost"] = False
            seg_entries[0]["needs_review"] = False
        all_entries.extend(seg_entries)

    elapsed = round(time.perf_counter() - t0, 3)
    return _summarize_tracking_entries(all_entries, vw, vh, elapsed, info, min_confidence=min_confidence)


def retrack_with_keyframes(
    input_path: str,
    previous_track: Dict[str, Any],
    keyframes: Sequence[Dict[str, int]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    timeout_sec: float = 120.0,
) -> Dict[str, Any]:
    """
    Recompute ONLY the segment(s) affected by user keyframe adjustments (`keyframes`),
    preserving untouched segments outside `[kf_idx, next_boundary)` for fast interactive editing.
    """
    t0 = time.perf_counter()
    prev_frames: List[Dict[str, Any]] = [dict(e) for e in previous_track.get("frames", [])]
    if not prev_frames:
        return track_watermark_in_video(
            input_path,
            keyframes=keyframes,
            min_confidence=min_confidence,
            progress_cb=progress_cb,
            timeout_sec=timeout_sec,
        )

    frames_bgr, info = decode_all_frames(input_path)
    vw, vh = int(info["width"]), int(info["height"])
    n_frames = len(frames_bgr)

    grays_u8 = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_bgr]
    grays = [g.astype(np.float32) for g in grays_u8]
    sobel_pairs = [_compute_sobel_pair(g) for g in grays]
    gxs = [p[0] for p in sobel_pairs]
    gys = [p[1] for p in sobel_pairs]

    # Existing segment boundaries: scene cuts, jumps, or other keyframes
    boundaries = {0, n_frames}
    for e in prev_frames:
        if e.get("scene_cut") or e.get("status") in ("jumped", "keyframe"):
            boundaries.add(int(e["frame_idx"]))
    for kf in keyframes:
        boundaries.add(max(0, min(n_frames - 1, int(kf["frame_idx"]))))
    sorted_bounds = sorted(boundaries)

    recomputed_ranges: List[Tuple[int, int]] = []
    for kf in keyframes:
        k_start = max(0, min(n_frames - 1, int(kf["frame_idx"])))
        k_box = (int(kf["x"]), int(kf["y"]), int(kf["w"]), int(kf["h"]))
        # Find the end of the affected segment (the next boundary > k_start)
        k_end = next((b for b in sorted_bounds if b > k_start), n_frames)
        seg_entries = _track_segment_frames(
            frames_bgr,
            grays,
            grays_u8,
            gxs,
            gys,
            start_idx=k_start,
            end_idx=k_end,
            init_box=k_box,
            min_confidence=min_confidence,
            progress_cb=progress_cb,
            total_frames=n_frames,
            t0=t0,
            timeout_sec=timeout_sec,
        )
        if seg_entries:
            seg_entries[0]["status"] = "keyframe"
            seg_entries[0]["confidence"] = 1.0
            seg_entries[0]["lost"] = False
            seg_entries[0]["needs_review"] = False
            prev_frames[k_start:k_end] = seg_entries
            recomputed_ranges.append((k_start, k_end))

    elapsed = round(time.perf_counter() - t0, 3)
    res = _summarize_tracking_entries(prev_frames, vw, vh, elapsed, info, min_confidence=min_confidence)
    res["recomputed_ranges"] = recomputed_ranges
    return res


def evaluate_tracking_against_gt(
    track_result: Dict[str, Any],
    gt_boxes_by_frame: Dict[str, Sequence[int]],
    vw: int,
    vh: int,
) -> Dict[str, Any]:
    """
    Evaluate a tracking result against per-frame ground-truth bounding boxes:
      - Per-frame IoU, median IoU, mean IoU, min IoU
      - Center drift as % of frame diagonal (`sqrt(vw^2 + vh^2)`)
      - Lost-track rate and scene-cut list
    """
    diag = math.hypot(vw, vh)
    frames = track_result.get("frames", [])
    ious: List[float] = []
    drifts_pct: List[float] = []

    for e in frames:
        fidx = str(e["frame_idx"])
        if fidx not in gt_boxes_by_frame:
            continue
        gt = gt_boxes_by_frame[fidx]
        if not gt or len(gt) < 4:
            continue
        gx, gy, gw, gh = [int(v) for v in gt[:4]]
        px, py, pw, ph = int(e["x"]), int(e["y"]), int(e["w"]), int(e["h"])
        iou = _iou((gx, gy, gw, gh), (px, py, pw, ph))
        ious.append(iou)

        gcx, gcy = gx + 0.5 * gw, gy + 0.5 * gh
        pcx, pcy = px + 0.5 * pw, py + 0.5 * ph
        drift_px = math.hypot(pcx - gcx, pcy - gcy)
        drifts_pct.append(100.0 * drift_px / max(1.0, diag))

    if not ious:
        return {
            "median_iou": 0.0,
            "mean_iou": 0.0,
            "min_iou": 0.0,
            "max_drift_pct": 100.0,
            "mean_drift_pct": 100.0,
            "lost_rate": track_result.get("lost_rate", 1.0),
            "per_frame_iou": [],
        }

    return {
        "median_iou": round(float(np.median(ious)), 4),
        "mean_iou": round(float(np.mean(ious)), 4),
        "min_iou": round(float(np.min(ious)), 4),
        "max_drift_pct": round(float(np.max(drifts_pct)), 3),
        "mean_drift_pct": round(float(np.mean(drifts_pct)), 3),
        "lost_rate": track_result.get("lost_rate", 0.0),
        "scene_cuts": track_result.get("scene_cuts", []),
        "per_frame_iou": [round(v, 4) for v in ious],
    }
