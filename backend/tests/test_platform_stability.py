"""Platform stability (19/10): extractor_broken is told apart from user and
network errors; the health check reads the outcome store (every attempt) and
acts — degrade, Cobalt first for FB/IG/X, try a newer yt-dlp; the yt-dlp
channel only promotes a version that fixes something without breaking what
works, keeps the previous one for rollback, survives a redeploy (boot) and
restarts the right processes; partners can read platform status."""
import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    r = fakeredis.FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    import app.core.download_outcomes as do
    monkeypatch.setattr(do, "_redis", lambda: r)
    return r


NOW = datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc)


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg,code", [
    ("ERROR: [TikTok] 123: Unable to extract universal data for rehydration; please report this issue", "extractor_broken"),
    ("ERROR: [facebook] x: KeyError('video')", "extractor_broken"),
    ("ERROR: HTTP Error 503: Service Unavailable", "provider_unavailable"),
    ("ERROR: [youtube] abc: Video unavailable", "video_unavailable"),
    ("ERROR: [instagram] x: Private video. Sign in", "private_or_login_required"),
])
def test_classification_separates_a_broken_extractor(msg, code):
    from app.core.extraction_errors import classify_extraction_error
    assert classify_extraction_error(msg)[1] == code


def test_extractor_broken_has_user_message():
    from app.core.error_codes import get_error_meta
    meta = get_error_meta("extractor_broken")
    assert meta["retryable"] is True and "Nền tảng" in meta["user_message"]


# ── health check over the outcome store ──────────────────────────────────────

def _traffic(do, platform, ok, codes, now=NOW):
    for _ in range(ok):
        do.record(platform, True, now=now)
    for code, n in codes.items():
        for _ in range(n):
            do.record(platform, False, code, now=now)


def test_recent_counts_split_user_system_broken(fr):
    import app.core.download_outcomes as do
    from app.core import platform_health as ph
    _traffic(do, "tiktok", 10, {"extractor_broken": 4, "video_unavailable": 3, "temporary_blocked": 2})
    c = ph.recent_counts(now=NOW)["tiktok"]
    assert c == {"ok": 10, "err": 9, "user_err": 3, "system_err": 6, "broken": 4, "total": 19}
    assert ph.verdict(c) == "broken"


@pytest.mark.parametrize("ok,codes,expect", [
    (20, {"video_unavailable": 15}, None),             # users' dead links: never an alert
    (5, {"temporary_blocked": 10}, "critical"),
    (14, {"provider_unavailable": 6}, "warning"),
    (2, {"extractor_broken": 3}, None),                # 5 attempts: too few to judge
    (5, {"extractor_broken": 3, "video_unavailable": 20}, "broken"),  # dead links do not dilute
])
def test_verdict(fr, ok, codes, expect):
    import app.core.download_outcomes as do
    from app.core import platform_health as ph
    _traffic(do, "facebook", ok, codes)
    assert ph.verdict(ph.recent_counts(now=NOW)["facebook"]) == expect


def test_broken_platform_triggers_degrade_cobalt_and_remediation(fr, monkeypatch):
    import app.core.download_outcomes as do
    from app.core import alerts, platform_health as ph
    sent, notified, queued = [], [], []
    monkeypatch.setattr(alerts, "send_admin_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(ph, "_notify_partners", lambda ev, p, r: notified.append((ev, p)))
    monkeypatch.setattr(ph, "request_remediation", lambda p, r: queued.append(p) or True)
    monkeypatch.setattr(ph, "probe_confirms_failure", lambda p: True)
    _traffic(do, "facebook", 6, {"extractor_broken": 6})
    found = ph.check_platforms(now=NOW)
    assert found[0]["verdict"] == "broken"
    assert sent and "đổi cấu trúc" in sent[0][1]
    assert ph.prefer_cobalt("facebook")
    assert "facebook" in ph.degraded()
    assert notified == [("platform.degraded", "facebook")]
    assert queued == ["facebook"]
    # a second check does not re-notify partners
    ph.check_platforms(now=NOW)
    assert notified == [("platform.degraded", "facebook")]


def test_recovery_clears_degraded_and_cobalt(fr, monkeypatch):
    from app.core import platform_health as ph
    notified = []
    monkeypatch.setattr(ph, "_notify_partners", lambda ev, p, r: notified.append((ev, p)))
    ph.mark_degraded("instagram", "probe")
    ph.set_prefer_cobalt("instagram", True)
    assert ph.mark_recovered("instagram")
    assert not ph.prefer_cobalt("instagram") and "instagram" not in ph.degraded()
    assert notified[-1] == ("platform.recovered", "instagram")


def test_remediation_has_a_cooldown(fr, monkeypatch):
    from app.core import platform_health as ph
    import app.tasks.video_tasks as vt
    calls = []
    monkeypatch.setattr(vt.ytdlp_remediate, "apply_async", lambda **k: calls.append(k))
    assert ph.request_remediation("tiktok", "x") is True
    assert ph.request_remediation("tiktok", "x") is False
    assert len(calls) == 1


def test_cobalt_override_reaches_the_downloader(fr, monkeypatch):
    from app.core import platform_health as ph
    from app.services.downloader import cobalt_first
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "")
    assert cobalt_first("facebook", "video") is False
    ph.set_prefer_cobalt("facebook", True)
    assert cobalt_first("facebook", "video") is True
    assert cobalt_first("facebook", "video_1080") is False      # explicit height stays yt-dlp
    assert ph.set_prefer_cobalt("tiktok", True) is False         # no Cobalt route for TikTok


def test_alert_entry_point_uses_the_outcome_store(fr, monkeypatch):
    import app.core.download_outcomes as do
    from app.core import alerts, platform_health as ph
    seen = []
    monkeypatch.setattr(ph, "check_platforms", lambda counts=None, **k: seen.append(counts))
    _traffic(do, "youtube", 3, {}, now=None)        # the check reads "now"
    alerts.check_platform_fail_rates()
    assert seen and "youtube" in seen[0]


def test_playbook_rotate_fallback_now_works(fr):
    from app.core import playbooks, platform_health as ph
    assert playbooks._action_rotate_fallback_order({"platform": "twitter"})["success"] is True
    assert ph.prefer_cobalt("twitter")
    assert playbooks._action_rotate_fallback_order({"platform": "youtube"})["success"] is False


# ── probe sweep → health ─────────────────────────────────────────────────────

def test_probe_break_and_recovery_reach_platform_health(fr, monkeypatch):
    from app.tasks import probe_tasks
    from app.core import platform_health as ph
    broken, recovered = [], []
    monkeypatch.setattr(probe_tasks, "get_redis", lambda: fr)
    monkeypatch.setattr(probe_tasks, "_send", lambda m: None)
    monkeypatch.setattr(ph, "on_platform_broken", lambda p, r: broken.append(p))
    monkeypatch.setattr(ph, "mark_recovered", lambda p: recovered.append(p) or True)
    probe_tasks._handle_alerts({"douyin": probe_tasks.FAILED}, {"douyin": "ok"})
    assert broken == ["douyin"]
    probe_tasks._handle_alerts({"douyin": probe_tasks.OK}, {"douyin": probe_tasks.FAILED})
    assert recovered == ["douyin"]


# ── yt-dlp channel ───────────────────────────────────────────────────────────

def _fake_ytdlp(path, version):
    os.makedirs(os.path.join(path, "yt_dlp"), exist_ok=True)
    with open(os.path.join(path, "yt_dlp", "version.py"), "w") as f:
        f.write(f"__version__ = '{version}'\n")


@pytest.fixture()
def chan(fr, tmp_path, monkeypatch):
    from app.core import ytdlp_channel as yc
    monkeypatch.setattr(yc, "OVERRIDE_DIR", str(tmp_path / "ytdlp_override"))
    monkeypatch.setattr(yc, "restart_processes", lambda: {"uvicorn": [], "celery": []})
    return yc


def test_version_order_handles_nightlies():
    from app.core.ytdlp_channel import newer
    assert newer("2026.10.10.233141.dev0", "2026.8.19")
    assert newer("2026.10.20", "2026.10.10.233141.dev0")
    assert not newer("2026.8.19", "2026.8.19")
    assert not newer("garbage", "2026.8.19")
    # PyPI name vs the version inside the package (real nightly, 19/10)
    from app.core.ytdlp_channel import same
    assert same("2026.10.10.233141.dev0", "2026.10.10.233141")
    assert not newer("2026.10.10.233141.dev0", "2026.10.10.233141")
    assert not newer("2026.10.10.233141", "2026.10.10.233141.dev0")


def test_latest_versions_picks_stable_and_newest_nightly(monkeypatch):
    from app.core import ytdlp_channel as yc
    data = {"info": {"version": "2026.8.19"}, "releases": {
        "2026.8.19": [{}], "2026.9.27.232945.dev0": [{}], "2026.10.10.233141.dev0": [{}],
        "2026.10.11.1.dev0": []}}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(data).encode()
    monkeypatch.setattr(yc.urllib.request, "urlopen", lambda *a, **k: R())
    assert yc.latest_versions() == {"stable": "2026.8.19", "nightly": "2026.10.10.233141.dev0"}


def _cands(monkeypatch, yc, results):
    """install writes a fake package; probe_with answers from `results[version]`."""
    def install(version, target):
        _fake_ytdlp(target, version)
        return None
    monkeypatch.setattr(yc, "install", install)
    # path None = the image copy; the version running now is asked first
    # (try_candidates' "does the current one already work?" check).
    monkeypatch.setattr(yc, "probe_with", lambda path, plats: {
        p: {"ok": p in results.get(yc.dir_version(path) if path else "image", set())} for p in plats})
    monkeypatch.setattr(yc, "currently_ok_platforms", lambda: ["youtube", "facebook"])


def test_promotes_the_first_candidate_that_fixes_without_regressing(chan, monkeypatch):
    yc = chan
    _cands(monkeypatch, yc, {
        "2026.9.1": {"youtube", "facebook"},                 # stable: tiktok still broken
        "2026.10.10.1.dev0": {"youtube", "facebook", "tiktok"},
    })
    out = yc.try_candidates(["tiktok"], [("2026.9.1", "stable"), ("2026.10.10.1.dev0", "nightly")], "t")
    assert out["promoted"]["version"] == "2026.10.10.1.dev0"
    assert out["tried"][0]["result"] == "rejected" and out["tried"][0]["still_broken"] == ["tiktok"]
    assert yc.dir_version(yc.OVERRIDE_DIR) == "2026.10.10.1.dev0"
    assert yc.get_state()["pinned"]["version"] == "2026.10.10.1.dev0"
    assert not os.path.exists(yc.OVERRIDE_DIR + ".cand")


def test_a_candidate_that_breaks_a_working_platform_is_rejected(chan, monkeypatch):
    yc = chan
    _fake_ytdlp(yc.OVERRIDE_DIR, "2026.8.19")
    _cands(monkeypatch, yc, {"2026.10.10.1.dev0": {"tiktok", "facebook"}})   # fixes tiktok, breaks youtube
    out = yc.try_candidates(["tiktok"], [("2026.10.10.1.dev0", "nightly")], "t")
    assert out["promoted"] is None
    assert out["tried"][0]["regressed"] == ["youtube"]
    assert yc.dir_version(yc.OVERRIDE_DIR) == "2026.8.19"          # untouched


def test_install_failure_is_reported_not_promoted(chan, monkeypatch):
    yc = chan
    monkeypatch.setattr(yc, "install", lambda v, t: "pip failed")
    monkeypatch.setattr(yc, "currently_ok_platforms", lambda: [])
    monkeypatch.setattr(yc, "probe_with", lambda path, plats: {p: {"ok": False} for p in plats})
    out = yc.try_candidates(["tiktok"], [("2026.10.10.1.dev0", "nightly")], "t")
    assert out["promoted"] is None and out["tried"][0]["result"] == "install_failed"


def test_promote_keeps_previous_and_rollback_restores_it(chan, monkeypatch):
    yc = chan
    _fake_ytdlp(yc.OVERRIDE_DIR, "2026.8.19")
    _fake_ytdlp(yc.OVERRIDE_DIR + ".cand", "2026.10.10.1.dev0")
    yc.promote(yc.OVERRIDE_DIR + ".cand", "2026.10.10.1.dev0", "nightly", "t")
    assert yc.dir_version(yc.OVERRIDE_DIR + ".prev") == "2026.8.19"
    entry = yc.rollback("test")
    assert entry["from"] == "2026.10.10.1.dev0" and entry["to"] == "2026.8.19"
    assert yc.dir_version(yc.OVERRIDE_DIR) == "2026.8.19"


def test_rollback_without_previous_returns_to_the_image(chan, monkeypatch):
    yc = chan
    monkeypatch.setattr(yc, "image_version", lambda: "2026.8.19")
    _fake_ytdlp(yc.OVERRIDE_DIR, "2026.10.10.1.dev0")
    entry = yc.rollback("test")
    assert entry["to"] == "2026.8.19" and not os.path.exists(yc.OVERRIDE_DIR)
    assert yc.get_state()["pinned"] is None


def test_remediate_tries_newer_versions_only_and_is_locked(chan, fr, monkeypatch):
    yc = chan
    monkeypatch.setattr(yc, "active_version", lambda: "2026.8.19")
    monkeypatch.setattr(yc, "latest_versions", lambda: {"stable": "2026.8.19", "nightly": "2026.10.10.1.dev0"})
    seen = {}
    monkeypatch.setattr(yc, "try_candidates", lambda b, c, r: seen.setdefault("c", c) and {"promoted": None, "tried": []})
    yc.remediate(["tiktok"], "probe")
    assert seen["c"] == [("2026.10.10.1.dev0", "nightly")]           # stable is not newer
    fr.set(yc.LOCK_KEY, "1")
    assert yc.remediate(["tiktok"], "probe") == {"skipped": "another run in progress"}


def test_boot_after_redeploy(chan, monkeypatch):
    yc = chan
    assert yc.boot() == "no pin"
    st = yc.get_state()
    st["pinned"] = {"version": "2026.10.10.1.dev0", "source": "nightly"}
    yc._save_state(st)
    monkeypatch.setattr(yc, "image_version", lambda: "2026.8.19")
    monkeypatch.setattr(yc, "install", lambda v, t: _fake_ytdlp(t, v))
    assert "reinstalled" in yc.boot()
    assert yc.dir_version(yc.OVERRIDE_DIR) == "2026.10.10.1.dev0"
    assert "present" in yc.boot()
    # a later image ships something newer: pin dropped, override removed
    monkeypatch.setattr(yc, "image_version", lambda: "2026.11.1")
    assert "dropped" in yc.boot()
    assert yc.get_state()["pinned"] is None and not os.path.exists(yc.OVERRIDE_DIR)


def test_restart_signals_uvicorn_supervisor_and_celery_main_only(monkeypatch):
    from app.core import ytdlp_channel as yc
    procs = [{"pid": pid, "ppid": ppid, "argv": argv} for pid, ppid, argv in [
        (1, 0, ["/bin/sh", "./docker-entrypoint.sh"]),
        # the entrypoint's shells carry the same words — must NOT be signalled
        (9, 1, ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --workers 2"]),
        (5, 1, ["sh", "-c", "while true; do celery -A app.core.celery_app worker -Q downloads; sleep 3; done"]),
        (10, 9, ["/usr/local/bin/python3.12", "/usr/local/bin/uvicorn", "app.main:app", "--workers", "2"]),
        (11, 10, ["/usr/local/bin/python", "-c", "from multiprocessing.spawn import spawn_main"]),
        (20, 5, ["/usr/local/bin/python", "/usr/local/bin/celery", "-A", "app.core.celery_app", "worker"]),
        (21, 20, ["/usr/local/bin/python", "/usr/local/bin/celery", "-A", "app.core.celery_app", "worker"]),
        (30, 1, ["/usr/local/bin/celery", "-A", "app.core.celery_app", "beat"]),
        (40, 1, ["bash", "-c", "kill uvicorn app.main:app celery worker"]),
    ]]
    killed = []
    monkeypatch.setattr(yc, "_procs", lambda: procs)
    monkeypatch.setattr(yc.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(yc, "_log", lambda *a, **k: {})
    yc.restart_processes()
    assert killed == [(10, signal.SIGHUP), (20, signal.SIGTERM)]


def test_probe_cli_runs_in_a_subprocess_with_the_given_path(tmp_path):
    """The real subprocess: an empty override + an unknown platform still
    answers with one JSON line (no target), proving the CLI wiring."""
    from app.core import ytdlp_channel as yc
    out = yc.probe_with(str(tmp_path), ["no_such_platform"])
    assert out["no_such_platform"]["ok"] is False


# ── partner + paid-provider guard ────────────────────────────────────────────

def test_partner_platform_status(fr, monkeypatch):
    from app.api import partner
    from app.core import platform_health as ph
    import app.api.routes as routes

    async def fake_status():
        return {"platforms": [{"platform": "tiktok", "status": "healthy", "message": "ok", "last_verified_at": "x"},
                              {"platform": "facebook", "status": "healthy", "message": "ok", "last_verified_at": "x"}]}
    monkeypatch.setattr(routes, "get_platform_status", fake_status)
    monkeypatch.setattr(ph, "_notify_partners", lambda *a: None)
    ph.mark_degraded("facebook", "probe")
    ph.set_prefer_cobalt("facebook", True)
    out = asyncio.run(partner.partner_platform_status(tenant=MagicMock()))
    rows = {r["platform"]: r for r in out["platforms"]}
    assert rows["tiktok"]["status"] == "healthy" and rows["tiktok"]["fallback_route"] is None
    assert rows["facebook"]["status"] == "degraded" and rows["facebook"]["fallback_route"] == "cobalt"
    assert "platform.degraded" in out["webhook_events"]


def test_douyin_probe_never_reaches_scraperapi(monkeypatch):
    from app.services import douyin_extractor as de
    from app.services.china_platforms.normalized_models import set_context
    from app.services.china_platforms.probes import PROBE_CONTEXT
    import app.core.scraperapi_pool as sp
    called = []
    monkeypatch.setattr(sp, "get_active_key", lambda: called.append(1) or "k")
    import contextvars
    ctx = contextvars.copy_context()
    ctx.run(set_context, PROBE_CONTEXT)
    assert ctx.run(lambda: asyncio.run(de._try_scraperapi_ssr("https://www.douyin.com/video/1"))) is None
    assert called == []



# ── security review 19/10 (MEDIUM-1 / MEDIUM-2) ──────────────────────────────

def test_traffic_alone_only_alerts(fr, monkeypatch):
    """Anyone can send dead links: without our own probe failing, a
    'broken' verdict must not notify partners, reroute, or remediate."""
    import app.core.download_outcomes as do
    from app.core import alerts, platform_health as ph
    sent, acted = [], []
    monkeypatch.setattr(alerts, "send_admin_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(ph, "on_platform_broken", lambda p, r: acted.append(p))
    monkeypatch.setattr(ph, "mark_degraded", lambda p, r: acted.append(p))
    monkeypatch.setattr(ph, "probe_confirms_failure", lambda p: False)
    _traffic(do, "tiktok", 5, {"extractor_broken": 6})
    _traffic(do, "youtube", 2, {"temporary_blocked": 10})
    ph.check_platforms(now=NOW)
    assert sent and acted == []
    assert any("chưa tự xử lý" in a[2] for a in sent)


def test_no_promotion_when_the_running_version_already_works(chan, monkeypatch):
    yc = chan
    installs = []
    monkeypatch.setattr(yc, "install", lambda v, t: installs.append(v))
    monkeypatch.setattr(yc, "currently_ok_platforms", lambda: ["youtube"])
    monkeypatch.setattr(yc, "probe_with", lambda path, plats: {p: {"ok": True} for p in plats})
    out = yc.try_candidates(["tiktok"], [("2026.10.10.1.dev0", "nightly")], "t")
    assert out["promoted"] is None and "already works" in out["note"] and installs == []


def test_install_rejects_extra_files_in_the_package(tmp_path):
    from app.core import ytdlp_channel as yc
    d = tmp_path / "cand"
    (d / "yt_dlp").mkdir(parents=True)
    (d / "yt_dlp-2026.10.10.dist-info").mkdir()
    assert yc.unexpected_entries(str(d)) == []
    (d / "sitecustomize.py").write_text("print('x')")
    (d / "evil.pth").write_text("import os")
    assert sorted(yc.unexpected_entries(str(d))) == ["evil.pth", "sitecustomize.py"]


def test_install_requires_a_wheel_hash(chan, monkeypatch):
    yc = chan
    monkeypatch.setattr(yc, "wheel_sha256", lambda v: None)
    assert "no wheel" in yc.install("2026.10.10.1.dev0", yc.OVERRIDE_DIR + ".cand")
