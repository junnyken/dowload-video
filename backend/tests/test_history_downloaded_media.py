"""Quality #3 in the history (28/10/2026): the ffprobe measurement of a finished
file is kept in download_jobs.downloaded_media and read back by /user/history.

The column is created by the API itself (database.ensure_columns) because the
Vibe Host Postgres has no migration runner — so every reader and writer must
also work while it is still absent.

Runs against a real Postgres when PG_TEST_DSN is set (see test_pg_client.py).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
DSN = os.getenv("PG_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN not set")

from app.core.pg_client import PgClient  # noqa: E402

SCHEMA = """
DROP SCHEMA public CASCADE; CREATE SCHEMA public;
CREATE TABLE download_jobs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), batch_id text, user_id uuid,
  original_url text, platform text, title text, thumbnail_url text, status text, file_size_mb float,
  downloaded_height int DEFAULT 0, is_audio_only boolean DEFAULT false, selected_quality text,
  source_surface text, error_message text, created_at timestamptz DEFAULT now());
CREATE TABLE job_metadata (job_id uuid PRIMARY KEY REFERENCES download_jobs(id), creator_handle text,
  creator_name text, duration_seconds int, view_count int, like_count int, upload_date text,
  language_detected text, hashtags text[], categories text[], description_snippet text, thumbnail_url text);
CREATE TABLE clip_notes (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), job_id uuid REFERENCES download_jobs(id));
SELECT 1
"""
USER = str(uuid.uuid4())
MEDIA = {"measured": True, "vcodec": "vp9", "short_side": 2160, "fps": 60.0, "bitrate_kbps": 12000, "hdr": True}


@pytest.fixture()
def db(monkeypatch):
    c = PgClient(DSN)
    c.run_sql(SCHEMA)
    import app.core.database as database
    import app.api.user as user_api
    monkeypatch.setattr(database, "get_supabase_client", lambda: c)
    monkeypatch.setattr(user_api, "get_supabase_client", lambda: c)
    monkeypatch.setattr(user_api, "get_user_tier", lambda _uid: "pro")
    return c


def _history():
    from app.api.user import get_user_history
    out = asyncio.run(get_user_history(
        user={"id": USER}, page=1, per_page=20, search=None, platform=None, creator=None,
        language=None, has_notes=False, sort_by="newest", date_from=None, date_to=None, tag=None))
    return out["items"]


def _has_column(c) -> bool:
    return bool(c.run_sql("SELECT 1 AS x FROM information_schema.columns "
                          "WHERE table_name = 'download_jobs' AND column_name = 'downloaded_media'"))


def test_history_still_reads_before_the_column_exists(db):
    db.table("download_jobs").insert({"user_id": USER, "status": "success", "title": "a"}).execute()
    items = _history()
    assert [i["title"] for i in items] == ["a"]
    assert "downloaded_media" not in items[0]


def test_ensure_columns_is_idempotent_and_refreshes_the_catalog(db):
    # the client has already cached download_jobs' columns (without the new one)
    db.table("download_jobs").insert({"user_id": USER, "status": "success", "title": "old"}).execute()
    from app.core.database import ensure_columns
    ensure_columns()
    ensure_columns()
    assert _has_column(db)
    db.table("download_jobs").insert({"user_id": USER, "status": "success", "title": "new",
                                      "downloaded_media": MEDIA}).execute()
    by_title = {i["title"]: i for i in _history()}
    assert by_title["new"]["downloaded_media"] == MEDIA
    assert by_title["old"]["downloaded_media"] is None


def test_worker_update_strips_the_column_when_absent(db):
    from app.tasks import video_tasks
    job = db.table("download_jobs").insert({"user_id": USER, "status": "processing"}).execute().data[0]
    monkey_missing = set()
    orig = video_tasks._MISSING_COLS
    video_tasks._MISSING_COLS = monkey_missing
    try:
        video_tasks._sb_update(db, {"status": "success", "downloaded_media": MEDIA}, job["id"])
    finally:
        video_tasks._MISSING_COLS = orig
    assert "downloaded_media" in monkey_missing
    row = db.table("download_jobs").select("status").eq("id", job["id"]).execute().data[0]
    assert row["status"] == "success"


def test_ensure_columns_is_a_no_op_without_postgres(monkeypatch):
    import app.core.database as database

    class SupabaseLike:  # no run_sql / _pool
        pass

    monkeypatch.setattr(database, "get_supabase_client", lambda: SupabaseLike())
    database.ensure_columns()  # must not raise
