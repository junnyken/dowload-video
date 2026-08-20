"""
Which PO token provider the YouTube extractor is configured with.

android_vr URLs need a PO token: measured on production, extraction succeeded
while every byte fetch returned 403 with the server's own IP matching the ip=
signed into the link. The HTTP provider's default host (`bgutil-pot`) is a
docker-compose service name that does not resolve on this split-project
deployment, so the token was never issued.

The image now carries the script provider, and BGUTIL_POT_URL still selects the
HTTP one when a service really is running — the compose layout must keep working.
"""
import pytest


def _yt_opts(monkeypatch, url="https://www.youtube.com/watch?v=abc"):
    from app.services import downloader
    opts = {}
    # _build_ydl_opts-style entry point differs by version; drive the branch the
    # same way the extractor does, through the module's option builder.
    build = getattr(downloader, "_build_youtube_opts", None)
    if build is None:
        pytest.skip("option builder not exposed under a known name")
    return build(url, opts)


def test_script_provider_used_when_no_service_url(monkeypatch):
    monkeypatch.delenv("BGUTIL_POT_URL", raising=False)
    monkeypatch.setenv("BGUTIL_POT_HOME", "/opt/bgutil-pot/server")
    from app.services import downloader
    src = __import__("inspect").getsource(downloader)
    assert "youtubepot-bgutilscript" in src, "script provider is never configured"
    assert '"server_home"' in src


def test_http_provider_still_selected_when_url_is_set():
    """The compose deployment sets BGUTIL_POT_URL and must keep using HTTP."""
    from app.services import downloader
    src = __import__("inspect").getsource(downloader)
    assert "youtubepot-bgutilhttp" in src
    assert "BGUTIL_POT_URL" in src


def test_no_unreachable_compose_hostname_default():
    """
    The old default was http://bgutil-pot:4416 — a hostname that cannot resolve
    outside compose, which silently produced no token instead of an error.
    """
    from app.services import downloader
    src = __import__("inspect").getsource(downloader)
    assert 'or "http://bgutil-pot:4416"' not in src, (
        "still defaulting to a compose-only hostname"
    )


def test_image_ships_the_script_and_its_runtime():
    """The Dockerfile must fetch generate_once.ts and keep Deno available."""
    from pathlib import Path
    df = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    assert "generate_once.ts" in df, "script provider source is never fetched"
    assert "deno" in df.lower(), "the script provider needs Deno"
    assert "BGUTIL_VERSION" in df, "version should be pinned, not floating"
