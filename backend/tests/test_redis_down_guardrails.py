"""
Losing Redis removed every cost control at once.

platform_lanes, fair_queue, admission_control, yt_quota and the /fetch-link
slot counter all fail open on a Redis error. Each choice is defensible alone —
do not block a paying user over infrastructure trouble. Together they mean one
Redis outage drops the daily quota, the per-platform lane, admission control
AND the concurrency ceiling in the same instant, while every download spends
real money on Gemini, ScraperAPI and residential proxy bandwidth. The failure
that is supposed to be invisible is the one that empties the budget.

The slot counter now falls back to an in-process ceiling instead of to
nothing. It is per-worker, so it cannot enforce the real global limit; the
point is that a worker refuses to run unbounded.

Also pins the auth on /admin/container-stats, which read "admin" in its path
and its docstring while taking no auth at all.
"""

from __future__ import annotations

import threading

import pytest


@pytest.fixture
def routes_module():
    from app.api import routes
    return routes


@pytest.fixture(autouse=True)
def _fresh_local_semaphore(routes_module):
    """Each test starts with an empty local ceiling."""
    original = routes_module._local_slots
    routes_module._local_slots = threading.BoundedSemaphore(routes_module._MAX_CONCURRENT_DL)
    routes_module._local_slot_held = threading.local()
    yield
    routes_module._local_slots = original


def _break_redis(monkeypatch, routes_module):
    """Make every get_redis() call raise, the way an outage looks from here."""
    import app.core.redis_client as rc

    def _boom():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(rc, "get_redis", _boom)


class TestTheCeilingSurvivesARedisOutage:

    def test_it_still_admits_work_up_to_the_limit(self, monkeypatch, routes_module):
        """Failing closed would be its own outage — the point is a ceiling,
        not a refusal."""
        _break_redis(monkeypatch, routes_module)
        limit = routes_module._MAX_CONCURRENT_DL
        for i in range(limit):
            ok, _ = routes_module._acquire_download_slot()
            assert ok, f"refused request {i + 1} of {limit} while below the ceiling"

    def test_it_refuses_past_the_limit_instead_of_waving_everything_through(
        self, monkeypatch, routes_module
    ):
        _break_redis(monkeypatch, routes_module)
        limit = routes_module._MAX_CONCURRENT_DL
        for _ in range(limit):
            assert routes_module._acquire_download_slot()[0]

        ok, active = routes_module._acquire_download_slot()
        assert ok is False, (
            "with Redis down this used to return (True, 0) forever — every "
            "concurrent request admitted, each one spending money"
        )
        assert active == limit

    def test_it_does_not_block_waiting_for_a_slot(self, monkeypatch, routes_module):
        """A blocking acquire would hang the request thread instead of
        answering; the refusal has to be immediate."""
        _break_redis(monkeypatch, routes_module)
        for _ in range(routes_module._MAX_CONCURRENT_DL):
            routes_module._acquire_download_slot()

        done = threading.Event()

        def _try():
            routes_module._acquire_download_slot()
            done.set()

        t = threading.Thread(target=_try, daemon=True)
        t.start()
        assert done.wait(timeout=2.0), "acquire blocked instead of refusing"

    def test_releasing_returns_the_slot(self, monkeypatch, routes_module):
        _break_redis(monkeypatch, routes_module)
        limit = routes_module._MAX_CONCURRENT_DL
        for _ in range(limit):
            routes_module._acquire_download_slot()
        assert routes_module._acquire_download_slot()[0] is False

        routes_module._release_download_slot()
        assert routes_module._acquire_download_slot()[0] is True


class TestReleaseTouchesTheRightCounter:
    """A release that frees the local semaphore for a Redis-backed slot would
    quietly raise the local ceiling on every later request — the guardrail
    would decay instead of failing loudly."""

    def test_a_redis_backed_slot_does_not_release_the_local_semaphore(
        self, monkeypatch, routes_module
    ):
        import app.core.redis_client as rc

        class _FakeRedis:
            def __init__(self):
                self.value = 0

            def incr(self, _key):
                self.value += 1
                return self.value

            def decr(self, _key):
                self.value -= 1
                return self.value

            def expire(self, *_a, **_k):
                return True

            def set(self, *_a, **_k):
                return True

        fake = _FakeRedis()
        monkeypatch.setattr(rc, "get_redis", lambda: fake)

        ok, _ = routes_module._acquire_download_slot()
        assert ok
        routes_module._release_download_slot()
        assert fake.value == 0, "the Redis counter was not decremented"

        # The local ceiling must be untouched: still exactly _MAX_CONCURRENT_DL
        # acquisitions available, not one more.
        _break_redis(monkeypatch, routes_module)
        limit = routes_module._MAX_CONCURRENT_DL
        for _ in range(limit):
            assert routes_module._acquire_download_slot()[0]
        assert routes_module._acquire_download_slot()[0] is False, (
            "the local ceiling grew — a Redis-backed release leaked into it"
        )

    def test_over_releasing_does_not_raise(self, monkeypatch, routes_module):
        """BoundedSemaphore.release() raises past its limit; a stray release
        must not turn into a 500 on a download that otherwise succeeded."""
        _break_redis(monkeypatch, routes_module)
        routes_module._release_download_slot()
        routes_module._release_download_slot()


def _is_admin_guard(fn) -> bool:
    """Identity comparison is not safe here.

    Other test modules reload app.api.admin, so `verify_admin` can exist as
    two distinct function objects in one session and `dep is verify_admin`
    fails on a route that is correctly guarded. Match on where the function
    came from instead.
    """
    return (
        getattr(fn, "__name__", "") == "verify_admin"
        and getattr(fn, "__module__", "") == "app.api.admin"
    )


class TestContainerStatsIsAdminOnly:

    def test_the_endpoint_declares_the_admin_dependency(self):
        """It read 'admin' in its path and its docstring while taking no auth
        at all, which is the kind of endpoint that stays unguarded."""
        import inspect

        from app.api.container import container_admin_stats

        params = inspect.signature(container_admin_stats).parameters
        deps = [
            p.default.dependency
            for p in params.values()
            if hasattr(p.default, "dependency")
        ]
        assert any(_is_admin_guard(d) for d in deps), (
            "/admin/container-stats does not require an admin"
        )

    def test_no_route_named_admin_is_left_unguarded(self):
        """The general form, so the next /admin/ route cannot ship open."""
        import inspect

        from app.main import app

        unguarded = []
        for route in app.routes:
            path = getattr(route, "path", "")
            endpoint = getattr(route, "endpoint", None)
            if "/admin" not in path or endpoint is None:
                continue
            if path.endswith("/admin/login"):
                continue  # login is how you become an admin
            source = ""
            try:
                source = inspect.getsource(endpoint)
            except (OSError, TypeError):
                pass
            if "verify_admin" in source or "_require_admin" in source:
                continue
            params = inspect.signature(endpoint).parameters
            has_dep = any(
                _is_admin_guard(getattr(p.default, "dependency", None))
                for p in params.values()
            )
            if not has_dep:
                unguarded.append(path)

        assert not unguarded, f"admin routes with no admin check: {sorted(set(unguarded))}"
