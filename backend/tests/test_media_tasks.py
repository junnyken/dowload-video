"""
Media worker dispatch tests
===========================
The watermark/merge endpoints used to run ffmpeg with subprocess.run() inside
the API process. These pin the new behaviour: the encode goes to the media
worker, the HTTP contract is unchanged, and losing the broker degrades CPU
isolation rather than taking the feature offline.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "postgrest", "gotrue", "storage3",
    "app.core.database",
):
    sys.modules.setdefault(_mod, MagicMock())


class TestRunFfmpeg:

    def test_returns_returncode_and_trimmed_stderr(self):
        from app.tasks.media_tasks import _run_ffmpeg
        completed = MagicMock(returncode=0, stderr="x" * 900)
        with patch("subprocess.run", return_value=completed):
            out = _run_ffmpeg(["ffmpeg"], 300)
        assert out["returncode"] == 0
        # Endpoints surfaced the last 500 chars to the client; keep that.
        assert len(out["stderr"]) == 500

    def test_timeout_becomes_a_failure_not_an_exception(self):
        """subprocess.TimeoutExpired used to escape as a 500 with no body."""
        from app.tasks.media_tasks import _run_ffmpeg
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 300)):
            out = _run_ffmpeg(["ffmpeg"], 300)
        assert out["returncode"] == -1
        assert "300" in out["stderr"]


class TestRunOnWorker:

    def test_dispatches_to_the_worker_and_returns_its_result(self):
        from app.tasks.media_tasks import run_on_worker
        task = MagicMock()
        task.delay.return_value.get.return_value = {"returncode": 0, "stderr": ""}

        with patch("app.tasks.media_tasks._run_ffmpeg") as inline:
            out = run_on_worker(task, ["ffmpeg", "-i", "a.mp4"], timeout=300)

        task.delay.assert_called_once_with(["ffmpeg", "-i", "a.mp4"], 300)
        inline.assert_not_called(), "must not also run ffmpeg in the API process"
        assert out["returncode"] == 0

    def test_waits_longer_than_the_ffmpeg_timeout(self):
        """The wait has to cover time spent queued behind another encode,
        otherwise a busy worker looks like a failure."""
        from app.tasks.media_tasks import run_on_worker, QUEUE_GRACE_SEC
        task = MagicMock()
        task.delay.return_value.get.return_value = {"returncode": 0, "stderr": ""}
        run_on_worker(task, ["ffmpeg"], timeout=300)
        _, kwargs = task.delay.return_value.get.call_args
        assert kwargs["timeout"] == 300 + QUEUE_GRACE_SEC

    def test_broker_down_falls_back_to_running_in_process(self):
        """Losing the worker must not take watermark/merge offline."""
        from app.tasks.media_tasks import run_on_worker
        task = MagicMock()
        task.delay.side_effect = OSError("broker unreachable")

        with patch("app.tasks.media_tasks._run_ffmpeg",
                   return_value={"returncode": 0, "stderr": ""}) as inline:
            out = run_on_worker(task, ["ffmpeg"], timeout=300)

        inline.assert_called_once()
        assert out["returncode"] == 0

    def test_worker_too_busy_returns_a_readable_failure(self):
        from app.tasks.media_tasks import run_on_worker
        task = MagicMock()
        task.delay.return_value.get.side_effect = TimeoutError("still queued")
        out = run_on_worker(task, ["ffmpeg"], timeout=300)
        assert out["returncode"] == -1
        assert out["stderr"], "a failure the user sees needs a message"


class TestQueueRouting:

    def test_task_names_match_the_media_queue_routes(self):
        """celery_app.py routes these exact names to the 'media' queue; a
        rename here would silently send them to the default queue."""
        from app.core.celery_app import celery_app
        routes = celery_app.conf.task_routes
        assert routes["watermark_task"]["queue"] == "media"
        assert routes["merge_audio_video_task"]["queue"] == "media"

    def test_media_tasks_module_is_registered_with_celery(self):
        from app.core.celery_app import celery_app
        assert "app.tasks.media_tasks" in celery_app.conf.include


class TestEndpointsNoLongerRunFfmpegInline:

    @pytest.mark.parametrize("module", ["app.api.watermark", "app.api.merge"])
    def test_no_direct_subprocess_run_of_ffmpeg(self, module):
        import importlib
        import inspect
        src = inspect.getsource(importlib.import_module(module))
        assert "run_on_worker(" in src
        # The short ffprobe duration reads stay inline on purpose — they are
        # metadata calls with a 10-15s timeout, not encodes. What must be gone
        # is the 300s encode.
        assert "subprocess.run(cmd, capture_output=True, text=True, timeout=300)" not in src, (
            f"{module} still runs the ffmpeg encode inside the API process"
        )
