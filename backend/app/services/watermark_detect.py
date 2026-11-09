"""
Video Watermark / Visible-Logo Auto-Detection (Phase P2)
========================================================
Samples 12–24 frames evenly across a video (avoiding scene-cut boundary frames)
and combines 4 signals to detect up to 3 candidate watermark regions:
  1. Temporal persistence on moving background (row-motion-gated + opaque/semi-transparent dual gate).
  2. Semi-transparency & high-frequency edge consistency (Sobel coherence + half-split cosine alignment).
  3. Spatial prior (corners, top/bottom margins, and multi-window jump / drift trajectories).
  4. Connected components + morphology + negative-sample suppression (rejects blinking/changing
     speech subtitles, temporary centered chapter captions, and scene-locked signage).

SCOPE: visible on-frame logos/watermarks only.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def _probe_video_basic(input_path: str) -> Dict[str, Any]:
    cap = cv2.VideoCapture(input_path)
    if cap.isOpened():
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        if vw > 0 and vh > 0:
            dur = round(n_frames / max(fps, 1.0), 2) if n_frames > 0 else 0.0
            return {"width": vw, "height": vh, "fps": round(fps, 2), "frames": n_frames, "duration": dur}
    return {"width": 0, "height": 0, "fps": 30.0, "frames": 0, "duration": 0.0}


def sample_video_frames(
    input_path: str,
    num_samples: int = 24,
) -> Tuple[List[np.ndarray], List[int], List[int], Dict[str, Any]]:
    """
    Sample `num_samples` (12–24) frames uniformly across the video.
    Detects scene cuts via frame-to-frame luminance jump and returns:
      (frames_bgr, frame_indices, scene_cut_positions, video_info)
    """
    num_samples = max(12, min(24, int(num_samples)))
    info = _probe_video_basic(input_path)
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise ValueError(f"Không mở được video: {input_path}")

    n_total = int(info.get("frames") or 0)
    frames: List[np.ndarray] = []
    indices: List[int] = []

    if n_total >= num_samples:
        raw_idxs = np.linspace(0, max(0, n_total - 2), num_samples, dtype=int).tolist()
        seen = set()
        for idx in raw_idxs:
            if idx in seen:
                continue
            seen.add(idx)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, fr = cap.read()
            if ok and fr is not None:
                frames.append(fr)
                indices.append(int(idx))
    else:
        idx = 0
        while True:
            ok, fr = cap.read()
            if not ok or fr is None:
                break
            frames.append(fr)
            indices.append(idx)
            idx += 1
        if len(frames) > num_samples:
            sel = np.linspace(0, len(frames) - 1, num_samples, dtype=int)
            frames = [frames[i] for i in sel]
            indices = [indices[i] for i in sel]

    cap.release()
    if not frames:
        raise ValueError("Không trích xuất được frame nào từ video.")

    vh, vw = frames[0].shape[:2]
    info["width"] = vw
    info["height"] = vh
    if not info.get("frames"):
        info["frames"] = len(frames)

    grays_small = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.float32) for f in frames]
    diffs = [float(np.mean(np.abs(grays_small[i] - grays_small[i - 1]))) for i in range(1, len(grays_small))]
    med_diff = float(np.median(diffs)) if diffs else 0.0
    cut_thresh = max(26.0, med_diff * 3.0)
    scene_cuts = [i for i, d in enumerate(diffs, start=1) if d > cut_thresh]

    return frames, indices, scene_cuts, info


def _spatial_prior_map(vw: int, vh: int) -> np.ndarray:
    """
    Signal 3: Spatial prior favoring the 4 corners and top/bottom watermark bands,
    while down-weighting the dead-center region where scene objects / chapter titles appear.
    """
    yy, xx = np.mgrid[0:vh, 0:vw].astype(np.float32)
    ny = yy / max(1.0, float(vh - 1))
    nx = xx / max(1.0, float(vw - 1))

    vert_prior = np.where(
        ny < 0.24,
        1.15,
        np.where(ny > 0.62, 1.15, 0.45),
    ).astype(np.float32)

    in_corner_x = (nx < 0.30) | (nx > 0.70)
    in_corner_y = (ny < 0.26) | (ny > 0.68)
    prior = vert_prior.copy()
    prior[in_corner_x & in_corner_y] = 1.30
    return prior


def _compute_row_motion_gate(t_std: np.ndarray) -> np.ndarray:
    """
    Suppress full-width static letterbox / header bars (where the entire horizontal row has near-zero
    temporal std) AND their horizontal border lines, while preserving 1.0 weight on rows containing
    moving video.
    """
    vh, vw = t_std.shape[:2]
    if float(np.percentile(t_std, 75)) <= 3.0:
        return np.ones((vh, vw), dtype=np.float32)

    row_p75 = np.percentile(t_std, 75, axis=1).astype(np.float32)
    rad = max(6, int(round(vh * 0.025)))
    row_min = np.array([
        float(np.min(row_p75[max(0, r - rad):min(vh, r + rad + 1)]))
        for r in range(vh)
    ], dtype=np.float32)
    row_gate = np.clip((row_min - 2.5) / 5.0, 0.0, 1.0)
    return np.broadcast_to(row_gate[:, None], (vh, vw)).copy()


def _build_static_persistence_map(
    gxs: np.ndarray,
    gys: np.ndarray,
    t_std: np.ndarray,
    row_gate: np.ndarray,
    prior: np.ndarray,
) -> np.ndarray:
    """
    Combine Signal 1 (motion gate), Signal 2 (signed gradient coherence & half-split alignment),
    and Signal 3 (spatial prior) into a 2D static persistence score map.
    """
    n = gxs.shape[0]
    mean_gx = gxs.mean(axis=0)
    mean_gy = gys.mean(axis=0)
    mean_mag = np.hypot(mean_gx, mean_gy)
    avg_mag = np.hypot(gxs, gys).mean(axis=0) + 3.0
    coherence = mean_mag / avg_mag

    half = max(1, n // 2)
    h1_gx, h1_gy = gxs[:half].mean(axis=0), gys[:half].mean(axis=0)
    h2_gx, h2_gy = gxs[half:].mean(axis=0), gys[half:].mean(axis=0)
    h1_norm = np.hypot(h1_gx, h1_gy) + 2.0
    h2_norm = np.hypot(h2_gx, h2_gy) + 2.0
    cos12 = np.maximum(0.0, (h1_gx * h2_gx + h1_gy * h2_gy) / (h1_norm * h2_norm))

    local_std = cv2.GaussianBlur(t_std, (25, 25), 0)
    # Branch 1: Opaque / high-contrast watermark (crisp persistent edges >= 24)
    opaque_gate = np.clip((mean_mag - 22.0) / 18.0, 0.0, 1.0)
    # Branch 2: Semi-transparent watermark over moving subject (local_std >= 7 AND persistent edges)
    semi_gate = np.clip((local_std - 6.5) / 7.5, 0.0, 1.0) * np.clip((mean_mag - 5.0) / 7.0, 0.0, 1.0)

    combined_gate = np.maximum(opaque_gate, semi_gate) * row_gate
    return mean_mag * coherence * (cos12 ** 2) * combined_gate * prior


def _cross_quarter_gradient_corr(
    gxs: np.ndarray,
    gys: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
) -> Tuple[float, float]:
    """
    Compute cross-quarter signed gradient template correlation and frame presence ratio
    inside candidate box (x, y, w, h).
    """
    n = gxs.shape[0]
    roi_gx = gxs[:, y:y + h, x:x + w]
    roi_gy = gys[:, y:y + h, x:x + w]
    mean_gx = roi_gx.mean(axis=0)
    mean_gy = roi_gy.mean(axis=0)
    tmpl_norm = float(np.sqrt(np.sum(mean_gx ** 2 + mean_gy ** 2))) + 1e-5

    frame_align = []
    for i in range(n):
        fgx, fgy = roi_gx[i], roi_gy[i]
        fnorm = float(np.sqrt(np.sum(fgx ** 2 + fgy ** 2))) + 1e-5
        cos_i = float(np.sum(fgx * mean_gx + fgy * mean_gy)) / (fnorm * tmpl_norm)
        frame_align.append(cos_i)

    presence_ratio = float(np.mean([1.0 if a > 0.25 else 0.0 for a in frame_align]))

    q_size = max(2, n // 4)
    quarters = []
    for q in range(4):
        s = q * q_size
        e = n if q == 3 else min(n, (q + 1) * q_size)
        if e > s:
            q_gx = roi_gx[s:e].mean(axis=0)
            q_gy = roi_gy[s:e].mean(axis=0)
            quarters.append((q_gx, q_gy))

    corrs = []
    for i in range(len(quarters)):
        for j in range(i + 1, len(quarters)):
            gx1, gy1 = quarters[i]
            gx2, gy2 = quarters[j]
            n1 = float(np.sqrt(np.sum(gx1 ** 2 + gy1 ** 2))) + 1e-5
            n2 = float(np.sqrt(np.sum(gx2 ** 2 + gy2 ** 2))) + 1e-5
            c = float(np.sum(gx1 * gx2 + gy1 * gy2)) / (n1 * n2)
            corrs.append(c)

    min_pair_corr = float(np.min(corrs)) if corrs else 0.0
    mean_pair_corr = float(np.mean(corrs)) if corrs else 0.0
    return 0.5 * (min_pair_corr + mean_pair_corr), presence_ratio


def _refine_box_tight(
    energy_map: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    vw: int,
    vh: int,
) -> Tuple[int, int, int, int]:
    """Refine a candidate bounding box tightly around its active high-persistence edge pixels."""
    sub = energy_map[y:y + h, x:x + w]
    if sub.size == 0:
        return x, y, w, h
    thr = max(float(np.percentile(sub, 55)), float(np.percentile(sub, 95)) * 0.22)
    ys, xs = np.where(sub >= thr)
    if len(xs) < 10:
        return x, y, w, h
    x0 = max(0, x + int(np.percentile(xs, 1)) - 3)
    y0 = max(0, y + int(np.percentile(ys, 1)) - 3)
    x1 = min(vw, x + int(np.percentile(xs, 99)) + 4)
    y1 = min(vh, y + int(np.percentile(ys, 99)) + 4)
    if (x1 - x0) >= 24 and (y1 - y0) >= 14:
        return x0, y0, x1 - x0, y1 - y0
    return x, y, w, h


def _extract_candidates_from_score_map(
    score_map: np.ndarray,
    vw: int,
    vh: int,
    min_area_ratio: float = 0.0012,
    max_area_ratio: float = 0.18,
) -> List[Tuple[int, int, int, int, float]]:
    """
    Signal 4: Horizontal morphological closing + connected components + area/aspect-ratio filtering.
    Returns list of (x, y, w, h, raw_score).
    """
    p995 = float(np.percentile(score_map, 99.5))
    if p995 <= 1.8:
        return []

    p984 = float(np.percentile(score_map, 98.4))
    thresh = max(1.8, p984, p995 * 0.20)
    binary = (score_map >= thresh).astype(np.uint8) * 255

    # Horizontal closing kernel bridges adjacent watermark letters without vertically merging clothing/collars
    kw = max(25, int(round(vw * 0.062)))
    kh = max(7, int(round(vh * 0.015)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    frame_area = float(vw * vh)
    candidates: List[Tuple[int, int, int, int, float]] = []

    for lbl in range(1, num_labels):
        cx = int(stats[lbl, cv2.CC_STAT_LEFT])
        cy = int(stats[lbl, cv2.CC_STAT_TOP])
        cw = int(stats[lbl, cv2.CC_STAT_WIDTH])
        ch = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        box_area = float(cw * ch)

        if box_area < frame_area * min_area_ratio or box_area > frame_area * max_area_ratio:
            continue
        aspect = cw / max(1.0, float(ch))
        if aspect < 1.15 or aspect > 11.0:
            continue
        if cw < 32 or ch < 16:
            continue
        if cw > vw * 0.78:
            continue

        rx, ry, rw, rh = _refine_box_tight(score_map, cx, cy, cw, ch, vw, vh)
        r_area = float(rw * rh)
        if r_area < frame_area * min_area_ratio or r_area > frame_area * max_area_ratio:
            continue
        r_aspect = rw / max(1.0, float(rh))
        if r_aspect < 1.15 or r_aspect > 11.0:
            continue

        patch_score = score_map[ry:ry + rh, rx:rx + rw]
        raw_val = float(np.percentile(patch_score, 90)) if patch_score.size else 0.0
        candidates.append((rx, ry, rw, rh, raw_val))

    candidates.sort(key=lambda item: item[4], reverse=True)
    return candidates


def _iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = boxA
    bx, by, bw, bh = boxB
    ix0 = max(ax, bx)
    iy0 = max(ay, by)
    ix1 = min(ax + aw, bx + bw)
    iy1 = min(ay + ah, by + bh)
    iw = max(0, ix1 - ix0)
    ih = max(0, iy1 - iy0)
    inter = float(iw * ih)
    union = float(aw * ah + bw * bh) - inter
    return inter / union if union > 0 else 0.0


def _signed_gradient_template_match(
    search_gx: np.ndarray,
    search_gy: np.ndarray,
    tmpl_gx: np.ndarray,
    tmpl_gy: np.ndarray,
) -> Tuple[float, Tuple[int, int]]:
    """
    Match a signed gradient template (tmpl_gx, tmpl_gy) over (search_gx, search_gy)
    using normalized cross-correlation on both X and Y gradient channels.
    """
    th, tw = tmpl_gx.shape[:2]
    sh, sw = search_gx.shape[:2]
    if th < 12 or tw < 24 or sh < th or sw < tw:
        return 0.0, (0, 0)
    res_x = cv2.matchTemplate(search_gx.astype(np.float32), tmpl_gx.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    res_y = cv2.matchTemplate(search_gy.astype(np.float32), tmpl_gy.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    res = 0.5 * (res_x + res_y)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return float(max_val), (int(max_loc[0]), int(max_loc[1]))


def _detect_jumping_or_drifting_watermark(
    frames: List[np.ndarray],
    gxs: np.ndarray,
    gys: np.ndarray,
    grays: np.ndarray,
    vw: int,
    vh: int,
    row_gate: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    Detect watermarks that either:
      (a) jump between 2 or 3 corner/margin positions across temporal segments (TikTok-style jump), or
      (b) drift smoothly along the top/bottom margin at a velocity distinct from background camera pan.
    Returns candidate region dicts anchored at frame 0's position (with `kind="moving"`).
    """
    n = len(frames)
    if n < 9:
        return []

    prior = _spatial_prior_map(vw, vh)

    # ── Case A: Jumping watermark (stationary during segment 0, then jumps & stays stationary in segment 1) ──
    for n_segs in (2, 3):
        seg_len = n // n_segs
        if seg_len < 4:
            continue
        score0 = _build_static_persistence_map(
            gxs[:seg_len], gys[:seg_len], grays[:seg_len].std(axis=0), row_gate, prior,
        )
        score1 = _build_static_persistence_map(
            gxs[seg_len:2 * seg_len], gys[seg_len:2 * seg_len],
            grays[seg_len:2 * seg_len].std(axis=0), row_gate, prior,
        )
        w0_gx = gxs[:seg_len].mean(axis=0)
        w0_gy = gys[:seg_len].mean(axis=0)
        s1_gx = gxs[seg_len:2 * seg_len].mean(axis=0)
        s1_gy = gys[seg_len:2 * seg_len].mean(axis=0)

        cands0 = _extract_candidates_from_score_map(score0, vw, vh)
        for (x, y, w, h, raw_s) in cands0[:3]:
            if raw_s < 8.0:
                continue
            cy_norm = (y + 0.5 * h) / max(1.0, float(vh))
            if 0.28 <= cy_norm <= 0.65:
                continue

            tmpl_gx = w0_gx[y:y + h, x:x + w]
            tmpl_gy = w0_gy[y:y + h, x:x + w]
            mval, (jx, jy) = _signed_gradient_template_match(s1_gx, s1_gy, tmpl_gx, tmpl_gy)
            dist = math.hypot(jx - x, jy - y)
            j_cy_norm = (jy + 0.5 * h) / max(1.0, float(vh))
            if mval < 0.65 or dist <= max(w, h) * 0.8 or (0.28 <= j_cy_norm <= 0.65):
                continue

            # Crucial verification: the watermark must be genuinely stationary across all sub-quarters
            # of segment 0 at (x, y) AND across all sub-quarters of segment 1 at (jx, jy)
            s0_corr, s0_pres = _cross_quarter_gradient_corr(gxs[:seg_len], gys[:seg_len], x, y, w, h)
            s1_corr, s1_pres = _cross_quarter_gradient_corr(
                gxs[seg_len:2 * seg_len], gys[seg_len:2 * seg_len],
                jx, jy, min(w, vw - jx), min(h, vh - jy),
            )
            target_patch = score1[jy:min(vh, jy + h), jx:min(vw, jx + w)]
            target_p90 = float(np.percentile(target_patch, 90)) if target_patch.size else 0.0
            if target_p90 >= 8.0 and s0_corr >= 0.50 and s0_pres >= 0.85 and s1_corr >= 0.50 and s1_pres >= 0.85:
                conf = round(min(0.95, 0.72 + 0.22 * mval), 2)
                return [{
                    "x": int(x),
                    "y": int(y),
                    "w": int(w),
                    "h": int(h),
                    "confidence": conf,
                    "kind": "moving",
                    "needs_review": conf < 0.75,
                    "jump_target": {"x": jx, "y": jy, "w": int(w), "h": int(h)},
                }]

    # ── Case B: Smoothly drifting watermark badge in top/bottom margin (`moving_drift`) ──
    ch0, ch1 = int(vh * 0.25), int(vh * 0.65)
    cw0, cw1 = int(vw * 0.25), int(vw * 0.75)
    bg_tmpl = grays[0][ch0:ch1, cw0:cw1].astype(np.float32)

    residuals = []
    bg_dxs = []
    for k in range(1, min(10, n)):
        res_bg = cv2.matchTemplate(grays[k].astype(np.float32), bg_tmpl, cv2.TM_CCOEFF_NORMED)
        _, _, _, bg_loc = cv2.minMaxLoc(res_bg)
        dx_bg = int(bg_loc[0]) - cw0
        dy_bg = int(bg_loc[1]) - ch0
        if k == 1:
            bg_dxs.append(dx_bg)
        if k >= 2:
            M = np.float32([[1, 0, -dx_bg], [0, 1, -dy_bg]])
            aligned_k = cv2.warpAffine(grays[k], M, (vw, vh), borderMode=cv2.BORDER_REPLICATE)
            residuals.append(np.abs(grays[0] - aligned_k))

    if len(residuals) >= 4:
        # 20th percentile across frames k>=2 eliminates shifted ghost overlap and isolates frame 0's box
        res_p20 = np.percentile(np.stack(residuals), 20, axis=0).astype(np.float32)
        margin_mask = np.zeros((vh, vw), dtype=np.float32)
        margin_mask[:int(vh * 0.26), :] = 1.0
        margin_mask[int(vh * 0.68):, :] = 1.0
        drift_map = res_p20 * margin_mask * row_gate

        cands_f0 = _extract_candidates_from_score_map(drift_map, vw, vh)
        for (x, y, w, h, raw_s) in cands_f0[:3]:
            if raw_s < 14.0:
                continue
            tmpl_gx = gxs[0, y:y + h, x:x + w]
            tmpl_gy = gys[0, y:y + h, x:x + w]
            match_scores = []
            positions = [(x, y)]
            for k in range(1, min(6, n)):
                band_y0 = max(0, y - 20)
                band_y1 = min(vh, y + h + 20)
                mval, (mx, my) = _signed_gradient_template_match(
                    gxs[k, band_y0:band_y1, :],
                    gys[k, band_y0:band_y1, :],
                    tmpl_gx,
                    tmpl_gy,
                )
                match_scores.append(mval)
                positions.append((mx, my + band_y0))

            if len(match_scores) >= 4 and float(np.mean(match_scores)) >= 0.70:
                dx_total = positions[-1][0] - positions[0][0]
                step_dxs = [positions[i][0] - positions[i - 1][0] for i in range(1, len(positions))]
                bg_dx0 = bg_dxs[0] if bg_dxs else 0
                if abs(dx_total) >= 24 and all(dx * dx_total >= -2 for dx in step_dxs) and abs(step_dxs[0] - bg_dx0) >= 5:
                    conf = round(min(0.94, 0.20 + float(np.mean(match_scores))), 2)
                    return [{
                        "x": int(x),
                        "y": int(y),
                        "w": int(w),
                        "h": int(h),
                        "confidence": conf,
                        "kind": "moving",
                        "needs_review": conf < 0.75,
                    }]

    return []


def detect_watermark_regions(
    input_path: str,
    max_candidates: int = 3,
    num_samples: int = 24,
) -> Dict[str, Any]:
    """
    Main entrypoint for Phase P2 watermark auto-detection.
    """
    t0 = time.perf_counter()
    frames, indices, scene_cuts, info = sample_video_frames(input_path, num_samples=num_samples)
    vw = int(info["width"])
    vh = int(info["height"])

    cut_set = set(scene_cuts)
    clean_frames = [f for idx_pos, f in enumerate(frames) if idx_pos not in cut_set]
    if len(clean_frames) < 8:
        clean_frames = frames

    grays = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in clean_frames])
    gxs = np.stack([cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) for g in grays])
    gys = np.stack([cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) for g in grays])

    t_std = grays.std(axis=0)
    row_gate = _compute_row_motion_gate(t_std)
    prior = _spatial_prior_map(vw, vh)

    static_score_map = _build_static_persistence_map(gxs, gys, t_std, row_gate, prior)
    raw_candidates = _extract_candidates_from_score_map(static_score_map, vw, vh)

    verified_regions: List[Dict[str, Any]] = []
    for (x, y, w, h, raw_s) in raw_candidates:
        q_corr, presence_ratio = _cross_quarter_gradient_corr(gxs, gys, x, y, w, h)

        if q_corr < 0.36 or presence_ratio < 0.82:
            continue

        cy_norm = (y + 0.5 * h) / max(1.0, float(vh))
        if 0.30 <= cy_norm <= 0.60 and q_corr < 0.65:
            continue

        if raw_s < 4.5:
            continue

        roi_std = float(t_std[y:y + h, x:x + w].mean())
        bg_std = float(np.percentile(t_std, 70)) + 1e-3
        if roi_std > bg_std * 0.45 or raw_s < 25.0:
            kind = "semi_transparent"
        else:
            kind = "logo"

        conf = float(np.clip(0.55 + 0.30 * q_corr + min(0.14, raw_s / 120.0), 0.50, 0.98))
        conf = round(conf, 2)
        verified_regions.append({
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "confidence": conf,
            "kind": kind,
            "needs_review": conf < 0.75,
            "_raw_score": raw_s * q_corr,
        })

    if not verified_regions:
        all_grays = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
        all_gxs = np.stack([cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) for g in all_grays])
        all_gys = np.stack([cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) for g in all_grays])
        moving_cands = _detect_jumping_or_drifting_watermark(
            frames, all_gxs, all_gys, all_grays, vw, vh, row_gate,
        )
        for mc in moving_cands:
            mc["_raw_score"] = mc["confidence"] * 20.0
            verified_regions.append(mc)

    verified_regions.sort(key=lambda r: r.get("_raw_score", 0.0), reverse=True)
    final_regions: List[Dict[str, Any]] = []
    for cand in verified_regions:
        box_c = (cand["x"], cand["y"], cand["w"], cand["h"])
        if any(_iou(box_c, (r["x"], r["y"], r["w"], r["h"])) > 0.35 for r in final_regions):
            continue
        cand_clean = {k: v for k, v in cand.items() if not k.startswith("_")}
        cand_clean["needs_review"] = bool(cand_clean.get("needs_review") or float(cand_clean.get("confidence", 0.0)) < 0.75)
        cand_clean["id"] = f"wm_{len(final_regions) + 1}"
        final_regions.append(cand_clean)
        if len(final_regions) >= max_candidates:
            break

    elapsed = round(time.perf_counter() - t0, 3)
    if not final_regions:
        status = "none"
    elif any(r.get("needs_review") for r in final_regions):
        status = "needs_review"
    else:
        status = "detected"

    needs_review = status != "detected"

    return {
        "status": status,
        "needs_review": needs_review,
        "regions": final_regions,
        "scene_cuts": scene_cuts,
        "sampled_frames": len(frames),
        "elapsed_sec": elapsed,
        "video_info": info,
    }
