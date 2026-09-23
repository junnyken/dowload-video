#!/usr/bin/env python3
"""
Tune the logo-cleanup mask dilation on REAL footage.

Why this exists
---------------
process_video_natural() grows the region the user selected by a fixed
dilate_px=12 before filling it. That constant is the single biggest lever on
how the result looks, and it is a guess — nothing in the engine measures the
logo it is being applied to.

Measured on synthetic clips (tests/test_flow_inpaint_engine.py builds the same
kind), against real ground truth:

    hard-edged box     0px -> 92% closer to truth,  12px -> 81%
    soft/translucent   0px -> 42%,  6px -> 69%,  12px -> 64%,  20px -> 57%

So dilation earns its keep on a soft-edged watermark — without it the faded
fringe survives — and costs real accuracy on a crisp one. The optimum moves
with how soft the logo's edge is, which is exactly what a constant cannot do.
Picking a better constant, or making it adaptive, needs numbers from the
footage people actually bring: this script produces them.

What it can and cannot tell you
-------------------------------
On real footage there is no clean copy to compare against, so the honest
answer is not a single score. This reports three proxies and writes out the
crops so a person can look:

  seam     gradient energy in a thin ring at the patch boundary, over the same
           in surrounding background. ~1 means the edge is invisible; well
           above 1 means a visible rectangle — usually too LITTLE dilation
           (logo fringe still there) or a patch that does not match.
  texture  gradient energy inside the patch over background. Near 0 means the
           fill is a smooth smear where the background has detail; near 1
           means it carries comparable structure.
  flicker  frame-to-frame change inside the patch over the same outside. Well
           above 1 means the patch is unstable and will shimmer on playback.
  dark     fraction of near-black pixels. Anything above ~0.02 is a censor bar.

Lower seam and flicker are better; texture closer to 1 is better. They
disagree sometimes — that is the point, look at the PNGs.

How far to trust the columns: run against the soft-edged synthetic clip, where
ground truth says 0px is clearly worst and 6px best, `seam` agreed on the part
that matters (orig 4.47 -> 0px 2.99 -> 6px 1.71 -> 12px 1.53) but ranked 12px
a shade above 6px, the reverse of ground truth. So these proxies reliably
separate "fringe still visible" from "fringe gone", and do NOT resolve
neighbouring values inside the good band. Use them to find the band, then pick
inside it with your eyes on the crops.

Usage
-----
    python scripts/tune-logo-dilation.py VIDEO --region X Y W H
    python scripts/tune-logo-dilation.py VIDEO --preset lower-right
    python scripts/tune-logo-dilation.py VIDEO --preset lower-right \
        --dilations 0,4,8,12,16 --out /tmp/tune --seconds 4

Crops land in --out as region_d<N>.png plus original.png.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import cv2                                            # noqa: E402
from app.api import flow_inpaint as fi                # noqa: E402


def probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, timeout=60,
    )
    if r.returncode != 0:
        raise SystemExit(f"ffprobe cannot read {path}")
    for s in json.loads(r.stdout).get("streams", []):
        if s.get("codec_type") == "video":
            num, den = (s.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
            return {
                "width": int(s["width"]), "height": int(s["height"]),
                "duration": float(s.get("duration", 0) or 0),
                "fps": round(int(num) / max(int(den), 1), 2),
            }
    raise SystemExit("no video stream")


def decode(path: str, vw: int, vh: int, limit: int | None = None) -> list:
    r = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path,
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1"],
        capture_output=True, timeout=600,
    )
    fsz = vw * vh * 3
    n = len(r.stdout) // fsz
    if limit:
        n = min(n, limit)
    return [
        np.frombuffer(r.stdout[i * fsz:(i + 1) * fsz], dtype=np.uint8)
          .reshape((vh, vw, 3)).copy()
        for i in range(n)
    ]


def preset_region(preset: str, vw: int, vh: int) -> tuple:
    """Mirrors _resolve_region in flow_cleanup.py — keep the two in step."""
    mx, my = max(int(vw * 0.16), 80), max(int(vh * 0.10), 40)
    px, py = max(int(vw * 0.02), 8), max(int(vh * 0.02), 8)
    table = {
        "lower-right": (vw - mx - px, vh - my - py, mx, my),
        "lower-left":  (px,           vh - my - py, mx, my),
        "upper-right": (vw - mx - px, py,           mx, my),
        "upper-left":  (px,           py,           mx, my),
    }
    if preset not in table:
        raise SystemExit(f"unknown preset {preset}; pick one of {', '.join(table)}")
    return table[preset]


def _grad(img) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))


def measure(frames: list, x: int, y: int, w: int, h: int, dilate: int) -> dict:
    """Proxies for how the patch reads, with no ground truth available."""
    vh, vw = frames[0].shape[:2]
    inner = np.zeros((vh, vw), np.uint8)
    inner[y:y + h, x:x + w] = 255
    grown = fi.dilate_fill_mask(inner, dilate) if dilate else inner
    ring = cv2.dilate(grown, np.ones((9, 9), np.uint8)) - cv2.erode(grown, np.ones((9, 9), np.uint8))
    outer = cv2.dilate(grown, np.ones((81, 81), np.uint8)) - cv2.dilate(grown, np.ones((17, 17), np.uint8))

    seam, tex, dark = [], [], []
    for f in frames:
        g = _grad(f)
        bg = float(g[outer > 0].mean()) or 1e-6
        seam.append(float(g[ring > 0].mean()) / bg)
        tex.append(float(g[grown > 0].mean()) / bg)
        reg = f[y:y + h, x:x + w].astype(np.int32)
        dark.append(float((reg.sum(axis=2) < 24).mean()))

    flick = []
    for i in range(1, len(frames)):
        d = np.abs(frames[i].astype(np.int32) - frames[i - 1].astype(np.int32)).sum(axis=2)
        bg = float(d[outer > 0].mean()) or 1e-6
        flick.append(float(d[grown > 0].mean()) / bg)

    return {
        "seam": round(float(np.mean(seam)), 3),
        "texture": round(float(np.mean(tex)), 3),
        "flicker": round(float(np.mean(flick)) if flick else 0.0, 3),
        "dark": round(float(np.max(dark)), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--preset", help="lower-right | lower-left | upper-right | upper-left")
    ap.add_argument("--dilations", default="0,4,8,12,16,20")
    ap.add_argument("--seconds", type=float, default=4.0, help="how much of the clip to run")
    ap.add_argument("--out", default="/tmp/logo-tune")
    a = ap.parse_args()

    if not (a.region or a.preset):
        ap.error("give --region X Y W H or --preset")

    info = probe(a.video)
    vw, vh = info["width"], info["height"]
    x, y, w, h = tuple(a.region) if a.region else preset_region(a.preset, vw, vh)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise SystemExit(f"region {x},{y} {w}x{h} does not fit in {vw}x{vh}")

    dur = min(a.seconds, info["duration"] or a.seconds)
    os.makedirs(a.out, exist_ok=True)

    src = a.video
    if info["duration"] and info["duration"] > dur:
        src = os.path.join(a.out, "clip.mp4")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", a.video, "-t", str(dur), "-c:v", "libx264", "-qp", "0",
                        "-pix_fmt", "yuv420p", "-an", src], check=True, timeout=600)

    original = decode(src, vw, vh)
    if not original:
        raise SystemExit("could not decode the clip")
    cv2.imwrite(os.path.join(a.out, "original.png"), original[0][y:y + h, x:x + w])

    print(f"{os.path.basename(a.video)}  {vw}x{vh} @ {info['fps']}fps  "
          f"region {x},{y} {w}x{h}  {len(original)} frames\n")
    print(f"{'dilate':>6}  {'seam':>6}  {'texture':>7}  {'flicker':>7}  {'dark':>6}   (lower seam/flicker better)")
    print(f"{'orig':>6}  " + "  ".join(f"{v:>6}" if k != 'texture' else f"{v:>7}"
          for k, v in measure(original, x, y, w, h, 0).items()))

    for d in [int(v) for v in a.dilations.split(",") if v.strip()]:
        out = os.path.join(a.out, f"cleaned_d{d}.mp4")
        stats = fi.process_video_natural(
            src, out, x, y, w, h, vw, vh,
            fps=info["fps"] or 30.0, duration=dur,
            dilate_px=d, feather_px=min(7, max(0, d - 5)),
        )
        fr = decode(out, vw, vh)
        if not fr:
            print(f"{d:>6}  (engine produced nothing)")
            continue
        cv2.imwrite(os.path.join(a.out, f"region_d{d}.png"), fr[0][y:y + h, x:x + w])
        m = measure(fr, x, y, w, h, d)
        print(f"{d:>6}  {m['seam']:>6}  {m['texture']:>7}  {m['flicker']:>7}  {m['dark']:>6}"
              f"   [{stats['mode']}, temporal {stats['temporal_frames']}/{stats['total_frames']}]")

    print(f"\ncrops written to {a.out} — look at them before trusting any column above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
