"""
Links to files the server produced in downloads/
================================================
The processing endpoints used to answer with the absolute server path
(`output_path: /app/downloads/sub_xxx.en.srt`) and a link carrying the same
path (`/download-local?filepath=/app/downloads/...`). That told every client
the container layout, and invited clients to send absolute paths back.

A file produced for a client is now named by its bare basename only:

    /api/v1/download-local?file=<basename>&filename=<display name>

GET /download-local resolves `file` inside the downloads directory and never
anywhere else: no separators, no leading dot (temp work dirs are
`.subtmp_*`), then a realpath containment check so a symlink cannot lead out.
The basename keeps whatever unguessable part the producer put in it — this
module only stops revealing the directory around it.

Links issued before this change (`?filepath=...`) keep working: they expire
with the file (20-60 min), and fetch-link / the Chrome extension still build
`filepath=` links from `local_file_path`, so the legacy branch has to stay
until those clients move to `file=` as well.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

# Basename only: starts with an alphanumeric (so never "." / ".." / a dotfile),
# then the characters our producers actually use.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def is_valid_file_id(name: str | None) -> bool:
    return bool(name) and bool(_NAME_RE.match(name)) and ".." not in name


def file_id_for(path: str) -> str:
    """The client-facing id of a file in downloads/: its basename."""
    name = os.path.basename(path)
    if not is_valid_file_id(name):
        raise ValueError(f"not a publishable download name: {name!r}")
    return name


def download_url(path: str, filename: str) -> str:
    """/api/v1/download-local link for a file in downloads/, without the directory."""
    return (f"/api/v1/download-local?file={quote(file_id_for(path))}"
            f"&filename={quote(filename)}")


def resolve_file_id(name: str, root: str) -> str | None:
    """
    realpath of `root/name` when `name` is a valid id and the result stays
    inside `root`; None otherwise. Existence is the caller's check.
    """
    if not is_valid_file_id(name):
        return None
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, name))
    if os.path.dirname(real) != real_root:
        return None
    return real
