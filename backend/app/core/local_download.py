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
characters" rather than an ASCII whitelist: files written before the
unguessable-name change are still on disk under older shapes (the yt-dlp
format join `jNQXAC9IVRw_230+140_9a8b655acfdbb63a.mp4`, Cobalt's "pretty"
title names) until cleanup removes them. New names are always
`<prefix><128-bit hex><ext>` (see new_download_name) — the title only ever
travels in `filename=` / Content-Disposition. None of those can traverse once
separators and leading dots are excluded and the realpath check holds. `download_url`
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
import secrets
import unicodedata
from urllib.parse import quote

# One path component. First char: anything but '.', separators, control chars.
_NAME_RE = re.compile(r"^[^./\\\x00-\x1f\x7f][^/\\\x00-\x1f\x7f]*$")
_MAX_NAME_BYTES = 255

_FALSEY = {"0", "false", "no", "off"}


# ── Names of files written into downloads/ ───────────────────────────
# /download-local has no session check: knowing a name is enough to fetch
# the file. So every name a producer writes is <safe prefix><128 random
# bits><ext> — never a title, a video id alone, or a short suffix. The
# human-readable name travels only in `filename=` / Content-Disposition.
_TOKEN_BYTES = 16                      # 128 bits
TOKEN_HEX_LEN = _TOKEN_BYTES * 2       # 32 hex chars
UNGUESSABLE_TOKEN_RE = re.compile(r"[0-9a-f]{%d}" % TOKEN_HEX_LEN)


def new_token() -> str:
    """128 random bits as lowercase hex."""
    return secrets.token_hex(_TOKEN_BYTES)


def new_download_name(prefix: str = "", ext: str = "") -> str:
    """`<prefix><32 hex><ext>` — ext with or without the leading dot."""
    if ext and not ext.startswith("."):
        ext = "." + ext
    return f"{prefix}{new_token()}{ext}"


def new_download_path(root: str, prefix: str = "", ext: str = "") -> str:
    """Absolute path of a fresh unguessable name inside `root`."""
    return os.path.join(root, new_download_name(prefix, ext))


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """
    Content-Disposition for a display name that may be non-ASCII (RFC 6266 /
    RFC 5987): an ASCII `filename="..."` fallback plus `filename*=UTF-8''...`.
    Control characters, quotes and backslashes never reach the header.
    """
    name = "".join(ch for ch in (filename or "") if ch >= " " and ch != "\x7f").strip() or "download"
    ascii_name = unicodedata.normalize("NFKD", name.replace("Đ", "D").replace("đ", "d")).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'["\\]', "", ascii_name).strip()
    if not ascii_name or ascii_name.startswith("."):
        ascii_name = "download" + ascii_name
    return (f'{disposition}; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(name, safe='')}")


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


def scrub_job_row(row: dict, strip_paths: bool | None = None) -> dict:
    """
    A download_jobs row on its way to a client (history, batch status).
    Adds `local_file_id` / `local_mp3_file_id` / `direct_file_id`; with
    EXPOSE_LEGACY_PATHS off — or `strip_paths=True`, which admin responses
    always pass — drops the path columns and turns a local `direct_mp4_url`
    into a /download-local?file= link.
    """
    if not isinstance(row, dict):
        return row
    if strip_paths is None:
        strip_paths = not expose_legacy_paths()
    out = dict(row)
    out["local_file_id"] = safe_file_id(row.get("local_file_path"))
    out["local_mp3_file_id"] = safe_file_id(row.get("local_mp3_path"))
    direct = row.get("direct_mp4_url")
    direct_local = _looks_local(direct)
    out["direct_file_id"] = safe_file_id(direct) if direct_local else None
    if strip_paths:
        for k in ("local_file_path", "local_mp3_path", "temp_artifact_path", "local_subtitle_path"):
            out.pop(k, None)
        if "error_message" in out:
            out["error_message"] = redact_paths_in_text(out["error_message"])
        if direct_local:
            fid = out["direct_file_id"]
            if fid:
                name = row.get("slugified_name") or os.path.splitext(fid)[0]
                ext = os.path.splitext(fid)[1]
                out["direct_mp4_url"] = download_url(fid, f"{name}{ext}")
            else:
                out["direct_mp4_url"] = None
    return out


# ── Outbound payloads and admin responses ────────────────────────────
# These never carry a server path, whatever EXPOSE_LEGACY_PATHS says: the
# legacy switch exists for installed clients that send paths back, and no
# webhook receiver or admin page does that.

def public_api_base(request=None) -> str:
    """
    Absolute origin of this API for links that leave the browser (webhooks).
    PUBLIC_API_URL wins (set it where the API sits behind a proxy that
    rewrites the host); otherwise the origin the request came in on;
    otherwise "" (a relative link — still no server path).
    """
    env = os.getenv("PUBLIC_API_URL", "").strip().rstrip("/")
    if env:
        return env
    if request is not None:
        try:
            return str(request.base_url).rstrip("/")
        except Exception:
            return ""
    return ""


def _download_roots() -> tuple:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    roots = {"/app/downloads", os.getenv("DOWNLOAD_DIR", ""), os.path.join(here, "downloads")}
    out = set()
    for r in roots:
        if r:
            out.add(r.rstrip("/"))
            out.add(os.path.realpath(r).rstrip("/"))
    return tuple(sorted(out))


def is_server_path(value, roots: tuple = ()) -> bool:
    """A string naming a file on this server: under /app/, under a downloads
    root (plus any extra `roots`), or the relative "downloads/..." form."""
    if not isinstance(value, str) or not value or "://" in value:
        return False
    if value.startswith(("downloads/", "./downloads/", "/app/")):
        return True
    extra = tuple(r.rstrip("/") for r in roots if r) + tuple(os.path.realpath(r) for r in roots if r)
    return any(value == r or value.startswith(r + "/") for r in _download_roots() + extra)


def link_for_server_path(value: str, filename: str | None = None, base: str = "") -> str | None:
    """
    `base`/api/v1/download-local?file=<id>&filename=<display> for a server
    path; None when the path has no publishable id.
    """
    fid = safe_file_id(value)
    if not fid:
        return None
    display = filename or fid
    if not os.path.splitext(display)[1]:
        display += os.path.splitext(fid)[1]
    return f"{base}{download_url(fid, display)}"


def public_download_link(value: str | None, filename: str | None = None, request=None) -> str | None:
    """A URL for an external receiver: remote URLs pass through, a server
    path becomes an absolute (when the API origin is known) file= link."""
    if not value or not is_server_path(value):
        return value or None
    return link_for_server_path(value, filename, public_api_base(request))


_EMBEDDED_PATH_RE = re.compile(r"(?:\./)?(?:/app|downloads)(?:/[^\s'\"<>()\[\],;]+)+")


def redact_paths_in_text(text):
    """Free text (an error message) with any embedded server path cut to its
    last component."""
    if not isinstance(text, str) or ("/app/" not in text and "downloads/" not in text):
        return text
    return _EMBEDDED_PATH_RE.sub(lambda m: m.group(0).rstrip("/").rsplit("/", 1)[-1], text)


def strip_server_paths(obj, base: str = "", display_name: str | None = None, roots: tuple = ()):
    """
    Copy of `obj` (dicts/lists, recursively) with every server path removed:
      * a `<x>_path` / `filepath` key holding one → `<x>_file_id` (or None);
      * any other string that is one → a download-local link (absolute when
        `base` is given), or None if it has no publishable id.
    """
    if isinstance(obj, list):
        return [strip_server_paths(v, base, display_name, roots) for v in obj]
    if not isinstance(obj, dict):
        if is_server_path(obj, roots):
            return link_for_server_path(obj, display_name, base)
        return redact_paths_in_text(obj)
    out: dict = {}
    for k, v in obj.items():
        if isinstance(v, str) and is_server_path(v, roots):
            if isinstance(k, str) and (k.endswith("_path") or k == "filepath"):
                stem = k[:-5] if k.endswith("_path") else "file"   # local_file_path → local_file
                id_key = stem + ("_id" if stem == "file" or stem.endswith("_file") else "_file_id")
                if out.get(id_key) is None:
                    out[id_key] = safe_file_id(v)
                continue
            out[k] = link_for_server_path(v, display_name, base)
        else:
            out[k] = strip_server_paths(v, base, display_name, roots)
    return out
