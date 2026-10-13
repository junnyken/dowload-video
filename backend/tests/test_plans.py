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


def test_no_unenforced_features_listed(rc):
    keys = set().union(*(t.keys() for t in plans.build_plans()["tiers"]))
    for banned in ("priority_queue", "cloud_save", "chapters", "concurrent_jobs"):
        assert banned not in keys


# ── settings ───────────────────────────────────────────────────────────────

def test_default_prices(rc):
    s = plans.get_settings()
    assert (s["price_month_vnd"], s["price_year_vnd"]) == (49_000, 399_000)


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
