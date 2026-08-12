"""
intelligence_tasks.py — Phase 12 VidGrab
=========================================
Celery tasks for automated intelligence loops:
  - Anomaly detection + auto-tuning
  - Schedule suggestion generation
  - Archive intelligence scan (tag suggestions + duplicate detection)
  - Auto-tune cycle (standalone metrics collection)

All tasks are fire-and-forget (max_retries=0) and swallow exceptions to
avoid crashing the beat scheduler.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
from typing import Any

from app.core.celery_app import celery_app
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis key constants
# ---------------------------------------------------------------------------

KEY_LAST_INTELLIGENCE_RUN = "vidgrab:ops:last_intelligence_run"
KEY_SCHEDULE_SUGGESTIONS = "vidgrab:schedule:suggestions"
KEY_ARCHIVE_TAG_SUGGESTIONS = "vidgrab:archive:tag_suggestions"
KEY_ARCHIVE_DUPLICATES = "vidgrab:archive:duplicates"

MAX_SCHEDULE_SUGGESTIONS = 50
MAX_TAG_SUGGESTIONS = 200
MAX_DUPLICATE_ENTRIES = 200

# ---------------------------------------------------------------------------
# Stopwords for keyword extraction (simple English + Vietnamese fillers)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "have", "will",
    "your", "more", "than", "they", "when", "also", "some", "into", "over",
    "then", "been", "its", "but", "not", "are", "was", "were", "can",
    "all", "one", "out", "has", "had", "her", "his", "our", "who",
    "bai", "video", "cua", "cho", "voi", "trong", "tren", "duoi", "mot",
    "hay", "hoac", "cac", "nhung", "cung", "nhu", "nay", "do", "va",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_disk_pct(path: str = "/") -> float:
    """Return disk usage percentage for the given path."""
    try:
        usage = shutil.disk_usage(path)
        return round(usage.used / usage.total * 100, 2)
    except Exception:
        return 0.0


def _get_queue_depth() -> int:
    """Approximate queue depth from Redis Celery default queue length."""
    try:
        r = get_redis()
        return r.llen("celery") or 0
    except Exception:
        return 0


def _get_retry_rate() -> float:
    """
    Estimate retry rate from Redis stats keys.
    Uses today's platform error vs ok counts as a proxy.
    Returns a float in [0.0, 1.0].
    """
    try:
        r = get_redis()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats_key = f"vidgrab:stats:{today}"
        data = r.hgetall(stats_key)
        if not data:
            return 0.0
        ok_total = sum(
            int(v) for k, v in data.items() if k.endswith(":ok")
        )
        err_total = sum(
            int(v) for k, v in data.items() if k.endswith(":err")
        )
        total = ok_total + err_total
        if total == 0:
            return 0.0
        return round(err_total / total, 4)
    except Exception:
        return 0.0


def _extract_keywords(text: str, min_len: int = 4) -> list[str]:
    """
    Simple keyword extractor: split on whitespace/punctuation, remove
    stopwords and short tokens, return deduplicated lowercased words.
    No AI — purely deterministic.
    """
    import re
    tokens = re.split(r"[\s\W_]+", text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token = token.strip()
        if (
            len(token) > min_len
            and token not in _STOPWORDS
            and token not in seen
            and token.isalpha()
        ):
            seen.add(token)
            result.append(token)
    return result[:10]


def _normalize_title(title: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    import re
    return re.sub(r"\s+", " ", (title or "").lower().strip())


# ---------------------------------------------------------------------------
# Task 1: run_anomaly_detection
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="run_anomaly_detection",
    max_retries=0,
    ignore_result=True,
)
def run_anomaly_detection(self) -> None:
    """
    Phase 12 master intelligence task.

    Steps:
    1. Run heuristic anomaly checks (rolling-window Redis stats).
    2. Run auto-tuner with current disk / queue / retry metrics.
    3. Auto-manage queue priorities based on system stress.
    4. Auto-manage adaptive fallback demotions / promotions.
    5. Record timestamp in Redis.
    """
    findings: dict[str, Any] = {
        "anomalies": [],
        "tuning_actions": [],
        "priority_actions": [],
        "demotion_actions": [],
    }

    # --- Step 1: Anomaly detection ---
    try:
        from app.core.anomaly_detector import run_all_checks
        anomalies = run_all_checks()
        findings["anomalies"] = anomalies
        logger.info("[intelligence] anomaly_detector found %d anomaly(ies)", len(anomalies))
    except Exception as exc:
        logger.exception("[intelligence] anomaly_detector failed: %s", exc)

    # --- Collect live metrics for subsequent steps ---
    disk_pct = _get_disk_pct()
    queue_depth = _get_queue_depth()
    retry_rate = _get_retry_rate()

    # --- Step 2: Auto-tuner ---
    try:
        from app.core.auto_tuner import run_auto_tune
        actions = run_auto_tune(
            disk_pct=disk_pct,
            queue_depth=queue_depth,
            retry_rate=retry_rate,
        )
        findings["tuning_actions"] = actions
        logger.info(
            "[intelligence] auto_tuner applied %d action(s) "
            "(disk=%.1f%%, queue=%d, retry_rate=%.2f)",
            len(actions),
            disk_pct,
            queue_depth,
            retry_rate,
        )
    except Exception as exc:
        logger.exception("[intelligence] auto_tuner failed: %s", exc)

    # --- Step 3: Queue priority management ---
    try:
        from app.core.queue_intelligence import auto_manage_priority
        priority_actions = auto_manage_priority(
            disk_pct=disk_pct,
            queue_depth=queue_depth,
        )
        findings["priority_actions"] = priority_actions
        logger.info(
            "[intelligence] queue_intelligence priority actions: %s",
            priority_actions,
        )
    except Exception as exc:
        logger.exception("[intelligence] queue_intelligence failed: %s", exc)

    # --- Step 4: Adaptive fallback demotions ---
    try:
        from app.core.adaptive_fallback import auto_manage_demotions
        demotion_results = auto_manage_demotions()
        findings["demotion_actions"] = demotion_results
        logger.info(
            "[intelligence] adaptive_fallback demotion results: %d item(s)",
            len(demotion_results),
        )
    except Exception as exc:
        logger.exception("[intelligence] adaptive_fallback failed: %s", exc)

    # --- Step 5: Record run timestamp ---
    try:
        r = get_redis()
        r.set(KEY_LAST_INTELLIGENCE_RUN, _now_iso(), ex=86400 * 7)
    except Exception as exc:
        logger.exception("[intelligence] failed to write last_run timestamp: %s", exc)

    # --- Summary print ---
    print(
        f"[run_anomaly_detection] DONE | "
        f"anomalies={len(findings['anomalies'])} | "
        f"tuning_actions={len(findings['tuning_actions'])} | "
        f"priority_actions={len(findings['priority_actions'])} | "
        f"demotion_actions={len(findings['demotion_actions'])} | "
        f"disk={disk_pct:.1f}% queue={queue_depth} retry_rate={retry_rate:.2%}"
    )


# ---------------------------------------------------------------------------
# Task 2: generate_schedule_suggestions
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="generate_schedule_suggestions",
    max_retries=0,
    ignore_result=True,
)
def generate_schedule_suggestions(self) -> None:
    """
    Analyse scheduled_jobs from Supabase and generate cron-time suggestions.

    Logic:
    - Jobs that never ran successfully in the last 7 days are flagged.
    - Jobs whose scheduled minute falls within ±2 min of another job are
      flagged for potential collision; a shifted cron is suggested.

    Results stored in Redis vidgrab:schedule:suggestions (list of JSON, max 50).
    """
    suggestions: list[dict] = []

    try:
        from app.core.database import get_service_client
        supabase = get_service_client()
    except Exception as exc:
        logger.exception("[schedule_suggestions] cannot connect to Supabase: %s", exc)
        return

    # Fetch scheduled jobs
    try:
        resp = supabase.table("scheduled_jobs").select(
            "id, cron_expression, last_run_at, last_run_status, name"
        ).execute()
        jobs: list[dict] = resp.data or []
    except Exception as exc:
        logger.exception("[schedule_suggestions] failed to fetch scheduled_jobs: %s", exc)
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # --- Check 1: jobs that haven't run successfully in 7 days ---
    for job in jobs:
        job_id = job.get("id")
        cron = job.get("cron_expression", "")
        last_run_at_raw = job.get("last_run_at")
        last_status = job.get("last_run_status", "")

        # Parse last_run_at
        last_run_at: datetime | None = None
        if last_run_at_raw:
            try:
                last_run_at = datetime.fromisoformat(
                    str(last_run_at_raw).replace("Z", "+00:00")
                )
            except Exception:
                pass

        ran_ok_recently = (
            last_run_at is not None
            and last_run_at >= cutoff
            and str(last_status).lower() in ("success", "ok", "done", "completed")
        )

        if not ran_ok_recently and cron:
            suggestions.append({
                "job_id": str(job_id),
                "current_cron": cron,
                "suggested_cron": cron,  # unchanged — operator should review
                "reason": (
                    "Job has not run successfully in the last 7 days. "
                    f"Last status: {last_status!r}, last_run_at: {last_run_at_raw!r}"
                ),
                "created_at": _now_iso(),
            })

    # --- Check 2: collision detection (±2 min window on the minute field) ---
    def _extract_minute(cron: str) -> int | None:
        """Return integer minute from a standard 5-field cron string, or None."""
        try:
            parts = cron.strip().split()
            if len(parts) >= 2:
                minute_field = parts[0]
                if minute_field.isdigit():
                    return int(minute_field)
        except Exception:
            pass
        return None

    # Build (job_id, minute) pairs for jobs with a fixed minute
    minute_map: list[tuple[str, str, int]] = []  # (job_id, cron, minute)
    for job in jobs:
        cron = job.get("cron_expression", "")
        minute = _extract_minute(cron)
        if minute is not None:
            minute_map.append((str(job.get("id")), cron, minute))

    seen_collisions: set[frozenset] = set()
    for i, (id_a, cron_a, min_a) in enumerate(minute_map):
        for id_b, cron_b, min_b in minute_map[i + 1:]:
            if abs(min_a - min_b) <= 2:
                pair = frozenset({id_a, id_b})
                if pair in seen_collisions:
                    continue
                seen_collisions.add(pair)
                # Suggest shifting job_b by +5 minutes
                new_minute = (min_b + 5) % 60
                parts = cron_b.strip().split()
                parts[0] = str(new_minute)
                suggested = " ".join(parts)
                suggestions.append({
                    "job_id": id_b,
                    "current_cron": cron_b,
                    "suggested_cron": suggested,
                    "reason": (
                        f"Potential collision with job {id_a} — both scheduled within "
                        f"±2 minutes (minute {min_a} vs {min_b}). "
                        f"Suggested shift: minute {min_b} → {new_minute}."
                    ),
                    "created_at": _now_iso(),
                })

    # Trim to max
    suggestions = suggestions[:MAX_SCHEDULE_SUGGESTIONS]

    # Store in Redis
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.delete(KEY_SCHEDULE_SUGGESTIONS)
        for s in suggestions:
            pipe.rpush(KEY_SCHEDULE_SUGGESTIONS, json.dumps(s, ensure_ascii=False))
        pipe.ltrim(KEY_SCHEDULE_SUGGESTIONS, 0, MAX_SCHEDULE_SUGGESTIONS - 1)
        pipe.execute()
        logger.info(
            "[schedule_suggestions] stored %d suggestion(s) in Redis", len(suggestions)
        )
    except Exception as exc:
        logger.exception(
            "[schedule_suggestions] failed to store suggestions in Redis: %s", exc
        )

    print(
        f"[generate_schedule_suggestions] DONE | "
        f"jobs_checked={len(jobs)} | suggestions_stored={len(suggestions)}"
    )


# ---------------------------------------------------------------------------
# Task 3: archive_intelligence_scan
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="archive_intelligence_scan",
    max_retries=0,
    ignore_result=True,
)
def archive_intelligence_scan(self) -> None:
    """
    Scan recent archive_items for intelligence opportunities:

    1. Items with no tags → suggest tags via keyword extraction (no AI).
    2. Items with the same normalized title within a 24 h window → flag as
       near-duplicates.

    Results persisted in Redis:
      vidgrab:archive:tag_suggestions  — list of JSON (max 200)
      vidgrab:archive:duplicates       — list of JSON (max 200)
    """
    try:
        from app.core.database import get_service_client
        supabase = get_service_client()
    except Exception as exc:
        logger.exception("[archive_scan] cannot connect to Supabase: %s", exc)
        return

    # Fetch last 7 days of archive items
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        resp = supabase.table("archive_items").select(
            "id, title, description, tags, created_at"
        ).gte("created_at", cutoff).limit(200).execute()
        items: list[dict] = resp.data or []
    except Exception as exc:
        logger.exception("[archive_scan] failed to fetch archive_items: %s", exc)
        return

    tag_suggestions: list[dict] = []
    duplicate_entries: list[dict] = []

    # --- Pass 1: tag suggestions for untagged items ---
    for item in items:
        existing_tags = item.get("tags") or []
        if existing_tags:
            continue  # already tagged

        item_id = str(item.get("id", ""))
        title = item.get("title") or ""
        description = item.get("description") or ""
        combined = f"{title} {description}"
        suggested = _extract_keywords(combined)

        if suggested:
            tag_suggestions.append({
                "item_id": item_id,
                "title": title[:200],
                "suggested_tags": suggested,
                "created_at": _now_iso(),
            })

    tag_suggestions = tag_suggestions[:MAX_TAG_SUGGESTIONS]

    # --- Pass 2: near-duplicate detection (same normalized title within 24 h) ---
    # Build a map: normalized_title → list of (item_id, created_at)
    title_map: dict[str, list[dict]] = {}
    for item in items:
        norm = _normalize_title(item.get("title") or "")
        if not norm:
            continue
        title_map.setdefault(norm, []).append({
            "item_id": str(item.get("id", "")),
            "created_at": item.get("created_at"),
        })

    for norm_title, group in title_map.items():
        if len(group) < 2:
            continue
        # Check if any two members are within 24 h of each other
        timestamps: list[tuple[str, datetime]] = []
        for entry in group:
            raw = entry.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                timestamps.append((entry["item_id"], ts))
            except Exception:
                pass

        timestamps.sort(key=lambda x: x[1])
        for idx in range(len(timestamps) - 1):
            id_a, ts_a = timestamps[idx]
            id_b, ts_b = timestamps[idx + 1]
            if (ts_b - ts_a) <= timedelta(hours=24):
                duplicate_entries.append({
                    "item_ids": [id_a, id_b],
                    "normalized_title": norm_title,
                    "time_delta_seconds": int((ts_b - ts_a).total_seconds()),
                    "created_at": _now_iso(),
                })

    duplicate_entries = duplicate_entries[:MAX_DUPLICATE_ENTRIES]

    # --- Store in Redis ---
    try:
        r = get_redis()
        pipe = r.pipeline()

        pipe.delete(KEY_ARCHIVE_TAG_SUGGESTIONS)
        for s in tag_suggestions:
            pipe.rpush(KEY_ARCHIVE_TAG_SUGGESTIONS, json.dumps(s, ensure_ascii=False))
        pipe.ltrim(KEY_ARCHIVE_TAG_SUGGESTIONS, 0, MAX_TAG_SUGGESTIONS - 1)

        pipe.delete(KEY_ARCHIVE_DUPLICATES)
        for d in duplicate_entries:
            pipe.rpush(KEY_ARCHIVE_DUPLICATES, json.dumps(d, ensure_ascii=False))
        pipe.ltrim(KEY_ARCHIVE_DUPLICATES, 0, MAX_DUPLICATE_ENTRIES - 1)

        pipe.execute()
        logger.info(
            "[archive_scan] tag_suggestions=%d duplicates=%d stored in Redis",
            len(tag_suggestions),
            len(duplicate_entries),
        )
    except Exception as exc:
        logger.exception("[archive_scan] failed to store results in Redis: %s", exc)

    print(
        f"[archive_intelligence_scan] DONE | "
        f"items_scanned={len(items)} | "
        f"tag_suggestions={len(tag_suggestions)} | "
        f"duplicates={len(duplicate_entries)}"
    )


# ---------------------------------------------------------------------------
# Task 4: auto_tune_cycle
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="auto_tune_cycle",
    max_retries=0,
    ignore_result=True,
)
def auto_tune_cycle(self) -> None:
    """
    Standalone auto-tune cycle.

    Collects real-time metrics (disk, queue depth, retry rate) from Redis /
    OS and passes them to the auto-tuner.  Intended for higher-frequency
    scheduling than the full intelligence loop.
    """
    disk_pct = _get_disk_pct()
    queue_depth = _get_queue_depth()
    retry_rate = _get_retry_rate()

    logger.info(
        "[auto_tune_cycle] metrics: disk=%.1f%% queue=%d retry_rate=%.2f",
        disk_pct,
        queue_depth,
        retry_rate,
    )

    actions: list[dict] = []
    try:
        from app.core.auto_tuner import run_auto_tune
        actions = run_auto_tune(
            disk_pct=disk_pct,
            queue_depth=queue_depth,
            retry_rate=retry_rate,
        )
        logger.info("[auto_tune_cycle] applied %d action(s)", len(actions))
    except Exception as exc:
        logger.exception("[auto_tune_cycle] run_auto_tune failed: %s", exc)

    # Log results to Redis for observability
    try:
        r = get_redis()
        record = json.dumps(
            {
                "disk_pct": disk_pct,
                "queue_depth": queue_depth,
                "retry_rate": retry_rate,
                "actions_count": len(actions),
                "actions": actions[:10],  # store up to 10 for brevity
                "ran_at": _now_iso(),
            },
            ensure_ascii=False,
        )
        r.lpush("vidgrab:tuning:cycle_log", record)
        r.ltrim("vidgrab:tuning:cycle_log", 0, 99)  # keep last 100 runs
    except Exception as exc:
        logger.exception("[auto_tune_cycle] failed to log cycle result: %s", exc)

    print(
        f"[auto_tune_cycle] DONE | "
        f"disk={disk_pct:.1f}% queue={queue_depth} retry_rate={retry_rate:.2%} "
        f"actions={len(actions)}"
    )
