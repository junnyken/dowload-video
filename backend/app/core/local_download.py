"""
Links to files the server produced in downloads/
================================================
The processing endpoints used to answer with the absolute server path
(`output_path: /app/downloads/sub_xxx.en.srt`) and a link carrying the same
path (`/download-local?filepath=/app/downloads/...`). That told every client
the container layout, and invited clients to send absolute paths back.

A file produced for a client is named by its bare basename only — its
*file id*:

    /api/v1/download-local?file=<basename>&filename=<display name>

Every endpoint that takes a server file as input (local_path, video_path,
source_path, video_local_path, media_path, /download-local?filepath=, ...)
goes through ONE validator, `resolve_local_input`, which accepts either a
file id or a legacy path and applies the same rules to both:

  * a file id is a single path component: no `/` or `\\`, no control
    characters, does not start with `.` (so never `.` / `..` / a dotfile such
    as the `.subtmp_*` work dirs), no `..` anywhere, at most 255 bytes;
  * the realpath must be inside the downloads directory, compared with a
    separator-terminated prefix so `/app/downloads_x/...` is not "inside"
    `/app/downloads`, and a symlink cannot lead out;
  * no component of a legacy path below the root may start with `.`.

The charset is deliberately "anything but separators and control
characters" rather than an ASCII whitelist: real output names include the
yt-dlp format join (`jNQXAC9IVRw_230+140_9a8b655acfdbb63a.mp4`), YouTube ids
that start with `-`/`_`, and Cobalt's "pretty" filenames (spaces, commas,
parentheses, non-ASCII titles). None of those can traverse once separators
and leading dots are excluded and the realpath check holds. `download_url`
percent-encodes the id (`+` → `%2B`, space → `%20`), so it round-trips
through a query string.

Legacy paths: old clients (extension ≤ 5.2.4) still read `local_file_path`
& co. and send them back, so responses carry both the path and its
`*_file_id` sibling while `EXPOSE_LEGACY_PATHS` is true (the default). Set it
to false to drop the path fields (migration step 4).
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

# One path component. First char: anything but '.', separators, control chars.
_NAME_RE = re.compile(r"^[^./\\\x00-\x1f\x7f][^/\\\x00-\x1f\x7f]*$")
_MAX_NAME_BYTES = 255

_FALSEY = {"0", "false", "no", "off"}


def expose_legacy_paths() -> bool:
    """EXPOSE_LEGACY_PATHS (default true): keep absolute-path fields in responses."""
    return os.getenv("EXPOSE_LEGACY_PATHS", "true").strip().lower() not in _FALSEY


def is_valid_file_id(name: str | None) -> bool:
    if not name or not isinstance(name, str):
        return False
    if not _NAME_RE.match(name) or ".." in name:
        return False
    try:
        return len(name.encode("utf-8")) <= _MAX_NAME_BYTES
    except UnicodeEncodeError:
        return False


def file_id_for(path: str) -> str:
    """The client-facing id of a file in downloads/: its basename."""
    name = os.path.basename(path)
    if not is_valid_file_id(name):
        raise ValueError(f"not a publishable download name: {name!r}")
    return name


def safe_file_id(path: str | None) -> str | None:
    """file_id_for(), but None for an empty / unpublishable path."""
    if not path:
        return None
    try:
        return file_id_for(path)
    except ValueError:
        return None


def download_url(path: str, filename: str) -> str:
    """/api/v1/download-local link for a file in downloads/, without the directory."""
    return (f"/api/v1/download-local?file={quote(file_id_for(path), safe='')}"
            f"&filename={quote(filename, safe='')}")


def resolve_file_id(name: str, root: str) -> str | None:
    """
    realpath of `root/name` when `name` is a valid id and the result stays
    directly inside `root`; None otherwise. Existence is the caller's check.
    """
    if not is_valid_file_id(name):
        return None
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, name))
    if os.path.dirname(real) != real_root:
        return None
    return real


def _is_bare_name(value: str) -> bool:
    return "/" not in value and "\\" not in value


def resolve_local_input(value: str | None, root: str) -> str | None:
    """
    THE validator for every server-file input. `value` is a file id
    (preferred) or a legacy path — absolute, or relative such as
    "downloads/x.mp4" (resolved against root's parent, as /download-local
    always did). Returns the realpath inside `root`, or None when it is not
    acceptable. Existence is the caller's check.
    """
    if not value or not isinstance(value, str) or "\x00" in value:
        return None
    if _is_bare_name(value):
        return resolve_file_id(value, root)

    real_root = os.path.realpath(root)
    path = value if os.path.isabs(value) else os.path.join(os.path.dirname(real_root), value)
    # Traversal in what the caller sent is refused outright, even when it
    # would resolve back inside the root.
    if ".." in re.split(r"[\\/]+", value):
        return None
    real = os.path.realpath(path)
    # Separator-terminated prefix: "/app/downloads_x" is not inside "/app/downloads".
    if not real.startswith(real_root + os.sep):
        return None
    rel = os.path.relpath(real, real_root)
    if any(part.startswith(".") for part in rel.split(os.sep)):
        return None
    return real


def file_fields(path: str | None, id_key: str, path_key: str | None = None) -> dict:
    """
    Response fragment for one server file: always `{id_key: <id or None>}`,
    plus `{path_key: path}` while EXPOSE_LEGACY_PATHS is on.
    """
    out = {id_key: safe_file_id(path)}
    if path_key and expose_legacy_paths():
        out[path_key] = path
    return out


_LOCAL_PATH_PREFIXES = ("/", "downloads/", "./downloads/")


def _looks_local(value) -> bool:
    return isinstance(value, str) and value.startswith(_LOCAL_PATH_PREFIXES) \
        and not value.startswith("/api/")


def scrub_job_row(row: dict) -> dict:
    """
    A download_jobs row on its way to a client (history, batch status).
    Adds `local_file_id` / `local_mp3_file_id` / `direct_file_id`; with
    EXPOSE_LEGACY_PATHS off, drops the path columns and turns a local
    `direct_mp4_url` into a /download-local?file= link.
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    out["local_file_id"] = safe_file_id(row.get("local_file_path"))
    out["local_mp3_file_id"] = safe_file_id(row.get("local_mp3_path"))
    direct = row.get("direct_mp4_url")
    direct_local = _looks_local(direct)
    out["direct_file_id"] = safe_file_id(direct) if direct_local else None
    if not expose_legacy_paths():
        for k in ("local_file_path", "local_mp3_path", "temp_artifact_path", "local_subtitle_path"):
            out.pop(k, None)
        if direct_local:
            fid = out["direct_file_id"]
            if fid:
                name = row.get("slugified_name") or os.path.splitext(fid)[0]
                ext = os.path.splitext(fid)[1]
                out["direct_mp4_url"] = download_url(fid, f"{name}{ext}")
            else:
                out["direct_mp4_url"] = None
    return out
