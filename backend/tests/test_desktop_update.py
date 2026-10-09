"""Task #6205: the Windows app updates itself ("Cập nhật ngay").

GET /api/v1/client/update/{target}/{arch}/{current_version} is the Tauri v2
dynamic update server: 204 when there is nothing to install, else the latest
GitHub Release's installer + the CONTENT of its .sig asset. Never an unsigned
update; the repo comes from DESKTOP_RELEASE_REPO. No test calls GitHub.
"""
import base64

import fakeredis
import pytest

from app.core import desktop_release as dr

REPO = "junnyken/dowload-video"
SIG = base64.b64encode(
    b"untrusted comment: signature from tauri secret key\n"
    b"RURjNAIiy1FN+Kc0000000000000000000000000000000000000000000000000000000000000000000000000000000000\n"
    b"trusted comment: timestamp:1791000000\tfile:VidGrab_0.11.1_x64-setup.exe\tversion:0.11.1\n"
    b"Qm9ndXNHbG9iYWxTaWduYXR1cmUwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMA==\n"
).decode()


def base(repo=REPO, tag="v0.11.1"):
    return f"https://github.com/{repo}/releases/download/{tag}"


def release(version="0.11.1", repo=REPO, sig=True, extra=(), **kw):
    tag = f"v{version}"
    exe = f"{base(repo, tag)}/VidGrab_{version}_x64-setup.exe"
    assets = [{"browser_download_url": f"{base(repo, tag)}/SHA256SUMS.txt"},
              {"browser_download_url": exe}, *extra]
    if sig:
        assets.append({"browser_download_url": exe + ".sig"})
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "published_at": "2026-10-09T08:00:00Z",
            "body": "VidGrab 0.11.1\n\n- Tự cập nhật trong ứng dụng.\n", "assets": assets, **kw}


@pytest.fixture
def gh(monkeypatch):
    rc = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", rc)
    monkeypatch.setenv("DESKTOP_RELEASE_SOURCE", "github")
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.10.0")
    monkeypatch.delenv("DESKTOP_RELEASE_REPO", raising=False)
    monkeypatch.delenv("DESKTOP_UPDATER_ENABLED", raising=False)
    dr._mem.update(at=0.0, ttl=0, val=None)
    return rc


class Fake:
    """GitHub stand-in: the release JSON and the .sig contents."""

    def __init__(self, rel, sig_text=SIG):
        self.rel, self.sig_text, self.repos, self.sig_urls = rel, sig_text, [], []

    def fetch(self, repo):
        self.repos.append(repo)
        return self.rel

    def fetch_sig(self, url):
        self.sig_urls.append(url)
        if isinstance(self.sig_text, Exception):
            raise self.sig_text
        return self.sig_text


def offer(fake, target="windows", arch="x86_64", current="0.11.0"):
    return dr.update_offer(target, arch, current, fake.fetch, fake.fetch_sig)


def test_newer_signed_release_is_offered_with_the_sig_content(gh):
    f = Fake(release())
    exe = f"{base()}/VidGrab_0.11.1_x64-setup.exe"
    assert offer(f) == {"version": "0.11.1", "notes": "Tự cập nhật trong ứng dụng.",
                        "pub_date": "2026-10-09T08:00:00Z", "url": exe, "signature": SIG}
    assert f.repos == [REPO] and f.sig_urls == [exe + ".sig"]


def test_signature_cached_with_the_release(gh):
    f = Fake(release())
    for _ in range(4):
        assert offer(f)["signature"] == SIG
    assert len(f.repos) == 1 and len(f.sig_urls) == 1


@pytest.mark.parametrize("current", ["0.11.1", "0.12.0", "1.0.0"])
def test_same_or_newer_app_gets_204(gh, current):
    assert offer(Fake(release()), current=current) is None


@pytest.mark.parametrize("target,arch", [("linux", "x86_64"), ("darwin", "aarch64"),
                                         ("windows", "i686"), ("windows", "aarch64")])
def test_other_platforms_get_nothing(gh, target, arch):
    f = Fake(release())
    assert offer(f, target, arch) is None
    assert f.repos == []


def test_no_sig_asset_means_no_update(gh):
    f = Fake(release(sig=False))
    assert offer(f) is None and f.sig_urls == []
    # the release itself still counts for /client/version and the download page
    assert dr.current(lambda repo: release(sig=False))["latest"] == "0.11.1"


@pytest.mark.parametrize("bad", ["", "<html>Not Found</html>", "not base64 !!",
                                 base64.b64encode(b"hello world").decode(), "A" * 5000,
                                 TimeoutError("slow")])
def test_unusable_sig_means_no_update_and_short_cache(gh, bad):
    f = Fake(release(), sig_text=bad)
    assert offer(f) is None
    assert 0 < gh.ttl(dr.CACHE_KEY) <= dr.FAIL_TTL_S


def test_sig_from_another_release_or_name_is_ignored(gh):
    # a .sig of another file / another release in the same asset list
    other = {"browser_download_url": f"{base(tag='v0.11.0')}/VidGrab_0.11.0_x64-setup.exe.sig"}
    f = Fake(release(sig=False, extra=(other,)))
    assert offer(f) is None and f.sig_urls == []


def test_installer_must_have_the_exact_name(gh):
    rel = release(sig=False)
    evil = f"{base()}/Evil_0.11.1_x64-setup.exe"
    rel["assets"] = [{"browser_download_url": evil}, {"browser_download_url": evil + ".sig"}]
    assert offer(Fake(rel)) is None


@pytest.mark.parametrize("bad", ["", "abc", "0.11", "0.11.0; rm", "latest"])
def test_malformed_current_version_gets_204(gh, bad):
    assert offer(Fake(release()), current=bad) is None


def test_draft_prerelease_and_offhost_assets_are_refused(gh):
    assert offer(Fake(release(prerelease=True))) is None
    dr._mem.update(at=0.0, ttl=0, val=None)
    gh.flushall()
    rel = release()
    rel["assets"] = [{"browser_download_url": "https://evil.example/VidGrab_0.11.1_x64-setup.exe"},
                     {"browser_download_url": "https://evil.example/VidGrab_0.11.1_x64-setup.exe.sig"}]
    assert offer(Fake(rel)) is None


def test_repo_override(gh, monkeypatch):
    monkeypatch.setenv("DESKTOP_RELEASE_REPO", "junnyken/vidgrab-releases")
    f = Fake(release(repo="junnyken/vidgrab-releases"))
    out = offer(f)
    assert f.repos == ["junnyken/vidgrab-releases"]
    assert out["url"].startswith("https://github.com/junnyken/vidgrab-releases/releases/download/v0.11.1/")
    # an asset of the OLD repo is not accepted once the repo is switched
    dr._mem.update(at=0.0, ttl=0, val=None)
    gh.flushall()
    assert offer(Fake(release(repo=REPO))) is None


def test_switches_off(gh, monkeypatch):
    monkeypatch.setenv("DESKTOP_UPDATER_ENABLED", "0")
    assert offer(Fake(release())) is None
    monkeypatch.delenv("DESKTOP_UPDATER_ENABLED")
    monkeypatch.setenv("DESKTOP_RELEASE_SOURCE", "env")
    f = Fake(release())
    assert offer(f) is None and f.repos == []


def test_env_floor_alone_never_offers_an_update(gh, monkeypatch):
    # the env says 0.12.0 is latest, but there is no signed installer for it
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.12.0")
    assert offer(Fake(None), current="0.11.0") is None


def test_valid_signature_negative_control():
    assert dr.valid_signature(SIG) == SIG
    assert dr.valid_signature(SIG.encode()) == SIG
    assert dr.valid_signature(" \n" + SIG + "\n") == SIG
    assert dr.valid_signature(None) is None


# ---------------------------------------------------------------- HTTP route

def test_route_200_shape(app, gh, monkeypatch):
    monkeypatch.setattr(dr, "github_latest",
                        lambda fetch=None, fetch_sig=None: {**dr.parse_release(release(), REPO), "signature": SIG})
    r = app.get("/api/v1/client/update/windows/x86_64/0.11.0")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"version", "notes", "pub_date", "url", "signature"}
    assert body["version"] == "0.11.1" and body["signature"] == SIG
    assert body["url"].endswith("/v0.11.1/VidGrab_0.11.1_x64-setup.exe")


@pytest.mark.parametrize("path", ["windows/x86_64/0.11.1", "windows/x86_64/0.12.0",
                                  "linux/x86_64/0.11.0", "windows/x86_64/garbage"])
def test_route_204(app, gh, monkeypatch, path):
    monkeypatch.setattr(dr, "github_latest",
                        lambda fetch=None, fetch_sig=None: {**dr.parse_release(release(), REPO), "signature": SIG})
    r = app.get(f"/api/v1/client/update/{path}")
    assert r.status_code == 204 and r.content == b""


def test_route_204_without_signature(app, gh, monkeypatch):
    monkeypatch.setattr(dr, "github_latest", lambda fetch=None, fetch_sig=None: dr.parse_release(release(), REPO))
    assert app.get("/api/v1/client/update/windows/x86_64/0.11.0").status_code == 204


def test_route_is_never_update_gated(app, gh, monkeypatch):
    monkeypatch.setenv("CLIENT_UPDATE_GATE_ENABLED", "1")
    monkeypatch.setenv("DESKTOP_MIN_VERSION", "0.11.0")
    monkeypatch.setattr(dr, "github_latest",
                        lambda fetch=None, fetch_sig=None: {**dr.parse_release(release(), REPO), "signature": SIG})
    hdr = {"Origin": "http://tauri.localhost", "X-VG-Client": "0.9.0"}
    assert app.get("/api/v1/client/update/windows/x86_64/0.9.0", headers=hdr).status_code == 200
    # negative control: another app route IS gated for the same old client
    assert app.get("/api/v1/client/history", headers=hdr).status_code == 426
