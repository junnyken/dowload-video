"""
Tests for Phase B5 — Live Stream Recorder (`live_recorder.py` & `live_record.py`)
=================================================================================
All subprocess calls are mocked; no live network or real ffmpeg/yt-dlp processes run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import live_record
from app.services import live_recorder as lr


class FakeStdin:
    def __init__(self):
        self.written = bytearray()

    def write(self, data: bytes):
        self.written.extend(data)

    async def drain(self):
        return None


class FakeProcess:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.stdin = FakeStdin()
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def terminate(self):
        # Simulate ffmpeg finalizing the output MP4 file on stop
        Path(self.output_path).write_bytes(b"\x00\x00\x00\x18ftypmp42_mock_live_stream_data")
        self.returncode = 0
        self._done.set()

    def kill(self):
        self.returncode = -9
        self._done.set()


@pytest.fixture(autouse=True)
def _clean_live_jobs(tmp_path, monkeypatch):
    lr.clear_jobs_for_test()
    monkeypatch.setattr(lr, "_DOWNLOADS_DIR", str(tmp_path))
    yield
    lr.clear_jobs_for_test()


def test_detect_live_platforms_and_plan_caps():
    assert lr.detect_live_platform("https://www.tiktok.com/@creator/live") == "tiktok"
    assert lr.detect_live_platform("https://www.youtube.com/live/dQw4w9WgXcQ") == "youtube"
    assert lr.detect_live_platform("https://www.twitch.tv/shroud") == "twitch"
    assert lr.detect_live_platform("https://kick.com/xqc") == "kick"
    assert lr.detect_live_platform("https://www.facebook.com/watch/live/?v=12345") == "facebook"
    assert lr.detect_live_platform("https://example.com/not-supported") is None

    # Plan duration limits: Free=600s (10m), Pro=3600s (60m), Team=10800s (180m)
    assert lr.max_duration_for_plan("free") == 600
    assert lr.max_duration_for_plan("pro") == 3600
    assert lr.max_duration_for_plan("team") == 10800

    # Clamp enforces plan cap
    assert lr.clamp_duration(99999, "free") == 600
    assert lr.clamp_duration(1800, "pro") == 1800
    assert lr.clamp_duration(99999, "pro") == 3600
    assert lr.clamp_duration(7200, "team") == 7200


def test_live_record_api_start_status_stop(monkeypatch):
    spawned_cmds: list[list[str]] = []

    async def fake_spawn(cmd: list[str]):
        spawned_cmds.append(cmd)
        out_idx = cmd.index("-o") + 1
        return FakeProcess(cmd[out_idx])

    monkeypatch.setattr(lr, "_spawn_recorder_process", fake_spawn)

    app = FastAPI()
    app.include_router(live_record.router, prefix="/api/v1/live")
    client = TestClient(app)

    # 1. Start recording on Free plan requesting 3600s -> clamped to 600s
    r_start = client.post("/api/v1/live/start", json={
        "url": "https://www.tiktok.com/@streamer/live",
        "duration_sec": 3600,
        "plan": "free",
    })
    assert r_start.status_code == 200
    start_data = r_start.json()
    job_id = start_data["job_id"]
    assert start_data["platform"] == "tiktok"
    assert start_data["status"] == "recording"
    assert start_data["max_duration_sec"] == 600
    assert len(spawned_cmds) == 1
    assert "ffmpeg:-t 600" in spawned_cmds[0]

    # 2. Poll status while recording
    r_status = client.get(f"/api/v1/live/{job_id}/status")
    assert r_status.status_code == 200
    assert r_status.json()["status"] == "recording"

    # 3. Stop recording -> finalizes MP4 and returns download_url
    r_stop = client.post(f"/api/v1/live/{job_id}/stop")
    assert r_stop.status_code == 200
    stop_data = r_stop.json()
    assert stop_data["status"] == "completed"
    assert stop_data["download_url"] is not None
    assert "/api/v1/download-local" in stop_data["download_url"]
