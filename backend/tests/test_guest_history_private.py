"""
Task #6137 — a guest must never see or delete another guest's downloads.
Anonymous jobs are stored with user_id NULL; /history used to return the
last of them to every guest (URL, title, download link) and
DELETE /history/{id} let any guest remove them. Signed-in history unchanged.
"""
from __future__ import annotations

import pytest

from tests.test_fetch_link_uuid_scope import route  # noqa: F401


class _Q:
    def __init__(self, rows, log):
        self.rows, self.log, self.filters = rows, log, []

    def select(self, *a, **k):
        return self

    def delete(self):
        self.log.append("delete")
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def is_(self, col, val):
        self.filters.append((col, "IS " + val))
        return self

    def execute(self):
        self.log.append(tuple(self.filters))
        out = [r for r in self.rows if all(r.get(c) == v for c, v in self.filters if not str(v).startswith("IS"))]

        class R:
            data = out
        return R()


@pytest.fixture
def db(monkeypatch, route):
    rows = [{"id": "g1", "user_id": None, "original_url": "https://other-guest", "status": "success"},
            {"id": "u1", "user_id": "u-1", "original_url": "https://mine", "status": "success"}]
    log = []

    class SB:
        def table(self, name):
            return _Q(rows, log)
    monkeypatch.setattr(route, "get_supabase_client", lambda: SB())
    return log


def test_guest_gets_no_server_history(app, db):
    r = app.get("/api/v1/history?limit=5")
    assert r.status_code == 200
    assert r.json() == {"success": True, "jobs": [], "local_only": True}
    assert db == []                                   # the table is never read for a guest


def test_guest_cannot_delete_a_row(app, db):
    assert app.delete("/api/v1/history/g1").status_code == 401
    assert "delete" not in db


def test_signed_in_sees_and_deletes_only_own(app, db):
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1"}
    try:
        body = app.get("/api/v1/history?limit=5").json()
        assert [j["original_url"] for j in body["jobs"]] == ["https://mine"]
        assert app.delete("/api/v1/history/g1").status_code == 404     # not theirs
        assert app.delete("/api/v1/history/u1").status_code == 200
    finally:
        main_mod.app.dependency_overrides.pop(get_optional_user, None)
