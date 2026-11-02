"""Anonymous callers without a session must never edit or delete notes."""
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_no_identity_cannot_mutate_note(monkeypatch, method):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import notes
    from app.core.auth_middleware import get_optional_user

    db = Mock()
    monkeypatch.setattr(notes, "get_service_client", lambda: db)
    async def guest():
        return None
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_optional_user] = guest
    try:
        response = TestClient(app).request(
            method, "/api/v1/jobs/synthetic-job/notes/synthetic-note",
            **({"json": {"note_text": "synthetic update"}} if method == "PATCH" else {}),
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 404
    # No unscoped query may ever execute, even with a valid-looking object ID.
    assert not any(c[0].endswith("execute") for c in db.mock_calls)


def test_malformed_hostname_is_rejected_before_dns(monkeypatch):
    from fastapi import HTTPException
    from app.core import ssrf_guard
    resolver = Mock(side_effect=AssertionError("must not resolve malformed host"))
    monkeypatch.setattr(ssrf_guard, "_resolve", resolver)
    with pytest.raises(HTTPException) as error:
        ssrf_guard.assert_safe_url("https://" + "a" * 64 + ".example/video")
    assert error.value.status_code == 400
    resolver.assert_not_called()
