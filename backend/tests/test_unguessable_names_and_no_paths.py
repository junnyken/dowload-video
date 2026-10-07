"""
Stored names are capabilities; responses never carry server paths.

GET /download-local has no session check — the file id (the basename) is the
whole capability. So:

  1. every producer writes `<safe prefix><128 random bits><ext>`; the video
     title (Cobalt's "pretty" filename, yt-dlp metadata) never reaches the
     stored name and only travels in `filename=` / Content-Disposition
     (RFC 5987 for non-ASCII);
  2. because titles never reach the stored name, a title such as `a..b`
     cannot cost a file its id;
  3. admin, archive, partner (GET + webhook) and the user webhook never carry
     a server path — whatever EXPOSE_LEGACY_PATHS says — and the user-facing
     responses carry none once it is off.

Cobalt, ffmpeg, Supabase, Celery and the extractors are mocked.
"""
import asyncio
import json
import os
import re
import time
from contextlib import contextmanager
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from tests.test_file_id_everywhere import (  # noqa: F401  (pytest fixtures)
    REAL_NAME, _Ok, _fetch, _write_out, assert_no_server_path, fetch_route, legacy_off, mode, root, video,
)

TITLE = "Me at the zoo (240p, h264, youtube)"
VI_TITLE = "Bài hát hay nhất — Đen Vâu"
HEX128 = r"[0-9a-f]{32}"


def assert_unguessable(name: str, prefix: str, ext: str, title: str = TITLE):
    assert re.fullmatch(re.escape(prefix) + HEX128 + re.escape(ext), name), name
    for word in re.findall(r"[A-Za-z]{3,}", title):
        assert word.lower() not in name.lower().replace(prefix.lower(), ""), (word, name)


# ── Cobalt mocked at the HTTP layer ─────────────────────────────────────

class _FakeResp:
    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=65536):
        yield b"cobalt-bytes"


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def stream(self, method, url):
        yield _FakeResp()


@pytest.fixture
def cobalt(monkeypatch):
    from app.services import cobalt_service

    def _set(filename):
        monkeypatch.setattr(cobalt_service, "fetch_cobalt_stream", lambda *a, **k: {
            "status": "tunnel", "url": "http://cobalt-api:9000/tunnel?id=1", "filename": filename})
        monkeypatch.setattr(cobalt_service.httpx, "Client", _FakeClient)
        # the fake bytes are not a video; content checks live in test_cookie_longevity
        monkeypatch.setattr(cobalt_service, "is_real_video", lambda path: True)
        return cobalt_service
    return _set


# ── 1. names ────────────────────────────────────────────────────────────

class TestNameEntropy:
    def test_helper_shape(self):
        from app.core.local_download import new_download_name, new_token
        assert re.fullmatch(HEX128, new_token())
        assert re.fullmatch(r"sub_" + HEX128 + r"\.srt", new_download_name("sub_", "srt"))
        names = {new_download_name("x_", ".mp4") for _ in range(200)}
        assert len(names) == 200

    @pytest.mark.parametrize("fn,prefix", [
        ("download_from_cobalt", "cobalt_"),
        ("download_instagram_via_cobalt", "instagram_"),
        ("download_facebook_via_cobalt", "facebook_"),
    ])
    def test_cobalt_never_stores_under_the_pretty_title(self, cobalt, tmp_path, fn, prefix):
        svc = cobalt(f"{TITLE}.mp4")
        args = ("https://youtu.be/jNQXAC9IVRw", "1080", str(tmp_path)) if fn == "download_from_cobalt" \
            else ("https://www.instagram.com/reel/abc/", str(tmp_path))
        out = getattr(svc, fn)(*args)
        path = out if isinstance(out, str) else out["filepath"]
        assert os.path.dirname(path) == str(tmp_path)
        assert_unguessable(os.path.basename(path), prefix, ".mp4")
        assert os.listdir(tmp_path) == [os.path.basename(path)]
        if isinstance(out, dict):
            assert out["title"] == TITLE  # the title survives — as metadata only

    def test_ytdlp_outtmpl_has_no_metadata_but_format(self):
        from app.services import downloader
        for q in ("video_1080", "mp3_128", "audio_m4a", "video"):
            tmpl = downloader._get_base_opts("https://example.com/a..b/My Title.mp4", quality=q)["outtmpl"]
            name = os.path.basename(tmpl)
            assert re.fullmatch(HEX128 + r"_%\(format_id\)s\.%\(ext\)s", name), name
            assert "%(title)s" not in tmpl and "%(id)s" not in tmpl

    def test_trim_gif_watermark_package_merge_names(self, root, video, monkeypatch):
        from app.api import routes, processing, watermark, merge
        from app.tasks import media_tasks
        monkeypatch.setattr(routes.subprocess, "run", lambda cmd, **k: (_write_out(cmd), _Ok())[1])
        r = asyncio.run(routes.trim_media.__wrapped__(
            routes.TrimRequest(local_path=REAL_NAME, start_time=0, end_time=1, filename=TITLE), None))
        assert_unguessable(r["trimmed_file_id"], "trim_output_", ".mp4")
        r = asyncio.run(routes.convert_to_gif.__wrapped__(
            routes.ToGifRequest(local_path=REAL_NAME, start_time=0, end_time=1), None))
        assert_unguessable(r["gif_file_id"], "gif_out_", ".gif")
        r = asyncio.run(processing.package_zip.__wrapped__(
            processing.PackageZipRequest(paths=[REAL_NAME], filename=TITLE), None))
        assert_unguessable(r["file_id"], "pkg_", ".zip")
        monkeypatch.setattr(watermark, "_get_duration", lambda p: 3.0)
        monkeypatch.setattr(media_tasks, "run_on_worker",
                            lambda task, cmd, timeout: (_write_out(cmd), {"returncode": 0, "stderr": ""})[1])
        r = watermark.watermark_embed.__wrapped__(
            None, watermark.WatermarkRequest(source_path=REAL_NAME, watermark_type="text", text="hi"))
        assert_unguessable(r["output_file_id"], "wm_", ".mp4")
        assert "jNQXAC9IVRw" not in r["output_file_id"]  # the source name no longer leaks in
        second = root / "b.mp4"
        second.write_bytes(b"x")
        rows = [{"id": "a", "status": "success", "local_file_path": str(video), "user_id": None},
                {"id": "b", "status": "success", "local_file_path": str(second), "user_id": None}]
        sb = MagicMock()
        sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = rows
        monkeypatch.setattr(merge, "get_supabase_client", lambda: sb)
        monkeypatch.setattr(merge, "_probe", lambda p: {"duration": 5.0, "vcodec": "h264"})
        monkeypatch.setattr(merge, "_probe_audio_codec", lambda p: "aac")
        r = merge.merge_clips.__wrapped__(None, merge.MergeRequest(job_ids=["a", "b"]), None)
        assert_unguessable(r["merged_file_id"], "merged_", ".mp4")

    def test_no_short_random_suffix_left_in_producers(self):
        """Static guard for the producers that are hard to drive in a unit
        test (Douyin, Twitter/Cobalt, Cobalt-primary inside the downloader,
        ASR, batch zips): no `uuid4().hex[:N]` or short token builds a name."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        producers = [
            "app/services/downloader.py", "app/services/cobalt_service.py",
            "app/services/archive_service.py", "app/api/processing.py", "app/api/routes.py",
            "app/api/merge.py", "app/api/watermark.py", "app/api/batch_rename.py",
            "app/api/transcript_translate.py", "app/api/flow_cleanup.py",
            "app/tasks/transcript_asr_tasks.py", "app/tasks/video_tasks.py",
        ]
        bad = re.compile(r"uuid4\(\)\.hex\[:\d+\]|token_hex\((?:[1-9]|1[0-5])\)|token_urlsafe\((?:[1-9]|1[0-5])\)"
                         r"|os\.path\.basename\(raw_name\)|f\"downloads/")
        offenders = []
        for rel in producers:
            with open(os.path.join(here, rel), encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if bad.search(line) and '"id":' not in line:
                        offenders.append(f"{rel}:{n}: {line.strip()}")
        assert not offenders, "\n".join(offenders)

    def test_batch_zip_is_keyed_and_cleanup_finds_it(self, tmp_path, monkeypatch):
        from app.services import archive_service, downloader
        from app.tasks import video_tasks
        monkeypatch.setattr(downloader, "_path_secret_cache", "s3cret", raising=False)
        monkeypatch.setattr(archive_service, "DOWNLOAD_DIR", str(tmp_path))
        bid = "6f1c1b7e-0000-4000-8000-000000000001"
        z, zc = archive_service.batch_zip_path(bid), archive_service.batch_zip_path(bid, True)
        assert_unguessable(os.path.basename(z), "batch_", ".zip")
        assert bid not in z and bid.replace("-", "") not in z
        assert archive_service.batch_zip_path(bid) == z  # idempotent re-zip
        work = archive_service.batch_work_dir(bid)
        assert os.path.basename(work).startswith(".")  # resolve_local_input refuses dot components
        for p in (z, zc):
            open(p, "wb").close()
        os.makedirs(work)
        open(os.path.join(work, f"{TITLE}.mp4"), "wb").close()
        video_tasks.delete_batch_resources.run(bid)
        assert os.listdir(tmp_path) == []
        monkeypatch.setattr(downloader, "_path_secret_cache", "other", raising=False)
        assert archive_service.batch_zip_path(bid) != z  # the secret is what makes it unguessable


# ── 2. titles with `..` ─────────────────────────────────────────────────

class TestDotDotTitle:
    def test_title_a_dot_dot_b_still_gets_an_id_and_downloads(self, app, cobalt, root, fetch_route,
                                                              monkeypatch, legacy_off):
        from app.core.local_download import download_url, is_valid_file_id
        path = cobalt("a..b.mp4").download_from_cobalt("https://youtu.be/x", "1080", str(root))
        assert path and is_valid_file_id(os.path.basename(path))
        body = _fetch(app, fetch_route, monkeypatch,
                      {"title": "a..b", "direct_mp4_url": "", "local_file_path": path},
                      "https://www.instagram.com/p/dotdot/")
        assert body["local_file_id"] == os.path.basename(path)
        assert_no_server_path(body, root)
        r = app.get(download_url(body["local_file_id"], "a..b.mp4"))
        assert r.status_code == 200 and r.content == b"cobalt-bytes"
        assert 'filename="a..b.mp4"' in r.headers["content-disposition"]


# ── Content-Disposition ─────────────────────────────────────────────────

class TestContentDisposition:
    def test_vietnamese_title(self, app, root, video):
        from app.core.local_download import download_url
        r = app.get(download_url(REAL_NAME, f"{VI_TITLE}.mp4"))
        assert r.status_code == 200
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment; ")
        assert f"filename*=UTF-8''{quote(VI_TITLE + '.mp4', safe='')}" in cd
        assert 'filename="Bai hat hay nhat  Den Vau.mp4"' in cd  # ASCII fallback for old agents
        cd.encode("latin-1")  # a header must be latin-1 encodable
        assert r.headers["access-control-expose-headers"].lower() == "content-disposition"

    @pytest.mark.parametrize("name", ['x"; filename="evil.exe', "a\r\nSet-Cookie: x=1", ".bashrc", "", "日本語"])
    def test_hostile_or_empty_display_names(self, name):
        from app.core.local_download import content_disposition
        cd = content_disposition(name)
        assert "\r" not in cd and "\n" not in cd
        ascii_part = re.search(r'filename="([^"]*)"', cd).group(1)
        assert ascii_part and not ascii_part.startswith(".")
        assert cd.count('"') == 2


# ── cleanup ─────────────────────────────────────────────────────────────

def test_cleanup_still_deletes_old_random_named_files(tmp_path, monkeypatch):
    from app.core import database
    from app.core.local_download import new_download_name
    from app.tasks import video_tasks

    def _no_db():
        raise RuntimeError("no db in tests")
    monkeypatch.setattr(database, "get_service_client", _no_db)
    monkeypatch.setattr(video_tasks, "_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setenv("LOCAL_FILE_TTL_SEC", "60")
    old, new = tmp_path / new_download_name("cobalt_", ".mp4"), tmp_path / new_download_name("", "_18.mp4")
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 3600
    os.utime(old, (past, past))
    video_tasks.periodic_cleanup_downloads.run()
    assert not old.exists() and new.exists()


# ── 3. no server paths ──────────────────────────────────────────────────

def _no_path_anywhere(obj, root=None):
    blob = json.dumps(obj, ensure_ascii=False, default=str)
    assert "/app/" not in blob, blob
    if root is not None:
        for r in {str(root), os.path.realpath(str(root))}:
            assert r not in blob, blob
    assert "filepath=" not in blob, blob


@pytest.fixture(params=["legacy_on", "legacy_off"])
def any_mode(request, monkeypatch):
    monkeypatch.setenv("EXPOSE_LEGACY_PATHS", "true" if request.param == "legacy_on" else "false")
    return request.param


FULL_ROW = {
    "id": "j1", "status": "failed", "slugified_name": "clip", "title": TITLE,
    "local_file_path": f"/app/downloads/{REAL_NAME}", "local_mp3_path": f"/app/downloads/{REAL_NAME}.mp3",
    "temp_artifact_path": f"/app/downloads/{REAL_NAME}", "local_subtitle_path": "/app/downloads/x.en.srt",
    "direct_mp4_url": f"/app/downloads/{REAL_NAME}",
    "error_message": f"ERROR: unable to open for writing: /app/downloads/{REAL_NAME}.part",
}


class TestAdminNeverShowsPaths:
    def test_stats_failed_jobs(self, any_mode, monkeypatch):
        from app.api import admin
        sb = MagicMock()
        tbl = sb.table.return_value
        tbl.select.return_value.execute.return_value.data = []
        (tbl.select.return_value.eq.return_value.order.return_value.limit.return_value
         .execute.return_value.data) = [dict(FULL_ROW)]
        tbl.select.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        monkeypatch.setattr(admin, "get_supabase_client", lambda: sb)
        import app.core.scraperapi_pool as pool
        monkeypatch.setattr(pool, "get_active_key", lambda: None)
        r = asyncio.run(admin.get_admin_stats(None))
        job = r["failed_jobs"][0]
        _no_path_anywhere(r)
        assert job["local_file_id"] == REAL_NAME
        assert job["direct_mp4_url"].startswith("/api/v1/download-local?file=")
        assert REAL_NAME + ".part" in job["error_message"]

    def test_flow_cleanup_jobs_list_and_detail(self, any_mode, tmp_path, monkeypatch):
        from app.api import admin
        monkeypatch.setattr(admin, "_FLOW_DOWNLOAD_DIR", str(tmp_path))
        tid = "0123456789abcdef0123456789abcdef"
        work = tmp_path / f"flow_{tid}"
        work.mkdir()
        (work / "metadata.json").write_text(json.dumps({  # an old job's metadata
            "temp_id": tid, "created_at": "2026-10-01T00:00:00Z",
            "source_path": f"/app/downloads/{REAL_NAME}", "mask_path": f"{work}/mask.png",
        }))
        listing = asyncio.run(admin.list_flow_cleanup_jobs(None))
        detail = asyncio.run(admin.get_flow_cleanup_job(tid, None))
        _no_path_anywhere(listing, tmp_path)
        _no_path_anywhere(detail, tmp_path)
        assert detail["source_file_id"] == REAL_NAME


class TestArchiveNeverShowsPaths:
    def test_list_get_search_export(self, any_mode, monkeypatch):
        from app.api import archive
        item = {"id": "i1", "title": TITLE, "original_url": "https://youtu.be/x",
                "local_file_path": f"/app/downloads/{REAL_NAME}", "notes_path": "/app/downloads/n.txt",
                "hashtags": [], "tags_user": [], "collection_ids": []}
        sb = MagicMock()
        q = sb.table.return_value.select.return_value
        q.eq.return_value.order.return_value.range.return_value.execute.return_value = \
            MagicMock(data=[dict(item)], count=1)
        q.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [dict(item)]
        q.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [dict(item)]
        q.eq.return_value.order.return_value.execute.return_value.data = [dict(item)]
        q.eq.return_value.execute.return_value.data = []
        monkeypatch.setattr(archive, "get_service_client", lambda: sb)
        user = {"id": "u1"}
        listing = asyncio.run(archive.list_archive(MagicMock(), user=user, page=1, limit=20, platform=None,
                                                   is_starred=None, language=None, date_from=None,
                                                   date_to=None, min_duration=None, max_duration=None,
                                                   collection_id=None, file_status=None,
                                                   sort="archived_at"))
        one = asyncio.run(archive.get_archive_item("i1", user=user))
        _no_path_anywhere(listing)
        _no_path_anywhere(one)
        assert one["local_file_id"] == REAL_NAME
        export = asyncio.run(archive.export_archive(format="json", user=user))

        async def _body(resp):
            return b"".join([c async for c in resp.body_iterator])
        _no_path_anywhere(json.loads(asyncio.run(_body(export))))


class TestPartnerAndWebhooks:
    def _sync(self, monkeypatch, row, api_base="https://api.example.test"):
        from app.tasks import partner_tasks
        from app.services import webhook_dispatcher
        sent, updates = [], []
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [row]
        sb.table.return_value.update.side_effect = lambda u: updates.append(u) or MagicMock()
        monkeypatch.setattr(partner_tasks, "get_service_client", lambda: sb)

        async def _dispatch(tenant_id, event_type, payload):
            sent.append(payload)
        monkeypatch.setattr(webhook_dispatcher, "dispatch_event", _dispatch)
        partner_tasks.sync_partner_job_task.run("p1", "d1", "t1", api_base=api_base)
        return sent[0], updates[0]

    def test_webhook_payload_has_an_absolute_file_link(self, any_mode, monkeypatch):
        monkeypatch.delenv("PUBLIC_API_URL", raising=False)
        row = {"status": "success", "direct_mp4_url": f"/app/downloads/{REAL_NAME}",
               "local_file_path": None, "title": TITLE, "slugified_name": "me-at-the-zoo",
               "file_size_mb": 1, "thumbnail_url": "", "error_message": None}
        payload, update = self._sync(monkeypatch, row)
        _no_path_anywhere(payload)
        _no_path_anywhere(update)
        url = payload["result"]["direct_mp4_url"]
        assert url.startswith("https://api.example.test/api/v1/download-local?file=")
        assert "filename=me-at-the-zoo.mp4" in url

    def test_public_api_url_env_wins_and_remote_urls_pass_through(self, any_mode, monkeypatch):
        monkeypatch.setenv("PUBLIC_API_URL", "https://dvid-api.example/")
        row = {"status": "success", "direct_mp4_url": None, "local_file_path": f"/app/downloads/{REAL_NAME}",
               "title": TITLE, "file_size_mb": 1, "thumbnail_url": "", "error_message": None}
        payload, _ = self._sync(monkeypatch, row, api_base="http://backend:8000")
        assert payload["result"]["direct_mp4_url"].startswith("https://dvid-api.example/api/v1/download-local?file=")
        row = dict(row, direct_mp4_url="https://cdn.example/v.mp4")
        payload, _ = self._sync(monkeypatch, row)
        assert payload["result"]["direct_mp4_url"] == "https://cdn.example/v.mp4"

    def test_get_and_list_rewrite_rows_stored_before_the_fix(self, any_mode, monkeypatch):
        from app.api import partner
        monkeypatch.delenv("PUBLIC_API_URL", raising=False)
        row = {"id": "p1", "status": "done", "url": "https://youtu.be/x", "quality": "best",
               "tenant_id": "t1", "created_at": "", "result": {
                   "direct_mp4_url": f"/app/downloads/{REAL_NAME}", "title": TITLE}}
        sb = MagicMock()
        q = sb.table.return_value.select.return_value.eq.return_value
        q.eq.return_value.single.return_value.execute.return_value.data = row
        q.order.return_value.limit.return_value.execute.return_value.data = [row]
        monkeypatch.setattr(partner, "get_service_client", lambda: sb)
        req = MagicMock(base_url="https://api.example.test/")
        tenant = MagicMock(tenant_id="t1", scopes=["read"], plan="pro")
        monkeypatch.setattr(partner, "_require_scope", lambda *a, **k: None)
        one = asyncio.run(partner.get_job("p1", req, tenant))
        many = asyncio.run(partner.list_jobs(req, None, 50, tenant))
        for out in (one.model_dump(), [m.model_dump() for m in many]):
            _no_path_anywhere(out)
        assert one.result["direct_mp4_url"].startswith("https://api.example.test/api/v1/download-local?file=")

    def test_user_webhook_direct_url(self, any_mode, monkeypatch):
        from app.core.local_download import public_download_link
        monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test")
        link = public_download_link(f"/app/downloads/{REAL_NAME}", "clip")
        assert link.startswith("https://api.example.test/api/v1/download-local?file=")
        assert public_download_link("https://cdn.example/v.mp4", "clip") == "https://cdn.example/v.mp4"
        # and process_video_task really uses it for the user webhook
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "app/tasks/video_tasks.py"), encoding="utf-8") as fh:
            src = fh.read()
        assert '"direct_url":   public_download_link(best_url, slug)' in src
        assert '"direct_url":   best_url' not in src


class TestUserFacingWithLegacyOff:
    def test_fetch_link_cobalt_shape(self, app, cobalt, root, fetch_route, monkeypatch, legacy_off):
        path = cobalt(f"{TITLE}.mp4").download_from_cobalt("https://youtu.be/x", "1080", str(root))
        body = _fetch(app, fetch_route, monkeypatch,
                      {"title": TITLE, "direct_mp4_url": "", "local_file_path": path},
                      "https://www.instagram.com/p/cobalt/")
        assert_no_server_path(body, root)
        assert_unguessable(body["local_file_id"], "cobalt_", ".mp4")

    def test_processing_and_history(self, app, root, video, monkeypatch, legacy_off):
        from app.api import processing, routes
        r = asyncio.run(processing.package_zip.__wrapped__(
            processing.PackageZipRequest(paths=[REAL_NAME]), None))
        assert_no_server_path(r, root)
        sb = MagicMock()
        q = sb.table.return_value.select.return_value.order.return_value
        q.is_.return_value.range.return_value.execute.return_value.data = [dict(FULL_ROW, status="success")]
        monkeypatch.setattr(routes, "get_supabase_client", lambda: sb)
        body = app.get("/api/v1/history").json()
        assert_no_server_path(body, root)
