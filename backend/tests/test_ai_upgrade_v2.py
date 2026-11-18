"""Tests for AI Upgrade v2 modules (anti_duplicate, self_healing, vietnamize, viral_clips)."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient


def test_anti_duplicate_levels_and_ffmpeg_cmds():
    from app.services.anti_duplicate import get_level_info, _build_ffmpeg_cmd

    levels = get_level_info()
    assert [lvl["id"] for lvl in levels] == ["light", "medium", "strong"]

    light_cmd = _build_ffmpeg_cmd("/tmp/in.mp4", "/tmp/out.mp4", level="light")
    assert "-c" in light_cmd and "copy" in light_cmd
    assert "-map_metadata" in light_cmd

    medium_cmd = _build_ffmpeg_cmd("/tmp/in.mp4", "/tmp/out.mp4", level="medium", has_audio=True)
    assert "-vf" in medium_cmd and "-af" in medium_cmd
    assert "-map" in medium_cmd and "0:a?" in medium_cmd
    assert "-an" not in medium_cmd

    strong_silent_cmd = _build_ffmpeg_cmd("/tmp/in.mp4", "/tmp/out.mp4", level="strong", has_audio=False)
    assert "-vf" in strong_silent_cmd
    assert "-af" not in strong_silent_cmd
    assert "-an" in strong_silent_cmd


def test_self_healing_threshold_reroute_and_clear(monkeypatch):
    from app.services import self_healing as sh

    monkeypatch.setattr(sh, "ENABLED", True)
    monkeypatch.setattr(sh, "TELEGRAM_ENABLED", False)
    monkeypatch.setattr(sh, "MIN_SAMPLES", 5)
    monkeypatch.setattr(sh, "ERROR_THRESHOLD", 30.0)
    monkeypatch.setattr(sh, "_diagnose_with_ai", lambda *args, **kwargs: None)

    hm = sh.HealthMonitor()
    diagnosis = None
    for i in range(5):
        diagnosis = hm.record_error(
            "youtube", "rate_limited", f"429 Too Many Requests #{i}",
            url_hash=f"h{i}", http_status=429, async_diagnose=False,
        )

    assert diagnosis is not None
    assert diagnosis["should_reroute"] is True
    assert hm.get_active_reroute("youtube") == "cobalt"

    hm.clear_reroute("youtube")
    assert hm.get_active_reroute("youtube") is None


def test_vietnamize_run_asr_and_tier_gate(monkeypatch, tmp_path):
    from app.api import vietnamize as vz
    from app.main import app
    from app.services.asr import Segment, TranscriptResult
    from app.services.subtitle_format import parse_srt

    class FakeProvider:
        def estimate_cost_usd(self, duration_sec: float, diarize: bool = False) -> float:
            return 0.0

        def transcribe(self, audio_path, *, language=None, diarize=False, duration_sec=None):
            return TranscriptResult(
                segments=[
                    Segment(start=0.5, end=2.5, text="Hello world"),
                    Segment(start=3.0, end=5.0, text="Testing subtitles"),
                ],
                language="en",
                duration_sec=float(duration_sec or 6.0),
            )

    monkeypatch.setenv("ASR_ENABLED", "true")
    monkeypatch.setattr("app.services.asr.get_provider", lambda name=None: FakeProvider())
    monkeypatch.setattr("app.services.asr.chunking.probe_duration_sec", lambda p: 6.0)
    monkeypatch.setattr("app.services.asr.budget.killswitch_on", lambda: False)

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"fake-mp3-data")

    srt_path = asyncio.run(vz._run_asr(str(audio_file), str(tmp_path), "auto"))
    with open(srt_path, "r", encoding="utf-8") as f:
        cues = parse_srt(f.read())
    assert len(cues) == 2
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Testing subtitles"

    client = TestClient(app)
    resp = client.post("/api/v1/process/vietnamize", json={"url": "https://example.com/v.mp4"})
    assert resp.status_code == 402


def test_viral_clips_helpers_and_tier_gate():
    from app.api import viral_clips as vc
    from app.main import app

    even = vc._fallback_even_clips(duration=180.0, max_clips=3, clip_duration=30)
    assert len(even) == 3
    assert all(0 <= c["start"] < c["end"] <= 180.0 for c in even)

    ai_clips = [{"start": 10.0, "end": 40.0, "confidence": 0.9, "label": "ai_hook"}]
    heur_clips = [
        {"start": 12.0, "end": 42.0, "confidence": 0.6, "label": "high_activity"},
        {"start": 80.0, "end": 110.0, "confidence": 0.7, "label": "speech_burst"},
    ]
    merged = vc._merge_clip_sources(heur_clips, ai_clips, max_clips=5)
    assert len(merged) == 2
    assert [m["start"] for m in merged] == [10.0, 80.0]

    client = TestClient(app)
    resp = client.post("/api/v1/process/viral-clips", json={"url": "https://example.com/v.mp4"})
    assert resp.status_code == 402


def test_anti_duplicate_levels_endpoint():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/process/anti-duplicate-levels")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["levels"]) == 3
