"""In-memory stand-ins for the Supabase client and Redis used by the Phase
32A ASR tests. The fake DB implements just the query-builder calls the ASR
code makes, with real filter semantics (so a conditional
`.update().eq().in_()` really matches nothing once a job is terminal — which
is what makes refund-at-most-once testable)."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

import fakeredis


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.op = "select"
        self.payload = None
        self._single = False
        self._limit = None

    # builders
    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and str(r.get(col)) < str(val))
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and str(r.get(col)) >= str(val))
        return self

    def is_(self, col, val):
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.db.fail_on.get((self.table, self.op)):
            raise RuntimeError(f"fake failure on {self.table}.{self.op}")
        if self.op == "insert":
            row = {"id": str(uuid.uuid4()), "created_at": self.db.now_iso(),
                   "updated_at": self.db.now_iso(), **copy.deepcopy(self.payload)}
            rows.append(row)
            return _Resp([copy.deepcopy(row)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "select":
            out = [copy.deepcopy(r) for r in matched][: self._limit or None]
            if self._single:
                return _Resp(out[0] if out else None)
            return _Resp(out)
        if self.op == "update":
            for r in matched:
                r.update(copy.deepcopy(self.payload))
            return _Resp([copy.deepcopy(r) for r in matched])
        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return _Resp([copy.deepcopy(r) for r in matched])
        raise AssertionError(self.op)


class _Rpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.rpc_calls.append((self.name, dict(self.params)))
        handler = self.db.rpc_handlers.get(self.name)
        if handler is None:
            raise RuntimeError(f"function {self.name} does not exist")
        return _Resp(handler(self.params))


class FakeDB:
    def __init__(self, now: datetime | None = None):
        self.tables: dict[str, list[dict]] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.rpc_handlers = {
            "reserve_transcript_asr_usage": lambda p: True,
            "refund_transcript_asr_usage": lambda p: True,
        }
        self.fail_on: dict = {}
        self._now = now or datetime(2030, 1, 1, tzinfo=timezone.utc)

    def now_iso(self) -> str:
        return self._now.isoformat()

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return _Rpc(self, name, params)

    def calls(self, name):
        return [p for n, p in self.rpc_calls if n == name]


def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


# ── shared fixture ────────────────────────────────────────────────────────
import pytest  # noqa: E402

# One pinned clock for both the code under test (budget.utcnow) and the
# seeded rows — never the real clock.
PINNED_NOW = datetime(2030, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


class AsrEnv:
    def __init__(self, db, redis, alerts):
        self.db, self.redis, self.alerts = db, redis, alerts


@pytest.fixture
def asr_env(monkeypatch):
    from app.services.asr import budget

    r = fake_redis()
    db = FakeDB(PINNED_NOW)
    alerts: list = []
    monkeypatch.setattr(budget, "_r", lambda: r)
    monkeypatch.setattr(budget, "utcnow", lambda: PINNED_NOW)
    monkeypatch.setattr("app.core.alerts.send_admin_alert", lambda *a, **k: alerts.append((a, k)))
    for name in ("ASR_ENABLED", "ASR_PROVIDER", "ASR_SPEND_CEILING_USD", "ASR_GLOBAL_DAILY_MINUTES_CAP",
                 "ASR_CHUNK_SEC", "ASR_CHUNK_MAX_RETRIES", "ASR_DIARIZATION_ENABLED",
                 "ASR_PRICE_PER_MIN_GEMINI", "ASR_PRICE_PER_MIN_WHISPER", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.services.asr.pipeline._sleep", lambda s: None)
    return AsrEnv(db, r, alerts)
