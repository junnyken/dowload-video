"""
Restore table rows from the daily database export (vidgrab-<date>.json.gz).

The daily backup (app/tasks/backup_tasks.py) is a logical JSON export through
PostgREST; there was no way back from it. This is the minimal one.

    python backend/scripts/restore_db_export.py FILE                    # list tables + counts
    python backend/scripts/restore_db_export.py FILE --table download_jobs
    python backend/scripts/restore_db_export.py FILE --table download_jobs --apply
    python backend/scripts/restore_db_export.py FILE --all --apply --overwrite

Target: env SUPABASE_URL + SUPABASE_SERVICE_KEY (or --url; the key only from env).

Safe by default:
  * DRY-RUN unless --apply. A dry-run with a target reads which row ids already
    exist (conflicts) and writes nothing; --offline skips the target entirely.
  * Rows that already exist are left alone (PostgREST resolution=ignore-
    duplicates) unless --overwrite (resolution=merge-duplicates).
  * Row contents are never printed — only table names and counts.
  * auth.users is NOT restored by this script (it is in the file as
    auth_users); see docs/runbooks/backup-restore.md. Restore users first,
    then tables whose rows reference them (profiles, ...).
  * Schema is not in the export: run database/migrations/ first on an empty DB.

Exit codes: 0 ok, 1 bad input / failed write, 2 conflicts on a dry-run.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BATCH = 500
ID_CHUNK = 100


def load_export(path: str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload.get("tables"), dict):
        raise ValueError("not a database export (no 'tables')")
    return payload


def _headers(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}", **(extra or {})}


def existing_ids(client: httpx.Client, url: str, key: str, table: str,
                 ids: list[Any]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), ID_CHUNK):
        chunk = ids[i:i + ID_CHUNK]
        quoted = ",".join(json.dumps(str(x)) for x in chunk)
        r = client.get(f"{url}/rest/v1/{table}", headers=_headers(key),
                       params={"select": "id", "id": f"in.({quoted})"})
        r.raise_for_status()
        found.update(str(row["id"]) for row in r.json())
    return found


def write_rows(client: httpx.Client, url: str, key: str, table: str,
               rows: list[dict], overwrite: bool) -> None:
    resolution = "merge-duplicates" if overwrite else "ignore-duplicates"
    for i in range(0, len(rows), BATCH):
        r = client.post(
            f"{url}/rest/v1/{table}",
            headers=_headers(key, {"Content-Type": "application/json",
                                   "Prefer": f"resolution={resolution},return=minimal"}),
            content=json.dumps(rows[i:i + BATCH], ensure_ascii=False, default=str),
        )
        if r.status_code not in (200, 201, 204):
            # Status + PostgREST message only; never the rows.
            raise RuntimeError(f"{table}: HTTP {r.status_code} {r.text[:160]}")


def run(argv: list[str], client: httpx.Client | None = None, out=print) -> int:
    ap = argparse.ArgumentParser(description="Restore rows from a DB JSON export")
    ap.add_argument("file")
    ap.add_argument("--table", action="append", default=[], help="repeatable")
    ap.add_argument("--all", action="store_true", help="every table in the file")
    ap.add_argument("--url", default=None, help="Supabase URL (default env SUPABASE_URL)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--offline", action="store_true", help="dry-run without contacting the target")
    args = ap.parse_args(argv)

    try:
        payload = load_export(args.file)
    except Exception as exc:  # noqa: BLE001
        out(f"Cannot read export: {exc}")
        return 1

    tables: dict[str, list] = payload["tables"]
    out(f"Export {args.file}: generated {payload.get('generated_at')}, "
        f"{len(tables)} tables, auth_users {len(payload.get('auth_users') or [])} "
        f"(auth users are NOT restored by this script)")

    chosen = sorted(tables) if args.all else args.table
    if not chosen:
        for t in sorted(tables):
            out(f"  {t:<40} {len(tables[t]):>8} rows")
        out("Pick tables with --table NAME (repeatable) or --all.")
        return 0
    unknown = [t for t in chosen if t not in tables]
    if unknown:
        out(f"Not in this export: {', '.join(unknown)}")
        return 1
    if args.apply and args.offline:
        out("--apply needs the target; drop --offline")
        return 1

    url = (args.url or os.getenv("SUPABASE_URL", "")).rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not args.offline and (not url or not key):
        out("No target: set SUPABASE_URL and SUPABASE_SERVICE_KEY (or use --offline)")
        return 1

    own_client = client is None and not args.offline
    if own_client:
        client = httpx.Client(timeout=httpx.Timeout(60.0))
    mode = "APPLY" if args.apply else "DRY-RUN (nothing written; add --apply)"
    out(f"Mode: {mode}{' + overwrite' if args.overwrite else ''}")
    conflicts_total = 0
    try:
        for t in chosen:
            rows = tables[t]
            line = f"  {t:<40} {len(rows):>8} rows in file"
            if not args.offline and rows and "id" in rows[0]:
                ids = [r["id"] for r in rows if "id" in r]
                n = len(existing_ids(client, url, key, t, ids))
                conflicts_total += n
                verb = "will be replaced" if args.overwrite else "kept as is"
                line += f", {n} already exist ({verb})"
            elif not args.offline and rows:
                line += ", no id column: existence not checked (primary key decides)"
            out(line)
            if args.apply and rows:
                write_rows(client, url, key, t, rows, args.overwrite)
                out(f"    written ({len(rows)} rows sent)")
    except Exception as exc:  # noqa: BLE001
        out(f"FAILED: {str(exc)[:200]}")
        return 1
    finally:
        if own_client:
            client.close()

    if not args.apply and conflicts_total and not args.overwrite:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
