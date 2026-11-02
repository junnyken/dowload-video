"""Regressions for the final security review: notes create, guest ZIP, Douyin hops."""
import asyncio
from unittest.mock import Mock

import pytest


class _Query:
    """Records every filter so tests can assert scoping."""
    def __init__(self, rows=None, count=0):
        self.filters, self.rows, self.count = [], rows or [], count

    def select(self, *a, **k): return self
    def insert(self, row): self.inserted = row; return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val)); return self

    def is_(self, col, val):
        self.filters.append(("is", col, val)); return self

    def execute(self):
        return Mock(data=self.rows, count=self.count)


def _client(monkeypatch, module, tables, user):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.auth_middleware import get_optional_user

    db = Mock()
    db.table.side_effect = lambda name: tables[name]
    monkeypatch.setattr(module, "get_service_client", lambda: db)

    async def identity():
        return user
    app.dependency_overrides[get_optional_user] = identity
    return TestClient(app), app


def test_anonymous_note_without_session_is_rejected(monkeypatch):
    from app.api import notes
    tables = {"clip_notes": _Query(), "download_jobs": _Query()}
    client, app = _client(monkeypatch, notes, tables, None)
    try:
        r = client.post("/api/v1/jobs/synthetic-job/notes", json={"note_text": "x"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 401
    assert not hasattr(tables["clip_notes"], "inserted")


def test_signed_in_user_cannot_note_another_users_job(monkeypatch):
    from app.api import notes
    jobs = _Query(rows=[])  # ownership query finds nothing for this user
    tables = {"clip_notes": _Query(), "download_jobs": jobs}
    client, app = _client(monkeypatch, notes, tables, {"id": "synthetic-user-b"})
    try:
        r = client.post("/api/v1/jobs/synthetic-job-of-a/notes", json={"note_text": "x"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 404
    assert ("eq", "user_id", "synthetic-user-b") in jobs.filters
    assert not hasattr(tables["clip_notes"], "inserted")


def test_owner_can_note_own_job_and_quota_is_scoped(monkeypatch):
    from app.api import notes
    jobs = _Query(rows=[{"id": "synthetic-job-a"}])
    clip = _Query(rows=[{"id": "n1"}])
    tables = {"clip_notes": clip, "download_jobs": jobs}
    client, app = _client(monkeypatch, notes, tables, {"id": "synthetic-user-a"})
    try:
        r = client.post("/api/v1/jobs/synthetic-job-a/notes", json={"note_text": "x"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 201
    assert clip.inserted["user_id"] == "synthetic-user-a"
    assert ("eq", "user_id", "synthetic-user-a") in clip.filters


def test_guest_batch_zip_is_rejected(monkeypatch):
    from app.api import batch_rename
    jobs = _Query(rows=[])
    client, app = _client(monkeypatch, batch_rename, {"download_jobs": jobs}, None)
    try:
        r = client.post("/api/v1/bulk-zip-rename", json={"batch_id": "synthetic-batch"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 401
    assert jobs.filters == []


def test_douyin_redirect_to_private_target_is_dropped(monkeypatch):
    from app.core import ssrf_guard
    from app.utils import link_resolver as resolver

    def fake_dns(host):
        return ("10.0.0.5",) if host == "internal.example" else ("93.184.216.34",)
    monkeypatch.setattr(ssrf_guard, "_resolve", fake_dns)

    class Resp:
        status_code = 302
        headers = {"Location": "http://internal.example/video/1234567890123456789"}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return Resp()

    monkeypatch.setattr(resolver.httpx, "AsyncClient", Client)
    assert asyncio.run(resolver.resolve_douyin_shortlink("https://v.douyin.com/abc/")) is None


def test_douyin_private_first_hop_makes_no_request(monkeypatch):
    from app.core import ssrf_guard
    from app.utils import link_resolver as resolver
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("127.0.0.1",))
    client = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(resolver.httpx, "AsyncClient", client)
    assert asyncio.run(resolver.resolve_douyin_shortlink("https://v.douyin.com/abc/")) is None
    client.assert_not_called()
