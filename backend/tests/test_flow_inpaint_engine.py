"""
Flow logo-cleanup engine — tests that actually run the engine
=============================================================
Every other test around this feature checks paperwork: error-code tables,
region validation, preset names, the file-extension whitelist, who is allowed
to call the endpoint. `test_inpaint_smoke.py` reads like coverage of the
engine and touches none of it. Before this file nothing in the tree called
process_video_natural, _telea_video, spatial_inpaint or decide_and_preview, so
the part that actually removes the logo could break in any way at all and the
suite stayed green.

These tests render real clips with ffmpeg and push them through the real
engine.

HOW CORRECTNESS IS MEASURED — and the trap that comes first
-----------------------------------------------------------
Each clip is generated TWICE from the same deterministic source: once clean
(ground truth) and once with a solid box drawn over the logo corner. The
engine runs on the dirty copy, and we compare its output against the clean
one inside the logo region. That gives a real number — how close did the
reconstruction get to the pixels that were really there.

The obvious cheaper check, "is the logo's colour gone from the region", is
a trap, and it is worth spelling out because it reported a working engine as
broken. `testsrc2` contains a magenta colour bar. Pan across it with a magenta
logo box and the colour keeps showing up in the region after a perfectly good
cleanup — not because the logo survived, but because the background there is
genuinely magenta. Ground truth has no such failure mode.

`_pairing_is_valid` guards the fixtures themselves: if the clean and dirty
renders ever stop matching OUTSIDE the logo region, the ground truth is not
ground truth and every number below is meaningless. The `gradients` lavfi
source fails exactly this check — it reseeds per render, so two runs differ
everywhere — which is why the static clip is built from a numpy image instead.

WHAT THE THRESHOLDS ARE, AND ARE NOT
------------------------------------
Measured on this hardware at the time of writing:

    natural / static   MAE 144.1 -> 16.8   (88.4% closer to truth)
    telea   / static   MAE 144.1 ->  8.1   (94.4%)
    natural / moving   MAE 156.6 -> 30.0   (80.9%)
    telea   / moving   MAE 156.6 -> 12.9   (91.8%)

The asserted floors sit well under those so ordinary codec and platform drift
cannot turn the suite red. They are a REGRESSION FLOOR: they catch "the engine
stopped removing the logo", "the mask drifted off the region", "a fallback now
ships the frame untouched".

They are NOT a quality ranking between methods, and the numbers above must not
be read as one. MAE rewards blur: these backgrounds are a smooth gradient and a
synthetic test pattern, and averaging the surrounding colour inward — which is
what TELEA does — is close to the right answer on both. SHIFTMAP synthesises
texture, which can be locally wrong per pixel while looking far better to a
person. Deciding which method looks better needs real footage and eyes on it,
not this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import numpy as np
import pytest

# conftest imports app.main for real, which is what keeps `from app.main import
# limiter` inside flow_cleanup from becoming a circular import here.
from app.api import flow_cleanup as fc
from app.api import flow_inpaint as fi

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="engine tests render real clips; ffmpeg/ffprobe not installed",
)

VW, VH = 320, 240
X, Y, W, H = 236, 188, 72, 44          # logo box, lower-right, inside the frame
FPS = 10.0
LOGO_COLOR = "0x00FF00"


# ── fixtures: paired clean/dirty clips ───────────────────────────────

def _render(path: str, source_args: list, vf: str) -> None:
    """Render one clip losslessly (-qp 0) so the pair differs only by the box."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    cmd += source_args
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _drawbox() -> str:
    return f"drawbox=x={X}:y={Y}:w={W}:h={H}:color={LOGO_COLOR}@1.0:t=fill"


def _frames(path: str) -> list:
    """Decode a clip to a list of BGR frames."""
    r = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path,
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{VW}x{VH}", "pipe:1"],
        capture_output=True, timeout=120,
    )
    fsz = VW * VH * 3
    return [
        np.frombuffer(r.stdout[i * fsz:(i + 1) * fsz], dtype=np.uint8)
          .reshape((VH, VW, 3)).copy()
        for i in range(len(r.stdout) // fsz)
    ]


@pytest.fixture(scope="module")
def static_clip(tmp_path_factory):
    """A still, textured background. Deterministic by construction.

    Built from a numpy image rather than an lavfi generator on purpose: see the
    `gradients` note in the module docstring.
    """
    import cv2
    d = tmp_path_factory.mktemp("static")
    yy, xx = np.mgrid[0:VH, 0:VW].astype(np.float32)
    tex = 12 * np.sin(xx / 7.0) * np.cos(yy / 9.0)
    img = np.clip(np.dstack([
        40 + xx * 0.35 + yy * 0.20 + tex,
        60 + yy * 0.45 + tex,
        90 + xx * 0.25 + tex,
    ]), 0, 255).astype(np.uint8)
    bg = str(d / "bg.png")
    cv2.imwrite(bg, img)

    src = ["-loop", "1", "-i", bg, "-t", "2", "-r", "10"]
    clean, dirty = str(d / "clean.mp4"), str(d / "dirty.mp4")
    _render(clean, src, "null")
    _render(dirty, src, _drawbox())
    return {"clean": clean, "dirty": dirty, "duration": 2.0, "dir": str(d)}


@pytest.fixture(scope="module")
def moving_clip(tmp_path_factory):
    """A panning background — the case the temporal path exists for.

    The logo sits at a fixed screen position while the scene slides under it,
    so neighbouring frames really do hold the background it covers.
    """
    d = tmp_path_factory.mktemp("moving")
    src = ["-f", "lavfi", "-i", "testsrc2=s=640x480:d=3:r=10"]
    pan = r"crop=320:240:x='min(t*60\,300)':y=60"
    clean, dirty = str(d / "clean.mp4"), str(d / "dirty.mp4")
    _render(clean, src, pan)
    _render(dirty, src, pan + "," + _drawbox())
    return {"clean": clean, "dirty": dirty, "duration": 3.0, "dir": str(d)}


# ── measurement helpers ──────────────────────────────────────────────

def _region(frame): return frame[Y:Y + H, X:X + W].astype(np.int32)


def _mae(a, b) -> float:
    return float(np.abs(_region(a) - _region(b)).mean())


def _pairing_is_valid(clean_frames, dirty_frames) -> float:
    """Mean difference OUTSIDE the logo box. Near zero, or the pair is a lie."""
    outside = np.ones((VH, VW), bool)
    outside[Y:Y + H, X:X + W] = False
    n = min(len(clean_frames), len(dirty_frames))
    return float(np.mean([
        np.abs(clean_frames[i][outside].astype(np.int32)
               - dirty_frames[i][outside].astype(np.int32)).mean()
        for i in range(n)
    ]))


def _closeness(clip, cleaned_path) -> dict:
    truth, dirty, clean = _frames(clip["clean"]), _frames(clip["dirty"]), _frames(cleaned_path)
    assert clean, "engine produced no decodable frames"
    pairing = _pairing_is_valid(truth, dirty)
    assert pairing < 1.0, (
        f"fixture pair differs outside the logo box (MAE {pairing:.3f}) — "
        "the ground truth is not ground truth, so no result below means anything"
    )
    n = min(len(truth), len(dirty), len(clean))
    before = float(np.mean([_mae(dirty[i], truth[i]) for i in range(n)]))
    after = float(np.mean([_mae(clean[i], truth[i]) for i in range(n)]))
    darkest = max((_region(clean[i]).sum(axis=2) < 24).mean() for i in range(n))
    return {
        "before": before,
        "after": after,
        "improvement": 1.0 - (after / before),
        "dark_fraction": float(darkest),
        "frames_in": len(dirty),
        "frames_out": len(clean),
    }


def _probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, timeout=60,
    )
    assert r.returncode == 0, "ffprobe cannot read the engine's output"
    for s in json.loads(r.stdout).get("streams", []):
        if s.get("codec_type") == "video":
            return s
    raise AssertionError("engine output has no video stream")


# ═══════════════════════════════════════════════════════════════════
# A — the natural engine (SHIFTMAP + temporal), the production default
# ═══════════════════════════════════════════════════════════════════

class TestNaturalEngine:

    def test_static_background_reconstructs_what_the_logo_covered(self, static_clip):
        out = os.path.join(static_clip["dir"], "natural.mp4")
        stats = fi.process_video_natural(
            static_clip["dirty"], out, X, Y, W, H, VW, VH,
            fps=FPS, duration=static_clip["duration"],
        )
        m = _closeness(static_clip, out)
        assert stats["mode"] == "static_patch", (
            "a still background must take the reuse-one-patch path; "
            f"got {stats['mode']}"
        )
        assert m["improvement"] > 0.70, (
            f"logo region only got {m['improvement']:.1%} closer to truth "
            f"(MAE {m['before']:.1f} -> {m['after']:.1f}); measured 88.4% when written"
        )

    def test_moving_background_recovers_real_pixels_from_neighbours(self, moving_clip):
        out = os.path.join(moving_clip["dir"], "natural.mp4")
        stats = fi.process_video_natural(
            moving_clip["dirty"], out, X, Y, W, H, VW, VH,
            fps=FPS, duration=moving_clip["duration"],
        )
        m = _closeness(moving_clip, out)
        assert stats["mode"] == "per_frame", (
            f"a panning scene must not reuse one frozen patch; got {stats['mode']}"
        )
        assert stats["temporal_frames"] > 0, (
            "no frame used motion-compensated fill — the temporal path, which is "
            "the whole reason this engine exists for moving footage, did nothing"
        )
        assert m["improvement"] > 0.60, (
            f"logo region only got {m['improvement']:.1%} closer to truth "
            f"(MAE {m['before']:.1f} -> {m['after']:.1f}); measured 80.9% when written"
        )

    @pytest.mark.parametrize("clip_name", ["static_clip", "moving_clip"])
    def test_never_ships_a_black_box(self, clip_name, request):
        """The fallback chain promises TELEA over a black hole. Hold it to that.

        A degenerate SHIFTMAP fill leaves an unfilled black core, and shipping
        that is worse than the logo: the viewer sees a censor bar.
        """
        clip = request.getfixturevalue(clip_name)
        out = os.path.join(clip["dir"], "black_check.mp4")
        fi.process_video_natural(
            clip["dirty"], out, X, Y, W, H, VW, VH,
            fps=FPS, duration=clip["duration"],
        )
        m = _closeness(clip, out)
        assert m["dark_fraction"] < 0.02, (
            f"{m['dark_fraction']:.1%} of the logo region came out near-black "
            "— that is a censor bar, not a cleanup"
        )

    def test_output_keeps_every_frame_and_stays_playable(self, static_clip):
        out = os.path.join(static_clip["dir"], "playable.mp4")
        fi.process_video_natural(
            static_clip["dirty"], out, X, Y, W, H, VW, VH,
            fps=FPS, duration=static_clip["duration"],
        )
        m = _closeness(static_clip, out)
        assert m["frames_out"] == m["frames_in"], (
            f"engine dropped frames: {m['frames_in']} in, {m['frames_out']} out"
        )
        stream = _probe(out)
        assert (stream["width"], stream["height"]) == (VW, VH), (
            "cleanup must not resize the video — only crop mode may do that"
        )

    def test_survives_without_xphoto(self, static_clip, monkeypatch):
        """opencv-contrib is an unpinned dependency; SHIFTMAP can vanish.

        `_shiftmap_fill` already returns None when cv2.xphoto is missing. What
        was never checked is that the rest of the chain then still produces a
        usable video rather than a black box or a crash.
        """
        monkeypatch.delattr(fi.cv2, "xphoto", raising=False)
        out = os.path.join(static_clip["dir"], "no_xphoto.mp4")
        fi.process_video_natural(
            static_clip["dirty"], out, X, Y, W, H, VW, VH,
            fps=FPS, duration=static_clip["duration"],
        )
        m = _closeness(static_clip, out)
        assert m["dark_fraction"] < 0.02, "fallback shipped a black region"
        assert m["improvement"] > 0.50, (
            f"fallback barely helped ({m['improvement']:.1%} closer to truth)"
        )


# ═══════════════════════════════════════════════════════════════════
# B — TELEA, the fast path still reachable from the API
# ═══════════════════════════════════════════════════════════════════

class TestTeleaEngine:

    @pytest.mark.parametrize("soft", [False, True], ids=["telea", "telea_soft"])
    def test_removes_the_logo(self, static_clip, soft):
        out = os.path.join(static_clip["dir"], f"telea_{int(soft)}.mp4")
        mask = fc._build_mask(X, Y, W, H, VW, VH)
        fc._telea_video(
            static_clip["dirty"], out, mask, X, Y, W, H, VW, VH, FPS, soft=soft,
        )
        m = _closeness(static_clip, out)
        assert m["improvement"] > 0.80, (
            f"TELEA only got {m['improvement']:.1%} closer to truth "
            f"(MAE {m['before']:.1f} -> {m['after']:.1f}); measured 94.4% when written"
        )
        assert m["frames_out"] == m["frames_in"]

    def test_cleans_up_its_intermediate_file(self, static_clip):
        """_telea_video writes <output>.noaudio.mp4 and must not leave it behind."""
        out = os.path.join(static_clip["dir"], "telea_tmp.mp4")
        mask = fc._build_mask(X, Y, W, H, VW, VH)
        fc._telea_video(static_clip["dirty"], out, mask, X, Y, W, H, VW, VH, FPS)
        assert not os.path.exists(out + ".noaudio.mp4"), (
            "intermediate left on disk — these accumulate under downloads/ "
            "until the job TTL sweeps the directory"
        )


# ═══════════════════════════════════════════════════════════════════
# C — the preview the user actually decides on
# ═══════════════════════════════════════════════════════════════════

class TestPreview:

    @pytest.mark.parametrize("clip_name", ["static_clip", "moving_clip"])
    def test_preview_frame_is_cleaned_and_describes_its_strategy(self, clip_name, request):
        """/preview-frame promises the preview reflects what the final will do.

        If the preview is clean but the final is not (or the other way round),
        the user approves one thing and receives another.
        """
        clip = request.getfixturevalue(clip_name)
        truth = _frames(clip["clean"])[0]
        dirty = _frames(clip["dirty"])[0]

        preview, info = fi.decide_and_preview(
            clip["dirty"], X, Y, W, H, VW, VH, clip["duration"], FPS,
        )
        assert preview.shape == (VH, VW, 3), "preview must be a full frame"
        assert "strategy" in info and info["strategy"], "preview did not report a strategy"

        before = _mae(dirty, truth)
        after = _mae(preview, truth)
        assert after < before * 0.5, (
            f"preview frame still looks like the logo is there "
            f"(MAE {before:.1f} -> {after:.1f})"
        )


# ═══════════════════════════════════════════════════════════════════
# D — mask geometry the endpoints hand the engine
# ═══════════════════════════════════════════════════════════════════

class TestMaskGeometry:

    def test_dilation_covers_more_than_the_box_but_stays_in_frame(self):
        base = fi._build_fill_mask(X, Y, W, H, VW, VH)
        grown = fi.dilate_fill_mask(base, 12)
        assert grown.shape == (VH, VW)
        assert int((grown > 0).sum()) > int((base > 0).sum()), (
            "dilation did nothing — anti-aliased logo fringes stay behind"
        )
        assert int(grown[:Y - 13, :].sum()) == 0, "dilation leaked far above the box"

    def test_region_is_clean_rejects_a_black_core(self):
        cmask = np.zeros((H, W), np.uint8)
        cmask[:] = 255
        assert not fi._region_is_clean(np.zeros((H, W, 3), np.uint8), cmask), (
            "an all-black fill was accepted — this is the guard that stops the "
            "engine shipping a censor bar"
        )
        rng = np.random.default_rng(0)
        textured = rng.integers(40, 200, size=(H, W, 3), dtype=np.uint8)
        assert fi._region_is_clean(textured, cmask), "a good fill was rejected"
