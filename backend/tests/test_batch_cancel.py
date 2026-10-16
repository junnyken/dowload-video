"""The "Dừng" button: POST /jobs/{batch_id}/cancel and the worker skip."""
import asyncio

import pytest
from fastapi import HTTPException

import app.api.routes as routes


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.patch, self.single_row = db, table, [], None, False

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def in_(self, k, vs):
        vs = list(vs)
        self.filters.append(lambda r: r.get(k) in vs)
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def single(self):
        self.single_row = True
        return self

    def execute(self):
        rows = [r for r in self.db if all(f(r) for f in self.filters)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
        data = (rows[0] if rows else None) if self.single_row else [dict(r) for r in rows]
        return type("R", (), {"data": data})()


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "download_jobs"
        return _Q(self.rows, name)


def _cancel(monkeypatch, rows, batch="B1"):
    db = _DB(rows)
    monkeypatch.setattr(routes, "get_supabase_client", lambda: db)
    fn = getattr(routes.cancel_batch, "__wrapped__", routes.cancel_batch)
    return asyncio.run(fn(batch, request=None)), db.rows


def _row(i, status, **kw):
    return {"id": f"j{i}", "batch_id": "B1", "status": status,
            "original_url": f"https://www.xiaohongshu.com/explore/{i}", "error_message": None, **kw}


def test_pending_jobs_stop_running_and_finished_ones_stay(monkeypatch):
    rows = [_row(0, "success"), _row(1, "processing"), _row(2, "pending"),
            _row(3, "pending"), _row(4, "failed"), _row(5, "queued")]
    res, after = _cancel(monkeypatch, rows)
    assert res == {"success": True, "cancelled": 3, "still_running": 1}
    assert [r["status"] for r in after] == [
        "success", "processing", "cancelled", "cancelled", "failed", "cancelled"]
    assert after[2]["error_message"] == "Đã dừng theo yêu cầu."


def test_channel_still_scanning_is_cancelled(monkeypatch):
    rows = [_row(0, "processing", error_message="Đang quét kênh..."),
            _row(1, "processing", error_message="Dang quet danh sach video... (35s)")]
    res, after = _cancel(monkeypatch, rows)
    assert res["cancelled"] == 2
    assert {r["status"] for r in after} == {"cancelled"}


def test_other_batches_and_zip_untouched(monkeypatch):
    rows = [_row(0, "pending"), dict(_row(1, "pending"), batch_id="B2"),
            dict(_row(2, "pending"), original_url="batch_zip")]
    res, after = _cancel(monkeypatch, rows)
    assert res["cancelled"] == 1
    assert [r["status"] for r in after] == ["cancelled", "pending", "pending"]


def test_unknown_batch_is_404(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _cancel(monkeypatch, [_row(0, "pending")], batch="nope")
    assert ei.value.status_code == 404


def test_scan_sees_the_cancel(monkeypatch):
    from app.tasks.video_tasks import _batch_cancelled
    db = _DB([_row(0, "cancelled"), _row(1, "processing")])
    assert _batch_cancelled(db, "j0") is True
    assert _batch_cancelled(db, "j1") is False
    assert _batch_cancelled(db, "missing") is False
