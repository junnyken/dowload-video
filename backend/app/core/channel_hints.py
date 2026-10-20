"""
Follow-up hints on a finished channel scan (20/10).

A Douyin profile listed from the server stops at the visitor's first page
(~41 videos) even when the channel has more; the VidGrab extension can list
the rest by scrolling the profile in the user's own browser. The scan task
records the hint against the channel row, GET /jobs/{batch_id} returns it as
`channel_hint`, and the web shows the matching call to action.

Kept in Redis rather than a new download_jobs column: it only matters while
the user is looking at that batch.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

_KEY = "channel:hint:{}"
_TTL_S = 7 * 24 * 3600

# wording: BA review
_SENTENCES = {
    "douyin_extension": (
        "Kênh này còn nhiều video hơn nhưng máy chủ chỉ lấy được những video mới nhất. "
        "Muốn lấy đầy đủ, hãy mở trang kênh trên Douyin và dùng tiện ích VidGrab."
    ),
}


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def hint_sentence(hint: str) -> str:
    return _SENTENCES.get(hint, "")


def save_channel_hint(job_id: str, hint: str, rc=None) -> None:
    if hint not in _SENTENCES or not job_id:
        return
    try:
        (rc or _r()).set(_KEY.format(job_id), hint, ex=_TTL_S)
    except Exception as exc:  # noqa: BLE001 — a missing hint only hides a button
        print(f"[ChannelHint] save failed: {type(exc).__name__}")


def channel_hints(job_ids: Iterable[str], rc=None) -> Dict[str, str]:
    """{job_id: hint} for the rows that have one."""
    ids = [str(j) for j in job_ids if j]
    if not ids:
        return {}
    try:
        vals = (rc or _r()).mget([_KEY.format(j) for j in ids])
    except Exception as exc:  # noqa: BLE001
        print(f"[ChannelHint] read failed: {type(exc).__name__}")
        return {}
    out: Dict[str, str] = {}
    for jid, v in zip(ids, vals):
        if isinstance(v, bytes):
            v = v.decode()
        if v in _SENTENCES:
            out[jid] = v
    return out


def get_channel_hint(job_id: str, rc=None) -> Optional[str]:
    return channel_hints([job_id], rc).get(str(job_id))
