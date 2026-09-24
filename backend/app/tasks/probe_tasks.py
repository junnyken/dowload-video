"""
Scheduled platform probes
=========================
Runs the active metadata probes from app.core.platform_probe and alerts on the
transition into failure.

Alerting on the TRANSITION, not on the state, is the point. A platform that has
been broken for two days should not send an alert every thirty minutes — people
mute the channel, and then the next real break is muted too.
"""

from __future__ import annotations

import json
from typing import Dict

from app.core.celery_app import celery_app
from app.core.platform_probe import (
    FAILED,
    OK,
    get_probe_states,
    get_targets,
    probe_once,
    record_probe,
)
from app.core.redis_client import get_redis

_ALERTED_KEY = "probe:alerted"   # set of platforms we have already alerted about


def _alerted() -> set:
    try:
        raw = get_redis().smembers(_ALERTED_KEY) or set()
        return {r.decode() if isinstance(r, bytes) else r for r in raw}
    except Exception:
        return set()


@celery_app.task(name="probe_all_platforms", ignore_result=True)
def probe_all_platforms() -> Dict[str, str]:
    """Probe every configured platform once. Returns {platform: status}."""
    targets = get_targets()
    if not targets:
        print("[Probe] no targets configured — nothing to probe")
        return {}

    # State before this run, so we can tell a new break from an ongoing one.
    was = {p: s.get("status") for p, s in get_probe_states().items()}

    results: Dict[str, str] = {}
    for platform, url in sorted(targets.items()):
        outcome = probe_once(url)
        record_probe(platform, outcome)
        results[platform] = OK if outcome.get("ok") else FAILED
        print(f"[Probe] {platform}: {results[platform]} ({outcome.get('ms')}ms)"
              + (f" — {outcome.get('reason')}" if not outcome.get("ok") else ""))

    _handle_alerts(results, was)
    return results


def _handle_alerts(results: Dict[str, str], was: Dict[str, str]) -> None:
    rc = get_redis()
    already = _alerted()

    broke = [p for p, st in results.items()
             if st == FAILED and was.get(p) != FAILED and p not in already]
    recovered = [p for p, st in results.items()
                 if st == OK and p in already]

    if broke:
        states = get_probe_states()
        lines = [f"🔴 <b>Nền tảng ngừng hoạt động</b>"]
        for p in broke:
            reason = (states.get(p, {}) or {}).get("reason") or "không rõ"
            lines.append(f"• <b>{p}</b> — {reason[:120]}")
        lines.append("\nDò bằng metadata (không tải). Kiểm tra lại bằng tay trước khi kết luận.")
        _send("\n".join(lines))
        try:
            rc.sadd(_ALERTED_KEY, *broke)
        except Exception:
            pass

    if recovered:
        _send("🟢 <b>Nền tảng hoạt động lại</b>\n" + "\n".join(f"• {p}" for p in recovered))
        try:
            rc.srem(_ALERTED_KEY, *recovered)
        except Exception:
            pass


def _send(message: str) -> None:
    try:
        from app.core.notifications import send_telegram_message_sync
        send_telegram_message_sync(message)
    except Exception as exc:
        print(f"[Probe] alert failed (non-fatal): {exc}")
