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
    claim_probe_slot,
    get_probe_states,
    get_targets,
    probe_once,
    probe_quality,
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
        # Platforms with a minimum interval (the paid ones: 24 h by default)
        # are skipped until it has passed. Nothing is recorded for a skip, so
        # the last real result keeps standing.
        if not claim_probe_slot(platform):
            print(f"[Probe] {platform}: skipped (minimum interval not reached)")
            continue
        outcome = probe_once(url, quality=probe_quality(platform))
        # The recorded status, not the raw outcome: a first failure is
        # UNCONFIRMED and must neither alert nor count as a break.
        results[platform] = record_probe(platform, outcome)
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
        # wording: BA review
        lines.append("\nHệ thống sẽ tự thử bản yt-dlp mới hơn (tối đa 1 lần mỗi 6 giờ cho mỗi nền tảng) "
                     "và báo kết quả. Facebook/Instagram/X tạm tải qua Cobalt.")
        _send("\n".join(lines))
        # app.core.platform_health (2026-10-19): act, not only report.
        for p in broke:
            try:
                from app.core import platform_health
                reason = (states.get(p, {}) or {}).get("reason") or "probe failed"
                platform_health.on_platform_broken(p, f"probe: {reason}")
            except Exception as exc:
                print(f"[Probe] remediation trigger failed for {p}: {exc}")
        try:
            rc.sadd(_ALERTED_KEY, *broke)
        except Exception:
            pass

    if recovered:
        _send("🟢 <b>Nền tảng hoạt động lại</b>\n" + "\n".join(f"• {p}" for p in recovered))
        for p in recovered:
            try:
                from app.core import platform_health
                platform_health.mark_recovered(p)
            except Exception:
                pass
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
