"""
Phase 34C (task #6334) — "Đo lường". Holds: the time-to-result histogram is
written only for successes and read back as bucket upper bounds · success
rate per platform/day, None (not 0) with zero attempts or a day without data
· cost per successful download excludes Channel Watch spend and is None with
zero successes · paid Apify runs are charged to the account id, guests to one
"_unattributed" bucket (no IP kept), admin/system to "_system" · ASR spend is
rolled up per day · proxy cost is "not measured" until bytes are recorded ·
spend alerts at 50 / 80 % fire once per day (apify_watch and ASR) · weekly
report text from fixture data, flag default OFF, once per ISO week, manual
send always · every /admin/metrics route requires admin auth.
fakeredis + an in-memory Supabase fake, no network, no paid calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.admin import verify_admin
from app.core import download_outcomes as do
from app.main import app as fastapi_app
from app.services import unit_metrics as um
from app.services.china_platforms import budget_guard, cost_metrics
from tests._china_fakes import clean_env, rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)   # a Monday
D0 = "2026-10-12"
D1 = "2026-10-11"


# ── fakes ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self._limit = db, name, [], None

    def select(self, *_a, **_k):
        return self

    def gte(self, c, v):
        self.f.append(lambda r: r.get(c) is not None and str(r.get(c)) >= v)
        return self

    def eq(self, c, v):
        self.f.append(lambda r: r.get(c) == v)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self.name not in self.db.tables:
            raise Exception("relation does not exist")
        rows = [dict(r) for r in self.db.tables[self.name] if all(fn(r) for fn in self.f)]
        return _Resp(rows[: self._limit] if self._limit else rows)


class FakeDB:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def admin(monkeypatch):
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    monkeypatch.setattr("app.api.admin_metrics.log_admin_action", lambda *a, **k: None)
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.alerts.send_admin_alert",
                        lambda level, title, body, alert_key="": sent.append((level, title, body)))
    return sent


def _rec(plat, ok, n=1, *, at=NOW, ms=None):
    for _ in range(n):
        do.record(plat, ok, None if ok else "x", now=at, duration_ms=ms)


# ── time to result + success ───────────────────────────────────────────────

def test_latency_bucket_edges():
    assert do.latency_bucket(0) == "1000"
    assert do.latency_bucket(1000) == "1000"
    assert do.latency_bucket(1001) == "2000"
    assert do.latency_bucket(10 ** 9) == "inf"
    assert do.latency_bucket("junk") == "inf"


def test_latency_written_only_for_success(rc):
    do.record("tiktok", True, now=NOW, duration_ms=1500)
    do.record("tiktok", False, "x", now=NOW, duration_ms=1500)
    do.record("tiktok", True, now=NOW)          # no duration → counted, no histogram entry
    assert rc.hgetall(f"vidgrab:latency:{D0}") == {"tiktok:2000": "1"}
    assert rc.hgetall(f"vidgrab:stats:{D0}") == {"tiktok:ok": "2", "tiktok:err": "1"}
    assert rc.ttl(f"vidgrab:latency:{D0}") > 30 * 86400


def test_percentile_upper_bounds_and_open_bucket():
    assert um.percentile({}, 0.5) == (None, None)
    hist = {"1000": 5, "5000": 4, "inf": 1}
    assert um.percentile(hist, 0.5) == (1000, "≤ 1 s")
    assert um.percentile(hist, 0.9) == (5000, "≤ 5 s")
    assert um.percentile(hist, 0.95) == (None, "> 300 s")


def test_success_report_math(rc):
    _rec("tiktok", True, 3, ms=900)
    _rec("tiktok", True, 1, ms=25000)
    _rec("tiktok", False, 1)
    _rec("youtube", False, 2)
    _rec("tiktok", True, 1, at=NOW - timedelta(days=1))       # day without histogram
    rep = um.success_report(7, NOW)
    assert rep["dates"][-1] == D0 and len(rep["dates"]) == 7
    tk = next(p for p in rep["platforms"] if p["platform"] == "tiktok")
    assert tk["totals"]["attempts"] == 6 and tk["totals"]["successes"] == 5
    assert tk["totals"]["success_rate"] == round(5 / 6, 4)
    today = tk["per_day"][-1]
    assert today["attempts"] == 5 and today["successes"] == 4 and today["success_rate"] == 0.8
    assert today["p50_ms"] == 1000 and today["p95_ms"] == 30000 and today["latency_samples"] == 4
    yday = tk["per_day"][-2]
    assert yday["available"] is True and yday["latency_samples"] is None   # not measured that day
    yt = next(p for p in rep["platforms"] if p["platform"] == "youtube")
    assert yt["totals"]["success_rate"] == 0.0
    assert yt["per_day"][-1]["latency_samples"] == 0 and yt["per_day"][-1]["p50_ms"] is None
    # a day with no hash at all is "no data", not zero
    assert tk["per_day"][0] == {"date": rep["dates"][0], "available": False}
    assert rep["dates"][0] in rep["days_without_data"]
    assert rep["measurable"]["time_to_result"] is True


def test_success_rate_zero_attempts_is_none(rc):
    assert um.rate(0, 0) is None
    rep = um.success_report(7, NOW)
    assert rep["platforms"] == [] and rep["totals"]["attempts"] == 0
    assert rep["totals"]["success_rate"] is None
    assert rep["measurable"]["time_to_result"] is False
    assert um.clamp_days(12) == 7 and um.clamp_days(30) == 30


# ── cost attribution ───────────────────────────────────────────────────────

def test_member_for_never_keeps_an_ip():
    assert um.member_for("user:abc-123") == "abc-123"
    assert um.member_for("ip:198.51.100.7") == um.UNATTRIBUTED
    assert um.member_for("dev:" + "a" * 32) == um.UNATTRIBUTED
    assert um.member_for("unknown") == um.UNATTRIBUTED
    assert um.member_for(None) == um.UNATTRIBUTED
    assert um.member_for("admin") == um.SYSTEM
    assert um.member_for("0f0e-uuid") == "0f0e-uuid"       # ASR quota key of a signed-in user


def test_reconcile_charges_the_account(rc, clean_env):
    res = budget_guard.reserve("douyin", "user:u-1", budget_guard.PaidCandidate("apify_douyin", "apify", 7000))
    budget_guard.reconcile(res, 0.02, True)
    res2 = budget_guard.reserve("douyin", "ip:198.51.100.7", budget_guard.PaidCandidate("apify_douyin", "apify", 7000))
    budget_guard.reconcile(res2, None, True)                 # unknown actual → estimate stays
    res3 = budget_guard.reserve("douyin", "user:u-1", budget_guard.PaidCandidate("apify_douyin", "apify", 7000))
    budget_guard.reconcile(res3, None, False)                # no run → nothing charged
    day = budget_guard.day_key()
    z = dict(rc.zrange(f"metrics:cost:user:{day}", 0, -1, withscores=True))
    assert z == {"u-1": 20000.0, um.UNATTRIBUTED: 7000.0}
    assert not any("198.51" in k for k in rc.keys("metrics:*"))


def _seed_costs(rc):
    d0, d1 = "20261012", "20261011"
    rc.hset(cost_metrics.key(d0, "douyin"), mapping={"spend_usd_micros": 30000, "managed_calls": 3})
    rc.hset(cost_metrics.key(d1, "douyin"), mapping={"spend_usd_micros": 10000, "managed_calls": 1})
    rc.hset(cost_metrics.key(d0, "instagram"), mapping={"spend_usd_micros": 50000})   # watch scans
    rc.set(um.K_SINCE, d1)
    um.record_user_cost("user:big", 30000, now=NOW)
    um.record_user_cost("user:small", 10000, now=NOW - timedelta(days=1))
    um.record_user_cost("ip:203.0.113.9", 4000, now=NOW)
    um.record_user_cost("admin", 50000, now=NOW)
    um.record_asr_cost("user:small", 0.25, now=NOW)
    _rec("douyin", True, 4)
    _rec("douyin", False, 4)
    _rec("instagram", True, 2)


def test_cost_report_cost_per_success_and_top_users(rc, clean_env):
    _seed_costs(rc)
    rep = um.cost_report(7, NOW)
    dy = next(p for p in rep["platforms"] if p["platform"] == "douyin")
    assert dy["download_cost_usd"] == 0.04 and dy["successes"] == 4
    assert dy["cost_per_success_usd"] == 0.01
    ig = next(p for p in rep["platforms"] if p["platform"] == "instagram")
    assert ig["watch_cost_usd"] == 0.05 and ig["download_cost_usd"] == 0.0
    assert ig["cost_per_success_usd"] == 0.0      # watch spend is not charged to downloads
    assert rep["asr_total_usd"] == 0.25
    assert rep["total_usd"] == round(0.04 + 0.05 + 0.25, 6)
    today = rep["per_day"][-1]
    assert today["platforms"]["douyin"] == {"apify_china": 0.03}
    assert today["platforms"]["instagram"] == {"apify_watch": 0.05}
    assert today["asr_usd"] == 0.25 and today["total_usd"] == 0.33
    assert rep["per_day"][0]["asr_usd"] is None               # before the ledger existed
    assert [u["user_id"] for u in rep["top_users"]] == ["small", "big"]   # most expensive first
    assert rep["top_users"][0]["cost_usd"] == 0.26            # 0.01 Apify + 0.25 ASR
    assert rep["unattributed"] == {"available": True, "guests_usd": 0.004, "system_usd": 0.05}
    assert rep["sources"]["proxy"]["available"] is False
    w = next(c for c in rep["ceilings"] if c["provider"] == "apify_watch")
    assert w["daily_ceiling_usd"] == 1.0 and w["peak_pct_of_ceiling"] == 5.0


def test_cost_per_success_none_without_successes_and_proxy_when_recorded(rc, clean_env):
    rc.hset(cost_metrics.key("20261012", "kuaishou"), mapping={"spend_usd_micros": 9000})
    rc.set(f"vidgrab:youtube:proxy_bytes:{D0}", 2 * 1024 ** 3)
    rep = um.cost_report(7, NOW)
    ks = next(p for p in rep["platforms"] if p["platform"] == "kuaishou")
    assert ks["successes"] == 0 and ks["cost_per_success_usd"] is None
    assert rep["sources"]["proxy"]["available"] is True
    yt = next(p for p in rep["platforms"] if p["platform"] == "youtube")
    assert yt["by_source_usd"]["proxy"] == round(2 * rep["sources"]["proxy"]["price_per_gb_usd"], 6)
    # no per-user ledger yet → "not measured", not an empty top list
    assert rep["top_users"] is None and rep["asr_total_usd"] is None


# ── spend alerts ───────────────────────────────────────────────────────────

def test_apify_watch_spend_alerts_50_80_once_per_day(rc, clean_env, alerts):
    day = budget_guard.day_key()
    k = budget_guard.k_provider_spend(day, "apify_watch")     # default daily ceiling 1.00 USD
    rc.set(k, 550000)
    assert budget_guard.check_spend_alerts("apify_watch") == [("daily", 50)]
    assert budget_guard.check_spend_alerts("apify_watch") == []
    rc.set(k, 850000)
    assert budget_guard.check_spend_alerts("apify_watch") == [("daily", 80)]
    assert budget_guard.check_spend_alerts("apify_watch") == []
    assert len(alerts) == 2 and "apify_watch" in alerts[1][1] and "80%" in alerts[1][1]


def test_asr_spend_alerts_50_80_once_per_day(rc, monkeypatch, alerts):
    from app.services.asr import budget as asr_budget
    monkeypatch.setenv("ASR_SPEND_CEILING_USD", "1.0")
    monkeypatch.setenv("ASR_GLOBAL_DAILY_MINUTES_CAP", "100000")
    day = asr_budget.reserve(0.3, 1)
    assert alerts == []
    asr_budget.reserve(0.3, 1)                    # 60 %
    asr_budget.reserve(0.05, 1)                   # 65 % — same threshold, no new alert
    assert len(alerts) == 1 and "50%" in alerts[0][1]
    asr_budget.reserve(0.3, 1)                    # 95 %
    assert len(alerts) == 2 and "80%" in alerts[1][1]
    assert asr_budget.check_spend_alerts(day) == []


def test_asr_settle_rolls_up_cost(rc, monkeypatch):
    from app.services.asr import budget as asr_budget
    day = asr_budget.reserve(0.1, 1)
    asr_budget.meta_set("job-1", quota_key="acc-9", spend_day=day, est_cost="0.1", minutes="1")
    asr_budget.settle("job-1", 0.08, paid_started=True)
    asr_budget.settle("job-1", 0.08, paid_started=True)       # exactly once
    dc = um._now().strftime("%Y%m%d")
    assert rc.hget(f"metrics:cost:src:{dc}", "asr:_none") == "80000"
    assert rc.zscore(f"metrics:cost:user:{dc}", "acc-9") == 80000.0


# ── weekly report ──────────────────────────────────────────────────────────

SUCCESS_FIX = {
    "dates": ["2026-10-06", "2026-10-12"],
    "totals": {"attempts": 200, "successes": 170, "success_rate": 0.85, "p50_label": "≤ 3 s", "p95_label": "≤ 20 s"},
    "platforms": [
        {"platform": "tiktok", "totals": {"attempts": 150, "successes": 140, "success_rate": 0.9333}},
        {"platform": "douyin", "totals": {"attempts": 50, "successes": 30, "success_rate": 0.6}},
    ],
}
COST_FIX = {
    "total_usd": 1.5,
    "platforms": [
        {"platform": "douyin", "download_cost_usd": 0.6, "watch_cost_usd": None, "cost_per_success_usd": 0.02},
        {"platform": "instagram", "download_cost_usd": 0.0, "watch_cost_usd": 0.4, "cost_per_success_usd": 0.0},
        {"platform": "tiktok", "download_cost_usd": 0.0, "watch_cost_usd": None, "cost_per_success_usd": 0.0},
    ],
    "asr_total_usd": 0.5,
    "top_users": [{"user_id": "11111111-2222-3333", "cost_usd": 0.3}],
    "unattributed": {"available": True, "guests_usd": 0.1, "system_usd": 0.4},
    "ceilings": [{"provider": "apify_watch", "available": True, "daily_ceiling_usd": 3.0,
                  "avg_pct_of_ceiling": 2.0, "peak_pct_of_ceiling": 6.7}],
    "sources": {"apify_china": {"available": True}, "proxy": {"available": False}},
}


def test_weekly_text_from_fixture():
    t = um.build_weekly_text(SUCCESS_FIX, COST_FIX)
    assert "Báo cáo tuần" in t and "2026-10-06 → 2026-10-12" in t
    assert "170/200 (85.0%)" in t and "p50 ≤ 3 s" in t
    assert "tiktok: 93.3% (140/150)" in t and "douyin: 60.0% (30/50)" in t
    assert "douyin: $0.6000, mỗi lượt thành công $0.0200" in t
    assert "instagram: Theo dõi kênh $0.4000" in t
    assert "phiên âm: $0.5000" in t
    assert "11111111…: $0.3000" in t and "khách vãng lai: $0.1000" in t
    assert "apify_watch: 2.0% / 6.7% của $3.00" in t
    assert "Chưa đo được: proxy" in t
    assert "tiktok: $" not in t               # zero-cost platforms are not listed under costs
    assert "@" not in t                       # no emails


def test_weekly_send_flag_default_off_once_per_week(rc, clean_env, monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.notifications.send_telegram_message_sync",
                        lambda msg, parse_mode="HTML": sent.append(msg) or True)
    assert um.weekly_report_enabled() is False
    assert um.send_weekly_report(now=NOW) == {"sent": False, "skipped": "disabled"}
    um.set_weekly_report_enabled(True)
    assert um.WEEKLY_FLAG in rc.smembers(um.CONFIG_INDEX)       # visible on the Config page
    assert um.send_weekly_report(now=NOW)["sent"] is True
    assert um.send_weekly_report(now=NOW + timedelta(hours=1))["skipped"] == "already_sent"
    assert um.send_weekly_report(force=True, now=NOW)["sent"] is True
    assert len(sent) == 2


def test_beat_schedule_monday_0800_vietnam():
    from app.core.celery_app import celery_app
    assert celery_app.conf.timezone == "UTC"
    e = celery_app.conf.beat_schedule["weekly-metrics-report"]
    assert e["task"] == "send_weekly_metrics_report"
    s = e["schedule"]
    assert s.hour == {1} and s.minute == {0} and s.day_of_week == {1}


# ── adoption ───────────────────────────────────────────────────────────────

def _iso(dt):
    return dt.isoformat()


def test_adoption_retention_and_tables(rc):
    # VN dates: NOW = 2026-10-12 17:00 +07
    reg = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)      # 2026-10-01 VN
    db = FakeDB({
        "profiles": [{"id": "a", "created_at": _iso(reg)}, {"id": "b", "created_at": _iso(reg)},
                     {"id": "c", "created_at": _iso(NOW)}],
        "analytics_events": [{"user_id": "a", "created_at": _iso(reg + timedelta(days=1))},
                             {"user_id": None, "created_at": _iso(reg + timedelta(days=1))}],
        "download_jobs": [{"user_id": "b", "created_at": _iso(reg + timedelta(days=7))},
                          {"user_id": "a", "created_at": _iso(reg + timedelta(days=7))}],
        "watch_subscriptions": [{"user_id": "a", "status": "active"}, {"user_id": "a", "status": "active"},
                                {"user_id": "b", "status": "active"}, {"user_id": "b", "status": "removed"}],
        "desktop_devices": [{"client_version": "0.13.1", "first_seen": _iso(NOW), "last_seen": _iso(NOW)},
                            {"client_version": "0.12.0", "first_seen": _iso(NOW - timedelta(days=90)),
                             "last_seen": _iso(NOW - timedelta(days=60))}],
    })
    rep = um.adoption_report(30, NOW, db=db)
    assert rep["registrations"]["total"] == 3
    assert rep["registrations"]["per_day"][-1] == {"date": "2026-10-12", "count": 1}
    assert rep["retention"]["d1"] == {"cohort_users": 2, "returned": 1, "rate": 0.5}
    assert rep["retention"]["d7"] == {"cohort_users": 2, "returned": 2, "rate": 1.0}
    assert rep["watch"]["accounts"] == 2 and rep["watch"]["active_sources"] == 3
    assert rep["watch"]["distribution"] == {"1": 1, "2-5": 1, "6+": 0}
    assert rep["inbox"]["available"] is False                    # table missing → not zero
    assert rep["app_devices"]["active_in_period"] == 1 and rep["app_devices"]["total"] == 2
    assert rep["app_devices"]["versions"] == [{"version": "0.13.1", "machines": 1}]
    assert rep["extension_versions"]["available"] is False


def test_retention_skips_incomplete_cohorts():
    from datetime import date
    today = date(2026, 10, 12)
    cohort = {date(2026, 10, 11): {"x"}, date(2026, 10, 1): {"y"}}
    activity = {date(2026, 10, 2): {"y"}}
    assert um.retention(cohort, activity, today, 1) == {"cohort_users": 1, "returned": 1, "rate": 1.0}
    assert um.retention({}, {}, today, 7) == {"cohort_users": 0, "returned": 0, "rate": None}


# ── admin API ──────────────────────────────────────────────────────────────

ROUTES = [("get", "/api/v1/admin/metrics/success?days=7"), ("get", "/api/v1/admin/metrics/costs?days=30"),
          ("get", "/api/v1/admin/metrics/adoption"), ("get", "/api/v1/admin/metrics/settings"),
          ("post", "/api/v1/admin/metrics/settings"), ("post", "/api/v1/admin/metrics/weekly-report/send")]


def test_auth_required_everywhere(rc):
    for method, path in ROUTES:
        r = getattr(client, method)(path, **({"json": {"weekly_report_enabled": True}} if method == "post" else {}))
        assert r.status_code in (401, 403), (method, path, r.status_code)
    assert rc.get(um.CONFIG_PREFIX + um.WEEKLY_FLAG) is None


def test_api_with_admin(rc, clean_env, admin, monkeypatch):
    _rec("tiktok", True, 2, at=um._now(), ms=500)
    r = client.get("/api/v1/admin/metrics/success?days=7")
    assert r.status_code == 200 and r.json()["platforms"][0]["platform"] == "tiktok"
    assert client.get("/api/v1/admin/metrics/success?days=10").status_code == 422
    assert client.get("/api/v1/admin/metrics/costs?days=30").status_code == 200
    monkeypatch.setattr("app.core.database.get_supabase_client", lambda: FakeDB({"profiles": []}))
    a = client.get("/api/v1/admin/metrics/adoption?days=7").json()
    assert a["registrations"]["available"] is True and a["watch"]["available"] is False
    assert client.get("/api/v1/admin/metrics/settings").json()["weekly_report_enabled"] is False
    assert client.post("/api/v1/admin/metrics/settings", json={"weekly_report_enabled": "yes"}).status_code == 422
    assert client.post("/api/v1/admin/metrics/settings", json={"weekly_report_enabled": True}).json() == \
        {"weekly_report_enabled": True}
    assert um.weekly_report_enabled() is True
    sent = []
    monkeypatch.setattr("app.core.notifications.send_telegram_message_sync",
                        lambda msg, parse_mode="HTML": sent.append(msg) or True)
    w = client.post("/api/v1/admin/metrics/weekly-report/send").json()
    assert w["sent"] is True and "Báo cáo tuần" in w["text"] and len(sent) == 1
