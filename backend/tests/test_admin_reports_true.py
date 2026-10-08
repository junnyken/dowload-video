"""Task #6148 — the admin Overview says what is true (08/10/2026).

  [1] Proxy fallback panel: vidgrab:fallback:* are hashes; reading them with
      GET raised WRONGTYPE and the panel showed one card named "ERROR".
  [2] /cookies/status tells which platforms need no pool cookie (Cobalt-first
      / cookie-last), so an empty pool is not "downloads will fail".
  [3] A failure alert fired on the absolute line is not worded as a spike
      ("62.5% vs 99.4% (0.6x spike)" for Douyin).
"""
import fakeredis
import pytest

from app.api import admin
from app.core import anomaly_detector as ad


# ── [1] fallback summary ────────────────────────────────────────────────────

def test_fallback_summary_reads_hashes_and_reports_totals():
    rc = fakeredis.FakeRedis(decode_responses=True)
    rc.hset("vidgrab:fallback:instagram:cobalt", mapping={"success_count": 9, "fail_count": 1})
    rc.hset("vidgrab:fallback:instagram:ytdlp", mapping={"success_count": 2, "fail_count": 5})
    rc.set("vidgrab:fallback:junk:layer", "not-a-hash")        # must not blank the panel
    out = admin.fallback_summary(rc, {"instagram": {"ok": 11, "err": 0},
                                      "youtube": {"ok": 8, "err": 1},
                                      "douyin": {"ok": 0, "err": 0}})
    assert set(out) == {"instagram", "youtube", "douyin"}       # no "error" pseudo-platform
    assert out["instagram"] == {"success_rate": 100.0, "total": 11, "top_layer": "cobalt"}
    assert out["youtube"] == {"success_rate": 88.9, "total": 9, "top_layer": "primary"}
    assert out["douyin"]["success_rate"] is None and out["douyin"]["total"] == 0


def test_fallback_summary_with_bytes_keys():
    rc = fakeredis.FakeRedis()                                   # bytes in, bytes out
    rc.hset("vidgrab:fallback:twitter:cobalt", mapping={"success_count": 3})
    out = admin.fallback_summary(rc, {"twitter": {"ok": 3, "err": 0}})
    assert out["twitter"]["top_layer"] == "cobalt"


# ── [2] no-cookie routes ────────────────────────────────────────────────────

def test_no_cookie_routes_follow_the_flags(monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "facebook,instagram,x")
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "instagram")
    monkeypatch.setattr("app.services.cobalt_service.cobalt_platform_tripped", lambda p: False)
    routes = admin.no_cookie_routes()
    assert routes["twitter"] == ["cobalt"]                       # "x" alias counts
    assert routes["instagram"] == ["cobalt", "anonymous"]
    assert routes["facebook"] == ["cobalt"]


def test_no_cookie_routes_empty_when_flags_off_or_tripped(monkeypatch):
    monkeypatch.delenv("COOKIE_LAST_PLATFORMS", raising=False)
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "twitter")
    monkeypatch.setattr("app.services.cobalt_service.cobalt_platform_tripped", lambda p: True)
    assert admin.no_cookie_routes() == {}                        # Cobalt tripped → the warning is real


# ── [3] failure alert wording ───────────────────────────────────────────────

def _alert(monkeypatch, today, week):
    monkeypatch.setattr(ad, "_get_today_platform_stats", lambda: {"douyin": today})
    monkeypatch.setattr(ad, "_get_rolling_platform_stats", lambda: {"douyin": week})
    monkeypatch.setattr(ad, "_store_anomaly", lambda a: a)
    return ad.check_failure_spike("douyin")


def test_absolute_line_on_a_platform_failing_for_days_is_not_a_spike(monkeypatch):
    a = _alert(monkeypatch, {"ok": 3, "err": 5}, {"ok": 1, "err": 160})
    assert a is not None
    assert "x spike)" not in a["likely_cause"]
    assert "62.5%" in a["likely_cause"] and "failing for days" in a["likely_cause"]


def test_absolute_line_on_a_healthy_week(monkeypatch):
    a = _alert(monkeypatch, {"ok": 4, "err": 6}, {"ok": 70, "err": 30})
    assert "x spike)" not in a["likely_cause"] and "above the 50% alert line" in a["likely_cause"]
    assert "failing for days" not in a["likely_cause"]


def test_a_real_rise_is_still_a_spike(monkeypatch):
    a = _alert(monkeypatch, {"ok": 4, "err": 6}, {"ok": 90, "err": 10})
    assert "(6.0x spike)" in a["likely_cause"]


def test_no_alert_below_both_lines(monkeypatch):
    assert _alert(monkeypatch, {"ok": 9, "err": 1}, {"ok": 90, "err": 10}) is None
