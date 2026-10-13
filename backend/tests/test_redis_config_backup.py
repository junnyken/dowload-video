"""
Redis configuration backup + restore (task #6332).

What matters: only configuration is exported (no caches/locks/counters), every
Redis type and TTL survives the round trip, credentials never land in the file
by default, and a restore is a dry-run that overwrites nothing unless told.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import fakeredis
import httpx
import pytest

from app.core import redis_config_backup as rcb
from app.tasks import backup_tasks as bt

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

COOKIE = "SID=SECRETcookieVALUE123; HSID=zzz"
PROXY = "http://user:SECRETproxyPASS@1.2.3.4:8080"
SCRAPER = "SECRETscraperKEY0001"
APIFY = "apify_api_SECRETtokenVALUE"
LEGACY_APIFY = "apify_api_SECRETlegacy"
THROWAWAY = json.dumps([{"email": "a@b.c", "password": "SECRETpw"}])
SECRET_VALUES = [COOKIE, "SECRETcookieVALUE123", PROXY, "SECRETproxyPASS", SCRAPER,
                 APIFY, LEGACY_APIFY, "SECRETpw"]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rc():
    return fakeredis.FakeRedis()


def _seed(r):
    # configuration (all five types)
    r.set("admin:config:metrics_weekly_report_enabled", "true")
    r.sadd("admin:config:__index__", "metrics_weekly_report_enabled")
    r.set("probe:targets", json.dumps({"youtube": "https://y/1"}))
    r.set("vidgrab:desktop:ytdlp", json.dumps({"version": "2026.10.01"}))
    r.hset("vidgrab:tuning:params", mapping={"concurrency": "4", "timeout": "30"})
    r.rpush("takedown:requests", json.dumps({"id": 1}), json.dumps({"id": 2}))
    r.zadd("china:watermark:douyin:apify", {"item-a": 1.0, "item-b": 2.5})
    r.set("cookie_meta:youtube:abc", json.dumps({"label": "acc1"}), ex=3600)
    # secrets
    r.rpush("cookie_pool:youtube", COOKIE)
    r.rpush("proxy_pool:default", PROXY)
    r.rpush("scraperapi:keys", SCRAPER)
    r.set("scraperapi:seeded", "1")
    r.hset("china:apify_pool:tokens", "1", APIFY)
    r.set("china:secret:apify_token", LEGACY_APIFY)
    r.set("china:secret:apify_token:meta", json.dumps({"last4": "ALUE"}))
    r.set("throwaway:accounts", THROWAWAY)
    # not configuration
    r.set("admin:session:tok", "x", ex=60)
    r.set("probe:last_run:youtube", "1", ex=60)
    r.set("cookie_cooldown:youtube:abc", "1", ex=60)
    r.set("scraperapi:active_idx", "0")
    r.incr("vidgrab:stats:2026-10-13")
    r.set("youtube:po_token", "tok")
    r.set("lock:something", "1")
    r.set("china:apify_pool:spend_total:1", "100")
    r.set("pcircuit:youtube:state", "open")


class TestAllowList:

    @pytest.mark.parametrize("key", [
        "admin:session:abc", "admin:lockout:1.2.3.4", "admin:attempts:1.2.3.4",
        "probe:last_run:youtube", "probe:state", "probe:alerted",
        "cookie_health:youtube:h", "cookie_cooldown:youtube:h", "cookie_lastused:youtube:h",
        "scraperapi:active_idx", "scraperapi:credits:h", "scraperapi:exhausted:h",
        "china:apify_pool:spend_total:1", "china:apify_pool:spend:1:day:2026-10-13",
        "china:apify_pool:calls:1:day:2026-10-13", "china:apify_pool:alerted:none_eligible",
        "china:benchmark:lock:douyin", "china:cache:douyin:x:h", "china:quota:user:d:u:p",
        "vidgrab:stats:2026-10-13", "vidcache:urlresult:x", "youtube:po_token",
        "youtube:po_token:lock", "lock:k", "pcircuit:youtube:state", "oracle_circuit:state",
        "cobalt:trip:youtube", "vidgrab:youtube:cb:state", "vg:events:buffer",
        "lane:youtube", "throttle:youtube", "asr:spend:2026-10-13", "watch:scanlock:1",
    ])
    def test_caches_locks_counters_are_not_configuration(self, key):
        assert rcb.classify(key) is None

    @pytest.mark.parametrize("key,secret", [
        ("admin:config:x", False), ("probe:targets", False), ("vidgrab:desktop:ytdlp", False),
        ("takedown:requests", False), ("china:secret:apify_token:meta", False),
        ("cookie_pool:youtube", True), ("proxy_pool:tiktok", True), ("scraperapi:keys", True),
        ("scraperapi:seeded", True), ("china:apify_pool:tokens", True),
        ("china:secret:apify_token", True), ("throwaway:accounts", True),
    ])
    def test_configuration_and_secret_classification(self, key, secret):
        rule = rcb.classify(key)
        assert rule is not None and rule.secret is secret

    def test_export_never_uses_keys_command(self, rc):
        _seed(rc)
        spy = MagicMock(wraps=rc)
        spy.keys.side_effect = AssertionError("KEYS must never be used")
        rcb.export_redis_config(spy)
        assert spy.scan_iter.called


class TestExport:

    def test_excluded_keys_are_not_in_the_file(self, rc):
        _seed(rc)
        keys = {e["key"] for e in rcb.export_redis_config(rc)["keys"]}
        for bad in ("admin:session:tok", "probe:last_run:youtube", "scraperapi:active_idx",
                    "vidgrab:stats:2026-10-13", "youtube:po_token", "lock:something",
                    "china:apify_pool:spend_total:1", "pcircuit:youtube:state",
                    "cookie_cooldown:youtube:abc"):
            assert bad not in keys

    def test_secrets_are_excluded_by_default(self, rc):
        _seed(rc)
        payload = rcb.export_redis_config(rc)
        blob = gzip.decompress(rcb.to_blob(payload))
        for v in SECRET_VALUES:
            assert v.encode() not in blob, f"secret value leaked: {v[:8]}…"
        for name in ("cookie_pool:youtube", "proxy_pool:default", "china:apify_pool:tokens",
                     "throwaway:accounts", "scraperapi:keys"):
            assert name.encode() not in blob, "secret key names are hashed, not stored"
        assert payload["include_secrets"] is False
        groups = {m["group"] for m in payload["secret_meta"]}
        assert groups == {"cookie_pool", "proxy_pool", "scraperapi_keys",
                          "apify_tokens", "throwaway_accounts"}
        assert payload["summary"]["cookie_pool"]["keys"] == 1
        assert payload["summary"]["cookie_pool"]["values_included"] is False
        # the non-secret Apify metadata is still there
        assert any(e["key"] == "china:secret:apify_token:meta" for e in payload["keys"])

    def test_include_secrets_setting_is_off_unless_explicit(self, rc):
        assert rcb.include_secrets_setting(rc) is False
        rc.set(rcb.INCLUDE_SECRETS_CONFIG_KEY, "true")
        assert rcb.include_secrets_setting(rc) is True
        rc.set(rcb.INCLUDE_SECRETS_CONFIG_KEY, "no")
        assert rcb.include_secrets_setting(rc) is False

    def test_round_trip_all_five_types_with_ttl(self, rc, tmp_path):
        _seed(rc)
        payload = rcb.load_blob(rcb.to_blob(rcb.export_redis_config(rc, include_secrets=True)))
        target = fakeredis.FakeRedis()
        plan = rcb.plan_restore(target, payload)
        assert rcb.apply_restore(target, plan) == len(payload["keys"])

        assert target.get("admin:config:metrics_weekly_report_enabled") == b"true"
        assert target.smembers("admin:config:__index__") == {b"metrics_weekly_report_enabled"}
        assert target.hgetall("vidgrab:tuning:params") == {b"concurrency": b"4", b"timeout": b"30"}
        assert target.lrange("takedown:requests", 0, -1) == rc.lrange("takedown:requests", 0, -1)
        assert target.zrange("china:watermark:douyin:apify", 0, -1, withscores=True) == \
            [(b"item-a", 1.0), (b"item-b", 2.5)]
        assert 3500_000 < target.pttl("cookie_meta:youtube:abc") <= 3600_000
        assert target.pttl("probe:targets") == -1
        assert target.lrange("cookie_pool:youtube", 0, -1) == [COOKIE.encode()]
        assert not target.exists("admin:session:tok")

    def test_binary_values_survive(self, rc):
        rc.set("admin:config:blob", b"\xff\x00\xfe")
        payload = rcb.load_blob(rcb.to_blob(rcb.export_redis_config(rc)))
        target = fakeredis.FakeRedis()
        rcb.apply_restore(target, rcb.plan_restore(target, payload))
        assert target.get("admin:config:blob") == b"\xff\x00\xfe"


class TestRestoreScript:

    @pytest.fixture
    def backup_file(self, rc, tmp_path):
        _seed(rc)
        p = tmp_path / "redis-config-2026-10-13.json.gz"
        p.write_bytes(rcb.to_blob(rcb.export_redis_config(rc, include_secrets=True)))
        return p

    def test_dry_run_writes_nothing_and_never_prints_secrets(self, backup_file):
        mod = _load_script("restore_redis_config")
        target = fakeredis.FakeRedis()
        lines: list[str] = []
        code = mod.run([str(backup_file), "--show-keys"], rc=target, out=lines.append)
        assert code == 0
        assert target.dbsize() == 0
        text = "\n".join(lines)
        assert "DRY-RUN" in text
        for v in SECRET_VALUES:
            assert v not in text
        assert "cookie_pool:youtube" not in text and "<cookie_pool #" in text

    def test_apply_restores(self, backup_file):
        mod = _load_script("restore_redis_config")
        target = fakeredis.FakeRedis()
        assert mod.run([str(backup_file), "--apply"], rc=target, out=lambda *_: None) == 0
        assert target.get("vidgrab:desktop:ytdlp") is not None
        assert target.lrange("proxy_pool:default", 0, -1) == [PROXY.encode()]

    def test_no_overwrite_without_flag(self, backup_file):
        mod = _load_script("restore_redis_config")
        target = fakeredis.FakeRedis()
        target.set("probe:targets", "CURRENT")
        lines: list[str] = []
        assert mod.run([str(backup_file)], rc=target, out=lines.append) == 2
        assert mod.run([str(backup_file), "--apply"], rc=target, out=lines.append) == 0
        assert target.get("probe:targets") == b"CURRENT"
        assert target.get("vidgrab:desktop:ytdlp") is not None  # others still written

        assert mod.run([str(backup_file), "--apply", "--overwrite"], rc=target,
                       out=lambda *_: None) == 0
        assert json.loads(target.get("probe:targets")) == {"youtube": "https://y/1"}

    def test_metadata_only_backup_lists_missing_secrets(self, rc, tmp_path):
        _seed(rc)
        p = tmp_path / "r.json.gz"
        p.write_bytes(rcb.to_blob(rcb.export_redis_config(rc)))
        mod = _load_script("restore_redis_config")
        target = fakeredis.FakeRedis()
        lines: list[str] = []
        mod.run([str(p), "--apply"], rc=target, out=lines.append)
        assert not target.exists("cookie_pool:youtube")
        assert any("NOT in backup" in line and "cookie_pool" in line for line in lines)

    def test_rejects_a_non_redis_file(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text(json.dumps({"tables": {}}))
        mod = _load_script("restore_redis_config")
        assert mod.run([str(p)], rc=fakeredis.FakeRedis(), out=lambda *_: None) == 1


class TestBackupHook:

    def test_prune_compares_dates_not_strings(self):
        names = ["vidgrab-2026-09-01.json.gz", "redis-config-2026-09-01.json.gz",
                 "vidgrab-2026-10-13.json.gz", "redis-config-2026-10-13.json.gz",
                 "something-else.txt"]
        assert bt._stale_names(names, "2026-10-01") == [
            "vidgrab-2026-09-01.json.gz", "redis-config-2026-09-01.json.gz"]

    def _run(self, rc, monkeypatch):
        uploads: dict[str, bytes] = {}
        monkeypatch.setattr(bt, "_upload", lambda c, u, k, path, blob: uploads.__setitem__(path, blob))
        s3, pg = {}, {}
        monkeypatch.setattr(bt, "_upload_offsite", lambda path, blob: s3.__setitem__(path, blob))
        monkeypatch.setattr(bt, "_upload_postgres",
                            lambda name, blob, check=None: pg.__setitem__(name, (blob, check(
                                json.loads(gzip.decompress(blob))))))
        part = bt._backup_redis_config(MagicMock(), "u", "k", "2026-10-13", rc=rc)
        return part, uploads, s3, pg

    def test_default_run_is_metadata_only_everywhere(self, rc, monkeypatch):
        _seed(rc)
        part, uploads, s3, pg = self._run(rc, monkeypatch)
        name = "redis-config-2026-10-13.json.gz"
        assert part["ok"] and part["file"] == name and part["secrets_included"] is False
        assert part["groups"]["cookie_pool"]["keys"] == 1 and part["keys"] > 0
        for blob in (uploads[name], s3[name], pg[name][0]):
            raw = gzip.decompress(blob)
            for v in SECRET_VALUES:
                assert v.encode() not in raw
        assert pg[name][1] is None  # Postgres check accepted it

    def test_secret_mode_only_reaches_the_supabase_bucket(self, rc, monkeypatch):
        _seed(rc)
        rc.set(rcb.INCLUDE_SECRETS_CONFIG_KEY, "true")
        part, uploads, s3, pg = self._run(rc, monkeypatch)
        name = "redis-config-2026-10-13.json.gz"
        assert part["secrets_included"] is True
        assert COOKIE.encode() in gzip.decompress(uploads[name])
        for blob in (s3[name], pg[name][0]):
            raw = gzip.decompress(blob)
            for v in SECRET_VALUES:
                assert v.encode() not in raw

    def test_postgres_check_refuses_a_copy_with_secrets(self):
        assert bt._check_redis_dump({"format": rcb.FORMAT, "include_secrets": True})
        assert bt._check_redis_dump({"format": rcb.FORMAT, "include_secrets": False}) is None

    def test_redis_down_is_a_warning_not_a_crash(self, monkeypatch):
        broken = MagicMock()
        broken.get.side_effect = ConnectionError("down")
        broken.exists.side_effect = ConnectionError("down")
        broken.scan_iter.side_effect = ConnectionError("down")
        part = bt._backup_redis_config(MagicMock(), "u", "k", "2026-10-13", rc=broken)
        assert part["ok"] is False and "FAILED" in part["error"]


class TestRestoreDbExport:

    @pytest.fixture
    def export_file(self, tmp_path):
        payload = {"generated_at": "2026-10-13T00:00:00+00:00",
                   "tables": {"download_jobs": [{"id": 1, "url": "SECRETrowDATA"},
                                                {"id": 2, "url": "b"}],
                              "settings": [{"k": "x", "v": "y"}]},
                   "auth_users": [{"id": "u1"}]}
        p = tmp_path / "vidgrab-2026-10-13.json.gz"
        p.write_bytes(gzip.compress(json.dumps(payload).encode()))
        return p

    def _client(self, existing_ids=(), status=201):
        calls = []

        def handler(req: httpx.Request):
            calls.append(req)
            if req.method == "GET":
                return httpx.Response(200, json=[{"id": i} for i in existing_ids])
            return httpx.Response(status)
        return httpx.Client(transport=httpx.MockTransport(handler)), calls

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")

    def test_lists_tables_without_selection(self, export_file):
        mod = _load_script("restore_db_export")
        lines: list[str] = []
        assert mod.run([str(export_file)], out=lines.append) == 0
        assert any("download_jobs" in line for line in lines)

    def test_dry_run_writes_nothing_and_reports_conflicts(self, export_file):
        mod = _load_script("restore_db_export")
        client, calls = self._client(existing_ids=[1])
        lines: list[str] = []
        code = mod.run([str(export_file), "--table", "download_jobs"], client=client,
                       out=lines.append)
        assert code == 2
        assert all(c.method == "GET" for c in calls)
        assert "1 already exist" in "\n".join(lines)
        assert "SECRETrowDATA" not in "\n".join(lines)

    def test_apply_ignores_duplicates_by_default(self, export_file):
        mod = _load_script("restore_db_export")
        client, calls = self._client()
        assert mod.run([str(export_file), "--all", "--apply"], client=client,
                       out=lambda *_: None) == 0
        posts = [c for c in calls if c.method == "POST"]
        assert len(posts) == 2
        assert all("resolution=ignore-duplicates" in c.headers["prefer"] for c in posts)

    def test_overwrite_needs_the_flag(self, export_file):
        mod = _load_script("restore_db_export")
        client, calls = self._client()
        mod.run([str(export_file), "--table", "download_jobs", "--apply", "--overwrite"],
                client=client, out=lambda *_: None)
        post = next(c for c in calls if c.method == "POST")
        assert "resolution=merge-duplicates" in post.headers["prefer"]

    def test_failed_write_is_reported(self, export_file):
        mod = _load_script("restore_db_export")
        client, _ = self._client(status=409)
        lines: list[str] = []
        assert mod.run([str(export_file), "--table", "download_jobs", "--apply"],
                       client=client, out=lines.append) == 1
        assert any("FAILED" in line for line in lines)

    def test_unknown_table_is_rejected(self, export_file):
        mod = _load_script("restore_db_export")
        assert mod.run([str(export_file), "--table", "nope", "--offline"],
                       out=lambda *_: None) == 1
