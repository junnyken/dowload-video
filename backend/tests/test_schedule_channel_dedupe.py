"""
Phase 33-0 (task #6254): a daily channel schedule must not re-download the
items a previous run already handled.

End-to-end-ish: the real scan_scheduled_jobs tick → _trigger_job →
_trigger_channel → scrape_channel_task body, with an in-memory Supabase,
fakeredis, a fixed channel listing and a recording stand-in for
process_video_task (nothing touches the network or a paid provider).
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.tasks import schedule_tasks, video_tasks


# ── In-memory Supabase ────────────────────────────────────────────────

def _parse_dt(v):
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class _Res:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload = "select", None
        self.filters, self._limit, self._order = [], None, None

    # builders
    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def neq(self, col, val):
        self.filters.append(lambda r: r.get(col) != val)
        return self

    def lte(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and _parse_dt(r[col]) <= _parse_dt(val))
        return self

    def in_(self, col, vals):
        vals = set(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        return self

    def _rows(self):
        return [r for r in self.db.tables.setdefault(self.table, []) if all(f(r) for f in self.filters)]

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            new = dict(self.payload)
            new.setdefault("id", str(uuid.uuid4()))
            new.setdefault("created_at", self.db.tick())
            rows.append(new)
            return _Res([dict(new)])
        if self.op == "update":
            hit = self._rows()
            for r in hit:
                r.update(self.payload)
            return _Res([dict(r) for r in hit])
        if self.op == "delete":
            hit = self._rows()
            self.db.tables[self.table] = [r for r in rows if r not in hit]
            return _Res([dict(r) for r in hit])
        hit = self._rows()
        if self._order:
            col, desc = self._order
            hit = sorted(hit, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            hit = hit[: self._limit]
        return _Res([dict(r) for r in hit], count=len(hit))


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._seq = itertools.count()

    def tick(self):
        # Monotonic created_at so order(created_at) is deterministic.
        return f"2026-01-01T00:00:{next(self._seq):06d}"

    def table(self, name):
        return _Query(self, name)


# ── Harness ───────────────────────────────────────────────────────────

CHANNEL = "https://www.youtube.com/@somechannel"


def _entry(vid):
    return {"url": f"https://www.youtube.com/watch?v={vid}", "title": f"video {vid}"}


class Harness:
    def __init__(self, monkeypatch):
        self.db = FakeSupabase()
        self.rc = fakeredis.FakeRedis(decode_responses=True)
        self.clock = [datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)]
        self.listing: list[dict] = []
        self.listing_calls: list[int] = []
        self.dispatched: list[str] = []    # video URLs handed to process_video_task

        monkeypatch.setattr("app.core.redis_client._client", self.rc)
        monkeypatch.setattr("app.core.database.get_service_client", lambda: self.db)
        monkeypatch.setattr("app.core.database.get_supabase_client", lambda: self.db)
        monkeypatch.setattr(video_tasks, "get_supabase_client", lambda: self.db)

        clock = self.clock

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0] if tz else clock[0].replace(tzinfo=None)

        monkeypatch.setattr(schedule_tasks, "datetime", _DT)

        def _listing(url, max_videos, min_views=0):
            self.listing_calls.append(max_videos)
            ents = self.listing[:max_videos]
            return {"channel_title": "c", "entries": [dict(e) for e in ents],
                    "total_found": len(self.listing), "total_queued": len(ents)}

        monkeypatch.setattr(video_tasks, "scrape_channel_entries_sync", _listing)

        def _apply_async(args=None, kwargs=None, countdown=0, **_kw):
            job_id, video_url = args[0], args[1]
            self.dispatched.append(video_url)
            # The "download" succeeds immediately.
            for r in self.db.tables.get("download_jobs", []):
                if r["id"] == job_id:
                    r["status"] = "success"

        monkeypatch.setattr(video_tasks.process_video_task, "apply_async", _apply_async)
        # Run the channel scrape inline instead of through the broker.
        monkeypatch.setattr(video_tasks.scrape_channel_task, "delay",
                            lambda **kw: video_tasks.scrape_channel_task(**kw))

    def add_schedule(self, *, max_videos=5, last_run_at=None, user_id="user-1"):
        row = {
            "id": str(uuid.uuid4()), "user_id": user_id, "job_type": "channel",
            "input_payload": {"url": CHANNEL, "max_videos": max_videos},
            "schedule_type": "daily", "run_at": "01:00:00", "run_on_weekday": None,
            "is_active": True, "next_run_at": self.clock[0].isoformat(),
            "last_run_at": last_run_at, "auto_collection_id": None,
        }
        self.db.tables.setdefault("scheduled_jobs", []).append(row)
        return row

    def tick(self):
        """One beat tick; returns the video URLs dispatched by it."""
        before = len(self.dispatched)
        schedule_tasks.scan_scheduled_jobs()
        return self.dispatched[before:]

    def advance_past_next_run(self, sched):
        nxt = _parse_dt(sched["next_run_at"])
        assert nxt > self.clock[0], "next_run_at must move forward after a run"
        self.clock[0] = nxt + timedelta(minutes=1)

    def child_jobs(self):
        return [r for r in self.db.tables.get("download_jobs", [])
                if r.get("original_url") != CHANNEL]


@pytest.fixture
def h(monkeypatch):
    for k in ("SCHEDULE_DEDUPE_ENABLED", "SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN"):
        monkeypatch.delenv(k, raising=False)
    return Harness(monkeypatch)


# ── Current behaviour (before the fix) ────────────────────────────────

def test_daily_channel_schedule_current_behaviour(h):
    sched = h.add_schedule(max_videos=5)
    h.listing = [_entry(f"v{i}") for i in range(1, 6)]

    run1 = h.tick()
    h.advance_past_next_run(sched)
    h.listing = [_entry("v6")] + h.listing            # one new upload
    run2 = h.tick()

    print(f"\nrun1 dispatched={len(run1)} run2 dispatched={len(run2)} "
          f"duplicates_in_run2={len(set(run1) & set(run2))} child_jobs={len(h.child_jobs())}")
    assert len(run1) == 5
    assert len(run2) == 5                       # 4 duplicates + 1 new
    assert len(set(run1) & set(run2)) == 4
