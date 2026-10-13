"""
Restore the Redis configuration from a daily backup file (task #6332).

    python backend/scripts/restore_redis_config.py redis-config-2026-10-13.json.gz
    python backend/scripts/restore_redis_config.py FILE --redis-url redis://host:6379/0 --apply
    python backend/scripts/restore_redis_config.py FILE --apply --overwrite

Target Redis: --redis-url, else env RESTORE_REDIS_URL, else REDIS_URL.

Safe by default:
  * DRY-RUN unless --apply: prints, per group, how many keys would be written
    and how many already exist (conflicts). Writes nothing.
  * An existing key is never touched unless --overwrite.
  * TTLs are restored as they were at backup time (remaining TTL then).
  * Values are never printed. Keys of secret groups are printed only as
    <group #digest>.
  * Secret groups that were backed up as metadata only are listed with their
    counts: re-enter them in admin.

Exit codes: 0 ok, 1 bad input, 2 conflicts on a dry-run (informational).
Drill and context: docs/runbooks/backup-restore.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import redis_config_backup as rcb  # noqa: E402


def run(argv: list[str], rc=None, out=print) -> int:
    ap = argparse.ArgumentParser(description="Restore Redis configuration backup")
    ap.add_argument("file", help="redis-config-<date>.json.gz (or .json)")
    ap.add_argument("--redis-url", default=None)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    ap.add_argument("--overwrite", action="store_true", help="replace keys that already exist")
    ap.add_argument("--show-keys", action="store_true", help="list key names (secrets as digests)")
    args = ap.parse_args(argv)

    try:
        payload = rcb.load_blob(Path(args.file).read_bytes())
    except Exception as exc:  # noqa: BLE001
        out(f"Cannot read backup: {exc}")
        return 1

    if rc is None:
        url = args.redis_url or os.getenv("RESTORE_REDIS_URL") or os.getenv("REDIS_URL")
        if not url:
            out("No target: pass --redis-url or set RESTORE_REDIS_URL / REDIS_URL")
            return 1
        import redis  # noqa: PLC0415
        rc = redis.from_url(url, socket_timeout=15)

    plan = rcb.plan_restore(rc, payload, overwrite=args.overwrite)
    mode = "APPLY" if args.apply else "DRY-RUN (nothing written; add --apply)"
    out(f"Backup {args.file}: generated {payload.get('generated_at')}, "
        f"secrets included: {payload.get('include_secrets')}")
    out(f"Mode: {mode}{' + overwrite' if args.overwrite else ''}")
    out(f"{'group':<20} {'in file':>8} {'write':>6} {'exists':>7} {'overwr':>7}")
    for group, g in sorted(plan["groups"].items()):
        out(f"{group:<20} {g['in_backup']:>8} {g['write']:>6} {g['conflict']:>7} {g['overwrite']:>7}")
    if args.show_keys:
        for action, entry in plan["actions"]:
            out(f"  {action:<9} {entry['type']:<6} {rcb.display_key(entry)}")
    for group, n in sorted(plan["secrets_not_in_backup"].items()):
        out(f"NOT in backup (secret, re-enter in admin): {group}: {n} key(s)")

    conflicts = sum(g["conflict"] for g in plan["groups"].values())
    if conflicts:
        out(f"{conflicts} key(s) already exist and will be SKIPPED (use --overwrite to replace)")

    if not args.apply:
        return 2 if conflicts else 0
    written = rcb.apply_restore(rc, plan)
    out(f"Written: {written} key(s)")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
