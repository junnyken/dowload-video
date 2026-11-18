"""
Live Stream Recorder Service — Phase B5
=======================================
Records ongoing live streams (TikTok Live, YouTube Live, Twitch, Kick, Facebook Live)
into downloadable MP4 files using non-blocking `asyncio.create_subprocess_exec`
with `yt-dlp` / `ffmpeg -t <effective_duration>`.

Plan-based maximum recording duration:
  - Free / Guest:        600s   (10 minutes)
  - Pro:                3600s   (60 minutes)
  - Team / Enterprise: 10800s  (180 minutes)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Plan-based max duration caps (in seconds)
PLAN_MAX_DURATION_SEC: dict[str, int] = {
    "guest":      600,     # 10 min
    "free":       600,     # 10 min
    "starter":    1200,    # 20 min
    "pro":        3600,    # 60 min
    "team":       10800,   # 180 min
    "enterprise": 10800,   # 180 min
    "api":        10800,   # 180 min
    "admin":      10800,   # 180 min
}

_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)

# Supported live platforms & URL patterns
_LIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("tiktok",   re.compile(r"tiktok\.com/@[^/?#\s]+/live", re.I)),
    ("youtube",  re.compile(r"(?:youtube\.com/(?:watch\?.*v=|live/|@[^/?#\s]+/live)|youtu\.be/)", re.I)),
    ("twitch",   re.compile(r"twitch\.tv/[A-Za-z0-9_]+", re.I)),
    ("kick",     re.compile(r"kick\.com/[A-Za-z0-9_-]+", re.I)),
    ("facebook", re.compile(r"(?:facebook\.com|fb\.watch)/", re.I)),
]


def detect_live_platform(url: str) -> Optional[str]:
    """Return platform slug if URL belongs to a supported live stream platform."""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return None
    for platform, pat in _LIVE_PATTERNS:
        if pat.search(u):
            return platform
    return None


def max_duration_for_plan(plan: Optional[str]) -> int:
    """Return maximum allowed recording duration in seconds for a user's plan."""
    tier = (plan or "free").strip().lower()
    return PLAN_MAX_DURATION_SEC.get(tier, PLAN_MAX_DURATION_SEC["free"])


def clamp_duration(requested_sec: Optional[int], plan: Optional[str]) -> int:
    """Clamp requested duration to [10, max_duration_for_plan(plan)]."""
    cap = max_duration_for_plan(plan)
    if requested_sec is None or requested_sec <= 0:
        return cap
    return max(10, min(int(requested_sec), cap))


def build_live_record_command(
    url: str,
    output_path: str,
    max_duration_sec: int,
    *,
    live_from_start: bool = False,
) -> list[str]:
    """
    Build the command line for `asyncio.create_subprocess_exec`.
    Uses `yt-dlp` with `--downloader ffmpeg` and `--downloader-args ffmpeg:-t <max_duration_sec>`
    so recording stops cleanly when the plan duration cap is reached.
    """
    cmd = [
        "yt-dlp",
        "--no-part",
        "--no-playlist",
        "--hls-use-mpegts",
        "--downloader", "ffmpeg",
        "--downloader-args", f"ffmpeg:-t {int(max_duration_sec)}",
        "--merge-output-format", "mp4",
        "-o", output_path,
    ]
    if live_from_start:
        cmd.append("--live-from-start")
    cmd.append(url)
    return cmd


@dataclass
class LiveRecordJob:
    job_id: str
    url: str
    platform: str
    user_id: Optional[str]
    plan: str
    max_duration_sec: int
    output_path: str
    status: str = "recording"      # "recording" | "stopping" | "completed" | "failed"
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    error: Optional[str] = None
    download_url: Optional[str] = None
    process: Optional[Any] = field(default=None, repr=False)
    monitor_task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def elapsed_sec(self) -> int:
        end = self.ended_at or time.time()
        return max(0, int(end - self.started_at))

    @property
    def file_size_mb(self) -> float:
        try:
            if self.output_path and os.path.exists(self.output_path):
                return round(os.path.getsize(self.output_path) / (1024 * 1024), 2)
        except Exception:
            pass
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "platform": self.platform,
            "plan": self.plan,
            "status": self.status,
            "max_duration_sec": self.max_duration_sec,
            "elapsed_sec": self.elapsed_sec,
            "file_size_mb": self.file_size_mb,
            "download_url": self.download_url,
            "error": self.error,
        }


# Active/completed live record jobs in process memory
_JOBS: dict[str, LiveRecordJob] = {}


async def _spawn_recorder_process(cmd: list[str]) -> asyncio.subprocess.Process:
    """
    Spawn async subprocess using `asyncio.create_subprocess_exec`.
    Extracted into a helper so unit tests can mock it without spawning real processes.
    """
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def _issue_download_url(output_path: str, platform: str, job_id: str) -> Optional[str]:
    if not output_path or not os.path.exists(output_path):
        return None
    try:
        from app.core.local_download import new_token
        token = new_token(output_path, f"live_{platform}_{job_id[:8]}.mp4", ttl=3600)
        return f"/api/v1/download-local?token={token}"
    except Exception:
        return f"/api/v1/download-local?file={os.path.basename(output_path)}"


async def _watch_job(job: LiveRecordJob) -> None:
    """Wait for the recorder subprocess or max_duration timeout and finalize status."""
    proc = job.process
    if proc is None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=job.max_duration_sec + 15)
    except asyncio.TimeoutError:
        await stop_live_recording(job.job_id)
        return
    except asyncio.CancelledError:
        return
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.ended_at = time.time()
        return

    job.ended_at = time.time()
    if job.status == "stopping":
        job.status = "completed"
    elif proc.returncode == 0 or (job.output_path and os.path.exists(job.output_path) and os.path.getsize(job.output_path) > 0):
        job.status = "completed"
    else:
        stderr_text = ""
        if proc.stderr:
            try:
                raw_err = await proc.stderr.read()
                stderr_text = raw_err.decode("utf-8", errors="replace").strip()[-300:]
            except Exception:
                pass
        job.status = "failed"
        job.error = stderr_text or f"Quá trình ghi livestream kết thúc với mã {proc.returncode}"

    if job.status == "completed":
        job.download_url = _issue_download_url(job.output_path, job.platform, job.job_id)


async def start_live_recording(
    url: str,
    *,
    plan: str = "free",
    user_id: Optional[str] = None,
    duration_sec: Optional[int] = None,
    live_from_start: bool = False,
    output_dir: Optional[str] = None,
) -> LiveRecordJob:
    """
    Validate URL, enforce plan duration cap, and start async live recording subprocess.
    """
    platform = detect_live_platform(url)
    if not platform:
        raise ValueError("URL livestream không được hỗ trợ. Hỗ trợ: TikTok Live, YouTube Live, Twitch, Kick, Facebook Live.")

    effective_duration = clamp_duration(duration_sec, plan)
    target_dir = output_dir or _DOWNLOADS_DIR
    os.makedirs(target_dir, exist_ok=True)

    job_id = uuid.uuid4().hex[:16]
    output_path = os.path.join(target_dir, f"live_{platform}_{job_id}.mp4")

    cmd = build_live_record_command(
        url,
        output_path,
        effective_duration,
        live_from_start=live_from_start,
    )

    proc = await _spawn_recorder_process(cmd)
    job = LiveRecordJob(
        job_id=job_id,
        url=url,
        platform=platform,
        user_id=user_id,
        plan=(plan or "free").lower(),
        max_duration_sec=effective_duration,
        output_path=output_path,
        status="recording",
        process=proc,
    )
    _JOBS[job_id] = job
    job.monitor_task = asyncio.create_task(_watch_job(job))
    return job


async def stop_live_recording(job_id: str) -> Optional[LiveRecordJob]:
    """
    Gracefully stop an active live recording job (sends 'q' / terminate to ffmpeg/yt-dlp)
    so the MP4 moov atom is finalized properly.
    """
    job = _JOBS.get(job_id)
    if not job:
        return None

    if job.status in ("completed", "failed"):
        return job

    job.status = "stopping"
    proc = job.process
    if proc is not None and proc.returncode is None:
        # Send 'q\n' to stdin so ffmpeg finalizes the MP4 container cleanly
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                await proc.stdin.drain()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

    job.ended_at = time.time()
    job.status = "completed"
    job.download_url = _issue_download_url(job.output_path, job.platform, job.job_id)
    return job


def get_live_recording_status(job_id: str) -> Optional[LiveRecordJob]:
    return _JOBS.get(job_id)


def clear_jobs_for_test() -> None:
    _JOBS.clear()
