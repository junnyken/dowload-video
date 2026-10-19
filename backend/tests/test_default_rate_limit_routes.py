"""The 60/min default limit reaches routes inside include_router again
(19/10). fastapi 0.141 nests them in _IncludedRouter, which slowapi's lookup
could not see, so every /api/v1/* route without its own @limiter.limit was
unlimited per IP. Exempt surfaces (polling, admin, partner) stay unlimited by
the DEFAULT; routes with their own decorator keep their own limit."""
import collections
import itertools

import pytest
from fastapi.testclient import TestClient

_ip = itertools.count(1)


@pytest.fixture()
def client():
    import app.main as m
    return TestClient(m.app)


def _hit(client, path, n, method="get"):
    ip = f"198.51.100.{100 + next(_ip)}"
    cnt = collections.Counter()
    for _ in range(n):
        cnt[getattr(client, method)(path, headers={"x-forwarded-for": ip}).status_code] += 1
    return cnt


def test_router_route_gets_the_default_limit(client):
    c = _hit(client, "/api/v1/extension/version", 70)
    assert c[429] == 10 and sum(v for k, v in c.items() if k != 429) == 60


@pytest.mark.parametrize("path", [
    "/api/v1/progress/some-token",
    "/api/v1/admin/enterprise/tenants",
    "/api/v1/partner/usage",
])
def test_exempt_paths_are_not_limited_by_the_default(client, path):
    assert 429 not in _hit(client, path, 80)


def test_route_with_its_own_higher_limit_is_not_capped_at_60(client):
    # /download-thumbnail declares 120/minute; validation fails (422) before the
    # handler, but a middleware default would have answered 429 from the 61st.
    assert 429 not in _hit(client, "/api/v1/download-thumbnail", 100)


def test_lookup_finds_nested_endpoints():
    import app.main as m
    from app.core.limiter_routes import find_route_handler
    scope = {"type": "http", "path": "/api/v1/extension/version", "method": "GET",
             "root_path": "", "query_string": b"", "headers": []}
    ep = find_route_handler(m.app.routes, scope)
    assert ep is not None and ep.__name__
    scope["path"] = "/api/v1/progress/abc"
    assert find_route_handler(m.app.routes, scope) is None      # exempt


def test_install_replaced_slowapi_lookup():
    import slowapi.middleware as sm
    from app.core.limiter_routes import find_route_handler
    assert sm._find_route_handler is find_route_handler


# ── every real polling flow, one minute, one IP (cadences from the client code) ─

FLOWS = {
    # web: one download, two tabs on the same token (DashboardContent.jsx 1.5 s)
    "web_progress_two_tabs": [("get", "/api/v1/progress/tok-1", 80), ("get", "/api/v1/platform-status", 2)],
    # web bulk page 2 s + extension popup (2 s for 30 s, then 5 s) + background tracker, same batch
    "bulk_web_plus_extension": [("get", "/api/v1/jobs/batch-1", 30 + 21 + 1)],
    # two batches at once from one IP: separate buckets per batch id
    "two_batches": [("get", "/api/v1/jobs/batch-a", 30), ("get", "/api/v1/jobs/batch-b", 30)],
    # channel discovery 800 ms-2.5 s (useContainerDiscover.js)
    "container_discovery": [("get", "/api/v1/discover-container/job-1", 75), ("get", "/api/v1/container/c-1", 40)],
    # AI analyze 2 s × 30
    "analyze": [("get", "/api/v1/analyze/a-1", 30)],
    # 10 signed-in users behind one NAT: active-jobs poll 8 s, ASR page 5 s × 2, translate 5 s
    "nat_ten_users": [("get", "/api/v1/history", 75), ("get", "/api/v1/transcript-asr/jobs", 120),
                      ("get", "/api/v1/transcript-asr/quota", 120), ("get", "/api/v1/transcript-translate/jobs", 120),
                      ("get", "/api/v1/user/usage", 20), ("get", "/api/v1/user/history", 20)],
    # admin: 3 tabs of the system snapshot (10 routes / 60 s) + Queue Health 15 s + active jobs 15 s
    "admin_three_tabs": [("get", p, 3) for p in (
        "/api/v1/admin/stats", "/api/v1/admin/analytics?days=1", "/api/v1/admin/analytics?days=7",
        "/api/v1/admin/ops-health", "/api/v1/admin/errors", "/api/v1/admin/platform-stats",
        "/api/v1/admin/users/signups", "/api/v1/admin/proxies/status", "/api/v1/admin/youtube/status",
        "/api/v1/admin/cookies/status")] + [("get", "/api/v1/admin/queue-health", 12),
                                           ("get", "/api/v1/intelligence/anomalies", 6)],
    # liveness: uptime monitor + every tab + extension
    "liveness": [("get", "/health", 90), ("get", "/api/v1/ping", 90)],
    # desktop app background (watch 15 min, version 30 min) — tiny; listed so a regression shows
    "desktop_background": [("get", "/api/v1/watch/status", 2), ("get", "/api/v1/client/version", 2)],
}


@pytest.mark.parametrize("flow", sorted(FLOWS))
def test_polling_flow_is_never_refused(client, flow):
    ip = f"203.0.113.{10 + sorted(FLOWS).index(flow)}"
    refused = collections.Counter()
    for method, path, n in FLOWS[flow]:
        for _ in range(n):
            if getattr(client, method)(path, headers={"x-forwarded-for": ip}).status_code == 429:
                refused[path] += 1
    assert not refused, f"{flow}: 429 on {dict(refused)}"


@pytest.mark.parametrize("path", ["/api/v1/extension/version", "/api/v1/plans", "/api/v1/error-codes"])
def test_non_polling_routes_are_limited_again(client, path):
    assert _hit(client, path, 65)[429] == 5


def test_exemption_is_per_method():
    from app.core.limiter_routes import is_exempt_path
    assert is_exempt_path("/api/v1/history", "GET")
    assert not is_exempt_path("/api/v1/history", "DELETE")
    assert not is_exempt_path("/api/v1/progress/x", "POST")
