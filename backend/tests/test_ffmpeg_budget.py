"""
FFmpeg CPU budget tests
=======================
Every ffmpeg re-encode runs inside the API process, so an unbounded encode
starves ordinary requests. These pin the cap and the escape hatch.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "postgrest", "gotrue", "storage3",
    "app.core.database", "app.tasks.video_tasks",
):
    sys.modules.setdefault(_mod, MagicMock())


class TestThreadArgs:

    def _reload(self, value=None):
        env = {} if value is None else {"FFMPEG_THREADS": value}
        with patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("FFMPEG_THREADS", None)
            import app.core.ffmpeg_budget as mod
            return importlib.reload(mod)

    def test_defaults_to_one_thread(self):
        """Default must be a real cap, not ffmpeg's all-cores behaviour."""
        mod = self._reload()
        assert mod.thread_args() == ["-threads", "1"]

    def test_respects_env_override(self):
        mod = self._reload("3")
        assert mod.thread_args() == ["-threads", "3"]

    def test_zero_restores_ffmpeg_default(self):
        """Escape hatch: 0 emits no flag so ffmpeg picks its own thread count."""
        mod = self._reload("0")
        assert mod.thread_args() == []

    def test_negative_is_treated_as_disabled(self):
        mod = self._reload("-1")
        assert mod.thread_args() == []


class TestReencodePathsAreCapped:
    """Guards against a future edit dropping the cap from a re-encode command."""

    def test_watermark_caps_threads(self):
        import inspect
        import app.api.watermark as wm
        source = inspect.getsource(wm)
        assert "_ffmpeg_threads()" in source, (
            "watermark re-encodes video (drawtext/overlay) and must cap threads"
        )

    def test_merge_caps_threads_on_reencode(self):
        import inspect
        import app.api.merge as mg
        source = inspect.getsource(mg)
        assert "_ffmpeg_threads()" in source
        # The remux branch must stay untouched — capping threads there is
        # pointless and would only add noise to the command.
        libx264_at = source.index("libx264")
        copy_at = source.index('"-c", "copy"')
        assert copy_at < libx264_at, "expected the -c copy branch before re-encode"
