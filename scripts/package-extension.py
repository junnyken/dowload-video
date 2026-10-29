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

Usage:  python3 scripts/package-extension.py           # the website build (with YouTube)
        python3 scripts/package-extension.py --store   # the Chrome Web Store build

The Chrome Web Store does not allow extensions that download YouTube. The
website build keeps it (owner, 22/10/2026); --store writes
chrome-extension/store-assets/VidGrab-store-v<version>.zip with the YouTube
host permissions and content-script matches removed — the popup reads its own
manifest and then sends YouTube links to the website. The store zip is never
served to users and is not one of the DESTINATIONS.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
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


# Our own scripts ship without comments (internal notes, task numbers, why a
# workaround exists). Only comments are dropped: names, whitespace and logic
# stay as written, so the store review still reads normal code. Third-party
# files under lib/ ship untouched with their license headers.
TERSER = ROOT / "frontend" / "node_modules" / "terser" / "bin" / "terser"

def strip_comments(path: pathlib.Path) -> bytes:
    out = subprocess.run(
        ["node", str(TERSER), str(path), "--beautify", "comments=false", "--comments", "false"],
        check=True, capture_output=True,
    ).stdout
    if not out.strip():
        raise RuntimeError(f"terser returned nothing for {path}")
    return out

def ship_bytes(path: pathlib.Path) -> bytes | None:
    """Bytes to write for a .js file we own, or None to copy the file as-is."""
    rel = path.relative_to(SOURCE)
    if path.suffix != ".js" or rel.parts[0] == "lib":
        return None
    return strip_comments(path)

YOUTUBE = ("youtube.com", "youtu.be")


def store_manifest(raw: str) -> str:
    """The manifest without YouTube (host permissions + content-script matches)
    and without "key" (5.22.0): the website build pins its id with that key so
    the app's native messaging host knows it; the Web Store refuses the field
    and gives the store item its own id (add it to native_reg.rs ALLOWED_IDS)."""
    m = json.loads(raw)
    m.pop("key", None)
    m["host_permissions"] = [h for h in m.get("host_permissions", []) if not any(y in h for y in YOUTUBE)]
    for cs in m.get("content_scripts", []):
        cs["matches"] = [x for x in cs.get("matches", []) if not any(y in x for y in YOUTUBE)]
    return json.dumps(m, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    manifest_path = SOURCE / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: no manifest at {manifest_path}", file=sys.stderr)
        return 1

    version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
    if not version:
        print("ERROR: manifest.json has no version", file=sys.stderr)
        return 1

    if not TERSER.exists():
        print(f"ERROR: {TERSER} missing — run npm ci in frontend/ first", file=sys.stderr)
        return 1

    files = collect()
    if manifest_path not in files:
        print("ERROR: manifest.json was excluded — refusing to build", file=sys.stderr)
        return 1

    store = "--store" in sys.argv[1:]
    dests = [SOURCE / "store-assets" / f"VidGrab-store-v{version}.zip"] if store else DESTINATIONS
    print(f"Packaging extension v{version} — {len(files)} files{' (Chrome Web Store build, no YouTube)' if store else ''}")

    stripped = {p: ship_bytes(p) for p in files}

    for dest in dests:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                if store and path == manifest_path:
                    z.writestr("manifest.json", store_manifest(path.read_text(encoding="utf-8")))
                elif stripped[path] is not None:
                    z.writestr(path.relative_to(SOURCE).as_posix(), stripped[path])
                else:
                    z.write(path, path.relative_to(SOURCE).as_posix())

        # Read the artifact back rather than trusting the write. A package that
        # cannot be opened, or that reports a different version from the source
        # manifest, must not reach a user.
        with zipfile.ZipFile(dest) as z:
            bad = z.testzip()
            if bad is not None:
                print(f"ERROR: {dest} is corrupt at {bad}", file=sys.stderr)
                return 1
            built_manifest = json.loads(z.read("manifest.json"))
            built = built_manifest.get("version")
            if store and any(any(y in h for y in YOUTUBE) for h in built_manifest.get("host_permissions", [])):
                print(f"ERROR: {dest} still has a YouTube host permission", file=sys.stderr)
                return 1
            if store and "key" in built_manifest:
                print(f"ERROR: {dest} still has the manifest key", file=sys.stderr)
                return 1
        if built != version:
            print(f"ERROR: {dest} reports v{built}, source says v{version}",
                  file=sys.stderr)
            return 1

        size_kb = dest.stat().st_size / 1024
        print(f"  {dest.relative_to(ROOT)}  —  v{built}, {size_kb:.1f} KB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
