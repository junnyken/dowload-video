"""
Daily database backup
=====================

There was no database backup. scripts/backup.sh saves Redis and a list of env
var NAMES — it contains no reference to pg_dump or Supabase — and nothing
scheduled it anyway. When download_jobs was emptied by an unauthenticated
DELETE endpoint, there was nothing to restore from, and the project's own
keepalive comment records that this was the second time production data was
lost this way.

Why this is a logical export and not pg_dump
--------------------------------------------
pg_dump needs a Postgres connection string. The backend has SUPABASE_URL plus
the anon and service keys — no DATABASE_URL, no database password — and the
image has no postgresql-client. Adding pg_dump means putting the database
password into the environment, which is a decision for whoever owns the
Supabase project, not something to assume.

So this exports through the APIs the service key already opens:

  * every table PostgREST exposes, paged, as JSON
  * auth.users via the Auth admin API (not reachable over PostgREST, and the
    single most painful thing to lose — it is what every profiles row hangs off)

What it does NOT capture: schema, functions, triggers, RLS policies. Those live
in database/migrations/ in git. Schema from git + data from here is a restore;
neither half alone is.

Destinations
------------
1. Supabase Storage — always. Needs no credential the app does not already
   hold, so it works the day it ships. It covers the failure that actually
   happened, a table wiped by application code. It is NOT off-site: if the
   project itself is lost or paused, this copy goes with it.

2. A Postgres database on the hosting platform — when BACKUP_PG_DSN is set.
   Different provider from Supabase, so it covers the realistic failure: the
   Supabase project lost, paused, or emptied by application code. It does NOT
   cover losing the hosting account, because then the app and this copy go
   together.

3. Any S3-compatible bucket — when BACKUP_S3_ENDPOINT / _BUCKET / _ACCESS_KEY
   / _SECRET_KEY are all set. Cloudflare R2 is the cheap option. This is the
   only one that is genuinely off-site from both of the above.

With no S3 target configured the task still succeeds and reports
"off-site: chưa cấu hình". When a target IS configured and the copy fails, that
is a warning on the run, because believing you have an off-site backup you do
not have is worse than knowing you have none.

Redis configuration (task #6332)
--------------------------------
The same run also writes ``redis-config-<date>.json.gz``: the admin-made
configuration held in Redis (allow-list and secret rules in
app/core/redis_config_backup.py). Secrets are metadata-only unless the admin
System Config entry ``backup_redis_include_secrets`` is ``true``; even then the
copy WITH secrets goes only to the private Supabase bucket — S3 and Postgres
always get the metadata-only copy. A Redis failure is a warning on the run; it
never fails the database backup. Restore: backend/scripts/restore_redis_config.py,
drill: docs/runbooks/backup-restore.md.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from app.core.celery_app import celery_app

BUCKET = os.getenv("BACKUP_BUCKET", "db-backups")
RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
PAGE = 1000
# Per-table ceiling so one runaway table cannot exhaust the worker's memory.
# Hitting it is reported, never silently truncated — a backup that quietly
# drops rows is worse than no backup, because you stop worrying.
MAX_ROWS_PER_TABLE = int(os.getenv("BACKUP_MAX_ROWS_PER_TABLE", "200000"))
TIMEOUT = httpx.Timeout(60.0)


def _cfg() -> tuple[str, str]:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    return url, key


def _headers(key: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _list_tables(client: httpx.Client, url: str, key: str) -> list[str]:
    """Ask PostgREST what it exposes rather than hardcoding a list that drifts."""
    r = client.get(f"{url}/rest/v1/", headers=_headers(key))
    r.raise_for_status()
    return sorted((r.json().get("definitions") or {}).keys())


def _order_column(row: dict[str, Any]) -> str | None:
    """
    Offset paging without ORDER BY can repeat or skip rows between requests.
    Pick a stable column off a sample row; tables with none are read in one
    page and flagged if they turn out to be larger than that.
    """
    for candidate in ("id", "created_at", "user_id", "date"):
        if candidate in row:
            return candidate
    return None


def _dump_table(client: httpx.Client, url: str, key: str, table: str) -> tuple[list[dict], str | None]:
    """Return (rows, warning)."""
    probe = client.get(
        f"{url}/rest/v1/{table}", headers=_headers(key), params={"select": "*", "limit": 1}
    )
    if probe.status_code != 200:
        return [], f"{table}: HTTP {probe.status_code} {probe.text[:120]}"

    sample = probe.json()
    if not sample:
        return [], None

    order = _order_column(sample[0])
    if not order:
        r = client.get(
            f"{url}/rest/v1/{table}",
            headers=_headers(key),
            params={"select": "*", "limit": MAX_ROWS_PER_TABLE},
        )
        rows = r.json() if r.status_code == 200 else []
        warn = (f"{table}: no stable ordering column; read a single page of "
                f"{len(rows)} rows") if len(rows) >= PAGE else None
        return rows, warn

    rows: list[dict] = []
    offset = 0
    while offset < MAX_ROWS_PER_TABLE:
        r = client.get(
            f"{url}/rest/v1/{table}",
            headers=_headers(key),
            params={"select": "*", "order": f"{order}.asc", "limit": PAGE, "offset": offset},
        )
        if r.status_code != 200:
            return rows, f"{table}: HTTP {r.status_code} at offset {offset}"
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows, None
        offset += PAGE

    return rows, (f"{table}: hit the {MAX_ROWS_PER_TABLE}-row cap — this backup is "
                  f"INCOMPLETE for this table; raise BACKUP_MAX_ROWS_PER_TABLE")


def _dump_auth_users(client: httpx.Client, url: str, key: str) -> tuple[list[dict], str | None]:
    """auth.users is invisible to PostgREST and is what profiles rows hang off."""
    users: list[dict] = []
    page = 1
    while True:
        r = client.get(
            f"{url}/auth/v1/admin/users",
            headers=_headers(key),
            params={"page": page, "per_page": 200},
        )
        if r.status_code != 200:
            return users, f"auth.users: HTTP {r.status_code} {r.text[:120]}"
        body = r.json()
        batch = body.get("users", body) if isinstance(body, dict) else body
        if not batch:
            return users, None
        users.extend(batch)
        if len(batch) < 200:
            return users, None
        page += 1


def _ensure_bucket(client: httpx.Client, url: str, key: str) -> None:
    r = client.post(
        f"{url}/storage/v1/bucket",
        headers={**_headers(key), "Content-Type": "application/json"},
        json={"name": BUCKET, "public": False},
    )
    # "Already exists" is the normal case on every run after the first, and
    # Supabase reports it as HTTP 400 with statusCode 409 in the body — so
    # matching on the status code alone printed an alarming line daily. A
    # warning that cries wolf every day is a warning you stop reading.
    if r.status_code in (200, 201, 409):
        return
    if r.status_code == 400 and "already exists" in r.text.lower():
        return
    print(f"[Backup] bucket create returned {r.status_code}: {r.text[:200]}")


def _upload(client: httpx.Client, url: str, key: str, path: str, blob: bytes) -> None:
    r = client.post(
        f"{url}/storage/v1/object/{BUCKET}/{path}",
        headers={**_headers(key), "Content-Type": "application/gzip", "x-upsert": "true"},
        content=blob,
    )
    r.raise_for_status()


def _s3_configured() -> bool:
    return all(os.getenv(v) for v in (
        "BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET",
        "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY",
    ))


def _upload_offsite(path: str, blob: bytes) -> str | None:
    """
    Copy the backup somewhere that is not the Supabase project it came from.

    Returns None when no off-site target is configured — that is a normal,
    reported state, not an error. It returns an error string when a target IS
    configured but the upload failed, because a backup you believe is off-site
    and is not is worse than knowing you have none.

    Works with any S3-compatible endpoint; Cloudflare R2 is the cheap one.
    """
    if not _s3_configured():
        return None

    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        return "boto3 not installed — off-site copy skipped"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
            aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"],
            region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        )
        s3.put_object(
            Bucket=os.environ["BACKUP_S3_BUCKET"],
            Key=f"vidgrab/{path}",
            Body=blob,
            ContentType="application/gzip",
        )
        return None
    except Exception as exc:  # noqa: BLE001
        return f"off-site upload FAILED: {str(exc)[:160]}"


def _pg_configured() -> bool:
    return bool(os.getenv("BACKUP_PG_DSN"))


def _check_db_dump(restored: dict[str, Any]) -> str | None:
    if not restored.get("tables"):
        return "restored dump has no tables"
    return None


def _check_redis_dump(restored: dict[str, Any]) -> str | None:
    from app.core.redis_config_backup import FORMAT  # noqa: PLC0415
    if restored.get("format") != FORMAT:
        return "restored file is not a Redis config backup"
    if restored.get("include_secrets"):
        # Defence in depth: this destination must never hold credentials.
        return "refusing: Redis copy WITH secrets must not go to Postgres"
    return None


def _upload_postgres(name: str, blob: bytes, check=_check_db_dump) -> str | None:
    """
    Second copy, into a Postgres database on the hosting platform.

    Same contract as _upload_offsite: None when no target is configured (a
    normal, reported state), an error string when one IS configured and the
    write failed — believing you have a second copy you do not have is worse
    than knowing you have one.

    Be honest about what this protects against. It lives at a different
    provider from Supabase, so it covers the realistic failure: the Supabase
    project lost, paused, or emptied by application code. It does NOT cover
    losing the hosting account, because then the app and this copy go together.
    BACKUP_S3_* remains the genuinely off-site target.

    The whole dump is one gzip blob of about 25 KB, so a single bytea column is
    the right shape — no large-object plumbing, and a restore is one SELECT.
    """
    dsn = os.getenv("BACKUP_PG_DSN", "")
    if not dsn:
        return None

    try:
        import psycopg2  # noqa: PLC0415
    except ImportError:
        return "psycopg2 not installed — Postgres copy skipped"

    try:
        # connect_timeout so a database that is down fails the copy in seconds
        # rather than hanging the whole nightly task on a TCP timeout.
        conn = psycopg2.connect(dsn, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001
        return f"Postgres copy FAILED (connect): {str(exc)[:160]}"

    try:
        conn.autocommit = False
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS db_backups (
                    name        TEXT        PRIMARY KEY,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    size_bytes  INTEGER     NOT NULL,
                    data        BYTEA       NOT NULL
                )
            """)
            # Re-running on the same day replaces that day's row rather than
            # failing on the primary key — the file name is per-day.
            cur.execute(
                """
                INSERT INTO db_backups (name, size_bytes, data)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                SET size_bytes = EXCLUDED.size_bytes,
                    data       = EXCLUDED.data,
                    created_at = NOW()
                """,
                (name, len(blob), psycopg2.Binary(blob)),
            )
            cur.execute(
                "DELETE FROM db_backups WHERE created_at < NOW() - %s::interval",
                (f"{RETENTION_DAYS} days",),
            )

        # Read it straight back and prove it is restorable.
        #
        # "The INSERT did not raise" is a much weaker claim than it sounds: it
        # says bytes reached the server, not that they come back intact or that
        # what comes back is a usable dump. This database is only reachable
        # from inside the platform network, so nobody outside can spot-check it
        # — the proof has to be produced here or it does not exist. Decompress
        # the stored bytes and confirm they still parse as the dump, with the
        # tables and auth users in them.
        with conn, conn.cursor() as cur:
            cur.execute("SELECT data, size_bytes FROM db_backups WHERE name = %s", (name,))
            row = cur.fetchone()
        if not row:
            return "Postgres copy FAILED: row missing immediately after write"

        stored = bytes(row[0])
        if len(stored) != len(blob):
            return (f"Postgres copy FAILED: stored {len(stored)} bytes, "
                    f"wrote {len(blob)}")
        try:
            restored = json.loads(gzip.decompress(stored).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return f"Postgres copy FAILED: stored bytes do not restore: {str(exc)[:120]}"
        problem = check(restored)
        if problem:
            return f"Postgres copy FAILED: {problem}"

        print(f"[Backup] Postgres copy verified: {name} {len(stored)} bytes, "
              f"{len(restored.get('tables', {}))} tables, "
              f"{len(restored.get('auth_users', []))} auth users, "
              f"{len(restored.get('keys', []))} redis keys")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"Postgres copy FAILED: {str(exc)[:160]}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


_DATED = re.compile(r"^(?:vidgrab|redis-config)-(\d{4}-\d{2}-\d{2})\.json\.gz$")


def _stale_names(names: list[str], cutoff: str) -> list[str]:
    """
    Backup files older than the cutoff date.

    This used to be ``name < "vidgrab-<cutoff>"``, a string comparison that
    would have deleted every ``redis-config-*`` file the day it was written
    ("r" sorts before "v"). Compare the DATE in the name, and leave anything
    that does not look like one of our files alone.
    """
    out = []
    for n in names:
        m = _DATED.match(n or "")
        if m and m.group(1) < cutoff:
            out.append(n)
    return out


def _prune(client: httpx.Client, url: str, key: str) -> int:
    """Drop backups past the retention window."""
    r = client.post(
        f"{url}/storage/v1/object/list/{BUCKET}",
        headers={**_headers(key), "Content-Type": "application/json"},
        json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
    )
    if r.status_code != 200:
        return 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    stale = _stale_names([o.get("name", "") for o in r.json()], cutoff)
    if not stale:
        return 0

    d = client.request(
        "DELETE",
        f"{url}/storage/v1/object/{BUCKET}",
        headers={**_headers(key), "Content-Type": "application/json"},
        json={"prefixes": stale},
    )
    return len(stale) if d.status_code == 200 else 0


def _redis_raw():
    """A client that returns bytes, so values are backed up exactly."""
    import redis  # noqa: PLC0415
    return redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"),
                          socket_timeout=15, socket_connect_timeout=10)


def _backup_redis_config(client: httpx.Client, url: str, key: str,
                         day: str, rc=None) -> dict[str, Any]:
    """
    Export the Redis configuration and send it to the same destinations as the
    database dump. Never raises: Redis being down must not cost the night's
    database backup. Returns the "redis" part of the run result.
    """
    from app.core import redis_config_backup as rcb  # noqa: PLC0415

    name = f"redis-config-{day}.json.gz"
    out: dict[str, Any] = {"ok": False, "file": name, "warnings": []}
    try:
        rc = rc if rc is not None else _redis_raw()
        with_secrets = rcb.include_secrets_setting(rc)
        safe = rcb.export_redis_config(rc, include_secrets=False)
        full = rcb.export_redis_config(rc, include_secrets=True) if with_secrets else None
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Redis export FAILED: {type(exc).__name__}: {str(exc)[:160]}"
        return out

    safe_blob = rcb.to_blob(safe)
    # Private Supabase bucket: the copy with secrets when the owner turned that
    # on; every other destination only ever receives the metadata-only copy.
    bucket_blob = rcb.to_blob(full) if full is not None else safe_blob
    warnings: list[str] = list(safe.get("warnings", []))
    try:
        _upload(client, url, key, name, bucket_blob)
        bucket_ok = True
    except Exception as exc:  # noqa: BLE001
        bucket_ok = False
        warnings.append(f"Redis config upload to Supabase FAILED: {str(exc)[:160]}")

    s3_err = _upload_offsite(name, safe_blob)
    if s3_err:
        warnings.append(f"redis: {s3_err}")
    pg_err = _upload_postgres(name, safe_blob, check=_check_redis_dump)
    if pg_err:
        warnings.append(f"redis: {pg_err}")

    summary = (full or safe)["summary"]
    out.update({
        "ok": bucket_ok,
        "bytes": len(bucket_blob),
        "keys": sum(g["keys"] for g in summary.values()),
        "secrets_included": full is not None,
        "groups": summary,
        "offsite": _s3_configured() and not s3_err,
        "postgres": _pg_configured() and not pg_err,
        "warnings": warnings,
    })
    return out


@celery_app.task(name="backup_database_daily", ignore_result=True)
def backup_database_daily() -> dict[str, Any]:
    """Export every table + auth.users to Supabase Storage. Never raises."""
    started = datetime.now(timezone.utc)
    try:
        url, key = _cfg()
    except KeyError as exc:
        print(f"[Backup] missing credential {exc}; skipped")
        return {"ok": False, "error": f"missing {exc}"}

    warnings: list[str] = []
    counts: dict[str, int] = {}

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            tables = _list_tables(client, url, key)

            payload: dict[str, Any] = {
                "generated_at": started.isoformat(),
                "supabase_url": url,
                "note": "Data only. Schema lives in database/migrations/ in git.",
                "tables": {},
            }

            for table in tables:
                rows, warn = _dump_table(client, url, key, table)
                payload["tables"][table] = rows
                counts[table] = len(rows)
                if warn:
                    warnings.append(warn)

            users, warn = _dump_auth_users(client, url, key)
            payload["auth_users"] = users
            counts["auth.users"] = len(users)
            if warn:
                warnings.append(warn)

            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
            blob = buf.getvalue()

            name = f"vidgrab-{started.strftime('%Y-%m-%d')}.json.gz"
            _ensure_bucket(client, url, key)
            _upload(client, url, key, name, blob)
            pruned = _prune(client, url, key)

        offsite_err = _upload_offsite(name, blob)
        if offsite_err:
            warnings.append(offsite_err)
        offsite = "có" if (_s3_configured() and not offsite_err) else "chưa cấu hình"

        pg_err = _upload_postgres(name, blob)
        if pg_err:
            warnings.append(pg_err)
        pg_state = "có" if (_pg_configured() and not pg_err) else "chưa cấu hình"

        with httpx.Client(timeout=TIMEOUT) as client:
            redis_part = _backup_redis_config(client, url, key, started.strftime("%Y-%m-%d"))
        if redis_part.get("error"):
            warnings.append(redis_part["error"])
        warnings.extend(redis_part.get("warnings", []))

        total_rows = sum(counts.values())
        size_kb = len(blob) / 1024
        print(f"[Backup] {name}: {total_rows} rows across {len(counts)} tables, "
              f"{size_kb:.1f} KB, pruned {pruned}")

        try:
            from app.core.notifications import send_telegram_message_sync

            top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
            lines = "\n".join(f"  • {t}: {n}" for t, n in top if n)
            warn_block = ("\n⚠️ " + "\n⚠️ ".join(warnings[:5])) if warnings else ""
            # wording: BA review
            if redis_part.get("ok"):
                secret_note = ("có kèm bí mật (chỉ trong kho Supabase)"
                               if redis_part.get("secrets_included")
                               else "bí mật chỉ ghi số lượng")
                redis_line = (f"🧠 Cấu hình Redis: {redis_part.get('keys', 0)} khoá, "
                              f"{redis_part.get('bytes', 0) / 1024:.1f} KB — {secret_note}\n")
            else:
                redis_line = "🧠 Cấu hình Redis: KHÔNG sao lưu được\n"
            send_telegram_message_sync(
                "💾 <b>Sao lưu CSDL</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 <code>{name}</code>\n"
                f"🔢 {total_rows} dòng / {len(counts)} bảng\n"
                f"📦 {size_kb:.1f} KB\n"
                f"🌍 Bản off-site (S3/R2): {offsite}\n"
                f"🐘 Bản Postgres: {pg_state}\n"
                f"{redis_line}"
                f"{lines}{warn_block}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Backup] telegram notify failed: {exc}")

        return {"ok": True, "file": name, "rows": total_rows,
                "tables": len(counts), "bytes": len(blob),
                "offsite": _s3_configured() and not offsite_err,
                "postgres": _pg_configured() and not pg_err,
                "redis": redis_part,
                "warnings": warnings}

    except Exception as exc:  # noqa: BLE001
        print(f"[Backup] FAILED: {exc}")
        try:
            from app.core.notifications import send_telegram_message_sync

            send_telegram_message_sync(
                "🔴 <b>Sao lưu CSDL THẤT BẠI</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{str(exc)[:300]}</code>"
            )
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}
