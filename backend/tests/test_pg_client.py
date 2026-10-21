"""PgClient: supabase-py's query interface over plain Postgres (21/10/2026).

Runs against a real Postgres when PG_TEST_DSN is set (skipped otherwise):
  docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_DB=vidgrab postgres:16-alpine
  PG_TEST_DSN=postgresql://postgres:test@localhost:55432/vidgrab pytest tests/test_pg_client.py
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
DSN = os.getenv("PG_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN not set")

from postgrest.exceptions import APIError  # noqa: E402

from app.core.pg_client import PgClient  # noqa: E402

SCHEMA = """
DROP SCHEMA public CASCADE; CREATE SCHEMA public;
CREATE TABLE profiles (id uuid PRIMARY KEY, email text UNIQUE, tier text DEFAULT 'free',
  downloads_today int DEFAULT 0, credits numeric(10,2) DEFAULT 0, created_at timestamptz DEFAULT now());
CREATE TABLE download_jobs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid REFERENCES profiles(id),
  status text, title text, job_metadata jsonb, collection_ids text[] DEFAULT '{}', file_size_mb float,
  local_file_path text, created_at timestamptz DEFAULT now(), is_audio_only boolean DEFAULT false);
CREATE TABLE job_metadata (job_id uuid PRIMARY KEY, data jsonb, hits int DEFAULT 0);
CREATE TABLE webhook_endpoints (id uuid PRIMARY KEY, url text);
CREATE TABLE webhook_deliveries (id serial PRIMARY KEY, endpoint_id uuid REFERENCES webhook_endpoints(id), status text);
CREATE FUNCTION bump_hits(p_job uuid) RETURNS int LANGUAGE sql AS
  $$ UPDATE job_metadata SET hits = hits + 1 WHERE job_id = p_job RETURNING hits $$;
CREATE FUNCTION jobs_of(p_user uuid) RETURNS SETOF download_jobs LANGUAGE sql AS
  $$ SELECT * FROM download_jobs WHERE user_id = p_user $$;
"""
U1, U2 = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture(scope="module")
def db():
    c = PgClient(DSN)
    c.run_sql(SCHEMA + " SELECT 1")
    c.table("profiles").insert([{"id": U1, "email": "a@x.vn", "tier": "pro"},
                                {"id": U2, "email": "b@x.vn"}]).execute()
    c.table("download_jobs").insert([
        {"user_id": U1, "status": "success", "title": "Mèo con", "job_metadata": {"k": [1, 2]},
         "collection_ids": ["c1", "c2"], "file_size_mb": 1.5, "local_file_path": "/d/a.mp4"},
        {"user_id": U1, "status": "pending", "title": "Chó", "collection_ids": ["c2"]},
        {"user_id": U2, "status": "failed", "title": None},
    ]).execute()
    return c


def test_select_filters_order_range_count(db):
    r = (db.table("download_jobs").select("title, status", count="exact").eq("user_id", U1)
         .order("title", desc=True).range(0, 0).execute())
    assert r.count == 2 and r.data == [{"title": "Mèo con", "status": "success"}]
    assert len(db.table("download_jobs").select("id").in_("status", ["success", "failed"]).execute().data) == 2
    assert db.table("download_jobs").select("id").in_("status", []).execute().data == []
    assert len(db.table("download_jobs").select("id").neq("status", "success").execute().data) == 2
    assert len(db.table("download_jobs").select("id").is_("title", "null").execute().data) == 1
    assert len(db.table("download_jobs").select("id").not_.is_("title", "null").execute().data) == 2
    assert len(db.table("download_jobs").select("id").ilike("title", "%MÈO%").execute().data) == 1
    assert len(db.table("download_jobs").select("id").contains("collection_ids", ["c2"]).execute().data) == 2
    assert len(db.table("download_jobs").select("id").or_(
        f"local_file_path.eq./d/a.mp4,status.eq.failed").execute().data) == 2
    assert len(db.table("download_jobs").select("id").not_.in_("status", ["success"]).execute().data) == 2
    assert len(db.table("download_jobs").select("id").gte("file_size_mb", 1).lt("file_size_mb", 2).execute().data) == 1
    assert db.table("download_jobs").select("id", count="exact").eq("status", "pending").limit(1).execute().count == 1


def test_values_come_back_like_postgrest(db):
    row = db.table("download_jobs").select("*").eq("status", "success").single().execute().data
    assert isinstance(row["id"], str) and isinstance(row["created_at"], str) and "T" in row["created_at"]
    assert row["job_metadata"] == {"k": [1, 2]} and row["collection_ids"] == ["c1", "c2"]
    p = db.table("profiles").select("credits, tier").eq("id", U1).single().execute().data
    assert p == {"credits": 0, "tier": "pro"}
    assert isinstance(db.table("profiles").select("created_at").eq("id", U2).execute().data[0]["created_at"], str)


def test_single_and_maybe_single(db):
    with pytest.raises(APIError) as e:
        db.table("profiles").select("id").eq("email", "none@x.vn").single().execute()
    assert e.value.code == "PGRST116"
    assert db.table("profiles").select("id").eq("email", "none@x.vn").maybe_single().execute() is None
    assert db.table("profiles").select("email").eq("id", U2).maybe_single().execute().data == {"email": "b@x.vn"}
    with pytest.raises(APIError):
        db.table("profiles").select("id").single().execute()       # two rows


def test_insert_update_upsert_delete_return_rows(db):
    jid = str(uuid.uuid4())
    ins = db.table("job_metadata").insert({"job_id": jid, "data": {"a": 1}}).execute()
    assert ins.data[0]["job_id"] == jid and ins.data[0]["hits"] == 0
    up = db.table("job_metadata").upsert({"job_id": jid, "data": {"a": 2}}, on_conflict="job_id").execute()
    assert up.data[0]["data"] == {"a": 2}
    up = db.table("job_metadata").upsert({"job_id": jid, "data": {"a": 3}}).execute()     # primary key
    assert up.data[0]["data"] == {"a": 3}
    keep = db.table("job_metadata").upsert({"job_id": jid, "data": {"a": 9}}, on_conflict="job_id",
                                           ignore_duplicates=True).execute()
    assert keep.data == []
    assert db.table("job_metadata").select("data").eq("job_id", jid).single().execute().data == {"data": {"a": 3}}
    u = db.table("profiles").update({"downloads_today": 5}).eq("id", U2).execute()
    assert u.data[0]["downloads_today"] == 5
    assert db.table("job_metadata").update({"data": None}).eq("job_id", jid).execute().data[0]["data"] is None
    d = db.table("job_metadata").delete().eq("job_id", jid).execute()
    assert len(d.data) == 1 and db.table("job_metadata").select("*").eq("job_id", jid).execute().data == []
    many = db.table("download_jobs").update({"status": "x"}).in_("local_file_path", ["/nope"]).execute()
    assert many.data == []


def test_errors_carry_the_postgres_code(db):
    with pytest.raises(APIError) as e:
        db.table("profiles").insert({"id": str(uuid.uuid4()), "email": "a@x.vn"}).execute()
    assert e.value.code == "23505"                                      # unique_violation, as PostgREST
    with pytest.raises(APIError) as e:
        db.table("profiles").select("nope").execute()
    assert e.value.code == "42703"
    with pytest.raises(APIError) as e:
        db.table("profiles").select("id").eq("nope", 1).execute()
    assert e.value.code == "42703"
    with pytest.raises(APIError) as e:
        db.table("profiles").update({"nope": 1}).eq("id", U1).execute()
    assert e.value.code == "PGRST204"
    with pytest.raises(APIError):
        db.table("profiles").select("id").eq("id; drop table profiles", 1).execute()
    with pytest.raises(APIError):
        db.table("no_such_table").select("*").execute()
    assert db.table("profiles").select("id").execute().data              # still there


def test_values_never_reach_sql_text(db):
    evil = "x'); DROP TABLE profiles; --"
    assert db.table("profiles").select("id").eq("email", evil).execute().data == []
    db.table("download_jobs").update({"title": evil}).eq("user_id", U2).execute()
    assert db.table("download_jobs").select("title").eq("user_id", U2).single().execute().data["title"] == evil


def test_embedded_relation_and_rpc(db):
    eid = str(uuid.uuid4())
    db.table("webhook_endpoints").insert({"id": eid, "url": "https://h"}).execute()
    db.table("webhook_deliveries").insert({"endpoint_id": eid, "status": "pending"}).execute()
    r = db.table("webhook_deliveries").select("*, webhook_endpoints(*)").eq("status", "pending").execute().data
    assert r[0]["webhook_endpoints"] == {"id": eid, "url": "https://h"}
    jid = str(uuid.uuid4())
    db.table("job_metadata").insert({"job_id": jid}).execute()
    assert db.rpc("bump_hits", {"p_job": jid}).execute().data == 1
    assert db.rpc("bump_hits", {"p_job": jid}).execute().data == 2
    jobs = db.rpc("jobs_of", {"p_user": U1}).execute().data
    assert len(jobs) == 2 and isinstance(jobs[0]["id"], str)


def test_works_after_fork_and_from_threads(db):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(8) as ex:
        counts = list(ex.map(lambda _: len(db.table("profiles").select("id").execute().data), range(32)))
    assert set(counts) == {2}
