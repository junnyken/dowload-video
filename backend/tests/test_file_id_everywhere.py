"""
File ids everywhere (migration steps 1-3).

Responses: every server file is named by a `*_file_id` (its basename) and
every /download-local link uses `file=`. The absolute path fields stay only
while EXPOSE_LEGACY_PATHS is on (default) — with it off, no response may
contain `/app/` or the downloads directory.

Inputs: every endpoint that takes a server file accepts a file id, and goes
through the one validator (app.core.local_download.resolve_local_input):
traversal, absolute paths outside downloads/, the sibling prefix
(`<root>_x/...`) and dotfiles are refused; a valid legacy path still works.

ffmpeg, Supabase, Celery and the extractor are mocked.
"""
import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# Real-shaped production name (2026-10-02): yt-dlp's format join has a '+'.
REAL_NAME = "jNQXAC9IVRw_230+140_9a8b655acfdbb63a.mp4"


# ── helpers ────────────────────────────────────────────────────────────

def assert_no_server_path(resp, root):
    """Scan a JSON-able response for anything that reveals the server layout."""
    blob = json.dumps(resp, ensure_ascii=False)
    assert "/app/" not in blob, blob
    for r in {str(root), os.path.realpath(str(root))}:
        assert r not in blob, blob
    assert "filepath=" not in blob, blob


def _write_out(cmd_or_path):
    path = cmd_or_path[-1] if isinstance(cmd_or_path, (list, tuple)) else cmd_or_path
    with open(path, "wb") as f:
        f.write(b"out")


class _Ok:
    returncode = 0
    stdout = b""
    stderr = b""


@pytest.fixture(params=["legacy_on", "legacy_off"])
def mode(request, monkeypatch):
    monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "true" if request.param == "legacy_on" else "false")
    return request.param


@pytest.fixture
def legacy_off(monkeypatch):
    monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "false")


@pytest.fixture
def root(monkeypatch, tmp_path):
    """One temp downloads/ for every module that resolves server files."""
    d = tmp_path / "downloads"
    d.mkdir()
    from app.api import processing, watermark, merge, flow_cleanup, routes, smart_analysis
    monkeypatch.setattr(processing, "_DOWNLOADS_DIR", str(d))
    monkeypatch.setattr(watermark, "_DOWNLOADS_DIR", str(d))
    monkeypatch.setattr(merge, "_DOWNLOADS_DIR", str(d))
    monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(d))
    monkeypatch.setattr(routes, "_LOCAL_DOWNLOADS_DIR", str(d))
    monkeypatch.setattr(smart_analysis, "_DOWNLOADS_DIR", str(d))
    monkeypatch.setattr(processing, "_schedule_cleanup", lambda p: None)
    from app.tasks import video_tasks
    monkeypatch.setattr(video_tasks.delete_local_file, "apply_async", lambda *a, **k: None)
    return d


@pytest.fixture
def video(root):
    p = root / REAL_NAME
    p.write_bytes(b"\x00" * 64)
    return p


# ── core: file ids ─────────────────────────────────────────────────────

class TestFileIdCore:
    def test_real_name_is_a_valid_id(self):
        from app.core.local_download import file_id_for, is_valid_file_id
        assert is_valid_file_id(REAL_NAME)
        assert file_id_for(f"/app/downloads/{REAL_NAME}") == REAL_NAME

    @pytest.mark.parametrize("name", [
        "-ZkKS2x8xxA_18_0123456789abcdef.mp4",       # YouTube ids may start with '-'
        "_abc_22_0123456789abcdef.mp4",
        "Me at the zoo (240p, h264, youtube).mp4",    # Cobalt "pretty" filename
        "Bài hát hay nhất.mp3",
    ])
    def test_names_the_downloader_produces_are_valid(self, name):
        from app.core.local_download import is_valid_file_id
        assert is_valid_file_id(name)

    @pytest.mark.parametrize("bad", [
        "", ".", "..", ".hidden.mp4", ".subtmp_x", "a/b", "a\\b", "../x",
        "a..b", "x\x00y", "x\ny", "a" * 256,
    ])
    def test_bad_ids_rejected(self, bad, tmp_path):
        from app.core.local_download import is_valid_file_id, resolve_file_id
        assert not is_valid_file_id(bad)
        assert resolve_file_id(bad, str(tmp_path)) is None

    def test_resolve_real_name(self, tmp_path):
        from app.core.local_download import resolve_file_id
        (tmp_path / REAL_NAME).write_bytes(b"x")
        assert resolve_file_id(REAL_NAME, str(tmp_path)) == os.path.realpath(tmp_path / REAL_NAME)

    def test_download_url_percent_encodes_plus(self):
        from urllib.parse import parse_qs, urlparse
        from app.core.local_download import download_url
        u = download_url(f"/app/downloads/{REAL_NAME}", "My clip+1.mp4")
        assert "file=jNQXAC9IVRw_230%2B140_9a8b655acfdbb63a.mp4" in u
        assert "/app/" not in u
        q = parse_qs(urlparse(u).query)
        assert q["file"] == [REAL_NAME]          # '+' does not decode to a space
        assert q["filename"] == ["My clip+1.mp4"]

    def test_expose_flag_parsing(self, monkeypatch):
        from app.core.local_download import expose_legacy_paths
        monkeypatch.delenv("EXPOSE_LEGACY_PATHS", raising=False)
        assert expose_legacy_paths() is True
        for v in ("false", "0", "no", "off", "FALSE"):
            monkeypatch.setenv("EXPOSE_LEGACY_PATHS", v)
            assert expose_legacy_paths() is False
        monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "true")
        assert expose_legacy_paths() is True

    def test_file_fields_both_modes(self, monkeypatch):
        from app.core.local_download import file_fields
        p = f"/app/downloads/{REAL_NAME}"
        monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "true")
        assert file_fields(p, "local_file_id", "local_file_path") == {
            "local_file_id": REAL_NAME, "local_file_path": p}
        monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "false")
        assert file_fields(p, "local_file_id", "local_file_path") == {"local_file_id": REAL_NAME}
        assert file_fields(None, "local_file_id", "local_file_path") == {"local_file_id": None}


# ── GET /download-local round trip with a real name ────────────────────

class TestDownloadLocalRealName:
    def test_file_param_round_trip(self, app, root, video):
        from app.core.local_download import download_url
        r = app.get(download_url(str(video), "clip.mp4"))
        assert r.status_code == 200
        assert r.content == b"\x00" * 64

    def test_unencoded_plus_is_a_space_and_misses(self, app, root, video):
        # Why encoding matters: a raw '+' is a space in a query string.
        r = app.get(f"/api/v1/download-local?file={REAL_NAME}&filename=x.mp4")
        assert r.status_code == 404

    def test_legacy_filepath_still_works_and_shares_the_validator(self, app, root, video):
        r = app.get("/api/v1/download-local", params={"filepath": str(video), "filename": "x.mp4"})
        assert r.status_code == 200
        sib = root.parent / "downloads_x"
        sib.mkdir()
        (sib / "a.mp4").write_bytes(b"secret")
        for bad in (str(sib / "a.mp4"), "/etc/passwd", f"{root}/../downloads_x/a.mp4"):
            r = app.get("/api/v1/download-local", params={"filepath": bad, "filename": "x"})
            assert r.status_code == 403, bad


# ── inputs: one validator for every endpoint ───────────────────────────

def _bad_inputs(root):
    sib = root.parent / f"{root.name}_x"
    sib.mkdir(exist_ok=True)
    (sib / "a.mp4").write_bytes(b"x")
    (root / ".hidden.mp4").write_bytes(b"x")
    outside = root.parent / "outside.mp4"
    outside.write_bytes(b"x")
    return {
        "traversal": "../outside.mp4",
        "traversal_abs": f"{root}/../outside.mp4",
        "abs_outside": str(outside),
        "etc_passwd": "/etc/passwd",
        "sibling_prefix": str(sib / "a.mp4"),
        "dotfile_id": ".hidden.mp4",
        "dotfile_path": str(root / ".hidden.mp4"),
    }


def _call_processing(value):
    from app.api.processing import _guard_local_path
    return _guard_local_path(value)


def _call_trim(value):
    from app.api import routes
    return routes._resolve_local_input_or_raise(value, "/nonexistent")


def _call_watermark(value):
    from app.api.watermark import _safe_path
    return _safe_path(value)


def _call_flow_from_local(value):
    from app.api import flow_cleanup
    with patch.object(flow_cleanup, "_probe", return_value={"width": 64, "height": 64}), \
         patch.object(flow_cleanup, "_extract_frame", return_value=None):
        r = flow_cleanup.cleanup_from_local_path.__wrapped__(
            flow_cleanup.FromLocalRequest(local_path=value), None, None)
    return r


def _call_smart_analysis(value):
    from app.api import smart_analysis
    body = smart_analysis.AnalyzeRequest(url="https://www.tiktok.com/@a/video/1", media_path=value)
    with patch.object(smart_analysis, "_check_analysis_quota", return_value=(True, "")), \
         patch.object(smart_analysis, "_get_db", return_value=None), \
         patch.object(smart_analysis, "_get_analyze_task", return_value=None):
        asyncio.run(smart_analysis.submit_analyze_media(body, MagicMock(), None))
    return body.media_path


INPUTS = {
    "processing(local_path/video_path/paths, asr, translate)": _call_processing,
    "trim/to-gif local_path": _call_trim,
    "watermark source_path": _call_watermark,
    "flow-cleanup/from-local local_path": _call_flow_from_local,
    "analyze-media media_path": _call_smart_analysis,
}


@pytest.mark.parametrize("call", INPUTS.values(), ids=INPUTS.keys())
class TestInputs:
    def test_accepts_file_id(self, root, video, call):
        out = call(REAL_NAME)
        if isinstance(out, str):
            assert out == os.path.realpath(video)

    def test_accepts_valid_legacy_path(self, root, video, call):
        out = call(str(video))
        if isinstance(out, str):
            assert out == os.path.realpath(video)

    @pytest.mark.parametrize("kind", [
        "traversal", "traversal_abs", "abs_outside", "etc_passwd",
        "sibling_prefix", "dotfile_id", "dotfile_path",
    ])
    def test_rejects(self, root, call, kind):
        value = _bad_inputs(root)[kind]
        with pytest.raises(HTTPException) as e:
            call(value)
        assert 400 <= e.value.status_code < 500


def test_endpoints_really_route_through_trim_gif_helper(root, video, monkeypatch):
    """trim and to-gif call the shared helper (not their old prefix check)."""
    from app.api import routes
    monkeypatch.setattr(routes.subprocess, "run", lambda cmd, **k: (_write_out(cmd), _Ok())[1])
    for bad in ("../outside.mp4", str(root.parent / f"{root.name}_x" / "a.mp4")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(routes.trim_media.__wrapped__(
                routes.TrimRequest(local_path=bad, start_time=0, end_time=1), None))
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            asyncio.run(routes.convert_to_gif.__wrapped__(
                routes.ToGifRequest(local_path=bad, start_time=0, end_time=1), None))
        assert e.value.status_code == 400


def test_merge_ignores_stored_paths_outside_downloads(root, video, monkeypatch):
    from app.api import merge
    outside = root.parent / "outside.mp4"
    outside.write_bytes(b"x")
    rows = [
        {"id": "a", "status": "success", "local_file_path": str(video), "user_id": None},
        {"id": "b", "status": "success", "local_file_path": str(outside), "user_id": None},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = rows
    monkeypatch.setattr(merge, "get_supabase_client", lambda: sb)
    with pytest.raises(HTTPException) as e:
        merge.merge_clips.__wrapped__(None, merge.MergeRequest(job_ids=["a", "b"]), None)
    assert e.value.status_code == 422  # only one clip survives the validator


# ── responses: both modes, scan with legacy off ────────────────────────

@pytest.fixture
def fetch_route(monkeypatch, root):
    import app.api.routes as routes
    import app.main as main_mod
    from app.core import ssrf_guard, disk_guardrail
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(root))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    for lim in {id(x): x for x in (main_mod.limiter, routes.limiter,
                                     getattr(main_mod.app.state, "limiter", None)) if x}.values():
        monkeypatch.setattr(lim, "enabled", False)
    for name in ("check_anon_quota",):
        monkeypatch.setattr(routes, name, lambda *a, **k: {"allowed": True, "downloads_today": 0,
                                                            "daily_limit": 99})
    monkeypatch.setattr(routes, "increment_anon_usage", lambda *a, **k: None)
    return routes


def _fetch(app, routes, monkeypatch, info, url):
    async def _info(u, *a, **k):
        return dict(info, original_url=u)
    monkeypatch.setattr(routes, "extract_video_info", _info)
    r = app.post("/api/v1/fetch-link", json={"url": url, "quality": "video",
                                              "subtitle_mode": "file"})
    assert r.status_code == 200, r.text[:300]
    return r.json()


class TestResponses:
    def test_fetch_link_tikwm_shape(self, app, fetch_route, monkeypatch, root, mode):
        # TikWM path: CDN URL + the copy the server saved locally.
        local = root / "tiktok_7000000000000000001_ab12cd34.mp4"
        local.write_bytes(b"x")
        mp3 = root / "tiktok_7000000000000000001_ab12cd34.mp3"
        mp3.write_bytes(b"x")
        body = _fetch(app, fetch_route, monkeypatch, {
            "title": "t", "direct_mp4_url": "https://cdn.tikwm.com/v.mp4",
            "local_file_path": str(local), "local_mp3_path": str(mp3),
        }, "https://www.tiktok.com/@x/video/7000000000000000001")
        assert body["local_file_id"] == local.name
        assert body["local_mp3_file_id"] == mp3.name
        if mode == "legacy_off":
            assert "local_file_path" not in body and "local_mp3_path" not in body
            assert_no_server_path(body, root)
        else:
            assert body["local_file_path"] == str(local)

    def test_fetch_link_ytdlp_shape_with_subtitle(self, app, fetch_route, monkeypatch, root, video, mode):
        sub = root / (os.path.splitext(REAL_NAME)[0] + ".en.srt")
        sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        body = _fetch(app, fetch_route, monkeypatch, {
            "title": "Me at the zoo", "direct_mp4_url": "",
            "local_file_path": str(video), "local_subtitle_path": str(sub),
        }, "https://www.instagram.com/p/abc/")
        assert body["local_file_id"] == REAL_NAME
        assert "file=" in body["subtitle_file_url"]
        assert "filepath=" not in body["subtitle_file_url"]
        got = app.get(body["subtitle_file_url"])
        assert got.status_code == 200 and "hi" in got.text
        if mode == "legacy_off":
            assert_no_server_path(body, root)

    def test_processing_package_zip(self, root, video, legacy_off):
        from app.api import processing
        r = asyncio.run(processing.package_zip.__wrapped__(
            processing.PackageZipRequest(paths=[REAL_NAME]), None))
        assert r["file_id"].startswith("pkg_")
        assert_no_server_path(r, root)

    def test_watermark(self, root, video, monkeypatch, mode):
        from app.api import watermark
        from app.tasks import media_tasks
        monkeypatch.setattr(watermark, "_get_duration", lambda p: 3.0)
        monkeypatch.setattr(media_tasks, "run_on_worker",
                            lambda task, cmd, timeout: (_write_out(cmd), {"returncode": 0, "stderr": ""})[1])
        r = watermark.watermark_embed.__wrapped__(
            None, watermark.WatermarkRequest(source_path=REAL_NAME, watermark_type="text", text="hi"))
        assert r["output_file_id"].startswith("wm_") and "file=" in r["download_url"]
        assert os.path.isfile(root / r["output_file_id"])
        if mode == "legacy_off":
            assert "output_path" not in r
            assert_no_server_path(r, root)
        else:
            assert r["output_path"] == str(root / r["output_file_id"])

    def test_merge(self, root, video, monkeypatch, mode):
        from app.api import merge
        from app.tasks import media_tasks
        second = root / "clip2_18_0123456789abcdef.mp4"
        second.write_bytes(b"x")
        rows = [{"id": "a", "status": "success", "local_file_path": str(video), "user_id": None},
                {"id": "b", "status": "success", "local_file_path": str(second), "user_id": None}]
        sb = MagicMock()
        sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = rows
        monkeypatch.setattr(merge, "get_supabase_client", lambda: sb)
        monkeypatch.setattr(merge, "_probe", lambda p: {"duration": 5.0, "vcodec": "h264"})
        monkeypatch.setattr(merge, "_probe_audio_codec", lambda p: "aac")
        monkeypatch.setattr(media_tasks, "run_on_worker",
                            lambda task, cmd, timeout: (_write_out(cmd), {"returncode": 0, "stderr": ""})[1])
        r = merge.merge_clips.__wrapped__(None, merge.MergeRequest(job_ids=["a", "b"]), None)
        assert r["merged_file_id"].startswith("merged_")
        assert os.path.isfile(root / r["merged_file_id"])
        if mode == "legacy_off":
            assert "merged_path" not in r
            assert_no_server_path(r, root)

    def test_flow_cleanup_process_and_result_download(self, app, root, video, monkeypatch, mode):
        from app.api import flow_cleanup
        monkeypatch.setattr(flow_cleanup, "_probe",
                            lambda p: {"width": 640, "height": 360, "duration": 2.0, "fps": 30.0})
        monkeypatch.setattr(flow_cleanup, "_extract_frame", lambda *a, **k: None)
        start = flow_cleanup.cleanup_from_local_path.__wrapped__(
            flow_cleanup.FromLocalRequest(local_path=REAL_NAME), None, None)
        assert_no_server_path(start, root)
        monkeypatch.setattr(flow_cleanup.subprocess, "run",
                            lambda cmd, **k: (_write_out(cmd), _Ok())[1])
        r = flow_cleanup.process_flow_cleanup.__wrapped__(
            flow_cleanup.ProcessRequest(temp_id=start["temp_id"], method="crop"), None, None)
        assert r["cleaned_file_id"].startswith("cleaned_")
        if mode == "legacy_off":
            assert "cleaned_path" not in r
            assert_no_server_path(r, root)
        got = app.get(r["download_url"])
        assert got.status_code == 200 and got.content == b"out"
        # The result route only serves cleaned_* of that session.
        bad = app.get(f"/api/v1/flow-cleanup/result/{start['temp_id']}/input.mp4")
        assert bad.status_code == 400

    def test_trim(self, root, video, monkeypatch, mode):
        from app.api import routes
        monkeypatch.setattr(routes.subprocess, "run", lambda cmd, **k: (_write_out(cmd), _Ok())[1])
        r = asyncio.run(routes.trim_media.__wrapped__(
            routes.TrimRequest(local_path=REAL_NAME, start_time=0, end_time=1), None))
        assert r["trimmed_file_id"].startswith("trim_output_")
        assert "file=" in r["download_url"]
        assert video.exists()  # the caller's file is never removed
        if mode == "legacy_off":
            assert "trimmed_file_path" not in r
            assert_no_server_path(r, root)

    def test_gif(self, root, video, monkeypatch, mode):
        from app.api import routes
        monkeypatch.setattr(routes.subprocess, "run", lambda cmd, **k: (_write_out(cmd), _Ok())[1])
        r = asyncio.run(routes.convert_to_gif.__wrapped__(
            routes.ToGifRequest(local_path=REAL_NAME, start_time=0, end_time=1), None))
        assert r["gif_file_id"].startswith("gif_out_")
        assert video.exists()  # used to be deleted after a local_path GIF
        if mode == "legacy_off":
            assert "gif_path" not in r
            assert_no_server_path(r, root)

    def test_asr_create(self, root, video, legacy_off):
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        from app.api.transcript_translate import resolve_identity
        db = MagicMock()
        db.rpc.return_value.execute.return_value.data = True
        db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "asr-1"}]
        fastapi_app.dependency_overrides[resolve_identity] = lambda: "user-1"
        ff = MagicMock(returncode=0, stdout="60", stderr="")
        try:
            with patch("subprocess.run", return_value=ff), \
                 patch("app.api.transcript_asr._get_db", return_value=db), \
                 patch("app.api.transcript_asr._get_transcribe_task", return_value=None):
                resp = TestClient(fastapi_app).post(
                    "/api/v1/transcript-asr/jobs", json={"video_local_path": REAL_NAME})
        finally:
            fastapi_app.dependency_overrides.pop(resolve_identity, None)
        assert resp.status_code == 200, resp.text
        assert_no_server_path(resp.json(), root)
        # The validator resolved the id to the real file inside downloads/.
        row = db.table.return_value.insert.call_args[0][0]
        assert row["video_local_path"] == os.path.realpath(video)

    def test_history_rows(self, app, root, monkeypatch, mode):
        from app.api import routes
        row = {"id": "j1", "status": "success", "slugified_name": "clip",
               "local_file_path": f"/app/downloads/{REAL_NAME}", "local_mp3_path": None,
               "temp_artifact_path": f"/app/downloads/{REAL_NAME}",
               "direct_mp4_url": f"/app/downloads/{REAL_NAME}"}
        sb = MagicMock()
        q = sb.table.return_value.select.return_value.order.return_value
        q.is_.return_value.range.return_value.execute.return_value.data = [row]
        monkeypatch.setattr(routes, "get_supabase_client", lambda: sb)
        body = app.get("/api/v1/history").json()
        job = body["jobs"][0]
        assert job["local_file_id"] == REAL_NAME and job["direct_file_id"] == REAL_NAME
        if mode == "legacy_off":
            assert_no_server_path(body, root)
            assert job["direct_mp4_url"].startswith("/api/v1/download-local?file=")
        else:
            assert job["local_file_path"] == f"/app/downloads/{REAL_NAME}"
