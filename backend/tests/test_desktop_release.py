"""Task #6172 (09/10): publishing a GitHub Release IS the release.

Owner: "nếu có 0.11.0 thì tôi phải vào đó thay đổi nữa hả" — /client/version,
the 426 gate and the admin page follow the latest GitHub Release; the env
values stay a floor and the fallback.
"""
import fakeredis
import pytest

from app.core import desktop_release as dr

REPO = "junnyken/dowload-video"
URL_10 = f"https://github.com/{REPO}/releases/download/v0.10.0/VidGrab_0.10.0_x64-setup.exe"


def release(tag="v0.10.0", url=URL_10, **kw):
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "body": "VidGrab cho Windows 0.10.0\n\n- Kiểm lượt tải bằng chữ ký số.\n- Ngoại tuyến 3 lượt/ngày.\n\nSHA-256: abc",
            "assets": [{"browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/x.sha256"},
                       {"browser_download_url": url}], **kw}


@pytest.fixture
def gh(monkeypatch):
    rc = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", rc)
    monkeypatch.setenv("DESKTOP_RELEASE_SOURCE", "github")
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.9.1")
    monkeypatch.setenv("DESKTOP_DOWNLOAD_URL", "https://github.com/x/old.exe")
    monkeypatch.setenv("DESKTOP_RELEASE_NOTES", "old notes")
    dr._mem.update(at=0.0, ttl=0, val=None)
    return rc


def test_newer_github_release_wins(gh):
    calls = []
    out = dr.current(lambda repo: calls.append(repo) or release())
    assert out == {"latest": "0.10.0", "downloadUrl": URL_10, "source": "github",
                   "notes": "Kiểm lượt tải bằng chữ ký số. Ngoại tuyến 3 lượt/ngày."}
    assert calls == [REPO]


def test_cached_one_call_per_ttl(gh):
    calls = []
    for _ in range(5):
        dr.current(lambda repo: calls.append(1) or release())
    assert len(calls) == 1 and 0 < gh.ttl(dr.CACHE_KEY) <= dr.CACHE_TTL_S


def test_env_floor_wins_over_an_older_release(gh, monkeypatch):
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.11.0")
    out = dr.current(lambda repo: release())
    assert out["latest"] == "0.11.0" and out["source"] == "env"


@pytest.mark.parametrize("bad", [
    None,                                            # GitHub unreachable / 403 rate limit
    release(prerelease=True),
    release(draft=True),
    release(tag="nightly"),
    release(url="https://evil.example/VidGrab_0.10.0_x64-setup.exe"),
    release(url=f"https://github.com/{REPO}/releases/download/v0.10.0/VidGrab_0.9.0_x64-setup.exe"),
    {"tag_name": "v0.10.0", "assets": []},           # no installer uploaded yet
])
def test_anything_unusable_falls_back_to_env(gh, bad):
    out = dr.current(lambda repo: bad)
    assert out == {"latest": "0.9.1", "downloadUrl": "https://github.com/x/old.exe",
                   "notes": "old notes", "source": "env"}


def test_failure_is_cached_briefly_and_never_raises(gh):
    def boom(repo):
        raise TimeoutError("github slow")
    assert dr.current(boom)["source"] == "env"
    assert 0 < gh.ttl(dr.CACHE_KEY) <= dr.FAIL_TTL_S


def test_source_env_never_calls_github(gh, monkeypatch):
    monkeypatch.setenv("DESKTOP_RELEASE_SOURCE", "env")
    calls = []
    assert dr.current(lambda repo: calls.append(1) or release())["latest"] == "0.9.1"
    assert calls == []


def test_client_version_and_update_gate_follow_github(app, gh, monkeypatch):
    monkeypatch.setattr(dr, "github_latest", lambda fetch=None: dr.parse_release(release(), REPO))
    v = app.get("/api/v1/client/version").json()
    assert v["latest"] == "0.10.0" and v["downloadUrl"] == URL_10
    from app.core import update_gate
    body = update_gate.update_required_response().body
    assert b'"latest":"0.10.0"' in body and URL_10.encode() in body
