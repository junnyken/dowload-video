"""27/10/2026 (owner): "Đường về nhà đẹp" arrived as "uong-ve-nha-ep" — every
server-side display name went through NFKD + ascii-ignore, which also drops Đ/đ.
Names now keep Vietnamese (same rules as the Windows app); headers go through
content_disposition (ASCII fallback with Đ→D + RFC 5987 filename*)."""
import pathlib
import re

import pytest

from app.core.local_download import content_disposition, display_name


@pytest.mark.parametrize("raw,want", [
    ("Đường về nhà đẹp", "Đường về nhà đẹp"),
    ("Tiếng Việt: 'ngoặc' ? * < > |", "Tiếng Việt 'ngoặc'"),
    ("😀 Video", "😀 Video"),
    ("a/b\\c", "a b c"),
    ("  nhiều    khoảng   trắng.  ", "nhiều khoảng trắng"),
    ("..", "video"),
    ("", "video"),
    (None, "video"),
    ("CON", "video"),
    ("com1.txt", "video"),
    ("100% thật", "100 thật"),
])
def test_display_name(raw, want):
    assert display_name(raw) == want


def test_long_vietnamese_is_cut_at_150_bytes_on_a_character():
    name = display_name("Đường về nhà đẹp " * 30)
    assert len(name.encode("utf-8")) <= 150
    name.encode("utf-8").decode("utf-8")      # never half a character
    assert name.startswith("Đường về nhà đẹp") and not name.endswith(" ")


def test_decomposed_input_is_composed():
    assert display_name("Đường") == "Đường"


def test_ascii_only_switch_folds_d_bar(monkeypatch):
    monkeypatch.setenv("FILENAME_ASCII_ONLY", "1")
    assert display_name("Đường về nhà đẹp") == "Duong ve nha dep"


def test_content_disposition_header():
    assert content_disposition("Đường về nhà đẹp.mp4") == (
        "attachment; filename=\"Duong ve nha dep.mp4\"; "
        "filename*=UTF-8''%C4%90%C6%B0%E1%BB%9Dng%20v%E1%BB%81%20nh%C3%A0%20%C4%91%E1%BA%B9p.mp4")


def test_no_ascii_stripping_left_for_file_names():
    """NFKD + encode('ascii','ignore') may stay only for URL slugs, not file names."""
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = sorted(str(p.relative_to(app)) for p in app.rglob("*.py")
                  if re.search(r"normalize\(\s*['\"]NFKD['\"]", p.read_text(encoding="utf-8")))
    assert hits == ["api/admin_partners.py", "api/workspace.py", "core/local_download.py", "utils/helpers.py"]


class _FakeResp:
    headers = {"content-type": "video/mp4"}

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size=65536):
        yield b"\0" * 10


@pytest.mark.parametrize("path,want", [
    ("/api/v1/proxy-download?url=https://cdn.example/v.mp4&filename=Đường về nhà đẹp&ext=mp4", "Đường về nhà đẹp.mp4"),
    ("/api/v1/download-thumbnail?url=https://cdn.example/t.jpg&filename=Đường về nhà đẹp", "Đường về nhà đẹp.jpg"),
])
def test_routes_send_the_vietnamese_name(monkeypatch, path, want):
    from urllib.parse import quote
    from fastapi.testclient import TestClient
    import app.api.routes as routes
    import app.core.ssrf_guard as guard
    from app.main import app as fastapi_app

    async def fake_open(client, method, url):
        async def aclose():
            return None
        return _FakeResp(), aclose
    monkeypatch.setattr(guard, "open_safe_stream", fake_open)
    monkeypatch.setattr(routes, "_assert_safe_url", lambda url: None)
    r = TestClient(fastapi_app).get(path)
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-disposition"].endswith("filename*=UTF-8''" + quote(want, safe=""))
