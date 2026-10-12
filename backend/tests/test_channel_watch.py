"""
Phase 33A Channel Watch core (task #6257).

Everything is in memory: FakeDB (unique keys of migration 038 enforced),
fakeredis, a recording provider instead of TikWM, a recording send_push.
No network, no paid provider.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fakeredis
import pytest

from app.core import watch_config as cfg
from app.services import channel_watch as cw
from tests._watch_fakes import FakeDB, UniqueViolation

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
CH = "https://www.tiktok.com/@somechannel"
CH2 = "https://www.tiktok.com/@otherchannel"
CH3 = "https://www.tiktok.com/@third.one"


def vids(*ids, user="somechannel"):
    return [{"url": f"https://www.tiktok.com/@{user}/video/{i}", "title": f"video {i}", "id": str(i)} for i in ids]


class World:
    def __init__(self, monkeypatch, *, enforce_unique=True):
        self.db = FakeDB(enforce_unique=enforce_unique)
        self.rc = fakeredis.FakeRedis(decode_responses=True)
        self.clock = [T0]
        self.listings: dict[str, list] = {}         # canonical_url -> entries (newest first)
        self.provider_calls: list[tuple[str, int]] = []
        self.provider_error: dict[str, cw.ListingError] = {}
        self.pushes: list[tuple[str, dict]] = []
        self.push_result = 1
        self.on_list = None

        monkeypatch.setattr(cw, "_sb", lambda: self.db)
        monkeypatch.setattr(cw, "_rc", lambda: self.rc)
        monkeypatch.setattr(cw, "utcnow", lambda: self.clock[0])
        monkeypatch.setitem(cw.PROVIDERS, "tiktok", ("tikwm", self._provider))
        monkeypatch.setattr("app.core.push_sender.send_push", self._send)

    def _provider(self, source, limit):
        self.provider_calls.append((source["canonical_url"], limit))
        if self.on_list:
            self.on_list(source)
        err = self.provider_error.get(source["canonical_url"])
        if err:
            raise err
        return [dict(e) for e in self.listings.get(source["canonical_url"], [])[:limit]]

    def _send(self, user_id, payload):
        self.pushes.append((user_id, payload))
        return self.push_result

    def advance(self, **kw):
        self.clock[0] = self.clock[0] + timedelta(**kw)

    def rows(self, t):
        return self.db.rows(t)

    def source(self, url=CH):
        handle = url.rsplit("@", 1)[1].lower()
        return next(s for s in self.rows("watch_sources") if s["external_channel_id"] == handle)

    def add(self, user="u1", url=CH, tier="free", admin=False, verified=True, ip="1.1.1.1", mode="one_tap"):
        return cw.add_source(user_id=user, tier=tier, is_admin=admin, email_verified=verified,
                             url=url, mode=mode, ip=ip)

    def scan(self, url=CH):
        return cw.scan_source(self.source(url)["id"])

    def make_due(self, url=CH):
        self.source(url)["next_scan_at"] = self.clock[0].isoformat()


@pytest.fixture
def env(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("WATCH_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WATCH_ENABLED", "true")
    monkeypatch.setenv("WATCH_PLATFORMS_ENABLED", "tiktok")
    monkeypatch.setenv("WATCH_ADMIN_ONLY", "false")
    return monkeypatch


@pytest.fixture
def w(env, monkeypatch):
    return World(monkeypatch)


# ── API harness ──────────────────────────────────────────────────────

@pytest.fixture
def api(w, monkeypatch):
    import app.main as main_mod
    from app.core.auth_middleware import get_optional_user

    who = {"user": None, "tier": "free", "admin": False}
    main_mod.app.dependency_overrides[get_optional_user] = lambda: who["user"]
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda uid: who["tier"])
    monkeypatch.setattr("app.core.quotas.is_admin_request", lambda req: who["admin"])
    yield who
    main_mod.app.dependency_overrides.pop(get_optional_user, None)


def login(who, uid="u1", tier="free", verified=True, admin=False):
    who["user"] = {"id": uid, "email": f"{uid}@example.com", "token": "t",
                   "email_confirmed_at": "2026-01-01T00:00:00Z" if verified else None}
    who["tier"], who["admin"] = tier, admin


def post(app, url=CH, ip="1.1.1.1", mode=None):
    body = {"url": url}
    if mode:
        body["mode"] = mode
    return app.post("/api/v1/watch/sources", json=body, headers={"X-Forwarded-For": ip})


# ── 1. Flags ─────────────────────────────────────────────────────────

USER_ROUTES = [
    ("get", "/api/v1/watch/status", None),
    ("post", "/api/v1/watch/sources", {"url": CH}),
    ("get", "/api/v1/watch/subscriptions", None),
    ("patch", "/api/v1/watch/subscriptions/x", {"status": "paused"}),
    ("delete", "/api/v1/watch/subscriptions/x", None),
    ("get", "/api/v1/watch/items", None),
]


@pytest.mark.parametrize("method,path,body", USER_ROUTES)
def test_flag_off_every_endpoint_is_disabled(app, w, api, env, method, path, body):
    env.delenv("WATCH_ENABLED")
    login(api)
    kw = {"json": body} if body else {}
    r = getattr(app, method)(path, **kw)
    assert r.status_code == 404 and r.json()["error_code"] == "watch_disabled"
    assert w.db.calls == []


def test_flag_off_beat_and_tasks_are_noops(w, env, monkeypatch):
    from app.tasks import watch_tasks
    env.delenv("WATCH_ENABLED")
    monkeypatch.setattr(cw, "tick", lambda **k: pytest.fail("tick ran with the flag off"))
    assert watch_tasks.watch_scan_tick()["skipped"] == "disabled"
    assert cw.scan_source("any") == {"outcome": "disabled"}
    assert cw.deliver_pending() == {"skipped": "disabled"}
    assert w.db.calls == [] and w.provider_calls == []


def test_defaults_are_off():
    import os
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("WATCH_")}
    try:
        assert cfg.enabled() is False and cfg.kill_switch() is False
        assert cfg.admin_only() is True and cfg.require_account() is True
        assert cfg.platforms_enabled() == frozenset()
        assert cfg.scan_batch_per_minute() == 20 and cfg.items_per_scan() == 10
        assert cfg.gap_followup_max_pages() == 2 and cfg.max_interval_sec() == 259200
        assert (cfg.max_sources("free"), cfg.max_sources("pro"), cfg.max_sources("team")) == (1, 30, 30)
        assert (cfg.min_interval_sec("free"), cfg.min_interval_sec("pro")) == (86400, 21600)
        assert (cfg.replace_cooldown_sec("free"), cfg.replace_cooldown_sec("pro")) == (86400, 3600)
        assert cfg.ip_daily_new_sources() == 3 and cfg.global_free_cap() == 200
    finally:
        os.environ.update(saved)


def test_kill_switch_stops_scans_deliveries_and_new_sources(app, w, api, env):
    login(api)
    w.listings[CH] = vids(1, 2)
    assert post(app).status_code == 201
    calls = len(w.provider_calls)
    env.setenv("WATCH_KILL_SWITCH", "true")
    w.make_due()
    from app.tasks import watch_tasks
    assert watch_tasks.watch_scan_tick()["skipped"] == "disabled"
    assert w.scan()["outcome"] == "disabled"
    assert cw.deliver_pending()["skipped"] == "disabled"
    assert len(w.provider_calls) == calls
    r = post(app, CH2)
    assert r.status_code == 503 and r.json()["error_code"] == "watch_disabled"
    # listing still works
    assert app.get("/api/v1/watch/subscriptions").status_code == 200


def test_admin_only_flag(app, w, api, env):
    env.setenv("WATCH_ADMIN_ONLY", "true")
    w.listings[CH] = vids(1)
    login(api)
    assert app.get("/api/v1/watch/status").json()["enabled"] is False
    r = post(app)
    assert r.status_code == 403 and r.json()["error_code"] == "watch_disabled"
    assert w.rows("watch_subscriptions") == []
    login(api, admin=True)
    assert app.get("/api/v1/watch/status").json()["enabled"] is True
    assert post(app).status_code == 201


# ── 2. Guests ────────────────────────────────────────────────────────

def test_guest_is_401_and_nothing_is_stored(app, w, api):
    w.listings[CH] = vids(1, 2)
    r = post(app)
    assert r.status_code == 401 and r.json()["error_code"] == "login_required"
    for path in ("/api/v1/watch/status", "/api/v1/watch/subscriptions", "/api/v1/watch/items"):
        assert app.get(path).json()["error_code"] == "login_required"
    assert w.db.calls == [] and w.provider_calls == [] and w.rc.keys("*") == []


def test_guest_negative_control_same_request_signed_in_stores(app, w, api):
    """The guest test is not vacuous: the identical request with a session
    creates a source, a subscription and calls the provider."""
    w.listings[CH] = vids(1, 2)
    login(api)
    assert post(app).status_code == 201
    assert len(w.rows("watch_subscriptions")) == 1 and w.provider_calls


# ── 3. Limits ────────────────────────────────────────────────────────

def test_free_limit_is_one(app, w, api):
    login(api)
    w.listings[CH], w.listings[CH2] = vids(1), vids(2, user="otherchannel")
    assert post(app).status_code == 201
    r = post(app, CH2)
    assert r.status_code == 403 and r.json()["error_code"] == "source_limit_reached"
    assert len(w.rows("watch_subscriptions")) == 1 and len(w.rows("watch_sources")) == 1
    # same channel again is idempotent, not a second slot
    r = post(app)
    assert r.status_code == 200 and r.json()["already_subscribed"] is True


def test_pro_limit_and_interval(app, w, api, env):
    env.setenv("WATCH_PRO_MAX_SOURCES", "2")
    login(api, tier="pro")
    for u in (CH, CH2):
        w.listings[u] = vids(1, user=u.rsplit("@", 1)[1])
    assert post(app, CH).status_code == 201
    assert post(app, CH2).status_code == 201
    assert post(app, CH3).json()["error_code"] == "source_limit_reached"
    assert {s["delivery_interval_sec"] for s in w.rows("watch_subscriptions")} == {21600}


def test_replace_cooldown_free_24h(app, w, api):
    login(api)
    w.listings[CH], w.listings[CH2] = vids(1), vids(2, user="otherchannel")
    sub_id = post(app).json()["subscription"]["id"]
    assert app.delete(f"/api/v1/watch/subscriptions/{sub_id}").status_code == 200
    w.advance(hours=23)
    r = post(app, CH2)
    assert r.status_code == 429 and r.json()["error_code"] == "replace_cooldown_active"
    assert "giờ" in r.json()["message"]
    assert not any(s["external_channel_id"] == "otherchannel" for s in w.rows("watch_sources"))
    w.advance(hours=1, seconds=1)
    assert post(app, CH2).status_code == 201


def test_replace_cooldown_pro_1h(app, w, api):
    login(api, tier="pro")
    w.listings[CH], w.listings[CH2] = vids(1), vids(2, user="otherchannel")
    sub_id = post(app).json()["subscription"]["id"]
    app.delete(f"/api/v1/watch/subscriptions/{sub_id}")
    w.advance(minutes=30)
    assert post(app, CH2).json()["error_code"] == "replace_cooldown_active"
    w.advance(minutes=31)
    assert post(app, CH2).status_code == 201


def test_ip_daily_cap_across_accounts(app, w, api):
    urls = [f"https://www.tiktok.com/@c{i}" for i in range(5)]
    for i, u in enumerate(urls):
        w.listings[u] = vids(100 + i, user=f"c{i}")
    for i in range(3):
        login(api, uid=f"user{i}")
        assert post(app, urls[i], ip="9.9.9.9").status_code == 201
    login(api, uid="user3")
    r = post(app, urls[3], ip="9.9.9.9")
    assert r.status_code == 429 and r.json()["error_code"] == "ip_daily_cap_reached"
    assert not any(s.get("user_id") == "user3" for s in w.rows("watch_subscriptions"))
    assert post(app, urls[3], ip="8.8.8.8").status_code == 201
    # stored hash, never the IP itself
    assert all("9.9.9.9" not in str(s) for s in w.rows("watch_subscriptions"))


def test_global_free_cap(app, w, api, env):
    env.setenv("WATCH_GLOBAL_FREE_SOURCES_CAP", "2")
    urls = [f"https://www.tiktok.com/@g{i}" for i in range(4)]
    for i, u in enumerate(urls):
        w.listings[u] = vids(200 + i, user=f"g{i}")
    for i in range(2):
        login(api, uid=f"f{i}")
        assert post(app, urls[i], ip=f"2.2.2.{i}").status_code == 201
    login(api, uid="f2")
    r = post(app, urls[2], ip="2.2.2.9")
    assert r.status_code == 403 and r.json()["error_code"] == "global_free_cap_reached"
    login(api, uid="p1", tier="pro")
    assert post(app, urls[3], ip="2.2.2.10").status_code == 201


def test_email_not_verified_free_only(app, w, api):
    w.listings[CH] = vids(1)
    login(api, verified=False)
    r = post(app)
    assert r.status_code == 403 and r.json()["error_code"] == "email_not_verified"
    assert w.rows("watch_subscriptions") == []
    login(api, uid="pro-unverified", tier="pro", verified=False)
    assert post(app).status_code == 201


def test_email_unknown_for_api_key_user_is_not_blocked(app, w, api, monkeypatch):
    """API-key callers carry no email_confirmed_at; the admin lookup failing
    must not block (documented gap)."""
    w.listings[CH] = vids(1)
    api["user"] = {"id": "k1", "email": "", "via_api_key": True}
    monkeypatch.setattr("app.core.database.get_service_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no admin api")))
    assert post(app).status_code == 201


# ── 4. URL validation ────────────────────────────────────────────────

@pytest.mark.parametrize("url,code", [
    ("https://www.youtube.com/@x", "platform_not_enabled"),      # implemented, flag off here
    ("https://youtu.be/dQw4w9WgXcQ", "unsupported_platform"),      # a video, not a channel
    ("https://www.douyin.com/user/abc", "unsupported_platform"),
    ("https://example.com/@x", "unsupported_url"),
    ("ftp://www.tiktok.com/@x", "unsupported_url"),
    ("https://www.tiktok.com/foryou", "unsupported_url"),
    ("https://user:pw@www.tiktok.com/@x", "unsupported_url"),
])
def test_url_errors_store_nothing(app, w, api, url, code):
    login(api)
    r = post(app, url)
    assert r.status_code == 400 and r.json()["error_code"] == code, r.text
    assert w.rows("watch_sources") == [] and w.provider_calls == []


def test_platform_not_enabled(app, w, api, env):
    env.setenv("WATCH_PLATFORMS_ENABLED", "")
    login(api)
    r = post(app)
    assert r.json()["error_code"] == "platform_not_enabled" and "TikTok" in r.json()["message"]


def test_resolve_channel_url_variants(w):
    ref = cw.resolve_channel_url("https://www.tiktok.com/@Some.Channel?lang=vi")
    assert (ref.platform, ref.external_channel_id, ref.canonical_url) == (
        "tiktok", "some.channel", "https://www.tiktok.com/@some.channel")
    assert cw.resolve_channel_url("tiktok.com/@abc/video/7300000000000000001").external_channel_id == "abc"
    short = cw.resolve_channel_url("https://vm.tiktok.com/ZS123/",
                                   resolve_short=lambda u: "https://www.tiktok.com/@shorty/video/7300000000000000002")
    assert short.external_channel_id == "shorty"
    with pytest.raises(cw.WatchError) as e:
        cw.resolve_channel_url("https://vm.tiktok.com/ZS123/", resolve_short=lambda u: "https://evil.example/@x")
    assert e.value.code == "unsupported_url"


def test_private_channel_is_rejected_and_nothing_kept(app, w, api):
    login(api)
    w.provider_error[CH] = cw.ListingError("not_found_or_private", permanent=True)
    r = post(app)
    assert r.status_code == 422 and r.json()["error_code"] == "private_or_login_required"
    for t in ("watch_sources", "watch_subscriptions", "watch_items_seen", "watch_scan_runs"):
        assert w.rows(t) == [], t
    # the IP slot is given back
    w.provider_error.clear()
    w.listings[CH] = vids(1)
    for i in range(3):
        login(api, uid=f"x{i}")
        assert post(app).status_code == 201


def test_transient_failure_keeps_subscription_baseline_pending(app, w, api):
    login(api)
    w.provider_error[CH] = cw.ListingError("network", permanent=False)
    r = post(app)
    assert r.status_code == 201 and r.json()["baseline"] == "pending"
    sub = w.rows("watch_subscriptions")[0]
    assert sub["baseline_completed_at"] is None
    # next scan does the baseline (no deliveries)
    w.provider_error.clear()
    w.listings[CH] = vids(1, 2, 3)
    w.advance(hours=25)
    assert w.scan()["outcome"] == "baseline"
    assert w.rows("watch_deliveries") == [] and sub["baseline_completed_at"]


# ── 5. Baseline / ledger / fan-out ───────────────────────────────────

def test_baseline_creates_no_deliveries_then_new_items_do(app, w, api):
    login(api)
    w.listings[CH] = vids(5, 4, 3, 2, 1)
    r = post(app)
    assert r.status_code == 201 and r.json()["baseline"] == "done"
    assert {i["state"] for i in w.rows("watch_items_seen")} == {"baseline"}
    assert len(w.rows("watch_items_seen")) == 5
    assert w.rows("watch_deliveries") == []
    assert app.get("/api/v1/watch/items").json()["items"] == []

    w.listings[CH] = vids(7, 6, 5, 4, 3, 2, 1)
    w.advance(days=1, hours=4)
    res = w.scan()
    assert res["outcome"] == "ok" and res["new_items"] == 2 and res["deliveries"] == 2
    items = app.get("/api/v1/watch/items").json()["items"]
    assert sorted(i["url"].rsplit("/", 1)[1] for i in items) == ["6", "7"]


def test_baseline_negative_control(w):
    """Same listing, but the source pretends its baseline already ran: the
    very same scan now creates deliveries — so the 0 above comes from the
    baseline rule, not from a scan that cannot create deliveries."""
    src = w.db.add("watch_sources", {"platform": "tiktok", "external_channel_id": "somechannel",
                                     "canonical_url": CH, "status": "active",
                                     "last_scan_at": (T0 - timedelta(days=1)).isoformat(),
                                     "next_scan_at": T0.isoformat()}, ignore=False)
    w.db.add("watch_subscriptions", {"user_id": "u1", "source_id": src["id"], "status": "active",
                                     "mode": "one_tap", "delivery_interval_sec": 86400,
                                     "baseline_completed_at": (T0 - timedelta(days=1)).isoformat()},
             ignore=False)
    w.listings[CH] = vids(5, 4, 3, 2, 1)
    assert w.scan()["deliveries"] == 5


def test_ledger_same_item_twice_is_one_row(w, monkeypatch):
    w.listings[CH] = vids(1, 2)
    w.add()
    w.listings[CH] = vids(3, 1, 2)
    w.advance(days=2)
    w.scan()
    # overlapping scans that both read "unknown" before either writes
    monkeypatch.setattr(cw, "_known_ids", lambda *a, **k: set())
    w.advance(days=2)
    r1 = w.scan()
    w.advance(days=2)
    r2 = w.scan()
    assert r1["new_items"] == 0 and r2["new_items"] == 0
    ids = [i["external_item_id"] for i in w.rows("watch_items_seen")]
    assert sorted(ids) == ["1", "2", "3"]
    assert len(w.rows("watch_deliveries")) == 1


def test_ledger_negative_control_without_unique_key(monkeypatch, env):
    """Without the (source_id, external_item_id) unique key the same race
    double-inserts — the guarantee rests on the migration's UNIQUE."""
    w = World(monkeypatch, enforce_unique=False)
    w.listings[CH] = vids(1, 2)
    w.add()
    monkeypatch.setattr(cw, "_known_ids", lambda *a, **k: set())
    w.advance(days=2)
    w.scan()
    assert len(w.rows("watch_items_seen")) > 2


def test_unique_keys_are_in_the_migration():
    sql = (ROOT / "database/migrations/038_channel_watch.sql").read_text()
    for key in ("UNIQUE (platform, external_channel_id)", "UNIQUE (user_id, source_id)",
                "UNIQUE (source_id, external_item_id)", "UNIQUE (subscription_id, item_id)"):
        assert key in sql
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 5
    assert "CREATE POLICY" not in sql
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert "DROP " not in body and "ALTER TABLE watch_" in body
    assert "CREATE TABLE IF NOT EXISTS" in body and not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", body)
    assert not re.search(r"CREATE INDEX (?!IF NOT EXISTS)", body)


def test_same_delivery_notified_once(w):
    w.listings[CH] = vids(1)
    w.add()
    w.listings[CH] = vids(2, 1)
    w.advance(days=2)
    w.scan()
    assert cw.deliver_pending()["pushes"] == 1
    w.advance(days=3)
    assert cw.deliver_pending()["notified"] == 0
    assert len(w.pushes) == 1
    # a second delivery run that saw the row as pending cannot claim it again
    d = w.rows("watch_deliveries")[0]
    assert d["status"] == "notified"


def test_fan_out_to_eligible_subscribers_only(w):
    w.listings[CH] = vids(1)
    w.add(user="active", tier="pro")
    w.add(user="paused", tier="pro", ip="1.1.1.2")
    w.add(user="removed", tier="pro", ip="1.1.1.3")
    subs = {s["user_id"]: s for s in w.rows("watch_subscriptions")}
    cw.update_subscription(w.db, "paused", subs["paused"]["id"], status="paused")
    cw.remove_subscription(w.db, "removed", subs["removed"]["id"])
    # joined but baseline still pending (e.g. scan lock was busy)
    late = w.db.add("watch_subscriptions", {"user_id": "late", "source_id": w.source()["id"],
                                            "status": "active", "mode": "one_tap",
                                            "delivery_interval_sec": 21600,
                                            "baseline_completed_at": None}, ignore=False)
    w.listings[CH] = vids(2, 1)
    w.advance(days=1)
    res = w.scan()
    assert res["new_items"] == 1 and res["deliveries"] == 1
    d = w.rows("watch_deliveries")
    assert [x["subscription_id"] for x in d] == [subs["active"]["id"]]
    assert late["baseline_completed_at"] == w.clock[0].isoformat()


def test_gap_followup_capped(w):
    w.listings[CH] = vids(*range(1000, 1010))
    w.add()
    w.provider_calls.clear()
    w.listings[CH] = vids(*range(2000, 2100))         # 100 brand-new items
    w.advance(days=2)
    res = w.scan()
    assert [c[1] for c in w.provider_calls] == [10, 20, 30]     # K + 2 follow-up pages
    assert res["possible_gap"] is True and res["new_items"] == 30
    run = w.rows("watch_scan_runs")[-1]
    assert run["possible_gap"] is True and run["items_requested"] == 60


def test_gap_followup_stops_at_overlap(w):
    w.listings[CH] = vids(*range(1000, 1010))
    w.add()
    w.provider_calls.clear()
    w.listings[CH] = vids(*range(2000, 2012)) + vids(*range(1000, 1010))
    w.advance(days=2)
    res = w.scan()
    assert [c[1] for c in w.provider_calls] == [10, 20]
    assert res["possible_gap"] is False and res["new_items"] == 12


# ── 6. Intervals ─────────────────────────────────────────────────────

def test_interval_calc(env):
    assert cw.source_interval_sec("tiktok", [86400]) == 86400
    assert cw.source_interval_sec("tiktok", [86400, 21600]) == 21600
    assert cw.source_interval_sec("tiktok", [60]) == 21600            # platform floor
    assert cw.source_interval_sec("tiktok", [10 ** 7]) == 259200      # max
    env.setenv("WATCH_PLATFORM_FLOOR_SEC_TIKTOK", "3600")
    assert cw.source_interval_sec("tiktok", [60]) == 3600
    assert cw.jittered_delay(86400, rnd=lambda: 0.0) == int(86400 * 0.88)
    assert cw.jittered_delay(86400, rnd=lambda: 1.0) == int(86400 * 1.12)
    mid = lambda: 0.5
    # permanent failure: one interval, then doubling
    assert [cw.failure_delay(21600, n, rnd=mid, permanent=True) for n in (1, 2, 3, 4, 5, 12)] == [
        21600, 43200, 86400, 172800, 259200, 259200]
    assert cw.failure_delay(86400, 3, rnd=lambda: 1.0, permanent=True) == 259200  # jitter never passes max
    # temporary failure (12/10): 10 min, doubling, never past the old schedule
    assert [cw.failure_delay(86400, n, rnd=mid) for n in (1, 2, 3, 4, 8, 9, 12)] == [
        600, 1200, 2400, 4800, 76800, 153600, 259200]
    assert cw.failure_delay(300, 1, rnd=mid) == 300
    env.setenv("WATCH_RETRY_BASE_SEC", "120")
    assert cw.failure_delay(86400, 1, rnd=mid) == 120


def _next_delay(w):
    s = w.source()
    return (datetime.fromisoformat(s["next_scan_at"]) - w.clock[0]).total_seconds()


def test_source_next_scan_free_vs_pro(w):
    w.listings[CH] = vids(1)
    w.add(user="free1")
    assert 86400 * 0.88 <= _next_delay(w) <= 86400 * 1.12
    assert w.source()["scan_interval_sec"] == 86400
    w.add(user="pro1", tier="pro", ip="1.1.1.9")
    assert w.source()["scan_interval_sec"] == 21600
    assert 21600 * 0.88 <= _next_delay(w) <= 21600 * 1.12


def test_failures_back_off_and_degrade(w, env):
    env.setenv("WATCH_DEGRADE_AFTER_FAILURES", "3")
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    w.provider_error[CH] = cw.ListingError("network", permanent=False)
    delays = []
    for _ in range(4):
        w.advance(days=4)
        assert w.scan()["outcome"] == "failed"
        delays.append(_next_delay(w))
    # temporary failure: 10 min, then doubling (12/10 — was one interval)
    assert delays[0] <= 600 * 1.12 and delays[1] >= 1200 * 0.88 and delays[1] > delays[0]
    assert max(delays) <= 259200
    s = w.source()
    assert s["status"] == "degraded" and s["consecutive_failures"] == 4
    assert s["last_failure_category"] == "network"
    assert [r["outcome"] for r in w.rows("watch_scan_runs")][-4:] == ["failed"] * 4
    # recovery
    w.provider_error.clear()
    w.advance(days=4)
    assert w.scan()["outcome"] == "ok"
    assert w.source()["status"] == "active" and w.source()["consecutive_failures"] == 0


# ── 7. Lock ──────────────────────────────────────────────────────────

def test_source_lock_serializes_overlapping_scans(w):
    w.listings[CH] = vids(1)
    w.add()
    w.listings[CH] = vids(2, 1)
    w.advance(days=2)
    nested = []
    w.on_list = lambda src: nested.append(cw.scan_source(src["id"])) if not nested else None
    res = w.scan()
    assert nested == [{"outcome": "locked"}]
    assert res["outcome"] == "ok" and res["new_items"] == 1
    assert w.rc.get(f"watch:scanlock:{w.source()['id']}") is None     # released
    w.on_list = None
    w.rc.set(f"watch:scanlock:{w.source()['id']}", "other", ex=300)
    calls = len(w.provider_calls)
    assert w.scan() == {"outcome": "locked"} and len(w.provider_calls) == calls


# ── 8. Delivery ──────────────────────────────────────────────────────

def test_free_delivery_interval_respected_when_pro_scans_faster(w):
    w.listings[CH] = vids(1)
    w.add(user="free")
    w.add(user="pro", tier="pro", ip="1.1.1.2")
    assert w.source()["scan_interval_sec"] == 21600

    w.listings[CH] = vids(2, 1)
    w.advance(hours=7)
    w.scan()
    cw.deliver_pending()
    assert sorted(u for u, _ in w.pushes) == ["free", "pro"]

    w.listings[CH] = vids(3, 2, 1)
    w.advance(hours=7)
    w.scan()
    cw.deliver_pending()
    assert [u for u, _ in w.pushes].count("pro") == 2
    assert [u for u, _ in w.pushes].count("free") == 1          # throttled, still pending
    assert any(d["status"] == "pending" for d in w.rows("watch_deliveries"))

    w.listings[CH] = vids(4, 3, 2, 1)
    w.advance(hours=7)
    w.scan()
    cw.deliver_pending()
    assert [u for u, _ in w.pushes].count("free") == 1          # 14 h after the first push

    w.advance(hours=11)                                          # 25 h after the first push
    cw.deliver_pending()
    free_pushes = [p for u, p in w.pushes if u == "free"]
    assert len(free_pushes) == 2
    assert free_pushes[1]["body"].startswith("2 video mới từ @somechannel")   # digest of 3 and 4
    assert all(d["status"] == "notified" for d in w.rows("watch_deliveries"))


def test_one_push_per_subscription_digest_and_deep_links(w):
    w.listings[CH] = vids(1)
    w.add(user="tap", mode="one_tap", tier="pro")
    w.add(user="note", mode="notify_only", tier="pro", ip="1.1.1.2")
    w.listings[CH] = vids(4, 3, 2, 1)
    w.advance(days=1)
    w.scan()
    stats = cw.deliver_pending()
    assert stats["pushes"] == 2 and stats["notified"] == 6
    by = {u: p for u, p in w.pushes}
    assert by["tap"]["body"] == "3 video mới từ @somechannel" and by["tap"]["url"] == "/watch"
    assert by["note"]["url"] == "/watch"
    # single new item + one_tap → deep link pre-fills the item URL
    w.listings[CH] = vids(5, 4, 3, 2, 1)
    w.advance(days=1)
    w.scan()
    cw.deliver_pending()
    tap = [p for u, p in w.pushes if u == "tap"][-1]
    assert tap["url"] == "/share-target?url=https%3A%2F%2Ftiktok.com%2F%40somechannel%2Fvideo%2F5"
    assert [p for u, p in w.pushes if u == "note"][-1]["url"] == "/watch"


def test_no_push_target_is_terminal(w):
    w.push_result = 0
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    w.listings[CH] = vids(2, 1)
    w.advance(days=1)
    w.scan()
    cw.deliver_pending()
    assert w.rows("watch_deliveries")[0]["status"] == "no_push_target"
    w.advance(days=1)
    cw.deliver_pending()
    assert len(w.pushes) == 1


def test_removed_subscription_pending_deliveries_skipped(w):
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    w.listings[CH] = vids(2, 1)
    w.advance(days=1)
    w.scan()
    sub = w.rows("watch_subscriptions")[0]
    cw.remove_subscription(w.db, "u1", sub["id"])
    cw.deliver_pending()
    assert w.pushes == [] and w.rows("watch_deliveries")[0]["status"] == "skipped"
    assert w.source()["status"] == "idle"


# ── 9. Beat tick ─────────────────────────────────────────────────────

def test_tick_selects_due_sources_with_subscribers_in_batches(w, env):
    env.setenv("WATCH_SCAN_BATCH_PER_MINUTE", "2")
    urls = [f"https://www.tiktok.com/@t{i}" for i in range(4)]
    for i, u in enumerate(urls):
        w.listings[u] = vids(300 + i, user=f"t{i}")
        w.add(user=f"pro{i}", tier="pro", url=u, ip=f"3.3.3.{i}")
    # idle source: last subscriber removed
    sub3 = next(s for s in w.rows("watch_subscriptions") if s["user_id"] == "pro3")
    cw.remove_subscription(w.db, "pro3", sub3["id"])
    w.advance(days=4)
    enq, dels = [], []
    r = cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: dels.append(1))
    assert r["enqueued"] == 2 and len(enq) == 2
    r2 = cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: dels.append(1))
    assert len(enq) == 3                                # the 3rd due one; no repeats
    assert w.source(urls[3])["id"] not in enq
    assert len(set(enq)) == 3 and dels == []


def test_tick_parks_sources_whose_watchers_all_paused(w):
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    sub = w.rows("watch_subscriptions")[0]
    cw.update_subscription(w.db, "u1", sub["id"], status="paused")
    w.advance(days=4)
    enq = []
    cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: None)
    assert enq == [] and w.source()["status"] == "idle"
    cw.update_subscription(w.db, "u1", sub["id"], status="active", now=w.clock[0])
    assert w.source()["status"] == "active"
    cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: None)
    assert enq == [w.source()["id"]]


def test_tick_skips_platform_disabled(w, env):
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    w.advance(days=4)
    env.setenv("WATCH_PLATFORMS_ENABLED", "youtube")
    enq = []
    cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: None)
    assert enq == []
    assert w.scan()["outcome"] == "platform_not_enabled"


def test_scan_task_chains_delivery(w, monkeypatch):
    from app.tasks import watch_tasks
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    w.listings[CH] = vids(2, 1)
    w.advance(days=1)
    sent = []
    monkeypatch.setattr(watch_tasks.deliver_watch_deliveries, "delay", lambda **kw: sent.append(kw))
    res = watch_tasks.scan_watch_source(w.source()["id"])
    assert res["deliveries"] == 1 and sent == [{"source_id": w.source()["id"]}]


# ── 10. Celery wiring ────────────────────────────────────────────────

def _queues_in(path: Path) -> list[set[str]]:
    text = path.read_text()
    return [set(m.group(1).split(",")) for m in re.finditer(r"worker[^\n]*?\\?\s*\n?\s*-Q\s+([\w,]+)", text)]


@pytest.mark.parametrize("name", ["watch_scan_tick", "scan_watch_source", "deliver_watch_deliveries"])
def test_watch_tasks_registered_and_consumed_in_production(name):
    from app.core.celery_app import celery_app
    celery_app.loader.import_default_modules()
    assert name in celery_app.tasks
    queue = celery_app.amqp.router.route({}, name)["queue"].name
    prod = _queues_in(ROOT / "backend/docker-entrypoint.sh")
    assert prod and queue in prod[0], f"{name} → {queue} not in production -Q {prod}"
    # and every compose file with workers has one consuming it
    for f in ("docker-compose.yml", "docker-compose.small.yml", "docker-compose.satellite.yml"):
        qs = re.findall(r"-Q\s+([\w,]+)", (ROOT / f).read_text())
        assert any(queue in q.split(",") for q in qs), f"{f}: no worker consumes {queue}"


def test_beat_runs_tick_every_minute():
    from app.core.celery_app import celery_app
    beat = [v for v in celery_app.conf.beat_schedule.values() if v["task"] == "watch_scan_tick"]
    assert beat and beat[0]["schedule"] == 60.0


# ── 11. Item identity ────────────────────────────────────────────────

def test_item_identity_tiktok_id_and_url_and_fallback():
    assert cw.item_identity("tiktok", {"id": "7300000000000000001", "url": "x"})[0] == "7300000000000000001"
    ext, url = cw.item_identity("tiktok", {"url": "https://www.tiktok.com/@a/video/7300000000000000002?is_from=x"})
    assert ext == "7300000000000000002" and url == "https://tiktok.com/@a/video/7300000000000000002"
    ext2, _ = cw.item_identity("tiktok", {"url": "https://www.tiktok.com/@a/photo/abc?x=1"})
    ext3, _ = cw.item_identity("tiktok", {"url": "https://www.tiktok.com/@a/photo/abc/"})
    assert ext2.startswith("url:") and ext2 == ext3                 # canonical-URL hash
    assert ext2 != cw.item_identity("tiktok", {"url": "https://www.tiktok.com/@a/photo/abd"})[0]


def test_normalize_drops_duplicates_and_idless():
    items = cw.normalize_entries("tiktok", vids(1, 1, 2) + [{"title": "no url"}, "junk"])
    assert [i.ext for i in items] == ["1", "2"]


# ── 12. API surface ──────────────────────────────────────────────────

def test_status_limits_used(app, w, api):
    login(api)
    w.listings[CH] = vids(1)
    st = app.get("/api/v1/watch/status").json()
    assert st["enabled"] is True and st["used"] == 0 and st["platforms"] == ["tiktok"]
    assert st["limits"] == {"max_sources": 1, "min_interval_sec": 86400, "replace_cooldown_sec": 86400}
    post(app)
    assert app.get("/api/v1/watch/status").json()["used"] == 1


def test_patch_pause_resume_mode_and_ownership(app, w, api):
    login(api)
    w.listings[CH] = vids(1)
    sid = post(app).json()["subscription"]["id"]
    r = app.patch(f"/api/v1/watch/subscriptions/{sid}", json={"status": "paused", "mode": "notify_only"})
    assert r.status_code == 200 and r.json()["subscription"]["status"] == "paused"
    assert r.json()["subscription"]["mode"] == "notify_only"
    assert app.patch(f"/api/v1/watch/subscriptions/{sid}", json={"mode": "auto_download"}).json()["error_code"] == "invalid_mode"
    assert app.patch(f"/api/v1/watch/subscriptions/{sid}", json={"status": "removed"}).json()["error_code"] == "invalid_status"
    login(api, uid="intruder")
    assert app.patch(f"/api/v1/watch/subscriptions/{sid}", json={"status": "active"}).status_code == 404
    assert app.delete(f"/api/v1/watch/subscriptions/{sid}").status_code == 404
    login(api)
    subs = app.get("/api/v1/watch/subscriptions").json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["source"]["display_name"] == "@somechannel"
    assert app.delete(f"/api/v1/watch/subscriptions/{sid}").status_code == 200
    assert app.get("/api/v1/watch/subscriptions").json()["subscriptions"] == []
    assert w.source()["status"] == "idle"


def test_resubscribe_reuses_row(w):
    w.listings[CH] = vids(1)
    w.add(tier="pro")
    sub = w.rows("watch_subscriptions")[0]
    cw.remove_subscription(w.db, "u1", sub["id"])
    w.advance(hours=2)
    w.listings[CH] = vids(2, 1)
    w.add(tier="pro")
    assert len(w.rows("watch_subscriptions")) == 1 and sub["status"] == "active"
    assert w.source()["status"] == "active"
    assert w.rows("watch_deliveries") == []      # item 2 appeared while idle: baseline for the rejoin


def test_no_secrets_or_signed_urls_stored(w):
    w.listings[CH] = [{"url": "https://www.tiktok.com/@somechannel/video/7300000000000000009?sig=SECRET&x-expires=1",
                       "title": "t", "id": "7300000000000000009"}]
    w.add()
    stored = str(w.db.tables)
    assert "SECRET" not in stored and "x-expires" not in stored


# ── 13. Admin ────────────────────────────────────────────────────────

def test_admin_overview_requires_admin(app, w):
    assert app.get("/api/v1/admin/watch/overview").status_code == 401
    assert app.post("/api/v1/admin/watch/sources/x/pause").status_code == 401


def test_admin_overview_and_pause_resume_audited(app, w, monkeypatch):
    import app.main as main_mod
    # The exact object the routes were declared with (another test module may
    # have re-imported app.api.admin since).
    from app.api.watch import verify_admin
    main_mod.app.dependency_overrides[verify_admin] = lambda: None
    logged = []
    monkeypatch.setattr("app.core.audit.log_admin_action", lambda req, action, **kw: logged.append((action, kw)))
    try:
        w.listings[CH] = vids(1)
        w.add()
        ov = app.get("/api/v1/admin/watch/overview").json()
        assert ov["sources_by_platform_status"] == {"tiktok:active": 1}
        assert ov["free_sources"] == {"used": 1, "cap": 200}
        assert ov["subscriptions_by_status"] == {"active": 1}
        assert len(ov["last_scan_runs"]) == 1 and ov["flags"]["enabled"] is True
        sid = w.source()["id"]
        assert app.post(f"/api/v1/admin/watch/sources/{sid}/pause").json()["status"] == "paused_admin"
        assert w.scan()["outcome"] == "inactive"
        assert app.post(f"/api/v1/admin/watch/sources/{sid}/resume").json()["status"] == "active"
        assert [a for a, _ in logged] == ["admin.watch.source.pause", "admin.watch.source.resume"]
        assert app.post("/api/v1/admin/watch/sources/nope/pause").status_code == 404
    finally:
        main_mod.app.dependency_overrides.pop(verify_admin, None)


# ── 14. TikWM listing (free provider), mocked HTTP ───────────────────

class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _fake_httpx(monkeypatch, pages):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append(params)
            p = pages[len(calls) - 1]
            if isinstance(p, Exception):
                raise p
            return _FakeResp(p)

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    return calls


def test_tikwm_listing_ids_published_and_errors(monkeypatch):
    from app.services import downloader as d
    _fake_httpx(monkeypatch, [{"code": 0, "data": {"videos": [
        {"video_id": "7300000000000000001", "title": "a", "create_time": 1760000000, "play_count": 5}],
        "hasMore": False}}])
    res = d.scrape_tiktok_user_posts(CH, 10, raise_on_error=True)
    e = res["entries"][0]
    assert e["id"] == "7300000000000000001" and e["published_at"].startswith("2025-10-09")

    _fake_httpx(monkeypatch, [{"code": -1, "msg": "user not exist"}])
    with pytest.raises(d.TikWMListingError) as ex:
        d.scrape_tiktok_user_posts(CH, 10, raise_on_error=True)
    assert ex.value.permanent is True
    _fake_httpx(monkeypatch, [RuntimeError("down")])
    with pytest.raises(d.TikWMListingError) as ex:
        d.scrape_tiktok_user_posts(CH, 10, raise_on_error=True)
    assert ex.value.permanent is False
    # default: old behaviour, no raise
    _fake_httpx(monkeypatch, [{"code": -1, "msg": "x"}])
    assert d.scrape_tiktok_user_posts(CH, 10)["entries"] == []


def _EMBED_DOWN(url):
    return 503, "overload-protect triggered"


def test_provider_maps_tikwm_errors(monkeypatch):
    from app.services import downloader as d

    def boom(*a, **k):
        raise d.TikWMListingError("private", permanent=True)
    monkeypatch.setattr(d, "scrape_tiktok_user_posts", boom)
    with pytest.raises(cw.ListingError) as e:
        cw._list_tiktok({"canonical_url": CH}, 10, embed_fetch=_EMBED_DOWN)
    assert e.value.category == "not_found_or_private" and e.value.permanent
    monkeypatch.setattr(d, "scrape_tiktok_user_posts", lambda *a, **k: {"entries": []})
    with pytest.raises(cw.ListingError) as e:
        cw._list_tiktok({"canonical_url": CH}, 10, embed_fetch=_EMBED_DOWN)
    assert e.value.category == "empty"


# ── 12/10/2026: TikWM user/posts behind Cloudflare → embed page first ──

def _embed_html(videos, *, uid="somechannel", is_error=False):
    import json as _j
    state = {"source": {"data": {f"/embed/@{uid}": {
        "userInfo": {"uniqueId": uid}, "videoList": videos, "isError": is_error}}}}
    return ('<html><script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
            + _j.dumps(state) + "</script></html>")


def _ev(vid, height=1280, desc="v", author="somechannel"):
    return {"id": str(vid), "desc": desc, "height": height, "playAddr": "", "authorUniqueId": author}


PINNED_OLD = 7100000000000000000      # older id → older post
NEW_A, NEW_B = 7695610326234041630, 7695608097624083742


def test_embed_lists_newest_first_skips_photos_and_reads_post_time():
    html = _embed_html([_ev(PINNED_OLD), _ev(NEW_B), _ev(NEW_A), _ev(7695600000000000000, height=0)])
    seen = []
    out = cw._tiktok_embed_entries(CH, fetch=lambda u: seen.append(u) or (200, html))
    assert seen == ["https://www.tiktok.com/embed/@somechannel"]
    assert [e["id"] for e in out] == [str(NEW_A), str(NEW_B), str(PINNED_OLD)]   # photo dropped
    assert out[0]["url"] == f"https://www.tiktok.com/@somechannel/video/{NEW_A}"
    assert out[0]["published_at"].startswith("2026-")


@pytest.mark.parametrize("status,text,cat", [
    (503, "overload-protect triggered", "embed_overload"),
    (403, "", "embed_http_403"),
    (200, "<html>no state</html>", "embed_unreadable"),
    (200, None, "embed_unreadable"),
])
def test_embed_failures_are_temporary(status, text, cat):
    html = _embed_html([], is_error=True) if text is None else text
    with pytest.raises(cw.ListingError) as e:
        cw._tiktok_embed_entries(CH, fetch=lambda u: (status, html))
    assert e.value.category == cat and e.value.permanent is False


def test_embed_exception_is_temporary():
    def boom(u):
        raise ConnectionResetError("tls")
    with pytest.raises(cw.ListingError) as e:
        cw._tiktok_embed_entries(CH, fetch=boom)
    assert e.value.permanent is False


def test_provider_uses_embed_then_falls_back_to_tikwm(monkeypatch):
    from app.services import downloader as d
    calls = []
    monkeypatch.setattr(d, "scrape_tiktok_user_posts",
                        lambda *a, **k: calls.append(1) or {"entries": vids(9)})
    out = cw._list_tiktok({"canonical_url": CH}, 10, embed_fetch=lambda u: (200, _embed_html([_ev(NEW_A)])))
    assert [e["id"] for e in out] == [str(NEW_A)] and calls == []
    out = cw._list_tiktok({"canonical_url": CH}, 10, embed_fetch=lambda u: (503, "overload"))
    assert [e["id"] for e in out] == ["9"] and calls == [1]

    def tikwm_down(*a, **k):
        raise d.TikWMListingError("request failed", permanent=False)
    monkeypatch.setattr(d, "scrape_tiktok_user_posts", tikwm_down)
    with pytest.raises(cw.ListingError) as e:
        cw._list_tiktok({"canonical_url": CH}, 10, embed_fetch=lambda u: (503, "overload"))
    assert e.value.category == "embed_overload" and e.value.permanent is False


def test_first_scan_temporary_failure_retries_in_minutes_not_a_day(w):
    w.provider_error[CH] = cw.ListingError("embed_overload", permanent=False)
    res = w.add()
    assert res["baseline"] == "pending"
    assert 0 < _next_delay(w) <= 600 * 1.12 + 1
    assert w.source()["last_failure_category"] == "embed_overload"
    w.provider_error.clear()
    w.listings[CH] = vids(3, 2, 1)
    w.advance(seconds=700)
    calls = []
    cw.tick(enqueue_scan=lambda sid: calls.append(sid) or cw.scan_source(sid), enqueue_delivery=lambda: None)
    assert calls == [w.source()["id"]]
    sub = w.rows("watch_subscriptions")[0]
    assert sub["baseline_completed_at"] and w.source()["consecutive_failures"] == 0


def test_tick_pulls_forward_sources_stuck_on_the_old_24h_rule(w):
    w.listings[CH] = vids(1)
    w.add()
    s = w.source()
    s.update(consecutive_failures=1, last_failure_category="network", scan_interval_sec=86400,
             next_scan_at=(w.clock[0] + timedelta(hours=23)).isoformat())
    calls = []
    cw.tick(enqueue_scan=calls.append, enqueue_delivery=lambda: None)
    assert calls == [s["id"]]
    assert cw._parse(w.source()["next_scan_at"]) == w.clock[0]


def test_pull_forward_leaves_new_schedule_and_permanent_failures_alone(w):
    w.listings[CH] = vids(1)
    w.add()
    s = w.source()
    later = (w.clock[0] + timedelta(seconds=600)).isoformat()
    s.update(consecutive_failures=1, last_failure_category="embed_overload", scan_interval_sec=86400,
             next_scan_at=later)
    assert cw.pull_forward_stale_retries(w.db, w.clock[0]) == 0
    s.update(last_failure_category="not_found_or_private",
             next_scan_at=(w.clock[0] + timedelta(hours=23)).isoformat())
    assert cw.pull_forward_stale_retries(w.db, w.clock[0]) == 0


class _R:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def _new_video_ready(w, user="u1", tier="pro"):
    w.listings[CH] = vids(1)
    w.add(user=user, tier=tier)
    w.listings[CH] = vids(2, 1)
    w.advance(days=2)
    w.scan()


def test_webhook_event_for_paid_user_with_active_webhook(w, monkeypatch):
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda uid: "pro")
    w.db.add("user_webhooks", {"user_id": "u1", "webhook_url": "https://hook.example/x",
                               "secret_hash": "h", "is_active": True}, ignore=False)
    _new_video_ready(w)
    sent = []
    stats = cw.deliver_pending(hook=lambda uid, p: sent.append((uid, p)))
    assert stats["webhooks"] == 1 and stats["pushes"] == 1
    uid, p = sent[0]
    assert uid == "u1" and p["event"] == "watch.new_items"
    assert p["channel"]["platform"] == "tiktok" and p["channel"]["url"] == CH
    assert [i["id"] for i in p["items"]] == ["2"]
    assert p["items"][0]["url"].endswith("/video/2") and p["items"][0]["title"] == "video 2"
    assert w.rows("watch_deliveries")[0]["status"] == "notified"


@pytest.mark.parametrize("tier,active", [("free", True), ("pro", False)])
def test_no_webhook_when_downgraded_or_inactive(w, monkeypatch, tier, active):
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda uid: tier)
    w.db.add("user_webhooks", {"user_id": "u1", "webhook_url": "https://hook.example/x",
                               "secret_hash": "h", "is_active": active}, ignore=False)
    _new_video_ready(w)
    sent = []
    assert cw.deliver_pending(hook=lambda uid, p: sent.append(uid))["webhooks"] == 0 and sent == []


def test_webhook_only_user_counts_as_notified_and_broker_failure_never_blocks(w, monkeypatch):
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda uid: "pro")
    w.db.add("user_webhooks", {"user_id": "u1", "webhook_url": "https://hook.example/x",
                               "secret_hash": "h", "is_active": True}, ignore=False)
    _new_video_ready(w)
    w.push_result = 0                                   # no browser/app push target
    assert cw.deliver_pending(hook=lambda uid, p: None)["webhooks"] == 1
    assert w.rows("watch_deliveries")[0]["status"] == "notified"


def test_webhook_broker_down_falls_back_to_push_status(w, monkeypatch):
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda uid: "pro")
    w.db.add("user_webhooks", {"user_id": "u1", "webhook_url": "https://hook.example/x",
                               "secret_hash": "h", "is_active": True}, ignore=False)
    _new_video_ready(w)

    def down(uid, p):
        raise ConnectionError("broker")
    stats = cw.deliver_pending(hook=down)
    assert stats["webhooks"] == 0 and stats["pushes"] == 1 and len(w.pushes) == 1


def test_webhook_task_sends_event_header(monkeypatch):
    from app.tasks import webhook_tasks as wt
    seen = {}

    class _Res:
        status_code, success, error = 200, True, None

    def fake_post(url, body, headers):
        seen.update(url=url, body=body, headers=headers)
        return _Res()
    db = FakeDB()
    db.add("user_webhooks", {"user_id": "u1", "webhook_url": "https://hook.example/x",
                             "secret_hash": "k", "is_active": True}, ignore=False)
    monkeypatch.setattr("app.core.database.get_service_client", lambda: db)
    monkeypatch.setattr("app.core.webhook_guard.post_webhook", fake_post)
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fakeredis.FakeRedis(decode_responses=True))
    payload = {"event": "watch.new_items", "items": []}
    wt.deliver_webhook_task.run("u1", payload)
    import hashlib as _h, hmac as _hm, json as _j
    assert seen["headers"]["X-VidGrab-Event"] == "watch.new_items"
    want = _hm.new(b"k", _j.dumps(payload, ensure_ascii=False).encode(), _h.sha256).hexdigest()
    assert seen["headers"]["X-VidGrab-Signature"] == f"sha256={want}"


# ── 33D: YouTube channels (free, all accounts) ──────────────────────

YT_ID = "UCBJycsmduvYEL83R_U4JriQ"


def _yt_page(cid=YT_ID, title="Marques Brownlee"):
    return (f'<html><link rel="canonical" href="https://www.youtube.com/channel/{cid}">'
            f'<meta property="og:title" content="{title}"></html>')


@pytest.mark.parametrize("url,page", [
    ("https://www.youtube.com/@mkbhd", "https://www.youtube.com/@mkbhd"),
    ("https://youtube.com/@mkbhd/videos", "https://www.youtube.com/@mkbhd"),
    ("https://m.youtube.com/channel/" + YT_ID + "/featured", "https://www.youtube.com/channel/" + YT_ID),
    ("https://www.youtube.com/c/mkbhd", "https://www.youtube.com/c/mkbhd"),
    ("www.youtube.com/user/marquesbrownlee", "https://www.youtube.com/user/marquesbrownlee"),
])
def test_youtube_links_resolve_to_one_channel_id(env, url, page):
    env.setenv("WATCH_PLATFORMS_ENABLED", "tiktok,youtube")
    seen = []
    ref = cw.resolve_channel_url(url, fetch_page=lambda u: seen.append(u) or _yt_page())
    assert seen == [page]
    assert (ref.platform, ref.external_channel_id) == ("youtube", YT_ID)
    assert ref.canonical_url == f"https://www.youtube.com/channel/{YT_ID}"
    assert ref.display_name == "Marques Brownlee"


@pytest.mark.parametrize("url,page,code", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", _yt_page(), "unsupported_url"),
    ("https://www.youtube.com/@mkbhd", "<html>consent wall</html>", "channel_not_found"),
    ("https://www.youtube.com/channel/" + YT_ID, _yt_page(cid="UC" + "x" * 22), "channel_not_found"),
])
def test_youtube_bad_links(env, url, page, code):
    env.setenv("WATCH_PLATFORMS_ENABLED", "youtube")
    with pytest.raises(cw.WatchError) as e:
        cw.resolve_channel_url(url, fetch_page=lambda u: page)
    assert e.value.code == code


def test_youtube_page_unreachable_is_retryable_message(env):
    env.setenv("WATCH_PLATFORMS_ENABLED", "youtube")

    def down(u):
        raise TimeoutError("slow")
    with pytest.raises(cw.WatchError) as e:
        cw.resolve_channel_url("https://www.youtube.com/@mkbhd", fetch_page=down)
    assert e.value.code == "channel_unreachable" and e.value.status == 503


def test_youtube_listing_maps_entries_and_errors():
    src = {"canonical_url": f"https://www.youtube.com/channel/{YT_ID}"}
    calls = []
    info = {"entries": [{"id": "_0G4OG39XjI", "title": "WTF is a Googlebook?"},
                        {"id": "bad"}, {"id": "3iRUwVzRDZQ", "title": "Fold", "timestamp": 1760000000}]}
    out = cw._list_youtube(src, 10, extract=lambda u, n: calls.append((u, n)) or info)
    assert calls == [(f"https://www.youtube.com/channel/{YT_ID}/videos", 10)]
    assert [e["id"] for e in out] == ["_0G4OG39XjI", "3iRUwVzRDZQ"]
    assert out[0]["url"] == "https://www.youtube.com/watch?v=_0G4OG39XjI"
    assert out[1]["published_at"].startswith("2025-10-09") and "published_at" not in out[0]
    for msg, cat, perm in [("This channel does not exist.", "not_found_or_private", True),
                           ("This channel does not have a videos tab", "empty", True),
                           ("HTTP Error 429: Too Many Requests", "network", False)]:
        def boom(u, n, msg=msg):
            raise RuntimeError(msg)
        with pytest.raises(cw.ListingError) as e:
            cw._list_youtube(src, 10, extract=boom)
        assert (e.value.category, e.value.permanent) == (cat, perm)


def test_youtube_item_identity_is_the_video_id():
    ext, url = cw.item_identity("youtube", {"id": "_0G4OG39XjI", "url": "https://www.youtube.com/watch?v=_0G4OG39XjI&t=3"})
    assert ext == "_0G4OG39XjI" and url == "https://youtube.com/watch?v=_0G4OG39XjI"


def test_youtube_subscription_end_to_end(w, env, monkeypatch):
    env.setenv("WATCH_PLATFORMS_ENABLED", "tiktok,youtube")
    monkeypatch.setattr(cw, "_youtube_channel_page", lambda u: _yt_page())
    yt = f"https://www.youtube.com/channel/{YT_ID}"
    monkeypatch.setitem(cw.PROVIDERS, "youtube", ("ytdlp_flat", w._provider))
    w.listings[yt] = [{"id": "AAAAAAAAAA1", "title": "old"}]
    res = cw.add_source(user_id="u1", tier="free", is_admin=False, email_verified=True,
                        url="https://www.youtube.com/@mkbhd", mode="one_tap", ip="1.1.1.1")
    assert res["baseline"] == "done" and res["source"]["platform"] == "youtube"
    w.listings[yt] = [{"id": "AAAAAAAAAA2", "title": "new"}, {"id": "AAAAAAAAAA1", "title": "old"}]
    w.advance(days=2)
    src = next(s for s in w.rows("watch_sources") if s["platform"] == "youtube")
    assert cw.scan_source(src["id"])["new_items"] == 1
    cw.deliver_pending()
    assert w.pushes and "/share-target?url=" in w.pushes[0][1]["url"]
    assert "watch%3Fv%3DAAAAAAAAAA2" in w.pushes[0][1]["url"]


# ── 33E: inbox (Hộp thư) ─────────────────────────────────────────────

def _two_channels_with_news(w):
    w.listings[CH] = vids(1)
    w.listings[CH2] = vids(11, user="otherchannel")
    w.add(tier="pro")
    w.add(url=CH2, tier="pro")
    w.listings[CH] = vids(3, 2, 1)
    w.listings[CH2] = vids(12, 11, user="otherchannel")
    w.advance(days=2)
    w.scan()
    w.scan(CH2)


def test_inbox_lists_all_channels_unread_then_marks(w):
    _two_channels_with_news(w)
    box = cw.inbox(w.db, "u1")
    assert box["ready"] is True and box["unread"] == 3
    assert sorted(i["title"] for i in box["items"]) == ["video 12", "video 2", "video 3"]
    assert all(i["read"] is False for i in box["items"])
    one = next(i for i in box["items"] if i["title"] == "video 2")
    assert cw.mark_read(w.db, "u1", item_ids=[one["id"]]) == {"unread": 2}
    box = cw.inbox(w.db, "u1", unread_only=True)
    assert len(box["items"]) == 2 and box["unread"] == 2
    sub2 = next(s for s in w.rows("watch_subscriptions") if s["source_id"] == w.source(CH2)["id"])
    assert [i["title"] for i in cw.inbox(w.db, "u1", subscription_id=sub2["id"])["items"]] == ["video 12"]
    assert cw.mark_read(w.db, "u1", all_items=True) == {"unread": 0}
    assert len(w.rows("watch_inbox_state")) == 1                  # one row per user, merged
    # a video found after "mark all" is unread again
    w.listings[CH] = vids(4, 3, 2, 1)
    w.advance(days=2)
    w.scan()
    assert cw.inbox(w.db, "u1")["unread"] == 1


def test_mark_read_ignores_other_users_items_and_bad_requests(w):
    _two_channels_with_news(w)
    w.listings[CH3] = vids(21, user="third.one")
    w.add(user="u2", url=CH3, tier="pro", ip="2.2.2.2")
    w.listings[CH3] = vids(22, 21, user="third.one")
    w.advance(days=2)
    w.scan(CH3)
    foreign = cw.inbox(w.db, "u2")["items"][0]["id"]
    assert cw.mark_read(w.db, "u1", item_ids=[foreign, "nope"]) == {"unread": 3}
    assert w.rows("watch_inbox_state")[0]["read_item_ids"] == []
    with pytest.raises(cw.WatchError) as e:
        cw.mark_read(w.db, "u1")
    assert e.value.code == "invalid_request"


def test_inbox_without_migration_039(w):
    _two_channels_with_news(w)
    w.db.fail_tables.add("watch_inbox_state")
    box = cw.inbox(w.db, "u1")
    assert box["ready"] is False and box["unread"] == 3
    with pytest.raises(cw.WatchError) as e:
        cw.mark_read(w.db, "u1", all_items=True)
    assert (e.value.code, e.value.status) == ("inbox_not_ready", 503)


def test_inbox_api(app, w, api):
    _two_channels_with_news(w)
    assert app.get("/api/v1/watch/inbox").status_code == 401
    login(api, tier="pro")
    r = app.get("/api/v1/watch/inbox?unread=true")
    assert r.status_code == 200 and r.json()["unread"] == 3
    ids = [i["id"] for i in r.json()["items"]][:1]
    r = app.post("/api/v1/watch/inbox/read", json={"item_ids": ids})
    assert r.status_code == 200 and r.json() == {"unread": 2}
    r = app.post("/api/v1/watch/inbox/read", json={"all": True})
    assert r.json() == {"unread": 0}
    assert app.post("/api/v1/watch/inbox/read", json={}).json()["error_code"] == "invalid_request"


# ── 33F: Instagram (paid, Pro only) ─────────────────────────────────

IG = "https://www.instagram.com/natgeo/"


@pytest.fixture
def ig_env(env, monkeypatch, w):
    env.setenv("WATCH_PLATFORMS_ENABLED", "tiktok,youtube,instagram")
    monkeypatch.setattr("app.core.redis_client._client", w.rc)
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: w.rc)
    env.setenv("CHINA_ACCESS_INSTAGRAM_MANAGED_DAILY_SPEND_CEILING_USD", "3")
    env.setenv("CHINA_ACCESS_APIFY_WATCH_DAILY_SPEND_CEILING_USD", "3")
    env.setenv("CHINA_ACCESS_APIFY_WATCH_MONTHLY_SPEND_CEILING_USD", "93")
    return env


def _ig_source(w):
    return next(s for s in w.rows("watch_sources") if s["platform"] == "instagram")


@pytest.mark.parametrize("url,ok", [
    ("https://www.instagram.com/natgeo/", True),
    ("https://instagram.com/NatGeo/reels/", True),
    ("https://www.instagram.com/natgeo", True),
    ("https://www.instagram.com/p/DeJjs2ipNPr/", False),
    ("https://www.instagram.com/reel/DeJjs2ipNPr/", False),
    ("https://www.instagram.com/stories/natgeo/1/", False),
    ("https://www.instagram.com/explore/", False),
])
def test_instagram_profile_links(ig_env, url, ok):
    if ok:
        ref = cw.resolve_channel_url(url)
        assert (ref.platform, ref.external_channel_id, ref.canonical_url) == ("instagram", "natgeo", IG)
    else:
        with pytest.raises(cw.WatchError) as e:
            cw.resolve_channel_url(url)
        assert e.value.code == "unsupported_url"


def test_instagram_is_pro_only(w, ig_env):
    with pytest.raises(cw.WatchError) as e:
        w.add(url=IG, tier="free")
    assert (e.value.code, e.value.status) == ("platform_pro_only", 403)
    assert "Instagram" in e.value.message and w.rows("watch_sources") == []


def test_instagram_add_queues_baseline_without_paying_in_the_request(w, ig_env, monkeypatch):
    calls = []
    monkeypatch.setitem(cw.PROVIDERS, "instagram", (cw.IG_PROVIDER, lambda s, n: calls.append(n) or
                                                    [{"id": "DeJjs2ipNPr", "title": "r"}]))
    res = w.add(url=IG, tier="pro")
    assert res["baseline"] == "pending" and calls == []
    enq = []
    cw.tick(enqueue_scan=enq.append, enqueue_delivery=lambda: None)
    assert enq == [_ig_source(w)["id"]]
    assert cw.scan_source(enq[0])["outcome"] == "baseline" and calls == [10]


def test_instagram_scan_skipped_when_no_paying_subscriber(w, ig_env, monkeypatch):
    calls = []
    monkeypatch.setitem(cw.PROVIDERS, "instagram", (cw.IG_PROVIDER, lambda s, n: calls.append(n) or []))
    w.add(url=IG, tier="pro")
    w.rows("watch_subscriptions")[0]["tier"] = "free"        # downgraded
    assert cw.scan_source(_ig_source(w)["id"])["outcome"] == "no_subscribers" and calls == []


def test_ig_actor_entries_tolerates_field_names():
    rows = [{"shortCode": "AAAAA1", "caption": "old", "timestamp": "2026-10-01T00:00:00.000Z"},
            {"url": "https://www.instagram.com/reel/BBBBB2/", "timestamp": "2026-10-11T00:00:00Z"},
            {"code": "bad code"}, {"error": "no_items"}, "junk"]
    out = cw.ig_actor_entries(rows, 10)
    assert [e["id"] for e in out] == ["BBBBB2", "AAAAA1"]
    assert out[0]["url"] == "https://www.instagram.com/reel/BBBBB2/" and out[0]["title"] == "Instagram Reel"
    assert cw.item_identity("instagram", out[0]) == ("BBBBB2", "https://instagram.com/reel/BBBBB2")


def test_instagram_listing_is_budgeted_and_capped(w, ig_env):
    seen = []

    def run(actor_input, n):
        seen.append((actor_input, n))
        return [{"shortCode": f"CODE{i}x", "timestamp": f"2026-10-1{i}T00:00:00Z"} for i in range(n)], True, 0.0
    src = {"external_channel_id": "natgeo"}
    out = cw._list_instagram(src, 50, run=run)
    inp, n = seen[0]
    assert n == 10 and inp["username"] == ["natgeo"] and inp["resultsLimit"] == 10
    assert inp["includeDownloadedVideo"] is False and inp["includeTranscript"] is False
    assert len(out) == 10
    from app.services.china_platforms import budget_guard as bg
    spent = int(w.rc.get(bg.k_platform_spend(bg.day_key(), "instagram")))
    assert spent == int(round((0.001 + 0.0026 * 10) * 1e6))           # floored at the estimate


def test_instagram_daily_ceiling_stops_paid_runs(w, ig_env):
    ig_env.setenv("CHINA_ACCESS_INSTAGRAM_MANAGED_DAILY_SPEND_CEILING_USD", "0.05")
    runs = []

    def run(actor_input, n):
        runs.append(1)
        return [{"shortCode": "CODE1x"}], True, None
    src = {"external_channel_id": "natgeo"}
    cw._list_instagram(src, 10, run=run)                 # 0.027
    with pytest.raises(cw.ListingError) as e:
        cw._list_instagram(src, 10, run=run)             # would pass 0.05
    assert e.value.permanent is False and e.value.category.startswith("budget_") and len(runs) == 1


def test_instagram_no_run_started_refunds_the_estimate(w, ig_env):
    from app.services.china_platforms import budget_guard as bg
    with pytest.raises(cw.ListingError):
        cw._list_instagram({"external_channel_id": "natgeo"}, 10, run=lambda i, n: ([], False, None))
    assert int(w.rc.get(bg.k_platform_spend(bg.day_key(), "instagram")) or 0) == 0


def _pool(monkeypatch, value):
    seen = []
    monkeypatch.setattr("app.core.proxy_pool.get_proxy_from_pool", lambda p: seen.append(p) or value)
    return seen


def test_embed_no_proxy_anywhere_means_direct_only(env, monkeypatch):
    import curl_cffi.requests as cr
    calls = []
    monkeypatch.setattr(cr, "get", lambda url, **k: calls.append(k.get("proxy")) or _R(503, "overload"))
    monkeypatch.delenv("YTDL_PROXY", raising=False)
    env.setenv("RESIDENTIAL_PROXY_URL", "http://res.invalid:1")
    assert _pool(monkeypatch, None) == []
    assert cw._embed_fetch("https://www.tiktok.com/embed/@x") == (503, "overload")
    assert calls == [None]                         # env proxy needs the flag: no billed call


def test_embed_uses_admin_pool_tiktok_watch_without_any_env(env, monkeypatch):
    import curl_cffi.requests as cr
    calls = []
    monkeypatch.setattr(cr, "get", lambda url, **k: calls.append(k.get("proxy")) or _R(503, "overload"))
    asked = _pool(monkeypatch, "http://pool.invalid:2")
    cw._embed_fetch("https://www.tiktok.com/embed/@x")
    assert asked == ["tiktok_watch"]               # never the download pool "tiktok"
    assert calls == [None, "http://pool.invalid:2"]
    calls.clear()
    monkeypatch.setattr(cr, "get", lambda url, **k: calls.append(k.get("proxy")) or _R(200, "ok"))
    assert cw._embed_fetch("https://www.tiktok.com/embed/@x") == (200, "ok") and calls == [None]


def test_embed_env_proxy_only_with_flag_and_after_the_pool(env, monkeypatch):
    import curl_cffi.requests as cr
    calls = []
    monkeypatch.setattr(cr, "get", lambda url, **k: calls.append(k.get("proxy")) or _R(503, "overload"))
    _pool(monkeypatch, None)
    env.setenv("WATCH_TIKTOK_EMBED_PROXY", "true")
    env.setenv("WATCH_TIKTOK_PROXY_URL", "http://watch.invalid:3")
    cw._embed_fetch("https://www.tiktok.com/embed/@x")
    assert calls == [None, "http://watch.invalid:3"]
    calls.clear()
    _pool(monkeypatch, "http://pool.invalid:2")
    cw._embed_fetch("https://www.tiktok.com/embed/@x")
    assert calls == [None, "http://pool.invalid:2"]


def test_admin_pool_status_lists_tiktok_watch(monkeypatch):
    import fakeredis as _f
    from app.core import proxy_pool as pp
    rc = _f.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pp, "get_redis", lambda: rc)
    pp.add_proxy("tiktok_watch", "http://u:p@h:1")
    st = pp.get_pool_status()
    assert st["tiktok_watch"]["redis_pool"] == 1 and st["tiktok"]["redis_pool"] == 0
    assert pp.get_proxy_from_pool("tiktok") is None or pp.get_proxy_from_pool("tiktok") != "http://u:p@h:1"


def test_new_scan_proxy_makes_blocked_tiktok_sources_due_now(w, monkeypatch):
    w.listings[CH] = vids(1)
    w.add()
    s = w.source()
    later = (w.clock[0] + timedelta(hours=2)).isoformat()
    s.update(consecutive_failures=5, last_failure_category="embed_overload", scan_interval_sec=86400,
             next_scan_at=later)
    proxy = {"v": None}
    monkeypatch.setattr(cw, "_watch_proxy", lambda: proxy["v"])
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 0      # no proxy yet
    proxy["v"] = "http://u:p@all.invalid:1"
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 1
    assert cw._parse(w.source()["next_scan_at"]) == w.clock[0]
    assert "all.invalid" not in (w.rc.get(cw.WATCH_PROXY_FP_KEY) or "")      # fingerprint only
    s["next_scan_at"] = later
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 0      # same proxy: once
    s["last_failure_category"] = "not_found_or_private"
    proxy["v"] = "http://u:p@other.invalid:2"
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 0      # permanent: left alone


def test_proxy_fingerprint_is_stable_with_several_proxies(w, monkeypatch):
    w.listings[CH] = vids(1)
    w.add()
    w.rc.rpush("proxy_pool:all", "http://a.invalid:1", "http://b.invalid:2")
    picks = iter(["http://a.invalid:1", "http://b.invalid:2", "http://a.invalid:1"])
    monkeypatch.setattr(cw, "_watch_proxy", lambda: next(picks))
    s = w.source()
    s.update(consecutive_failures=2, last_failure_category="embed_overload")
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 1
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 0     # different pick, same pool
    assert cw.rescan_blocked_on_new_proxy(w.db, w.rc, w.clock[0]) == 0
