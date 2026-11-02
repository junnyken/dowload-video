"""Unexpected provider errors must not be reflected into API responses."""
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("method,url,module", [
    ("GET", "/api/v1/jobs/synthetic-job/notes", "notes"),
    ("DELETE", "/api/v1/presets/synthetic-preset", "presets"),
    ("POST", "/api/v1/bulk-zip-rename", "batch_rename"),
])
def test_unexpected_database_error_is_not_reflected(monkeypatch, method, url, module):
    import importlib
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.auth_middleware import get_optional_user

    handler = importlib.import_module(f"app.api.{module}")
    sentinel = "synthetic-private-db-host:5432 password=synthetic-do-not-return"
    db = Mock()
    db.table.side_effect = RuntimeError(sentinel)
    monkeypatch.setattr(handler, "get_service_client", lambda: db)

    async def owner():
        return {"id": "synthetic-user-a"}

    if module == "presets":
        monkeypatch.setattr(handler, "_auth", owner)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_optional_user] = owner
    try:
        kwargs = {"json": {"batch_id": "synthetic-batch"}} if method == "POST" else {}
        response = TestClient(app).request(method, url, **kwargs)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 500
    assert sentinel not in response.text
    assert "synthetic-private-db-host" not in response.text
    assert "password=" not in response.text
