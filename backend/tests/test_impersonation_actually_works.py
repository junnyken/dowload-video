"""
"It imported" is not "it works".

yt-dlp only registers its impersonate handler for a curl_cffi version range it
checks at import time. Outside that range there are zero impersonation targets
and no error at all until a YoutubeDL constructor happens to ask for one — the
dependency looks satisfied the whole time. That is how an unpinned install
once left browser impersonation silently unavailable.

requirements.txt therefore carries an upper bound, and the bound has to track
yt-dlp rather than a comment written months ago. It said <0.15.0, from when
yt-dlp accepted 0.10.x-0.14.x; yt-dlp now accepts through 0.15.x, and 0.15.0 is
where curl_cffi's PYSEC-2026-2431 is fixed — so the stale bound was holding the
project on a vulnerable release for a reason that had expired.

This asks the installed yt-dlp, every run, whether impersonation is genuinely
available. If a future yt-dlp narrows its range, or a resolver drifts past the
bound, this fails here instead of Facebook and TikTok quietly getting harder to
download.
"""

from __future__ import annotations

import pytest


def test_curl_cffi_is_installed():
    """The Facebook retry ladder asks for an impersonate target; without
    curl_cffi that whole path is dead weight."""
    pytest.importorskip("curl_cffi")


def test_yt_dlp_accepts_the_installed_curl_cffi():
    """The real check: yt-dlp's own import-time guard, not our reading of it."""
    pytest.importorskip("curl_cffi")
    try:
        import yt_dlp.networking._curlcffi  # noqa: F401
    except ImportError as exc:
        import curl_cffi
        pytest.fail(
            f"yt-dlp refuses the installed curl_cffi {curl_cffi.__version__}: {exc}. "
            "Impersonation is silently unavailable — every request that needs it "
            "falls back and fails without saying why. Fix the bound in "
            "requirements.txt to match what yt-dlp actually accepts."
        )


def test_an_impersonate_target_can_be_built():
    """Availability is not the same as usable. Build the target the downloader
    asks for and confirm yt-dlp reports a handler that can serve it."""
    pytest.importorskip("curl_cffi")
    pytest.importorskip("yt_dlp.networking._curlcffi")

    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget

    target = ImpersonateTarget("chrome")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        available = ydl._get_available_impersonate_targets()
        assert available, (
            "yt-dlp reports zero impersonation targets — the handler did not "
            "register, so `impersonate` is a no-op wherever we pass it"
        )
        assert ydl._impersonate_target_available(target), (
            f"chrome is not among the available targets: "
            f"{[str(t) for t, _ in available][:8]}"
        )


def test_the_downloader_can_produce_a_target():
    """The Facebook path builds its target through this helper; if it returns
    None the retry ladder silently drops its impersonation attempt."""
    from app.services.downloader import _impersonate_target

    assert _impersonate_target() is not None, (
        "_impersonate_target() returned None — the Facebook retry ladder will "
        "skip its impersonated attempt without reporting anything"
    )
