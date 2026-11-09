"""
Phase P5.4 tests — Held-out empty directory guard, Contact Sheet export & Temporal Flicker metric
=================================================================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np

from benchmarks.synthetic_hard_suite import (
    compute_temporal_flicker_in_mask,
    evaluate_suite,
    export_contact_sheet,
)


def test_empty_heldout_dir_returns_empty_without_downloading(tmp_path):
    """P5.4: When `real-tune/` or `real-heldout/` is empty, `evaluate_suite` stops cleanly and does not fetch external videos."""
    empty_in = tmp_path / "real-heldout"
    empty_lbl = tmp_path / "labels-real"
    empty_in.mkdir()
    empty_lbl.mkdir()

    res = evaluate_suite("real-heldout", empty_in, None, empty_lbl)
    assert res["status"] == "empty"
    assert res["video_count"] == 0
    assert list(empty_in.glob("*.mp4")) == []


def test_contact_sheet_and_temporal_flicker_in_mask(tmp_path):
    """P5.4: Verifies 6-frame before/after contact sheet generation and temporal flicker calculation inside mask."""
    before = [np.full((120, 160, 3), i * 10, dtype=np.uint8) for i in range(12)]
    after = [np.full((120, 160, 3), i * 10, dtype=np.uint8) for i in range(12)]
    cs_out = tmp_path / "sheet.jpg"
    saved = export_contact_sheet(before, after, cs_out, title="unit-test", n_samples=6)
    assert saved is not None
    assert os.path.isfile(saved) and os.path.getsize(saved) > 500

    boxes = {str(i): [20, 20, 40, 30] for i in range(12)}
    flicker = compute_temporal_flicker_in_mask(after, boxes)
    # Consecutive frames differ by exactly 10 intensity units inside the mask
    assert abs(flicker - 10.0) < 1e-3
