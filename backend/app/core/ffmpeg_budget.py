"""
FFmpeg CPU budget
=================
Every ffmpeg re-encode in this codebase runs synchronously inside the API
process (the `media` queue exists in celery_app.py but none of the tasks routed
to it were ever implemented, so watermark/merge never leave the request
handler). ffmpeg with no `-threads` starts one worker thread per detected CPU
and, being CPU-bound, then wins most of the scheduler against uvicorn's mostly
idle threads — so a couple of concurrent watermark requests make plain
downloads and even /health crawl.

Capping threads per process does not make one encode meaningfully slower on a
1.6-CPU container (there was never that much CPU to parallelise across), but it
stops a single encode from crowding out every other request.

FFMPEG_THREADS=0 restores ffmpeg's own default (all cores) if this ever needs
to be undone without a deploy.
"""

import os

_THREADS = int(os.getenv("FFMPEG_THREADS", "1"))


def thread_args() -> list[str]:
    """`-threads N` for an ffmpeg command, or [] when explicitly disabled."""
    if _THREADS <= 0:
        return []
    return ["-threads", str(_THREADS)]
