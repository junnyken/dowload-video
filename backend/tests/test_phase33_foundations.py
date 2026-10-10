"""
Task #6256 — foundations Phase 33A relies on:
  * webhook delivery tasks are registered with the worker and routed to a
    queue the production worker consumes;
  * POST /schedule/{id}/run works (was a TypeError → 500);
  * child jobs of a scheduled channel run belong to the schedule owner, so
    they show up in that user's /history and nobody else's;
  * a schedule run is claimed atomically, and keyword schedules no longer run
    yt-dlp inside the beat tick.
No network, no paid provider: in-memory Supabase + fakeredis (shared harness
from test_schedule_channel_dedupe).
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from app.tasks import schedule_tasks, video_tasks
from tests.test_schedule_channel_dedupe import CHANNEL, Harness, _Query, _entry


@pytest.fixture
def h(monkeypatch):
    for k in ("SCHEDULE_DEDUPE_ENABLED", "SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN"):
        monkeypatch.delenv(k, raising=False)
    # /history paginates with .range(); the shared fake has no such builder.
    monkeypatch.setattr(_Query, "range", lambda self, a, b: self, raising=False)
    return Harness(monkeypatch)


# ── 1. Celery registry + routing ─────────────────────────────────────

def _production_queues() -> set[str]:
    entry = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
    m = re.search(r"worker\s*\\?\s*\n?\s*-Q\s+([\w,]+)", entry.read_text())
    assert m, "worker -Q line not found in docker-entrypoint.sh"
    return set(m.group(1).split(","))


@pytest.mark.parametrize("name", [
    "deliver_webhook_task",
    "app.services.webhook_dispatcher.retry_webhook_delivery",
    "run_keyword_schedule_task",
    "scan_scheduled_jobs",
])
def test_worker_registers_and_consumes_task(name):
    from app.core.celery_app import celery_app
    celery_app.loader.import_default_modules()
    assert name in celery_app.tasks, f"{name} not registered with the worker"
    queue = celery_app.amqp.router.route({}, name)["queue"].name
    assert queue in _production_queues(), f"{name} → {queue}, not consumed in production"


# ── 2. POST /schedule/{id}/run ───────────────────────────────────────

@pytest.fixture
def api(h, monkeypatch):
    import app.api.schedule as sched_api
    import app.main as main_mod
    from app.core.auth_middleware import get_optional_user, get_required_user
    monkeypatch.setattr(sched_api, "get_service_client", lambda: h.db)
    who = {"id": None}

    def _required():
        if not who["id"]:
            from fastapi import HTTPException
            raise HTTPException(401, "Authentication required")
        return {"id": who["id"]}

    main_mod.app.dependency_overrides[get_required_user] = _required
    main_mod.app.dependency_overrides[get_optional_user] = lambda: ({"id": who["id"]} if who["id"] else None)
    yield who
    main_mod.app.dependency_overrides.pop(get_required_user, None)
    main_mod.app.dependency_overrides.pop(get_optional_user, None)


def test_run_now_requires_auth(app, h, api):
    s = h.add_schedule(user_id="user-1")
    assert app.post(f"/api/v1/schedule/{s['id']}/run").status_code == 401
    assert h.dispatched == []


def test_run_now_own_schedule_starts_it(app, h, api):
    api["id"] = "user-1"
    s = h.add_schedule(user_id="user-1")
    next_before = s["next_run_at"]
    h.listing = [_entry(f"v{i}") for i in range(1, 4)]
    r = app.post(f"/api/v1/schedule/{s['id']}/run")
    assert r.status_code == 202, r.text
    assert len(h.dispatched) == 3
    assert s["last_run_status"] == "success" and s["last_run_at"]
    assert s["next_run_at"] == next_before            # timetable untouched by a manual run


def test_run_now_other_users_schedule_is_404(app, h, api):
    api["id"] = "user-2"
    s = h.add_schedule(user_id="user-1")
    assert app.post(f"/api/v1/schedule/{s['id']}/run").status_code == 404
    assert h.dispatched == [] and s.get("last_run_at") is None


# ── 3. Scheduled child jobs belong to the owner → /history ───────────

def test_scheduled_channel_children_in_owner_history_only(app, h, api, monkeypatch):
    import app.api.routes as routes
    monkeypatch.setattr(routes, "get_supabase_client", lambda: h.db)
    h.add_schedule(user_id="user-1")
    h.listing = [_entry(f"v{i}") for i in range(1, 4)]
    assert len(h.tick()) == 3
    assert all(r.get("user_id") == "user-1" for r in h.child_jobs())

    api["id"] = "user-1"
    urls = {j["original_url"] for j in app.get("/api/v1/history?limit=50").json()["jobs"]}
    assert {_entry(f"v{i}")["url"] for i in range(1, 4)} <= urls

    api["id"] = "user-2"
    assert app.get("/api/v1/history?limit=50").json()["jobs"] == []


def test_manual_channel_children_unchanged(h):
    """Manual bulk/channel scans keep creating children without user_id."""
    h.listing = [_entry("v1")]
    video_tasks.scrape_channel_task(channel_url=CHANNEL, batch_id="b-manual",
                                    channel_job_id="manual", max_videos=5, user_id="user-1")
    (child,) = h.child_jobs()
    assert "user_id" not in child


# ── 4. Claim: overlapping ticks run a schedule once ──────────────────

def _keyword_schedule(h, user_id="user-1", schedule_type="daily"):
    row = h.add_schedule(user_id=user_id)
    row.update({"job_type": "keyword", "schedule_type": schedule_type,
                "input_payload": {"keyword": "cats", "platform": "youtube", "count": 3}})
    return row


@pytest.fixture
def keyword_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(schedule_tasks.run_keyword_schedule_task, "delay",
                        lambda **kw: calls.append(kw))
    return calls


@pytest.mark.parametrize("schedule_type", ["daily", "weekly", "once"])
def test_overlapping_ticks_trigger_once(h, keyword_calls, schedule_type):
    s = _keyword_schedule(h, schedule_type=schedule_type)
    now = h.clock[0].isoformat()
    # Both ticks read the row while it was due, before either claimed it.
    snap_a, snap_b = copy.deepcopy(s), copy.deepcopy(s)
    assert schedule_tasks._trigger_job(h.db, snap_a, now) is True
    assert schedule_tasks._trigger_job(h.db, snap_b, now) is False
    assert len(keyword_calls) == 1
    assert keyword_calls[0]["schedule_id"] == s["id"] and keyword_calls[0]["user_id"] == "user-1"
    if schedule_type == "once":
        assert s["is_active"] is False
    else:
        assert s["next_run_at"] > now


def test_overlap_negative_control_without_claim(h, keyword_calls, monkeypatch):
    """Same scenario with the claim bypassed fires twice — the test above
    really depends on the claim."""
    monkeypatch.setattr(schedule_tasks, "_claim_job", lambda *a, **k: True)
    s = _keyword_schedule(h)
    now = h.clock[0].isoformat()
    schedule_tasks._trigger_job(h.db, copy.deepcopy(s), now)
    schedule_tasks._trigger_job(h.db, copy.deepcopy(s), now)
    assert len(keyword_calls) == 2


def test_keyword_tick_does_not_search_inline(h, keyword_calls, monkeypatch):
    searched = []
    monkeypatch.setattr(schedule_tasks, "_run_keyword_search", lambda *a, **k: searched.append(a))
    _keyword_schedule(h)
    h.tick()
    h.tick()                                      # second tick: not due any more
    assert searched == [] and len(keyword_calls) == 1


def test_keyword_task_failure_marks_schedule_failed(h, monkeypatch):
    s = _keyword_schedule(h)

    def _boom(*a, **k):
        raise RuntimeError("search failed")
    monkeypatch.setattr(schedule_tasks, "_run_keyword_search", _boom)
    schedule_tasks.run_keyword_schedule_task(user_id="user-1", payload=s["input_payload"],
                                             schedule_id=s["id"])
    assert s["last_run_status"] == "failed"


def test_claim_db_error_skips_run(h, keyword_calls, monkeypatch):
    s = _keyword_schedule(h)

    class _Broken:
        def table(self, name):
            raise ConnectionError("db down")
    assert schedule_tasks._trigger_job(_Broken(), copy.deepcopy(s), h.clock[0].isoformat()) is False
    assert keyword_calls == []
