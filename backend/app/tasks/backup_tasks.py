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

Destination
-----------
Supabase Storage, because it works with credentials the app already has. Be
clear-eyed about what that does and does not protect against: it covers the
failure that actually happened — a table wiped by application code — but a
backup living in the same project as the data is not off-site. If the project
itself is lost or paused, so is this. Set BACKUP_S3_* (and add boto3) when an
off-site target exists; until then this is a real backup of the realistic risk,
not a pretence of disaster recovery.
"""

from __future__ import annotations

import gzip
import io
import json
import os
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
    # 409 = already there, which is the normal case after the first run.
    if r.status_code not in (200, 201, 409):
        print(f"[Backup] bucket create returned {r.status_code}: {r.text[:200]}")


def _upload(client: httpx.Client, url: str, key: str, path: str, blob: bytes) -> None:
    r = client.post(
        f"{url}/storage/v1/object/{BUCKET}/{path}",
        headers={**_headers(key), "Content-Type": "application/gzip", "x-upsert": "true"},
        content=blob,
    )
    r.raise_for_status()


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
    stale = [o["name"] for o in r.json()
             if o.get("name", "") < f"vidgrab-{cutoff}"]
    if not stale:
        return 0

    d = client.request(
        "DELETE",
        f"{url}/storage/v1/object/{BUCKET}",
        headers={**_headers(key), "Content-Type": "application/json"},
        json={"prefixes": stale},
    )
    return len(stale) if d.status_code == 200 else 0


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

        total_rows = sum(counts.values())
        size_kb = len(blob) / 1024
        print(f"[Backup] {name}: {total_rows} rows across {len(counts)} tables, "
              f"{size_kb:.1f} KB, pruned {pruned}")

        try:
            from app.core.notifications import send_telegram_message_sync

            top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
            lines = "\n".join(f"  • {t}: {n}" for t, n in top if n)
            warn_block = ("\n⚠️ " + "\n⚠️ ".join(warnings[:5])) if warnings else ""
            send_telegram_message_sync(
                "💾 <b>Sao lưu CSDL</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 <code>{name}</code>\n"
                f"🔢 {total_rows} dòng / {len(counts)} bảng\n"
                f"📦 {size_kb:.1f} KB\n"
                f"{lines}{warn_block}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Backup] telegram notify failed: {exc}")

        return {"ok": True, "file": name, "rows": total_rows,
                "tables": len(counts), "bytes": len(blob), "warnings": warnings}

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
