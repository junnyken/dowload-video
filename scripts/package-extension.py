#!/usr/bin/env python3
"""
Build the Chrome extension package from chrome-extension/.

Why this script exists: the packaging rule was only ever encoded inside the
built zip itself. Working out which files belonged meant unzipping the shipped
artifact and diffing it against the source directory — so the next person to
publish would have to guess, and a guess that includes tests/ or store-assets/
ships dead weight to every user while a guess that misses web-bridge.js breaks
the "Kết nối tài khoản" bridge silently.

The package goes to TWO destinations, and both are served to real users:

    backend/extension/VidGrab-extension.zip
        mounted at /app/extension/... in the backend container. This is the
        FIRST path GET /api/v1/extension/download checks, so it is what the
        download page hands out.

    telegram-bot/extension/VidGrab-extension.zip
        mounted at the same path in the telegram-bot container and uploaded by
        bot.py. Updating only one leaves the bot distributing an older build
        than the website, which is the kind of drift nobody notices until a
        user reports a bug that was fixed weeks ago.

Usage:  python3 scripts/package-extension.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "chrome-extension"

DESTINATIONS = [
    ROOT / "backend" / "extension" / "VidGrab-extension.zip",
    ROOT / "telegram-bot" / "extension" / "VidGrab-extension.zip",
]

# Everything the extension needs at runtime ships; everything else is for the
# Chrome Web Store listing or for CI, and would only inflate the download.
EXCLUDED_DIRS = {"store-assets", "tests", "__pycache__", ".git"}
EXCLUDED_FILES = {"STORE_SUBMISSION.md", ".DS_Store"}


def collect() -> list[pathlib.Path]:
    files = []
    for path in sorted(SOURCE.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SOURCE)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.name in EXCLUDED_FILES or rel.name.startswith("."):
            continue
        files.append(path)
    return files


def main() -> int:
    manifest_path = SOURCE / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: no manifest at {manifest_path}", file=sys.stderr)
        return 1

    version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
    if not version:
        print("ERROR: manifest.json has no version", file=sys.stderr)
        return 1

    files = collect()
    if manifest_path not in files:
        print("ERROR: manifest.json was excluded — refusing to build", file=sys.stderr)
        return 1

    print(f"Packaging extension v{version} — {len(files)} files")

    for dest in DESTINATIONS:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                z.write(path, path.relative_to(SOURCE).as_posix())

        # Read the artifact back rather than trusting the write. A package that
        # cannot be opened, or that reports a different version from the source
        # manifest, must not reach a user.
        with zipfile.ZipFile(dest) as z:
            bad = z.testzip()
            if bad is not None:
                print(f"ERROR: {dest} is corrupt at {bad}", file=sys.stderr)
                return 1
            built = json.loads(z.read("manifest.json")).get("version")
        if built != version:
            print(f"ERROR: {dest} reports v{built}, source says v{version}",
                  file=sys.stderr)
            return 1

        size_kb = dest.stat().st_size / 1024
        print(f"  {dest.relative_to(ROOT)}  —  v{built}, {size_kb:.1f} KB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
