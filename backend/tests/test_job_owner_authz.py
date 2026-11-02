"""Synthetic A/B regression tests for the focused job ownership boundary.

Run ONLY in the OS-enforced offline, credential-free, read-only-source sandbox
required by the security review. No real DB, Redis or queue is used by these tests.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


A = "synthetic-owner-a"
B = "synthetic-owner-b"
JOB_A = "synthetic-job-a"
JOB_B = "synthetic-job-b"


class ScopedDB:
    """In-memory PostgREST double: apply actual filters to synthetic A/B rows."""

    def __init__(self):
        self.rows = [
            {"id": jid, "user_id": owner, "original_url": "https://example.invalid/video",
             "status": "failed", "job_stage": "failed", "recovery_attempts": 0,
             "selected_quality": "video", "created_at": "2026-01-01T00:00:00Z"}
            for jid, owner in ((JOB_A, A), (JOB_B, B))
        ]
        self.queries = []
        self.events = []
        self.fail_update = False

    def table(self, name):
        assert name == "download_jobs"
        query = Query(self)
        self.queries.append(query)
        return query


class Query:
    def __init__(self, db):
        self.db = db
        self.filters = []
        self.patch = None
        self.one = False
        self.max_rows = None

    def select(self, columns):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, column, desc=False):
        return self

    def limit(self, value):
        assert 1 <= value <= 20
        self.max_rows = value
        return self

    def single(self):
        self.one = True
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        matches = [row for row in self.db.rows
                   if all(row.get(key) == value for key, value in self.filters)]
        self.db.events.append("read" if self.patch is None else "write")
        if self.patch is not None:
            if self.db.fail_update:
                raise RuntimeError("synthetic-private-db-error-must-not-escape")
            for row in matches:
                row.update(self.patch)
        if self.max_rows is not None:
            matches = matches[:self.max_rows]
        return SimpleNamespace(data=deepcopy(matches[0] if matches and self.one
                                             else None if self.one else matches))


@pytest.fixture
def boundary(monkeypatch):
    # Shared conftest initializes the real application before importing routes.
    # Do not enter its lifespan: it starts unrelated recovery/worker services.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import mobile, routes
    from app.core import auth_middleware, database, recovery
    from app.tasks import video_tasks

    db = ScopedDB()
    get_db = Mock(return_value=db)
    monkeypatch.setattr(database, "get_service_client", get_db)
    monkeypatch.setattr(mobile, "get_service_client", get_db)
    invalidate = Mock(side_effect=lambda job: db.events.append("invalidate"))
    delay = Mock(side_effect=lambda *args: db.events.append("enqueue"))
    log = Mock()
    monkeypatch.setattr(recovery, "_invalidate_job_lease", invalidate)
    monkeypatch.setattr(recovery, "_log_recovery", log)
    monkeypatch.setattr(video_tasks.process_video_task, "delay", delay)
    monkeypatch.setattr(routes.limiter, "enabled", False)

    principal = {"user": None}

    async def identity():
        return principal["user"]

    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/v1")
    app.include_router(routes.router, prefix="/api/v1")
    # Keep get_required_user real: it must reject the anonymous optional result.
    app.dependency_overrides[auth_middleware.get_optional_user] = identity
    client = TestClient(app)
    yield SimpleNamespace(db=db, get_db=get_db, invalidate=invalidate, delay=delay,
                          log=log, principal=principal, client=client, recovery=recovery)
    client.close()


@pytest.mark.parametrize("session", ["", "?session_id=synthetic-session"])
def test_mobile_anonymous_empty_without_db(boundary, session):
    response = boundary.client.get("/api/v1/mobile/recent-jobs" + session)
    assert response.status_code == 200
    assert response.json() == []
    boundary.get_db.assert_not_called()


def test_mobile_anonymous_cookie_empty_without_db(boundary):
    boundary.client.cookies.set("vg_session_id", "synthetic-cookie")
    response = boundary.client.get("/api/v1/mobile/recent-jobs")
    assert response.status_code == 200
    assert response.json() == []
    boundary.get_db.assert_not_called()


@pytest.mark.parametrize("owner,job", [(A, JOB_A), (B, JOB_B)])
def test_mobile_only_returns_own_jobs_despite_session_input(boundary, owner, job):
    boundary.principal["user"] = {"id": owner}
    response = boundary.client.get("/api/v1/mobile/recent-jobs?session_id=untrusted&user_id=other&limit=20")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [job]
    assert boundary.db.queries[0].filters == [("user_id", owner)]


@pytest.mark.parametrize("limit", ["0", "-1", "21", "invalid"])
def test_mobile_rejects_out_of_bounds_limits_without_db(boundary, limit):
    boundary.principal["user"] = {"id": A}
    response = boundary.client.get("/api/v1/mobile/recent-jobs?limit=" + limit)
    assert response.status_code == 422
    boundary.get_db.assert_not_called()


def assert_no_side_effects(boundary, original):
    assert boundary.db.rows == original
    boundary.invalidate.assert_not_called()
    boundary.delay.assert_not_called()
    boundary.log.assert_not_called()


def test_refresh_http_anonymous_401_no_query(boundary):
    original = deepcopy(boundary.db.rows)
    response = boundary.client.post(f"/api/v1/jobs/{JOB_A}/refresh")
    assert response.status_code == 401
    boundary.get_db.assert_not_called()
    assert_no_side_effects(boundary, original)


@pytest.mark.parametrize("identity", [None, ""])
def test_refresh_service_rejects_missing_identity_before_db(boundary, identity):
    original = deepcopy(boundary.db.rows)
    assert boundary.recovery.refresh_job_link(JOB_A, identity)["success"] is False
    boundary.get_db.assert_not_called()
    assert_no_side_effects(boundary, original)


def test_refresh_nonowner_cannot_reset_lease_mutate_or_enqueue(boundary):
    boundary.principal["user"] = {"id": B}
    original = deepcopy(boundary.db.rows)
    response = boundary.client.post(f"/api/v1/jobs/{JOB_A}/refresh?user_id={A}",
                                    json={"user_id": A})
    assert response.status_code == 400
    assert boundary.db.queries[0].filters == [("id", JOB_A), ("user_id", B)]
    assert len(boundary.db.queries) == 1
    assert_no_side_effects(boundary, original)


def test_refresh_service_nonowner_independently_rejected(boundary):
    original = deepcopy(boundary.db.rows)
    assert boundary.recovery.refresh_job_link(JOB_A, B)["success"] is False
    assert boundary.db.queries[0].filters == [("id", JOB_A), ("user_id", B)]
    assert_no_side_effects(boundary, original)


@pytest.mark.parametrize("owner,job", [(A, JOB_A), (B, JOB_B)])
def test_refresh_owner_scopes_read_and_write_and_dispatches_owner(boundary, owner, job):
    boundary.principal["user"] = {"id": owner}
    other_before = deepcopy([row for row in boundary.db.rows if row["id"] != job])
    response = boundary.client.post(f"/api/v1/jobs/{job}/refresh?user_id=untrusted",
                                    json={"user_id": "untrusted"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(boundary.db.queries) == 2
    for query in boundary.db.queries:
        assert query.filters == [("id", job), ("user_id", owner)]
    owned = next(row for row in boundary.db.rows if row["id"] == job)
    assert owned["status"] == "pending"
    assert owned["recovery_attempts"] == 1
    assert [row for row in boundary.db.rows if row["id"] != job] == other_before
    boundary.invalidate.assert_called_once_with(job)
    boundary.delay.assert_called_once_with(job, owned["original_url"], owner, "video")
    assert boundary.db.events == ["read", "invalidate", "write", "enqueue"]


def test_refresh_error_does_not_disclose_exception(boundary):
    boundary.principal["user"] = {"id": A}
    boundary.db.fail_update = True
    response = boundary.client.post(f"/api/v1/jobs/{JOB_A}/refresh")
    assert response.status_code == 400
    assert "synthetic-private-db-error" not in response.text
    boundary.delay.assert_not_called()
