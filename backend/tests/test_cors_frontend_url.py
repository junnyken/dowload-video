"""
FRONTEND_URL accepts several comma-separated origins.

It used to take exactly one. Moving the frontend to a custom domain
(dowloadvideo.io.vn) while the API still allowed only the old platform domain
made every download fail with "Failed to fetch": the browser's CORS preflight
got a 400 with no Access-Control-Allow-Origin.

main.py and payments.py read FRONTEND_URL at import time, so each case runs in
a fresh interpreter with its own environment.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

_PROBE = r"""
import json, sys
from fastapi.testclient import TestClient
from app.main import app
from app.api import payments
c = TestClient(app)
out = {"redirect_base": payments._FRONTEND_URL, "allow": {}}
for origin in json.loads(sys.argv[1]):
    r = c.options("/api/v1/resolve-input", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    out["allow"][origin] = r.headers.get("access-control-allow-origin")
print("__PROBE__" + json.dumps(out))
"""


def _probe(frontend_url, origins):
    env = {**os.environ, "FRONTEND_URL": frontend_url}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(origins)],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("__PROBE__")), None)
    assert line, f"probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return json.loads(line[len("__PROBE__"):])


def test_every_listed_origin_is_allowed_and_others_are_not():
    listed = [
        "https://dowloadvideo.io.vn",
        "https://www.dowloadvideo.io.vn",
        "https://dvid.vibe1.tinhgon.xyz",
    ]
    # Spaces and a trailing slash are the usual copy-paste noise in env values.
    res = _probe(
        "https://dowloadvideo.io.vn/ , https://www.dowloadvideo.io.vn,https://dvid.vibe1.tinhgon.xyz",
        listed + ["https://evil.example"],
    )
    for origin in listed:
        assert res["allow"][origin] == origin
    assert res["allow"]["https://evil.example"] is None


def test_payment_redirects_use_the_first_origin():
    res = _probe("https://dowloadvideo.io.vn/,https://dvid.vibe1.tinhgon.xyz", [])
    assert res["redirect_base"] == "https://dowloadvideo.io.vn"


def test_single_origin_still_works():
    res = _probe("https://dvid.vibe1.tinhgon.xyz", ["https://dvid.vibe1.tinhgon.xyz"])
    assert res["allow"]["https://dvid.vibe1.tinhgon.xyz"] == "https://dvid.vibe1.tinhgon.xyz"
    assert res["redirect_base"] == "https://dvid.vibe1.tinhgon.xyz"
