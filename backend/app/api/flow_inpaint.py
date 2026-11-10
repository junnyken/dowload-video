"""
Natural logo inpainting engine (CPU, no GPU)
============================================
Replaces the blurry TELEA fast-marching fill with patch/temporal methods:

  • SHIFTMAP (xphoto exemplar inpaint) — synthesizes real texture instead of
    smearing colour inward. Run on a small CROP around the logo so it stays
    fast (~tens of ms), and only ONCE per clip for static backgrounds.
  • Temporal motion-compensated fill — for moving footage, the background
    hidden by a fixed-position logo is visible (at a shifted position) in
    neighbouring frames. Estimate inter-frame motion (ORB + homography on the
    UNMASKED area), warp a neighbour in, and copy the real pixels. Zero blur.
  • Feather blend + mask dilation — no visible box edge, covers anti-aliased
    logo fringes. The old `soft` Gaussian blur (which made things blurrier) is
    gone.

Falls back gracefully: temporal → static SHIFTMAP patch → TELEA, so output is
never worse than the previous behaviour even when motion estimation fails.

SCOPE: visible on-frame logo only (same as flow_cleanup). No GPU, no models.
"""

import os
import queue as _queue
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

# Motion-estimation validity thresholds (tuned conservative — prefer the safe
# static patch over a wrong warp that would ghost).
_MIN_INLIERS = 18
_MAX_TRANSLATION_FRAC = 0.35   # reject warps that shift > 35% of frame (likely bad match)
_TEMPORAL_COVERAGE_STOP_FRAC = 0.98
_MAX_TEMPORAL_WARP_ATTEMPTS = 8


# ── Mask helpers ─────────────────────────────────────────────────────

def dilate_fill_mask(fill_mask: np.ndarray, px: int) -> np.ndarray:
    """Grow the 255=fill region by `px` to swallow anti-aliased logo fringes."""
    if px <= 0:
        return fill_mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px * 2 + 1, px * 2 + 1))
    return cv2.dilate(fill_mask, k)


def _feather_alpha(fill_mask: np.ndarray, feather_px: int) -> np.ndarray:
    """
    Soft 0..1 alpha for blending the fill over the original: 1 deep inside the
    hole, ramping to 0 at the boundary so the patch edge is invisible.
    """
    if feather_px <= 0:
        return (fill_mask > 0).astype(np.float32)
    dist = cv2.distanceTransform((fill_mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    alpha = np.clip(dist / float(feather_px), 0.0, 1.0)
    return alpha.astype(np.float32)


def feather_blend(orig: np.ndarray, filled: np.ndarray,
                  fill_mask: np.ndarray, feather_px: int = 6) -> np.ndarray:
    """Blend `filled` into `orig` using a feathered alpha from `fill_mask` (ROI-bounded for 720p/1080p speed)."""
    bx, by, bw, bh = cv2.boundingRect(fill_mask)
    if bw <= 0 or bh <= 0:
        return orig.copy()
    vh, vw = orig.shape[:2]
    x0 = max(0, bx - 2)
    y0 = max(0, by - 2)
    x1 = min(vw, bx + bw + 2)
    y1 = min(vh, by + bh + 2)
    out = orig.copy()
    sub_mask = fill_mask[y0:y1, x0:x1]
    a = _feather_alpha(sub_mask, feather_px)[:, :, None]
    sub_orig = orig[y0:y1, x0:x1].astype(np.float32)
    sub_fill = filled[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = np.clip(sub_orig * (1.0 - a) + sub_fill * a, 0, 255).astype(np.uint8)
    return out


# ── Spatial inpaint (crop-local SHIFTMAP, TELEA fallback) ────────────

def _crop_window(x: int, y: int, w: int, h: int, vw: int, vh: int,
                 margin: int) -> Tuple[int, int, int, int]:
    cx0 = max(0, x - margin)
    cy0 = max(0, y - margin)
    cx1 = min(vw, x + w + margin)
    cy1 = min(vh, y + h + margin)
    return cx0, cy0, cx1, cy1


def _shiftmap_fill(crop: np.ndarray, cmask: np.ndarray, max_dim: int) -> Optional[np.ndarray]:
    """
    SHIFTMAP-inpaint a crop, DOWNSCALING first so large holes fill fully and
    fast. Full-res SHIFTMAP on a big hole is ~O(area): a 480px hole takes ~46s
    and can leave an unfilled black core. Downscaling to ~max_dim makes it a
    few seconds and reliably fills, then we upscale back.
    cmask: 255 = fill, 0 = keep.  Returns filled crop or None.
    """
    if not hasattr(cv2, "xphoto"):
        return None
    ch, cw = crop.shape[:2]
    longest = max(ch, cw)
    scale = (max_dim / float(longest)) if longest > max_dim else 1.0
    if scale < 1.0:
        sw, sh = max(1, int(cw * scale)), max(1, int(ch * scale))
        cs = cv2.resize(crop, (sw, sh), interpolation=cv2.INTER_AREA)
        ms = cv2.resize(cmask, (sw, sh), interpolation=cv2.INTER_NEAREST)
    else:
        cs, ms = crop, cmask
    keep = cv2.bitwise_not(ms)          # xphoto: non-zero = KNOWN
    dst = np.zeros_like(cs)
    try:
        cv2.xphoto.inpaint(cs, keep, dst, cv2.xphoto.INPAINT_SHIFTMAP)
    except Exception:
        return None
    if dst is None or dst.shape != cs.shape:
        return None
    if scale < 1.0:
        dst = cv2.resize(dst, (cw, ch), interpolation=cv2.INTER_LINEAR)
    return dst


def _region_is_clean(filled: np.ndarray, cmask: np.ndarray) -> bool:
    """Reject a degenerate inpaint (black core / flat) so we never ship a
    black box — caller falls back to TELEA, which is blurry but never black."""
    reg = filled[cmask > 0]
    if reg.size == 0:
        return False
    black_frac = float((reg.reshape(-1, 3).astype(np.int32).sum(axis=1) < 24).mean())
    return black_frac < 0.02 and float(reg.std()) > 3.0


def spatial_inpaint(frame: np.ndarray, fill_mask: np.ndarray,
                    x: int, y: int, w: int, h: int,
                    best: bool = False) -> Tuple[np.ndarray, str]:
    """
    Inpaint the masked region using (downscaled) SHIFTMAP on a local crop —
    fast + natural. Falls back to TELEA if xphoto is missing, errors, or
    produces a degenerate (black/flat) fill.

    fill_mask: 255 = region to fill, 0 = keep.
    Returns (full-frame image with the region filled, method_used) where
    method_used ∈ {"shiftmap", "telea"} — "telea" signals a weak/fallback fill.
    """
    vh, vw = frame.shape[:2]
    # Margin gives SHIFTMAP surrounding texture to sample from.
    margin = max(int(max(w, h) * 0.9), 48)
    cx0, cy0, cx1, cy1 = _crop_window(x, y, w, h, vw, vh, margin)
    crop = frame[cy0:cy1, cx0:cx1]
    cmask = fill_mask[cy0:cy1, cx0:cx1]

    out = frame.copy()
    filled = _shiftmap_fill(crop, cmask, max_dim=(512 if best else 340))
    if filled is not None and _region_is_clean(filled, cmask):
        out[cy0:cy1, cx0:cx1] = filled
        return out, "shiftmap"

    # Fallback: TELEA on the crop (blurry but complete — never black).
    out[cy0:cy1, cx0:cx1] = cv2.inpaint(crop, cmask, 3, cv2.INPAINT_TELEA)
    return out, "telea"


# ── Temporal motion-compensated fill ─────────────────────────────────

def _detect_orb_features(bgr: np.ndarray, fill_mask: np.ndarray):
    """Compute ORB keypoint coordinates `(N, 2)` and descriptors outside `fill_mask` once per frame."""
    vh, vw = bgr.shape[:2]
    known = cv2.bitwise_not(fill_mask)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    max_dim = max(vh, vw)
    if max_dim > 720:
        scale = 720.0 / float(max_dim)
        sw, sh = max(2, int(round(vw * scale))), max(2, int(round(vh * scale)))
        gray_s = cv2.resize(gray, (sw, sh), interpolation=cv2.INTER_AREA)
        known_s = cv2.resize(known, (sw, sh), interpolation=cv2.INTER_NEAREST)
        orb = cv2.ORB_create(nfeatures=900)
        kps, desc = orb.detectAndCompute(gray_s, known_s)
        if not kps or desc is None:
            return None, None
        inv_scale = 1.0 / scale
        pts = np.array([kp.pt for kp in kps], dtype=np.float32) * inv_scale
        return pts, desc
    orb = cv2.ORB_create(nfeatures=1200)
    kps, desc = orb.detectAndCompute(gray, known)
    if not kps or desc is None:
        return None, None
    pts = np.array([kp.pt for kp in kps], dtype=np.float32)
    return pts, desc


def _match_homography_from_features(
    k1,
    d1,
    k2,
    d2,
    vw: int,
    vh: int,
) -> Optional[np.ndarray]:
    if d1 is None or d2 is None or k1 is None or k2 is None:
        return None
    if len(k1) < _MIN_INLIERS or len(k2) < _MIN_INLIERS:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = matcher.knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in raw if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < _MIN_INLIERS:
        return None
    if len(good) > 120:
        good.sort(key=lambda m: m.distance)
        good = good[:120]
    if isinstance(k1, np.ndarray):
        pts1 = k1[[m.queryIdx for m in good]].reshape(-1, 1, 2)
    else:
        pts1 = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    if isinstance(k2, np.ndarray):
        pts2 = k2[[m.trainIdx for m in good]].reshape(-1, 1, 2)
    else:
        pts2 = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, inliers = cv2.findHomography(pts1, pts2, cv2.RANSAC, 3.0, maxIters=250, confidence=0.99)
    if H is None or inliers is None or int(inliers.sum()) < _MIN_INLIERS:
        return None
    if abs(H[0, 2]) > _MAX_TRANSLATION_FRAC * vw or abs(H[1, 2]) > _MAX_TRANSLATION_FRAC * vh:
        return None
    return H


def _estimate_homography(
    src: np.ndarray,
    dst: np.ndarray,
    fill_mask: np.ndarray,
    src_mask: Optional[np.ndarray] = None,
) -> Optional[np.ndarray]:
    """
    Estimate the homography warping `src` onto `dst`, using features OUTSIDE
    the logo region only (so the logo itself never drives the alignment).
    When `src_mask` is provided (e.g. per-frame tracked watermark mask on `src`),
    features on `src` are masked by `src_mask` while features on `dst` are masked
    by `fill_mask`.
    Returns the 3x3 matrix or None when the match is weak/implausible.
    """
    vh, vw = dst.shape[:2]
    k1, d1 = _detect_orb_features(src, src_mask if src_mask is not None else fill_mask)
    k2, d2 = _detect_orb_features(dst, fill_mask)
    return _match_homography_from_features(k1, d1, k2, d2, vw, vh)


def _estimate_expected_clean_area(
    mask_roi: np.ndarray,
    bx: int,
    by: int,
    bw: int,
    bh: int,
    nb_box_dilated: Tuple[int, int, int, int],
    est_dx: float,
    est_dy: float,
    vw: int,
    vh: int,
) -> int:
    """
    Estimate how many pixels of `mask_roi` will be cleanly exposed (not occluded
    by the neighbour's logo bbox and within the neighbour's frame bounds) when
    the neighbour is warped onto the current frame by `(est_dx, est_dy)`.
    """
    if abs(est_dx) > _MAX_TRANSLATION_FRAC * vw or abs(est_dy) > _MAX_TRANSLATION_FRAC * vh:
        return 0
    dx_i = int(round(est_dx))
    dy_i = int(round(est_dy))

    rx0 = max(0, max(0, dx_i) - bx)
    ry0 = max(0, max(0, dy_i) - by)
    rx1 = min(bw, min(vw, vw + dx_i) - bx)
    ry1 = min(bh, min(vh, vh + dy_i) - by)
    if rx1 <= rx0 or ry1 <= ry0:
        return 0

    in_frame_px = int(mask_roi[ry0:ry1, rx0:rx1].sum())
    if in_frame_px <= 0:
        return 0

    nbx, nby, nbw, nbh = nb_box_dilated
    if nbw <= 0 or nbh <= 0:
        return in_frame_px

    ox0 = max(rx0, (nbx + dx_i) - bx)
    oy0 = max(ry0, (nby + dy_i) - by)
    ox1 = min(rx1, (nbx + nbw + dx_i) - bx)
    oy1 = min(ry1, (nby + nbh + dy_i) - by)
    occluded_px = int(mask_roi[oy0:oy1, ox0:ox1].sum()) if (ox1 > ox0 and oy1 > oy0) else 0
    return max(0, in_frame_px - occluded_px)


def _dedup_and_rank_neighbors(
    neighbors: list,
    fill_mask: np.ndarray,
    mask_roi: np.ndarray,
    bx: int,
    by: int,
    bw: int,
    bh: int,
    vw: int,
    vh: int,
    cur_fidx: Optional[int] = None,
    bg_vel: Optional[Sequence[float]] = None,
    dilate_px: int = 12,
) -> list:
    """
    Deduplicate candidate neighbours by frame index and rank them in descending
    order of expected unoccluded background area (`expected_clean_px`).
    """
    seen_fidxs = set()
    seen_obj_ids = set()
    candidates = []
    total_mask_px = max(1, int(mask_roi.sum()))
    stop_px = int(total_mask_px * _TEMPORAL_COVERAGE_STOP_FRAC)

    for orig_idx, nb_item in enumerate(neighbors):
        nb_fidx: Optional[int] = None
        nb_box: Optional[Tuple[int, int, int, int]] = None
        k1 = d1 = None

        if isinstance(nb_item, tuple):
            if len(nb_item) >= 6:
                nb, nb_mask, k1, d1, nb_fidx, nb_box = nb_item[:6]
            elif len(nb_item) == 5:
                nb, nb_mask, k1, d1, nb_fidx = nb_item
            elif len(nb_item) == 4:
                nb, nb_mask, k1, d1 = nb_item
            elif len(nb_item) == 2:
                nb, nb_mask = nb_item
            else:
                nb, nb_mask = nb_item[0], fill_mask
        else:
            nb, nb_mask = nb_item, fill_mask

        if nb_fidx is not None:
            if cur_fidx is not None and int(nb_fidx) == int(cur_fidx):
                continue
            if int(nb_fidx) in seen_fidxs:
                continue
            seen_fidxs.add(int(nb_fidx))
        else:
            obj_id = id(nb)
            if obj_id in seen_obj_ids:
                continue
            seen_obj_ids.add(obj_id)

        if nb_box is not None and len(nb_box) >= 4:
            nx, ny, nw, nh = int(nb_box[0]), int(nb_box[1]), int(nb_box[2]), int(nb_box[3])
            if nw <= 0 or nh <= 0:
                nb_box_dilated = (0, 0, 0, 0)
            else:
                x0 = max(0, nx - dilate_px)
                y0 = max(0, ny - dilate_px)
                x1 = min(vw, nx + nw + dilate_px)
                y1 = min(vh, ny + nh + dilate_px)
                nb_box_dilated = (x0, y0, max(0, x1 - x0), max(0, y1 - y0))
        else:
            nb_box_dilated = cv2.boundingRect(nb_mask)

        if bg_vel is not None and cur_fidx is not None and nb_fidx is not None:
            df = float(cur_fidx - int(nb_fidx))
            est_dx = df * float(bg_vel[0])
            est_dy = df * float(bg_vel[1])
        else:
            est_dx = 0.0
            est_dy = 0.0

        expected_clean_px = _estimate_expected_clean_area(
            mask_roi, bx, by, bw, bh, nb_box_dilated, est_dx, est_dy, vw, vh
        )
        abs_df = abs(int(cur_fidx) - int(nb_fidx)) if (cur_fidx is not None and nb_fidx is not None) else 0
        candidates.append((
            -min(expected_clean_px, stop_px),
            abs_df,
            -expected_clean_px,
            orig_idx,
            (nb, nb_mask, k1, d1, nb_fidx, nb_box_dilated),
        ))

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [item[4] for item in candidates]


def temporal_fill(
    cur: np.ndarray,
    neighbors: list,
    fill_mask: np.ndarray,
    cur_features: Optional[Tuple[Any, Any]] = None,
    cur_fidx: Optional[int] = None,
    bg_vel: Optional[List[float]] = None,
    dilate_px: int = 12,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Reconstruct the masked region of `cur` from neighbour frames via motion
    compensation (ROI-bounded warp for 720p/1080p speed).
    Neighbours are deduplicated and ranked pre-warp by expected unoccluded
    background coverage. Stops when >= `_TEMPORAL_COVERAGE_STOP_FRAC` (98%) of
    the mask is covered OR after `_MAX_TEMPORAL_WARP_ATTEMPTS` (8) valid warps.
    Returns `(filled_region_image, valid_mask)` where `valid_mask` is
    255 where a real pixel was recovered, 0 elsewhere.
    """
    vh, vw = cur.shape[:2]
    bx, by, bw, bh = cv2.boundingRect(fill_mask)
    if bw <= 0 or bh <= 0:
        return np.zeros_like(cur), np.zeros((vh, vw), dtype=np.uint8)

    if cur_features is not None:
        k2, d2 = cur_features
    else:
        k2, d2 = _detect_orb_features(cur, fill_mask)
    if d2 is None or k2 is None or len(k2) < _MIN_INLIERS:
        return np.zeros_like(cur), np.zeros((vh, vw), dtype=np.uint8)

    acc_roi = np.zeros((bh, bw, 3), dtype=np.float32)
    cnt_roi = np.zeros((bh, bw), dtype=np.float32)
    mask_roi = fill_mask[by : by + bh, bx : bx + bw] > 0
    total_mask_px = max(1, int(mask_roi.sum()))
    stop_mask_px = int(total_mask_px * _TEMPORAL_COVERAGE_STOP_FRAC)
    t_roi = np.array([[1.0, 0.0, -float(bx)], [0.0, 1.0, -float(by)], [0.0, 0.0, 1.0]], dtype=np.float64)

    ranked_neighbors = _dedup_and_rank_neighbors(
        neighbors, fill_mask, mask_roi, bx, by, bw, bh, vw, vh,
        cur_fidx=cur_fidx, bg_vel=bg_vel, dilate_px=dilate_px,
    )
    warp_attempts = 0
    vel_updated = False

    for nb, nb_mask, k1, d1, nb_fidx, _ in ranked_neighbors:
        if k1 is None or d1 is None:
            k1, d1 = _detect_orb_features(nb, nb_mask)
        H = _match_homography_from_features(k1, d1, k2, d2, vw, vh)
        if H is None:
            continue
        warp_attempts += 1
        if (
            not vel_updated
            and bg_vel is not None
            and isinstance(bg_vel, list)
            and len(bg_vel) >= 2
            and cur_fidx is not None
            and nb_fidx is not None
            and int(nb_fidx) != int(cur_fidx)
        ):
            df = float(int(cur_fidx) - int(nb_fidx))
            inst_vx = float(H[0, 2]) / df
            inst_vy = float(H[1, 2]) / df
            if bg_vel[0] == 0.0 and bg_vel[1] == 0.0:
                bg_vel[0] = inst_vx
                bg_vel[1] = inst_vy
            else:
                bg_vel[0] = 0.7 * float(bg_vel[0]) + 0.3 * inst_vx
                bg_vel[1] = 0.7 * float(bg_vel[1]) + 0.3 * inst_vy
            vel_updated = True

        h_roi = t_roi @ H.astype(np.float64)
        warped_roi = cv2.warpPerspective(
            nb, h_roi, (bw, bh), flags=cv2.INTER_LINEAR, borderValue=0
        )
        # The neighbour's own logo region, warped, must NOT be used.
        nb_logo_warp_roi = cv2.warpPerspective(
            nb_mask, h_roi, (bw, bh), flags=cv2.INTER_NEAREST
        )
        valid_roi = mask_roi & (nb_logo_warp_roi == 0) & (warped_roi.sum(axis=2) > 0)
        if bool(valid_roi.any()):
            acc_roi[valid_roi] += warped_roi[valid_roi]
            cnt_roi[valid_roi] += 1.0
            if int((cnt_roi > 0).sum()) >= stop_mask_px:
                break
        if warp_attempts >= _MAX_TEMPORAL_WARP_ATTEMPTS:
            break

    got_roi = cnt_roi > 0
    filled = np.zeros_like(cur)
    valid_mask = np.zeros((vh, vw), dtype=np.uint8)
    if bool(got_roi.any()):
        filled_roi = np.zeros((bh, bw, 3), dtype=np.uint8)
        filled_roi[got_roi] = (acc_roi[got_roi] / cnt_roi[got_roi, None]).astype(np.uint8)
        filled[by : by + bh, bx : bx + bw] = filled_roi
        valid_roi_u8 = np.zeros((bh, bw), dtype=np.uint8)
        valid_roi_u8[got_roi] = 255
        valid_mask[by : by + bh, bx : bx + bw] = valid_roi_u8
    return filled, valid_mask


# ── Full video: base SHIFTMAP patch + per-frame temporal fill ────────

def _replicate_padded_odd_border(frame: np.ndarray) -> np.ndarray:
    """If FFmpeg `pad=vw:vh:0:0:black` added a 1px right/bottom border for an odd-dimension video,
    replicate the adjacent edge pixel so `yuv420p` 2x2 chroma subsampling has zero black-edge bleeding."""
    vh, vw = frame.shape[:2]
    if vw >= 2 and int(frame[:, -1].max()) == 0 and int(frame[:, -2].max()) > 0:
        frame[:, -1] = frame[:, -2]
    if vh >= 2 and int(frame[-1, :].max()) == 0 and int(frame[-2, :].max()) > 0:
        frame[-1, :] = frame[-2, :]
    return frame


def _grab_frame_at(input_path: str, ts: float, vw: int, vh: int) -> Optional[np.ndarray]:
    vw = max(2, int(vw) + (int(vw) % 2))
    vh = max(2, int(vh) + (int(vh) % 2))
    frame_size = vw * vh * 3
    vf_pad = f"scale='min(iw,{vw})':'min(ih,{vh})',pad={vw}:{vh}:0:0:black"
    r = subprocess.run(
        ["ffmpeg", "-ss", f"{ts:.3f}", "-i", input_path, "-frames:v", "1",
         "-vf", vf_pad, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if len(r.stdout) < frame_size:
        return None
    fr = np.frombuffer(r.stdout[:frame_size], dtype=np.uint8).reshape((vh, vw, 3)).copy()
    return _replicate_padded_odd_border(fr)


def _frame_ok(fr: Optional[np.ndarray]) -> bool:
    """A usable frame isn't mostly black / flat (guards bad seeks)."""
    return fr is not None and float(fr.mean()) > 8.0 and float(fr.std()) > 3.0


def _extract_middle_frame(input_path: str, vw: int, vh: int,
                          duration: float) -> Optional[np.ndarray]:
    """Decode a representative frame for the static base patch; fall back to
    frame 0 if the mid-clip seek returns a degenerate (black) frame."""
    mid = _grab_frame_at(input_path, max(0.0, (duration or 0.0) / 2.0), vw, vh)
    if _frame_ok(mid):
        return mid
    f0 = _grab_frame_at(input_path, 0.0, vw, vh)
    return f0 if _frame_ok(f0) else mid


def _is_static_around(input_path: str, x: int, y: int, w: int, h: int,
                      vw: int, vh: int, duration: float,
                      thresh: float = 6.0) -> bool:
    """
    Decide whether the background AROUND the logo is stationary across the clip.
    Samples frames and measures temporal change in a ring just outside the logo
    (the logo itself is excluded). Low change → the hidden background is also
    static → a single base patch can be reused. High change → the scene moves →
    must fill per-frame (else a reused patch would look frozen).
    """
    if not duration or duration <= 0:
        return True  # can't sample → assume static (safe: base patch path)
    times = [duration * f for f in (0.15, 0.35, 0.55, 0.75, 0.92)]
    frames = [f for f in (_grab_frame_at(input_path, t, vw, vh) for t in times) if f is not None]
    if len(frames) < 2:
        return True
    # Ring around the logo: dilated region minus the region itself.
    m = max(int(max(w, h) * 0.6), 40)
    rx0, ry0 = max(0, x - m), max(0, y - m)
    rx1, ry1 = min(vw, x + w + m), min(vh, y + h + m)
    ref = frames[len(frames) // 2]
    diffs = []
    for fr in frames:
        ring_a = fr[ry0:ry1, rx0:rx1].astype(np.int16)
        ring_b = ref[ry0:ry1, rx0:rx1].astype(np.int16)
        d = np.abs(ring_a - ring_b)
        # zero-out the inner logo area so only the surrounding ring counts
        iy0, ix0 = y - ry0, x - rx0
        d[max(0, iy0):iy0 + h, max(0, ix0):ix0 + w] = 0
        diffs.append(float(d.mean()))
    return (max(diffs) if diffs else 0.0) < thresh


def _telea_crop(frame: np.ndarray, fill_mask: np.ndarray,
                x: int, y: int, w: int, h: int) -> np.ndarray:
    """Fast per-frame TELEA on a local crop (moving-clip fallback)."""
    vh, vw = frame.shape[:2]
    margin = max(int(max(w, h) * 0.5), 24)
    cx0, cy0, cx1, cy1 = _crop_window(x, y, w, h, vw, vh, margin)
    out = frame.copy()
    out[cy0:cy1, cx0:cx1] = cv2.inpaint(
        frame[cy0:cy1, cx0:cx1], fill_mask[cy0:cy1, cx0:cx1], 3, cv2.INPAINT_TELEA)
    return out


def _resolve_per_frame_box_and_mask(
    frame_idx: int,
    default_box: Tuple[int, int, int, int],
    default_fill: np.ndarray,
    vw: int,
    vh: int,
    dilate_px: int,
    frame_boxes: Optional[Union[Sequence[Sequence[int]], Dict[Any, Sequence[int]]]] = None,
    frame_masks: Optional[Union[Sequence[np.ndarray], Callable[[int], np.ndarray]]] = None,
) -> Tuple[int, int, int, int, np.ndarray]:
    """Resolve `(fx, fy, fw, fh, fill_mask)` for frame `frame_idx` (0-indexed)."""
    if frame_masks is not None:
        if callable(frame_masks):
            raw_m = frame_masks(frame_idx)
        elif 0 <= frame_idx < len(frame_masks):
            raw_m = frame_masks[frame_idx]
        else:
            raw_m = frame_masks[-1] if len(frame_masks) > 0 else default_fill
        if raw_m is None or raw_m.shape[:2] != (vh, vw):
            return default_box[0], default_box[1], default_box[2], default_box[3], default_fill
        m_u8 = (raw_m > 0).astype(np.uint8) * 255
        if int(m_u8.sum()) == 0:
            return 0, 0, 0, 0, m_u8
        bx, by, bw, bh = cv2.boundingRect(m_u8)
        return int(bx), int(by), int(bw), int(bh), m_u8

    if frame_boxes is not None:
        b = None
        if isinstance(frame_boxes, dict):
            b = frame_boxes.get(str(frame_idx)) or frame_boxes.get(frame_idx)
            if b is None and frame_boxes:
                b = frame_boxes.get("0") or next(iter(frame_boxes.values()))
        elif isinstance(frame_boxes, (list, tuple)) and len(frame_boxes) > 0:
            b = frame_boxes[min(frame_idx, len(frame_boxes) - 1)]
        if b is not None and len(b) >= 4:
            bx, by, bw, bh = [int(round(v)) for v in b[:4]]
            bx = max(0, min(vw - 2, bx))
            by = max(0, min(vh - 2, by))
            bw = max(0, min(vw - bx, bw))
            bh = max(0, min(vh - by, bh))
            if bw <= 0 or bh <= 0:
                return 0, 0, 0, 0, np.zeros((vh, vw), dtype=np.uint8)
            if (bx, by, bw, bh) == default_box:
                return bx, by, bw, bh, default_fill
            m = _build_fill_mask(bx, by, bw, bh, vw, vh)
            return bx, by, bw, bh, dilate_fill_mask(m, dilate_px)

    return default_box[0], default_box[1], default_box[2], default_box[3], default_fill


def process_video_natural(
    input_path: str,
    output_path: str,
    x: int, y: int, w: int, h: int,
    vw: int, vh: int,
    fps: float,
    duration: float = 0.0,
    dilate_px: int = 12,   # must be > feather_px so the feather ramp lands in
    feather_px: int = 7,   # real background, never on the logo itself
    temporal: bool = True,
    n_neighbors: int = 2,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    frame_boxes: Optional[Union[Sequence[Sequence[int]], Dict[Any, Sequence[int]]]] = None,
    frame_masks: Optional[Union[Sequence[np.ndarray], Callable[[int], np.ndarray]]] = None,
) -> dict:
    """
    Natural logo removal for video:
      • base patch = SHIFTMAP inpaint of the region on a middle frame (once),
        the universal fallback (correct for static backgrounds);
      • per frame, temporal motion-compensated fill recovers REAL background
        from neighbouring/strided frames where motion or watermark movement
        exposed it; residual pixels use the base patch / TELEA; the composite
        is feather-blended into the frame.
      • Phase P3: accepts optional `frame_boxes` (list or dict of `[x, y, w, h]`
        per frame) or `frame_masks` (sequence or callable returning `(vh, vw)`
        uint8 mask per frame), while preserving full backward compatibility
        with fixed `(x, y, w, h)` callers.

    Returns stats {temporal_frames, total_frames, mode, dynamic_mask} for telemetry.
    """
    vw = max(2, int(vw) + (int(vw) % 2))
    vh = max(2, int(vh) + (int(vh) % 2))
    x = max(0, min(vw - 2, int(x)))
    y = max(0, min(vh - 2, int(y)))
    w = max(2, min(vw - x, int(w)))
    h = max(2, min(vh - y, int(h)))
    default_box = (int(x), int(y), int(w), int(h))
    fill = _build_fill_mask(x, y, w, h, vw, vh)
    fill = dilate_fill_mask(fill, dilate_px)

    has_dynamic_mask = frame_boxes is not None or frame_masks is not None
    boxes_vary = False
    if isinstance(frame_boxes, dict) and len(frame_boxes) > 1:
        first_b = tuple(int(v) for v in next(iter(frame_boxes.values()))[:4])
        boxes_vary = any(tuple(int(v) for v in b[:4]) != first_b for b in frame_boxes.values() if b)
    elif isinstance(frame_boxes, (list, tuple)) and len(frame_boxes) > 1:
        first_b = tuple(int(v) for v in frame_boxes[0][:4])
        boxes_vary = any(tuple(int(v) for v in b[:4]) != first_b for b in frame_boxes if b)
    elif frame_masks is not None:
        boxes_vary = True

    # Is the background around the logo static? Decides reuse vs per-frame.
    # If the watermark itself moves across frames (`boxes_vary`), we must process per-frame.
    static_bg = (not boxes_vary) and _is_static_around(input_path, x, y, w, h, vw, vh, duration)

    # Cap the (expensive) per-frame temporal pass on long clips so a moving
    # clip can't run long enough to trip an upstream gateway timeout. The
    # static fast path is unaffected.
    est_frames = (duration or 0.0) * (fps or 30.0)
    if est_frames > 1200:
        temporal = False
    total_est = max(1, int(round(est_frames))) if est_frames > 0 else 100

    # Base natural patch (SHIFTMAP, once) — only safe to reuse on static clips.
    base_filled = None
    if static_bg:
        mid = _extract_middle_frame(input_path, vw, vh, duration)
        if mid is not None:
            base_filled, _ = spatial_inpaint(mid, fill, x, y, w, h)
    if base_filled is None:
        static_bg = False  # SHIFTMAP unavailable/failed → treat as per-frame

    # Pre-sample strided reference frames across the clip for ALL moving-background
    # clips (`not static_bg`) so `temporal_fill` has wider-baseline unoccluded
    # reference frames right from frame 0 (both for panning backgrounds behind a
    # static corner logo and for moving/jumping watermarks).
    global_ref_neighbors: list = []
    if not static_bg and temporal and duration and duration > 0:
        sample_ts_list = [duration * frac for frac in (0.08, 0.25, 0.45, 0.68, 0.88)]
        fps_safe = float(fps or 30.0)
        for fixed_sec in (0.6, 1.8):
            if fixed_sec < duration:
                sample_ts_list.append(fixed_sec)
        # Also sample frames immediately before and after any bbox jump discontinuity
        # so jumping watermarks recover 100% unoccluded background from 1-2 frames across the jump!
        if boxes_vary:
            prev_b = None
            for idx_check in range(total_est):
                bx, by, bw, bh, _ = _resolve_per_frame_box_and_mask(
                    idx_check, default_box, fill, vw, vh, dilate_px, frame_boxes, frame_masks,
                )
                if prev_b is not None and (abs(bx - prev_b[0]) + abs(by - prev_b[1])) > 20:
                    for delta in (-18, -2, 1, 18):
                        cand_idx = max(0, min(total_est - 1, idx_check + delta))
                        sample_ts_list.append(cand_idx / fps_safe)
                prev_b = (bx, by, bw, bh)

        seen_ref_idxs = set()
        for ts in sample_ts_list:
            ref_idx = max(0, int(round(ts * fps_safe)))
            if ref_idx in seen_ref_idxs:
                continue
            seen_ref_idxs.add(ref_idx)
            ref_fr = _grab_frame_at(input_path, ts, vw, vh)
            if ref_fr is not None and _frame_ok(ref_fr):
                rx, ry, rw, rh, ref_mask = _resolve_per_frame_box_and_mask(
                    ref_idx, default_box, fill, vw, vh, dilate_px, frame_boxes, frame_masks,
                )
                rk, rd = _detect_orb_features(ref_fr, ref_mask)
                if rd is not None and rk is not None and len(rk) >= _MIN_INLIERS:
                    global_ref_neighbors.append((ref_fr, ref_mask, rk, rd, ref_idx, (rx, ry, rw, rh)))

    frame_size = vw * vh * 3
    fps_str = f"{fps:.3f}"
    output_path = os.path.abspath(output_path)
    video_only = output_path + ".noaudio.mp4"
    q: _queue.Queue = _queue.Queue(maxsize=8)
    reader_errors: list = []
    reader_proc_ref: list = [None]

    def _reader() -> None:
        vf_pad = f"scale='min(iw,{vw})':'min(ih,{vh})',pad={vw}:{vh}:0:0:black"
        proc = subprocess.Popen(
            ["ffmpeg", "-i", input_path,
             "-vf", vf_pad, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        reader_proc_ref[0] = proc
        try:
            while True:
                chunk = proc.stdout.read(frame_size)  # type: ignore[union-attr]
                if len(chunk) < frame_size:
                    break
                q.put(chunk)
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
            proc.wait()
            q.put(None)

    write_proc = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{vw}x{vh}", "-r", fps_str, "-i", "pipe:0",
         "-c:v", "libx264", "-crf", "12", "-preset", "fast",
         "-pix_fmt", "yuv420p", video_only],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    neighbors: list = []          # immediate causal window of recent ORIGINAL frames
    strided_neighbors: list = []  # strided past frames (every 12th frame) for wider baseline recovery
    bg_vel: List[float] = [0.0, 0.0]
    if len(global_ref_neighbors) >= 2:
        for gi in range(len(global_ref_neighbors) - 1):
            _, _, gk1, gd1, gidx1, _ = global_ref_neighbors[gi]
            _, _, gk2, gd2, gidx2, _ = global_ref_neighbors[gi + 1]
            gdf = int(gidx2) - int(gidx1)
            if 0 < abs(gdf) <= 60:
                gH = _match_homography_from_features(gk1, gd1, gk2, gd2, vw, vh)
                if gH is not None:
                    bg_vel[0] = float(gH[0, 2]) / float(gdf)
                    bg_vel[1] = float(gH[1, 2]) / float(gdf)
                    break
    stats = {
        "total_frames": 0,
        "temporal_frames": 0,
        "mode": "static_patch" if static_bg else "per_frame",
        "dynamic_mask": has_dynamic_mask,
    }
    write_errors: list = []
    try:
        while True:
            raw = q.get(timeout=120)
            if raw is None:
                break
            frame = _replicate_padded_odd_border(
                np.frombuffer(raw, dtype=np.uint8).reshape((vh, vw, 3)).copy()
            )
            fidx = stats["total_frames"]
            stats["total_frames"] += 1

            if progress_cb is not None and (stats["total_frames"] == 1 or stats["total_frames"] % 5 == 0):
                if progress_cb(stats["total_frames"], max(total_est, stats["total_frames"])) is False:
                    raise RuntimeError("Job cancelled by user")

            fx, fy, fw, fh, cur_fill = _resolve_per_frame_box_and_mask(
                fidx, default_box, fill, vw, vh, dilate_px, frame_boxes, frame_masks,
            )

            if fw <= 0 or fh <= 0 or int(cur_fill.sum()) == 0:
                write_proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
                continue

            if static_bg:
                # Static background → reuse the one natural SHIFTMAP patch.
                comp = base_filled  # read-only; feather_blend never mutates it
            else:
                # Moving scene or dynamic watermark → temporal motion-comp
                # recovers REAL pixels first; any residual unrecovered pixels
                # are inpainted directly from the recovered context.
                fk, fd = (_detect_orb_features(frame, cur_fill) if temporal else (None, None))
                seen_active_fidxs = {fidx}
                active_neighbors = []
                for cand in neighbors + strided_neighbors + global_ref_neighbors:
                    cand_fidx = cand[4] if (isinstance(cand, tuple) and len(cand) >= 5) else None
                    if cand_fidx is not None:
                        if int(cand_fidx) in seen_active_fidxs:
                            continue
                        seen_active_fidxs.add(int(cand_fidx))
                    active_neighbors.append(cand)
                if temporal and active_neighbors and fd is not None:
                    tfill, tvalid = temporal_fill(
                        frame, active_neighbors, cur_fill, cur_features=(fk, fd),
                        cur_fidx=fidx, bg_vel=bg_vel, dilate_px=dilate_px,
                    )
                    if int(tvalid.sum()) > 0:
                        stats["temporal_frames"] += 1
                        work = frame.copy()
                        work[tvalid > 0] = tfill[tvalid > 0]
                        residual = cv2.bitwise_and(cur_fill, cv2.bitwise_not(tvalid))
                        if int(residual.sum()) > 0:
                            res_dil = dilate_fill_mask(residual, 2)
                            comp = _telea_crop(work, res_dil, fx, fy, fw, fh)
                        else:
                            comp = work
                    else:
                        comp = _telea_crop(frame, cur_fill, fx, fy, fw, fh)
                else:
                    comp = _telea_crop(frame, cur_fill, fx, fy, fw, fh)

            out = feather_blend(frame, comp, cur_fill, feather_px)
            write_proc.stdin.write(out.tobytes())  # type: ignore[union-attr]

            if not static_bg and temporal:
                nb_entry = (frame, cur_fill, fk, fd, fidx, (fx, fy, fw, fh))
                neighbors.append(nb_entry)
                if len(neighbors) > n_neighbors:
                    neighbors.pop(0)
                if fidx % 12 == 0:
                    strided_neighbors.append(nb_entry)
                    if len(strided_neighbors) > 4:
                        strided_neighbors.pop(0)
    except Exception as exc:
        write_errors.append(exc)
    finally:
        try:
            write_proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        if write_errors:
            if reader_proc_ref[0] is not None:
                try:
                    reader_proc_ref[0].terminate()
                except Exception:
                    pass
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception:
                pass

    reader_thread.join(timeout=30)
    write_proc.wait(timeout=300)

    if reader_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise reader_errors[0]
    if write_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise write_errors[0]
    if write_proc.returncode != 0:
        err = write_proc.stderr.read().decode(errors="replace")[-400:]  # type: ignore[union-attr]
        raise ValueError(f"Natural inpaint encode failed: {err}")
    if not os.path.isfile(video_only) or os.path.getsize(video_only) == 0:
        raise ValueError(f"Natural inpaint output missing: {video_only}")

    # Mux original audio back in (transcode non-MP4 audio like Opus/Vorbis to AAC when needed).
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", video_only, "-i", input_path,
         "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "copy",
         "-movflags", "+faststart", output_path],
        capture_output=True, timeout=180,
    )
    if r2.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", video_only, "-i", input_path,
             "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", output_path],
            capture_output=True, timeout=180,
        )
    try:
        os.remove(video_only)
    except Exception:
        pass
    if r2.returncode != 0:
        raise ValueError(f"Audio mux failed: {r2.stderr.decode(errors='replace')[-300:]}")
    return stats


def _build_fill_mask(x: int, y: int, w: int, h: int, vw: int, vh: int) -> np.ndarray:
    mask = np.zeros((vh, vw), dtype=np.uint8)
    mask[y:y + h, x:x + w] = 255
    return mask


# ── Decision layer + consistent preview (preview == final family) ────

# Region area fraction above which natural cleanup gets unreliable → crop.
_LARGE_REGION_FRAC = 0.16
# Temporal coverage below which a moving-scene fill is "mostly TELEA" → weak.
_WEAK_TEMPORAL_COV = 0.20


def decide_and_preview(input_path: str, x: int, y: int, w: int, h: int,
                       vw: int, vh: int, duration: float = 0.0,
                       fps: float = 30.0) -> Tuple[np.ndarray, dict]:
    """
    Run the SAME branch the final video will use, on FRAME 0 (so the 'Sau'
    preview matches 'Trước' and reflects final quality), and judge whether the
    result is clean enough to promote — or whether the user should crop.

    Returns (preview_frame_bgr, info). info keys:
      strategy        "static_patch" | "motion_aware"
      motion          "static" | "moving"
      fill_method     "shiftmap" | "telea" | "temporal+telea"
      temporal_cov    float (moving only)
      region_frac     float
      quality         "clean" | "weak"
      recommend_crop  bool
      reason          short VI string
    """
    fill = dilate_fill_mask(_build_fill_mask(x, y, w, h, vw, vh), 12)
    region_frac = (w * h) / float(max(1, vw * vh))
    frame0 = _grab_frame_at(input_path, 0.0, vw, vh)
    if frame0 is None or not _frame_ok(frame0):
        # Can't read a usable frame → don't pretend; push toward crop.
        blank = frame0 if frame0 is not None else np.zeros((vh, vw, 3), np.uint8)
        return blank, {"strategy": "motion_aware", "motion": "unknown",
                       "fill_method": "none", "temporal_cov": 0.0,
                       "region_frac": region_frac, "quality": "weak",
                       "recommend_crop": True,
                       "reason": "Không đọc được khung hình rõ — nên Cắt viền."}

    static = _is_static_around(input_path, x, y, w, h, vw, vh, duration)
    temporal_cov = 0.0

    if static:
        strategy, motion = "static_patch", "static"
        filled, fill_method = spatial_inpaint(frame0, fill, x, y, w, h, best=True)
    else:
        strategy, motion = "motion_aware", "moving"
        # Sample both immediate neighbours and wider-baseline frames across the
        # clip so panning backgrounds behind a static corner logo (as well as
        # moving scenes) expose the true background to `temporal_fill`.
        fps_safe = max(1.0, float(fps or 30.0))
        cand_times = [2.0 / fps_safe, 4.0 / fps_safe, 6.0 / fps_safe]
        dur_safe = float(duration or 0.0)
        for sec in (0.25, 0.55, 1.0, 1.8, 2.8, 4.0):
            if dur_safe <= 0 or sec < dur_safe:
                cand_times.append(sec)
        if dur_safe > 0:
            for frac in (0.15, 0.35, 0.60, 0.85):
                cand_times.append(dur_safe * frac)
        seen_t = set()
        dedup_times = []
        for t in cand_times:
            tk = round(t, 2)
            if tk > 0 and tk not in seen_t:
                seen_t.add(tk)
                dedup_times.append(t)
        neigh = [f for f in (_grab_frame_at(input_path, t, vw, vh) for t in dedup_times)
                 if f is not None and _frame_ok(f)]
        if neigh:
            tfill, tvalid = temporal_fill(frame0, neigh, fill)
            cov = float((tvalid > 0).sum()) / float(max(1, (fill > 0).sum()))
            temporal_cov = cov
            if cov > 0:
                work = frame0.copy()
                work[tvalid > 0] = tfill[tvalid > 0]
                residual = cv2.bitwise_and(fill, cv2.bitwise_not(tvalid))
                if int(residual.sum()) > 0:
                    res_dil = dilate_fill_mask(residual, 2)
                    comp = _telea_crop(work, res_dil, x, y, w, h)
                else:
                    comp = work
            else:
                comp, _ = spatial_inpaint(frame0, fill, x, y, w, h, best=True)
        else:
            comp, _ = spatial_inpaint(frame0, fill, x, y, w, h, best=True)
        filled = comp
        fill_method = "temporal+telea" if temporal_cov > 0 else "telea"

    preview = feather_blend(frame0, filled, fill, 7)

    # ── Quality judgement → crop recommendation ──
    reasons = []
    weak = False
    if region_frac > _LARGE_REGION_FRAC:
        weak = True
        reasons.append("vùng logo quá lớn")
    if static and fill_method == "telea":
        weak = True
        reasons.append("nền phức tạp, lấp lại còn lem")
    if not static and temporal_cov < _WEAK_TEMPORAL_COV:
        weak = True
        reasons.append("chuyển động khó bám, dễ nhòe")

    if weak:
        reason = "Preview còn lem (" + ", ".join(reasons) + ") — nên chuyển sang Cắt viền."
    elif motion == "moving":
        reason = "Đã dùng chế độ chuyển động (lấy nền thật từ khung hình lân cận)."
    else:
        reason = "Nền tĩnh — tái tạo texture tự nhiên."

    return preview, {
        "strategy": strategy, "motion": motion, "fill_method": fill_method,
        "temporal_cov": round(temporal_cov, 3), "region_frac": round(region_frac, 4),
        "quality": "weak" if weak else "clean",
        "recommend_crop": weak, "reason": reason,
    }
