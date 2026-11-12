"""Plans (owner 13/10): one table built from enforced limits, manual Pro
with expiry, admin settings, transfer code."""
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import plans
from app.core.entitlements import resolve_effective_tier


@pytest.fixture()
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plans, "_r", lambda: r)
    return r


def _iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ── manual Pro expiry ───────────────────────────────────────────────────────

def test_manual_pro_active_until_expiry():
    assert resolve_effective_tier({"tier": "pro", "billing_status": "manual", "subscription_expiry": _iso(3)}) == "pro"


def test_manual_pro_expired_is_free():
    assert resolve_effective_tier({"tier": "pro", "billing_status": "manual", "subscription_expiry": _iso(-1)}) == "free"


def test_manual_without_expiry_is_free():
    assert resolve_effective_tier({"tier": "pro", "billing_status": "manual"}) == "free"


def test_stripe_active_unchanged():
    assert resolve_effective_tier({"tier": "pro", "billing_status": "active"}) == "pro"


# ── plan table comes from the enforced numbers ─────────────────────────────

def test_table_uses_enforced_limits(rc, monkeypatch):
    from app.core import quotas as q
    monkeypatch.setattr(q, "platform_limit_anon", lambda: 7)
    monkeypatch.setattr(q, "platform_limit_user", lambda: 33)
    monkeypatch.setattr(q, "platform_limit_for_tier", lambda t: 444)
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert (t["guest"]["daily_downloads"], t["free"]["daily_downloads"], t["pro"]["daily_downloads"]) == (7, 33, 444)
    assert t["pro"]["zip"] is True and t["free"]["zip"] is False
    assert t["pro"]["batch_links"] == q.TIER_PERMISSIONS["pro"]["batch_limit"]


def test_free_4k_when_owner_sets_free_max_height(rc, monkeypatch):
    from app.core import quotas as q
    monkeypatch.setitem(q.TIER_PERMISSIONS["free"], "max_height", 4320)
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert t["free"]["max_height"] == 4320
    assert t["pro"]["max_height"] >= t["free"]["max_height"]   # Pro never shown below Free


def test_watch_not_advertised_when_closed(rc, monkeypatch):
    monkeypatch.setattr(plans, "_watch", lambda: {"available": False})
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert t["free"]["watch_sources"] == 0 and t["pro"]["watch_sources"] == 0
    assert t["pro"]["watch_instagram"] is False


def test_watch_plans_match_enforced_limits_all_tiers(rc, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core import watch_config as wc
    from app.main import app as fastapi_app

    monkeypatch.setenv("WATCH_ENABLED", "true")
    monkeypatch.setenv("WATCH_ADMIN_ONLY", "false")
    monkeypatch.setenv("WATCH_KILL_SWITCH", "false")
    monkeypatch.delenv("WATCH_FREE_MAX_SOURCES", raising=False)
    monkeypatch.delenv("WATCH_STARTER_MAX_SOURCES", raising=False)
    monkeypatch.delenv("WATCH_PRO_MAX_SOURCES", raising=False)
    monkeypatch.delenv("WATCH_TEAM_MAX_SOURCES", raising=False)
    monkeypatch.delenv("WATCH_ENTERPRISE_MAX_SOURCES", raising=False)
    monkeypatch.delenv("WATCH_FREE_MIN_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("WATCH_PRO_MIN_INTERVAL_SEC", raising=False)

    assert wc.max_sources("starter") == 2
    assert wc.max_sources("pro") == 30
    assert wc.max_sources("team") == 90
    assert wc.max_sources("enterprise") == 90

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    data = client.get("/api/v1/plans").json()
    by_id = {row["id"]: row for row in data["tiers"]}

    for tier_id, row in by_id.items():
        assert row["watch_sources"] == wc.max_sources(tier_id), f"{tier_id} watch_sources mismatch"
        assert row["watch_interval_h"] == wc.min_interval_sec(tier_id) / 3600, f"{tier_id} watch_interval_h mismatch"

    # Also verify custom env overrides propagate identically to both watch_config and /api/v1/plans
    monkeypatch.setenv("WATCH_TEAM_MAX_SOURCES", "120")
    monkeypatch.setenv("WATCH_ENTERPRISE_MAX_SOURCES", "150")
    monkeypatch.setenv("WATCH_PRO_MIN_INTERVAL_SEC", "7200")
    assert wc.max_sources("team") == 120
    assert wc.max_sources("enterprise") == 150
    data2 = client.get("/api/v1/plans").json()
    by_id2 = {row["id"]: row for row in data2["tiers"]}
    assert by_id2["starter"]["watch_interval_h"] == 2
    assert by_id2["team"]["watch_sources"] == 120


def test_no_unenforced_features_listed(rc):
    keys = set().union(*(t.keys() for t in plans.build_plans()["tiers"]))
    for banned in ("priority_queue", "cloud_save", "chapters", "concurrent_jobs"):
        assert banned not in keys


# ── settings ───────────────────────────────────────────────────────────────

def test_default_prices(rc):
    s = plans.get_settings()
    assert (s["price_month_vnd"], s["price_year_vnd"]) == (99_000, 890_000)
    assert (s["starter_month_vnd"], s["starter_year_vnd"]) == (49_000, 590_000)
    assert (s["team_month_vnd"], s["team_year_vnd"]) == (279_000, 2_790_000)


def test_set_settings_and_payment_ready(rc):
    plans.set_settings({"price_month_vnd": 59000, "bank_name": " VCB ", "bank_account": "0123", "bank_holder": "NGUYEN VAN A"})
    d = plans.build_plans()
    pro = [t for t in d["tiers"] if t["id"] == "pro"][0]
    assert pro["price_month_vnd"] == 59000 and d["payment"]["ready"] is True
    assert d["payment"]["bank_name"] == "VCB"


@pytest.mark.parametrize("bad", [{"price_month_vnd": 0}, {"price_year_vnd": "abc"}, {"contact": "x" * 500}])
def test_bad_settings_rejected(rc, bad):
    with pytest.raises(ValueError):
        plans.set_settings(bad)


def test_payment_not_ready_without_bank(rc):
    assert plans.build_plans()["payment"]["ready"] is False


# ── transfer code ──────────────────────────────────────────────────────────

def test_transfer_code_roundtrip():
    uid = "1a2b3c4d-1111-2222-3333-444455556666"
    code = plans.transfer_code(uid)
    assert code == "VG1A2B3C4D"
    assert plans.code_prefix(code) == "1a2b3c4d"
    assert plans.code_prefix(" vg1a2b3c4d ") == "1a2b3c4d"


@pytest.mark.parametrize("bad", ["", "VG123", "VGZZZZZZZZ", "1a2b3c4d5"])
def test_bad_codes(bad):
    assert plans.code_prefix(bad) is None


# ── admin grant for N days ─────────────────────────────────────────────────

class _Q:
    def __init__(self, row):
        self.row = row
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def single(self): return self
    def execute(self):
        class R: pass
        r = R(); r.data = self.row
        return r


class _SB:
    def __init__(self, row): self.row = row
    def table(self, name): return _Q(self.row)


def test_grant_days_from_today():
    from app.api.admin import _manual_pro_fields
    f = _manual_pro_fields(_SB({"billing_status": "none"}), "u", 30)
    exp = datetime.fromisoformat(f["subscription_expiry"])
    assert f["billing_status"] == "manual"
    assert abs((exp - datetime.now(timezone.utc)) - timedelta(days=30)) < timedelta(minutes=1)


def test_grant_extends_running_manual():
    from app.api.admin import _manual_pro_fields
    cur = datetime.now(timezone.utc) + timedelta(days=10)
    f = _manual_pro_fields(_SB({"billing_status": "manual", "subscription_expiry": cur.isoformat()}), "u", 30)
    exp = datetime.fromisoformat(f["subscription_expiry"])
    assert abs(exp - (cur + timedelta(days=30))) < timedelta(seconds=5)


def test_grant_ignores_expired_manual():
    from app.api.admin import _manual_pro_fields
    old = datetime.now(timezone.utc) - timedelta(days=5)
    f = _manual_pro_fields(_SB({"billing_status": "manual", "subscription_expiry": old.isoformat()}), "u", 30)
    exp = datetime.fromisoformat(f["subscription_expiry"])
    assert exp > datetime.now(timezone.utc) + timedelta(days=29)


def test_expire_task_includes_manual():
    import inspect
    from app.tasks import video_tasks
    src = inspect.getsource(video_tasks.expire_subscriptions)
    assert '"manual"' in src


def test_paid_never_below_free(monkeypatch):
    from app.core import quotas as q
    monkeypatch.setitem(q.TIER_PERMISSIONS["free"], "batch_limit", 500)
    monkeypatch.setitem(q.TIER_PERMISSIONS["free"], "max_height", 4320)
    monkeypatch.setitem(q.TIER_PERMISSIONS["pro"], "batch_limit", 100)
    monkeypatch.setitem(q.TIER_PERMISSIONS["pro"], "max_height", 2160)
    monkeypatch.setitem(q.TIER_PERMISSIONS["team"], "batch_limit", 200)
    q._paid_never_below_free()
    assert q.TIER_PERMISSIONS["pro"]["batch_limit"] == 500
    assert q.TIER_PERMISSIONS["pro"]["max_height"] == 4320
    assert q.TIER_PERMISSIONS["team"]["batch_limit"] == 500
    assert q.TIER_PERMISSIONS["enterprise"]["batch_limit"] == -1
    assert q.TIER_PERMISSIONS["enterprise"]["max_height"] == 4320


def test_smart_analysis_signed_in_never_below_guest(rc, monkeypatch):
    from app.api import smart_analysis as sa
    monkeypatch.setenv("AI_ANALYSIS_ANON_DAILY_LIMIT", "1000")
    assert sa.free_limit() == 1000 and sa.pro_limit() >= 1000
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert t["guest"]["smart_analysis_per_day"] <= t["free"]["smart_analysis_per_day"] <= t["pro"]["smart_analysis_per_day"]


# ── admin /api/v1/admin/plans/settings full-field & strict Redis readback ───

def test_settings_body_covers_all_plan_settings_keys(rc):
    from app.api.plans import SettingsBody
    assert set(SettingsBody.model_fields.keys()) == set(plans.get_settings().keys())


def test_admin_plans_settings_saves_all_tiers_and_rejects_unknown_fields(rc):
    from fastapi.testclient import TestClient
    from app.api.admin import verify_admin
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        full_payload = {
            "starter_month_usd_cents": 249,
            "starter_year_usd_cents": 2599,
            "starter_month_vnd": 55_000,
            "starter_year_vnd": 550_000,
            "price_month_usd_cents": 499,
            "price_year_usd_cents": 4500,
            "price_month_vnd": 99_000,
            "price_year_vnd": 890_000,
            "team_month_usd_cents": 1299,
            "team_year_usd_cents": 12900,
            "team_month_vnd": 299_000,
            "team_year_vnd": 2_990_000,
            "api_calls_month_pro": 3_500,
            "api_calls_month_team": 20_000,
            "bank_name": "ACB",
            "bank_account": "0766223536",
            "bank_holder": "NGUYEN THIEN TRIEU",
            "contact": "support@dowloadvideo.io.vn",
            "rapidapi_proxy_secret": "rp-secret",
            "rapidapi_url": "https://rapidapi.com/hub",
            "paypal_me_url": "https://paypal.me/vidgrab",
            "bank_bin": "970416",
            "sepay_webhook_secret": "whsec_test_123",
        }
        res = client.post("/api/v1/admin/plans/settings", json=full_payload)
        assert res.status_code == 200, res.text
        saved = res.json()
        for k, v in full_payload.items():
            assert saved[k] == v, f"Field {k} mismatch: {saved.get(k)} != {v}"

        # Verify values are truly read from Redis hash `plans:settings` and visible on GET /api/v1/plans
        raw_redis = rc.hgetall(plans.SETTINGS_KEY)
        if isinstance(raw_redis, list):
            raw_redis = dict(zip(raw_redis[0::2], raw_redis[1::2]))
        assert raw_redis["price_month_vnd"] == "99000"
        assert raw_redis["price_year_vnd"] == "890000"
        assert raw_redis["price_month_usd_cents"] == "499"
        assert raw_redis["starter_month_vnd"] == "55000"
        assert raw_redis["team_month_vnd"] == "299000"

        pub = client.get("/api/v1/plans").json()
        tiers = {t["id"]: t for t in pub["tiers"]}
        assert tiers["starter"]["price_month_vnd"] == 55_000
        assert tiers["starter"]["price_month_usd"] == 2.49
        assert tiers["pro"]["price_month_vnd"] == 99_000
        assert tiers["pro"]["price_year_vnd"] == 890_000
        assert tiers["pro"]["price_month_usd"] == 4.99
        assert tiers["pro"]["price_year_usd"] == 45.0
        assert tiers["team"]["price_month_vnd"] == 299_000
        assert tiers["team"]["price_year_usd"] == 129.0

        # Unknown field -> HTTP 422 clearly naming the unknown field (never silently ignored)
        bad_res = client.post(
            "/api/v1/admin/plans/settings",
            json={"price_month_vnd": 99_000, "typo_price_field": 123_000},
        )
        assert bad_res.status_code == 422
        assert "typo_price_field" in bad_res.text
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_admin_plans_settings_returns_503_when_redis_unwritable(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.admin import verify_admin
    from app.main import app as fastapi_app

    class _DeadRedis:
        def hgetall(self, *_a, **_kw):
            raise ConnectionError("redis down")

        def hset(self, *_a, **_kw):
            raise ConnectionError("redis down")

    monkeypatch.setattr(plans, "_r", lambda: _DeadRedis())
    client = TestClient(fastapi_app, raise_server_exceptions=False)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        res = client.post("/api/v1/admin/plans/settings", json={"price_month_vnd": 99_000})
        assert res.status_code == 503
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)
