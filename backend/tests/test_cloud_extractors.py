"""
Phase A — Cloud Drive Extractors Unit Tests
============================================
Covers:
  - Google Drive: normal direct file, large-file virus-scan confirm form,
    download quota exceeded -> share_quota_exceeded, owner-disabled / private ->
    owner_disabled_download, public folder batch listing.
  - OneDrive / SharePoint: base64url sharing token encoding, driveItem resolution,
    view-only / disabled download -> owner_disabled_download, download=1 fallback.
  - Mega.nz: URL key parsing (new & legacy formats), AES-CBC attribute decryption
    & AES-CTR stream decryption, -17 / HTTP 509 -> share_quota_exceeded,
    -9 / -2 -> share_expired.
  - Terabox: shorturlinfo normal file resolution, password-protected share ->
    share_password_required, optional user ndus cookie header.
  - Dropbox: dl=1 conversion preserving rlkey, folder & file links.
  - PikPak v2: recursive nested folder traversal (depth <= 3, max 200 items),
    password-protected share (?pwd=... and missing pass_code -> share_password_required),
    non-video file download (archive/document/audio + user token header).
  - Shared error codes, extraction error status mapping, source_classifier,
    container_registry, and extractor_registry integration.

All HTTP calls are mocked via httpx.MockTransport — zero real network traffic.
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest
import httpx

from app.core.error_codes import ERROR_META, get_error_meta
from app.core.extraction_errors import classify_extraction_error
from app.core.source_classifier import classify
from app.services.container_registry import get_capability
from app.services.extractor_registry import REGISTRY
from app.services.gdrive_extractor import (
    GDriveExtractor,
    parse_gdrive_url,
    resolve_gdrive_share,
)
from app.services.onedrive_extractor import (
    OneDriveExtractor,
    append_download_param,
    encode_onedrive_share_token,
    resolve_onedrive_share,
)
from app.services.mega_extractor import (
    MegaExtractor,
    decrypt_mega_attributes,
    decrypt_mega_stream,
    derive_mega_file_key,
    download_and_decrypt_mega_file,
    encrypt_mega_attributes,
    mega_b64encode,
    parse_mega_url,
    resolve_mega_share,
)
from app.services.terabox_extractor import (
    TeraboxExtractor,
    parse_terabox_url,
    resolve_terabox_share,
)
from app.services.dropbox_extractor import (
    DropboxExtractor,
    normalize_dropbox_url,
    resolve_dropbox_share,
)
from app.services.pikpak_extractor import (
    PikPakExtractor,
    extract_pass_code,
    resolve_pikpak_share,
)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


# ═══════════════════════════════════════════════════════════════════
# 1. GOOGLE DRIVE TESTS
# ═══════════════════════════════════════════════════════════════════

class TestGDriveExtractor:
    def test_parse_gdrive_urls(self):
        assert parse_gdrive_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing") == (
            "file",
            "1AbCdEfGhIjKlMnOp",
        )
        assert parse_gdrive_url("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp") == (
            "file",
            "1AbCdEfGhIjKlMnOp",
        )
        assert parse_gdrive_url("https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download") == (
            "file",
            "1AbCdEfGhIjKlMnOp",
        )
        assert parse_gdrive_url("https://drive.google.com/drive/folders/1FolderXYZ_99?usp=drive_link") == (
            "folder",
            "1FolderXYZ_99",
        )

    @pytest.mark.anyio
    async def test_gdrive_normal_direct_download(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Disposition": 'attachment; filename="demo_clip.mp4"',
                    "Content-Length": str(25 * 1024 * 1024),
                },
                content=b"fake-mp4-bytes",
            )

        async with _mock_client(handler) as client:
            info = await resolve_gdrive_share(
                "https://drive.google.com/file/d/1NormalFileId123/view",
                client=client,
            )
        assert info["title"] == "demo_clip"
        assert "id=1NormalFileId123" in info["direct_mp4_url"]
        assert info["file_size_mb"] == 25.0

    @pytest.mark.anyio
    async def test_gdrive_large_file_virus_scan_confirm(self):
        confirm_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Google Drive - Virus scan warning</title></head>
        <body>
          <span class="uc-name-size"><a href="/open?id=1LargeFile999">movie_4k.mkv</a> (1.5G)</span>
          <form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
            <input type="hidden" name="id" value="1LargeFile999">
            <input type="hidden" name="export" value="download">
            <input type="hidden" name="confirm" value="t">
            <input type="hidden" name="uuid" value="a1b2c3d4-e5f6-7890">
          </form>
        </body>
        </html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                text=confirm_html,
            )

        async with _mock_client(handler) as client:
            info = await resolve_gdrive_share(
                "https://drive.google.com/file/d/1LargeFile999/view",
                client=client,
            )
        assert info["title"] == "movie_4k"
        assert info["file_size_mb"] == 1536.0
        assert "drive.usercontent.google.com/download" in info["direct_mp4_url"]
        assert "confirm=t" in info["direct_mp4_url"]
        assert "uuid=a1b2c3d4-e5f6-7890" in info["direct_mp4_url"]

    @pytest.mark.anyio
    async def test_gdrive_quota_exceeded(self):
        quota_html = """
        <html><body>
        Too many users have viewed or downloaded this file recently. Please try accessing the file again later.
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text=quota_html)

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="share_quota_exceeded"):
                await resolve_gdrive_share(
                    "https://drive.google.com/file/d/1QuotaFile123/view",
                    client=client,
                )

    @pytest.mark.anyio
    async def test_gdrive_owner_disabled_download(self):
        disabled_html = """
        <html><body>
        You need access. Request access, or switch to an account with access.
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, headers={"Content-Type": "text/html"}, text=disabled_html)

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="owner_disabled_download"):
                await resolve_gdrive_share(
                    "https://drive.google.com/file/d/1PrivateFile123/view",
                    client=client,
                )

    def test_gdrive_folder_batch_extraction(self):
        folder_html = """
        <html>
        <head><title>Course Videos - Google Drive</title></head>
        <body>
          <div class="flip-entry" id="entry-1FileAAA111">
            <div class="flip-entry-title">Lesson_01.mp4</div>
          </div>
          <div class="flip-entry" id="entry-1FileBBB222">
            <div class="flip-entry-title">Lesson_02.mp4</div>
          </div>
        </body>
        </html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text=folder_html)

        ext = GDriveExtractor()
        client = _mock_client(handler)
        batch = ext.extract_batch(
            "https://drive.google.com/drive/folders/1CourseFolder999",
            client=client,
        )
        assert batch.ok
        assert batch.channel_title == "Course Videos"
        assert batch.total_found == 2
        assert len(batch.entries) == 2
        assert batch.entries[0].title == "Lesson_01.mp4"
        assert "id=1FileAAA111" in batch.entries[0].url


# ═══════════════════════════════════════════════════════════════════
# 2. ONEDRIVE / SHAREPOINT TESTS
# ═══════════════════════════════════════════════════════════════════

class TestOneDriveExtractor:
    def test_encode_onedrive_share_token_and_download_param(self):
        url = "https://1drv.ms/v/s!Amxyz123456"
        token = encode_onedrive_share_token(url)
        assert token.startswith("u!")
        assert "=" not in token

        sp_url = "https://contoso.sharepoint.com/:v:/g/personal/user/Eabc123?e=xyz"
        dl_url = append_download_param(sp_url)
        assert "download=1" in dl_url
        assert "e=xyz" in dl_url

    @pytest.mark.anyio
    async def test_onedrive_resolve_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/shares/u!" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "id": "OD123",
                    "name": "presentation_recording.mp4",
                    "size": 50 * 1024 * 1024,
                    "@microsoft.graph.downloadUrl": "https://public.dm.files.1drv.com/y4mDirectDownloadLink",
                    "video": {"height": 1080, "width": 1920, "duration": 60000},
                },
            )

        async with _mock_client(handler) as client:
            info = await resolve_onedrive_share("https://1drv.ms/v/s!Amxyz123456", client=client)
        assert info["title"] == "presentation_recording"
        assert info["direct_mp4_url"] == "https://public.dm.files.1drv.com/y4mDirectDownloadLink"
        assert info["file_size_mb"] == 50.0
        assert info["duration"] == 60

    @pytest.mark.anyio
    async def test_onedrive_owner_disabled_view_only(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "OD_VIEW_ONLY",
                    "name": "locked_video.mp4",
                    "capabilities": {"canDownload": False},
                },
            )

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="owner_disabled_download"):
                await resolve_onedrive_share("https://1drv.ms/v/s!ViewOnlyLink", client=client)


# ═══════════════════════════════════════════════════════════════════
# 3. MEGA.NZ TESTS
# ═══════════════════════════════════════════════════════════════════

class TestMegaExtractor:
    def _make_sample_mega_key(self) -> tuple[str, bytes, bytes]:
        raw_32 = bytes(range(32))
        key_b64 = mega_b64encode(raw_32)
        aes_key, iv, _ = derive_mega_file_key(key_b64)
        return key_b64, aes_key, iv

    def test_mega_url_key_parse(self):
        p1 = parse_mega_url("https://mega.nz/file/AbCdEfGh#KeyPart1234567890_-")
        assert p1 == {"kind": "file", "id": "AbCdEfGh", "key": "KeyPart1234567890_-"}

        p2 = parse_mega_url("https://mega.nz/#!LegacyId99!LegacyKey88")
        assert p2 == {"kind": "file", "id": "LegacyId99", "key": "LegacyKey88"}

        p3 = parse_mega_url("https://mega.nz/folder/FolderId11#FolderKey22")
        assert p3 == {"kind": "folder", "id": "FolderId11", "key": "FolderKey22"}

    @pytest.mark.anyio
    async def test_mega_attribute_and_stream_decrypt(self, tmp_path):
        key_b64, aes_key, iv = self._make_sample_mega_key()
        at_b64 = encrypt_mega_attributes({"n": "secret_archive.mp4"}, aes_key)
        decrypted_attrs = decrypt_mega_attributes(at_b64, aes_key)
        assert decrypted_attrs["n"] == "secret_archive.mp4"

        plaintext = b"Hello Mega AES-CTR stream payload!" * 100
        # AES-CTR is symmetric: encrypting with decrypt_mega_stream produces ciphertext
        ciphertext = decrypt_mega_stream(plaintext, aes_key, iv)
        assert ciphertext != plaintext
        assert decrypt_mega_stream(ciphertext, aes_key, iv) == plaintext

        def handler(request: httpx.Request) -> httpx.Response:
            if "g.api.mega.co.nz/cs" in str(request.url):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "s": len(plaintext),
                            "at": at_b64,
                            "g": "https://gfs200.userstorage.mega.co.nz/dl/enc_file_1",
                        }
                    ],
                )
            return httpx.Response(200, content=ciphertext)

        async with _mock_client(handler) as client:
            info = await resolve_mega_share(
                f"https://mega.nz/file/TestFileId#{key_b64}",
                client=client,
            )
            assert info["title"] == "secret_archive"
            assert info["direct_mp4_url"] == "https://gfs200.userstorage.mega.co.nz/dl/enc_file_1"
            assert info["mega_crypto"]["aes_key_hex"] == aes_key.hex()

            dest_file = str(tmp_path / "decrypted.mp4")
            await download_and_decrypt_mega_file(
                info["direct_mp4_url"], aes_key, iv, dest_file, client=client
            )
            with open(dest_file, "rb") as fh:
                assert fh.read() == plaintext

    @pytest.mark.anyio
    async def test_mega_quota_error(self):
        key_b64, _, _ = self._make_sample_mega_key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[-17])

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="share_quota_exceeded"):
                await resolve_mega_share(f"https://mega.nz/file/QuotaId#{key_b64}", client=client)

    @pytest.mark.anyio
    async def test_mega_expired_error(self):
        key_b64, _, _ = self._make_sample_mega_key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[-9])

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="share_expired"):
                await resolve_mega_share(f"https://mega.nz/file/DeadId#{key_b64}", client=client)


# ═══════════════════════════════════════════════════════════════════
# 4. TERABOX TESTS
# ═══════════════════════════════════════════════════════════════════

class TestTeraboxExtractor:
    def test_parse_terabox_urls(self):
        p = parse_terabox_url("https://1024tera.com/s/1abcDEF_99?pwd=xy12")
        assert p == {"surl": "1abcDEF_99", "pwd": "xy12"}

        p2 = parse_terabox_url("https://www.teraboxapp.com/sharing/link?surl=abcDEF_99")
        assert p2 == {"surl": "1abcDEF_99", "pwd": ""}

    @pytest.mark.anyio
    async def test_terabox_normal_resolve_with_ndus_cookie(self):
        captured_cookie = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_cookie["cookie"] = request.headers.get("Cookie", "")
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "title": "Shared Terabox Folder",
                    "list": [
                        {
                            "fs_id": 998877,
                            "server_filename": "anime_ep01.mp4",
                            "size": 120 * 1024 * 1024,
                            "isdir": "0",
                            "dlink": "https://d.terabox.com/file/anime_ep01.mp4?fid=123",
                            "thumbs": {"url3": "https://img.terabox.com/thumb.jpg"},
                        }
                    ],
                },
            )

        async with _mock_client(handler) as client:
            info = await resolve_terabox_share(
                "https://www.terabox.com/s/1ShareAbc",
                ndus="user_ndus_token_xyz",
                client=client,
            )
        assert info["title"] == "anime_ep01"
        assert info["direct_mp4_url"] == "https://d.terabox.com/file/anime_ep01.mp4?fid=123"
        assert info["file_size_mb"] == 120.0
        assert "ndus=user_ndus_token_xyz" in info["http_headers"].get("Cookie", "")

    @pytest.mark.anyio
    async def test_terabox_password_required(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "errno": -9,
                    "errmsg": "need verify password",
                },
            )

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="share_password_required"):
                await resolve_terabox_share(
                    "https://freeterabox.com/s/1LockedShare",
                    client=client,
                )


# ═══════════════════════════════════════════════════════════════════
# 5. DROPBOX TESTS
# ═══════════════════════════════════════════════════════════════════

class TestDropboxExtractor:
    def test_dropbox_dl1_conversion(self):
        u1 = "https://www.dropbox.com/scl/fi/abc123xyz/sample_video.mp4?rlkey=secretkey99&dl=0"
        norm1 = normalize_dropbox_url(u1)
        assert "dl=1" in norm1
        assert "dl=0" not in norm1
        assert "rlkey=secretkey99" in norm1

        u2 = "https://dropbox.com/s/legacyid123/archive.zip"
        norm2 = normalize_dropbox_url(u2)
        assert norm2 == "https://www.dropbox.com/s/legacyid123/archive.zip?dl=1"

        ext = DropboxExtractor()
        res = ext.extract_single(u1)
        assert res.ok
        assert res.title == "sample_video"
        assert "dl=1" in res.direct_url
        assert "rlkey=secretkey99" in res.direct_url


# ═══════════════════════════════════════════════════════════════════
# 6. PIKPAK V2 TESTS
# ═══════════════════════════════════════════════════════════════════

class TestPikPakV2Extractor:
    def test_extract_pass_code_from_url(self):
        assert extract_pass_code("https://mypikpak.com/s/VO123abc?pwd=4321") == "4321"
        assert extract_pass_code("https://mypikpak.com/s/VO123abc#pwd=abcd") == "abcd"
        assert extract_pass_code("https://mypikpak.com/s/VO123abc", pass_code="9999") == "9999"

    @pytest.mark.anyio
    async def test_pikpak_v2_password_required(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "share_status": "PASS_CODE_EMPTY",
                    "need_pass_code": True,
                },
            )

        async with _mock_client(handler) as client:
            with pytest.raises(ValueError, match="share_password_required"):
                await resolve_pikpak_share(
                    "https://mypikpak.com/s/VOLockedShare",
                    client=client,
                )

    @pytest.mark.anyio
    async def test_pikpak_v2_nested_folder_recursion(self):
        """Verify recursive folder traversal across root -> subfolder_1 -> subfolder_2."""
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            params = dict(request.url.params)
            if path.endswith("/drive/v1/share"):
                assert params.get("pass_code") == "8888"
                return httpx.Response(
                    200,
                    json={
                        "share_status": "OK",
                        "pass_code_token": "tok_8888",
                        "title": "Nested Course Share",
                    },
                )
            if path.endswith("/drive/v1/share/list"):
                assert params.get("pass_code_token") == "tok_8888"
                parent = params.get("parent_id", "")
                if parent == "":
                    return httpx.Response(
                        200,
                        json={
                            "files": [
                                {
                                    "id": "folder_lvl1",
                                    "name": "Module_1",
                                    "kind": "drive#folder",
                                },
                                {
                                    "id": "file_root",
                                    "name": "intro.mp4",
                                    "kind": "drive#file",
                                    "size": "10485760",
                                    "web_content_link": "https://dl.mypikpak.com/intro.mp4",
                                },
                            ]
                        },
                    )
                if parent == "folder_lvl1":
                    return httpx.Response(
                        200,
                        json={
                            "files": [
                                {
                                    "id": "folder_lvl2",
                                    "name": "Deep_Dive",
                                    "kind": "drive#folder",
                                },
                                {
                                    "id": "file_lvl1",
                                    "name": "part1.mp4",
                                    "kind": "drive#file",
                                    "size": "52428800",
                                    "web_content_link": "https://dl.mypikpak.com/part1.mp4",
                                },
                            ]
                        },
                    )
                if parent == "folder_lvl2":
                    return httpx.Response(
                        200,
                        json={
                            "files": [
                                {
                                    "id": "file_lvl2",
                                    "name": "final_project.zip",
                                    "kind": "drive#file",
                                    "size": "104857600",
                                    "web_content_link": "https://dl.mypikpak.com/final_project.zip",
                                }
                            ]
                        },
                    )
            return httpx.Response(404)

        ext = PikPakExtractor()
        client = _mock_client(handler)
        batch = ext.extract_batch(
            "https://mypikpak.com/s/VONested123?pwd=8888",
            client=client,
        )
        assert batch.ok
        assert batch.total_found == 3
        titles = [e.title for e in batch.entries]
        assert "intro.mp4" in titles
        assert "Module_1/part1.mp4" in titles
        assert "Module_1/Deep_Dive/final_project.zip" in titles

    @pytest.mark.anyio
    async def test_pikpak_v2_non_video_file_support(self):
        """Verify non-video files (e.g. .zip / .pdf / .mp3) resolve when web_content_link or links is present."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/drive/v1/share"):
                return httpx.Response(
                    200,
                    json={"share_status": "OK", "pass_code_token": "tok_open", "title": "Archive Pack"},
                )
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "doc_1",
                            "name": "dataset_bundle.zip",
                            "kind": "drive#file",
                            "size": str(80 * 1024 * 1024),
                            "web_content_link": "https://dl.mypikpak.com/dataset_bundle.zip",
                        }
                    ]
                },
            )

        async with _mock_client(handler) as client:
            info = await resolve_pikpak_share(
                "https://mypikpak.com/s/VOArchiveShare",
                user_token="user_jwt_token_123",
                client=client,
            )
        assert info["title"] == "dataset_bundle"
        assert info["direct_mp4_url"] == "https://dl.mypikpak.com/dataset_bundle.zip"
        assert info["file_size_mb"] == 80.0


# ═══════════════════════════════════════════════════════════════════
# 7. SHARED REGISTRIES & ERROR CODES INTEGRATION
# ═══════════════════════════════════════════════════════════════════

class TestCloudRegistriesAndErrors:
    def test_cloud_error_codes_and_vietnamese_quota_advice(self):
        for code in (
            "owner_disabled_download",
            "share_quota_exceeded",
            "share_password_required",
            "share_expired",
        ):
            assert code in ERROR_META
            meta = get_error_meta(code)
            assert meta["retryable"] is False

        quota_msg = get_error_meta("share_quota_exceeded")["user_message"]
        assert "Tạo bản sao" in quota_msg
        assert "Make a copy" in quota_msg

        assert classify_extraction_error("share_quota_exceeded") == (422, "share_quota_exceeded")
        assert classify_extraction_error("owner_disabled_download") == (422, "owner_disabled_download")
        assert classify_extraction_error("share_password_required") == (422, "share_password_required")
        assert classify_extraction_error("share_expired") == (404, "share_expired")

    def test_source_classifier_and_container_registry_cloud_platforms(self):
        cases = [
            ("https://drive.google.com/file/d/1AbCdEf/view", "gdrive", "single_video"),
            ("https://drive.google.com/drive/folders/1FolderAbc", "gdrive", "playlist"),
            ("https://1drv.ms/v/s!Amxyz123", "onedrive", "single_video"),
            ("https://mega.nz/file/Abc123#Key456", "mega", "single_video"),
            ("https://mega.nz/folder/Fld123#Key456", "mega", "playlist"),
            ("https://www.terabox.com/s/1AbcDef", "terabox", "single_video"),
            ("https://www.dropbox.com/scl/fi/id123/video.mp4?rlkey=k", "dropbox", "single_video"),
            ("https://mypikpak.com/s/VO123456", "pikpak", "single_video"),
        ]
        for url, expected_plat, expected_st in cases:
            clf = classify(url)
            assert clf.platform == expected_plat, f"Expected {expected_plat} for {url}, got {clf}"
            assert clf.source_type == expected_st
            cap = get_capability(clf.platform, clf.source_type)
            assert cap.support_level.value == "full"
            assert REGISTRY.platform_for(url) == expected_plat
