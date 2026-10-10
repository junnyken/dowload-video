"""
In-memory Supabase stand-in for the Channel Watch tests (task #6257).

Unlike the schedule tests' fake, this one enforces the UNIQUE keys of
database/migrations/038_channel_watch.sql (insert → conflict error, upsert
with ignore_duplicates → only the rows actually inserted come back, like
PostgREST's ON CONFLICT DO NOTHING ... RETURNING), supports count="exact",
gte/lte/in_/order(desc)/limit, and conditional updates return the rows they
changed. `enforce_unique=False` is the negative control.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime

UNIQUE_KEYS = {
    "watch_sources": [("platform", "external_channel_id")],
    "watch_subscriptions": [("user_id", "source_id")],
    "watch_items_seen": [("source_id", "external_item_id")],
    "watch_deliveries": [("subscription_id", "item_id")],
}


class UniqueViolation(Exception):
    code = "23505"


def _cmp_val(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return v
    return v


class _Res:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.opts = "select", None, {}
        self.filters, self._limit, self._order, self._count = [], None, None, None

    def select(self, *_a, count=None, **_k):
        self.op, self._count = "select", count
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, rows, on_conflict="", ignore_duplicates=False, **_k):
        self.op, self.payload = "upsert", rows
        self.opts = {"on_conflict": on_conflict, "ignore": ignore_duplicates}
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

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def lte(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and _cmp_val(r[col]) <= _cmp_val(val))
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and _cmp_val(r[col]) >= _cmp_val(val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        return self

    def _match(self):
        return [r for r in self.db.rows(self.table) if all(f(r) for f in self.filters)]

    def execute(self):
        self.db.calls.append((self.table, self.op))
        if self.db.fail_tables and self.table in self.db.fail_tables:
            raise RuntimeError(f"table {self.table} unavailable")
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            out = [self.db.add(self.table, r, ignore=False) for r in rows]
            return _Res([dict(r) for r in out])
        if self.op == "upsert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in rows:
                new = self.db.add(self.table, r, ignore=self.opts.get("ignore", False))
                if new is not None:
                    out.append(dict(new))
            return _Res(out)
        if self.op == "update":
            hit = self._match()
            for r in hit:
                r.update(self.payload)
            return _Res([dict(r) for r in hit])
        if self.op == "delete":
            hit = self._match()
            ids = {id(r) for r in hit}
            self.db.tables[self.table] = [r for r in self.db.rows(self.table) if id(r) not in ids]
            return _Res([dict(r) for r in hit])
        hit = self._match()
        if self._order:
            col, desc = self._order
            hit = sorted(hit, key=lambda r: str(_cmp_val(r.get(col)) or ""), reverse=desc)
        total = len(hit)
        if self._limit is not None:
            hit = hit[: self._limit]
        return _Res([dict(r) for r in hit], count=total if self._count else None)


class FakeDB:
    def __init__(self, *, enforce_unique: bool = True):
        self.tables: dict[str, list[dict]] = {}
        self.enforce_unique = enforce_unique
        self.calls: list[tuple[str, str]] = []
        self.fail_tables: set[str] = set()
        self._seq = itertools.count()

    def rows(self, table):
        return self.tables.setdefault(table, [])

    def add(self, table, row, *, ignore: bool):
        new = dict(row)
        new.setdefault("id", str(uuid.uuid4()))
        if self.enforce_unique:
            for key in UNIQUE_KEYS.get(table, []):
                val = tuple(new.get(c) for c in key)
                if any(tuple(r.get(c) for c in key) == val for r in self.rows(table)):
                    if ignore:
                        return None
                    raise UniqueViolation(f"duplicate key {table}{key}")
        self.rows(table).append(new)
        return new

    def table(self, name):
        return _Query(self, name)

    def count(self, table, **eq):
        return sum(1 for r in self.rows(table) if all(r.get(k) == v for k, v in eq.items()))
