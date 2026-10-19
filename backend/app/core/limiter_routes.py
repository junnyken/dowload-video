"""
Default rate limit for routes inside include_router (2026-10-19)
================================================================
slowapi's middleware applies `default_limits` (60/minute) to the route it
finds with `_find_route_handler(app.routes, scope)`: it takes the first route
whose `.matches()` is FULL *and that has an `.endpoint`*. Since fastapi 0.141
every `include_router` is an `_IncludedRouter` with no `.endpoint`, so the
lookup found nothing for every /api/v1/* route, and slowapi treats "no
handler" as EXEMPT — only the few routes declared directly on `app` (/health,
/api/v1/api-info…) were ever limited. Measured: /health 60×200 then 429;
/api/v1/extension/version 80×200.

`install()` replaces that lookup with one that descends into
`_IncludedRouter` through fastapi's own `_match` (the same resolution the
router uses to dispatch), so the default limit reaches every route again.

Exemptions: polling and authenticated surfaces that legitimately call more
than 60/min from one IP (see EXEMPT_PATHS). For them the lookup returns None,
which slowapi reads as "exempt from the DEFAULT limit" — routes that carry
their own `@limiter.limit(...)` keep it, because that check runs inside the
route's wrapper, not in the middleware.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from starlette.routing import BaseRoute, Match
from starlette.types import Scope

# (path regex, methods or None for any, reason) — exempt from the 60/min
# DEFAULT limit. Measured 19/10: the default bucket is per (IP or verified
# key) AND per concrete path, so /jobs/<a> and /jobs/<b> count separately;
# polls of one id stay under 60/min unless noted.
EXEMPT_PATHS = [
    # Admin: every call needs an admin session (verify_admin); the system
    # snapshot fires 10 routes every 60 s, pages add 15-30 s widgets, and an
    # admin often has several tabs. Brute force: login lockout.
    (r"^/api/v1/admin/", None, "admin session; multi-widget auto-refresh, several tabs"),
    (r"^/api/v1/intelligence/", None, "admin session (dashboards)"),
    # Partner API: its own per-key requests/minute (partner_auth).
    (r"^/api/v1/partner/", None, "own per-key limit"),
    # Download progress: every 1.5 s per running download, per open tab
    # (DashboardContent.jsx) — 40/min for one tab, 80 for two on one token.
    (r"^/api/v1/progress/", ("GET",), "progress polling 40/min per tab"),
    # Channel/container discovery: 800 ms-2.5 s (useContainerDiscover.js) → ~75/min.
    (r"^/api/v1/discover-container/", ("GET",), "discovery polling ~75/min"),
    (r"^/api/v1/container/[^/]+$", ("GET",), "container state polling"),
    # Liveness / status: cheap, polled by every tab, monitors, extension, app.
    (r"^/api/v1/ping$", ("GET", "HEAD"), "liveness"),
    (r"^/health$", ("GET", "HEAD"), "liveness (uptime monitors)"),
    (r"^/api/v1/platform-status$", ("GET", "HEAD"), "status banner, 1/min per tab"),
    # Fixed paths polled by every signed-in user while work is active — one
    # bucket per IP, so users behind one NAT/CGNAT add up (5-8 s polls).
    (r"^/api/v1/history$", ("GET",), "active-jobs poll 7.5/min per user (NAT adds up)"),
    (r"^/api/v1/user/history$", ("GET",), "history list per user"),
    (r"^/api/v1/user/usage$", ("GET",), "usage widget per user"),
    (r"^/api/v1/transcript-asr/(jobs|quota)$", ("GET",), "ASR page 2 calls / 5 s per user"),
    (r"^/api/v1/transcript-translate/jobs$", ("GET",), "translate page 1 call / 5 s per user"),
]
_EXEMPT_RE = [(re.compile(p), m) for p, m, _ in EXEMPT_PATHS]


def is_exempt_path(path: str, method: str = "GET") -> bool:
    for rx, methods in _EXEMPT_RE:
        if rx.search(path or "") and (methods is None or (method or "GET").upper() in methods):
            return True
    return False


def _endpoint_in(route: BaseRoute, scope: Scope, depth: int = 0) -> Optional[Callable]:
    """Endpoint of `route` for this request, descending into _IncludedRouter."""
    if depth > 8:
        return None
    try:
        from fastapi.routing import _IncludedRouter  # noqa: PLC0415
    except Exception:  # older fastapi: no nesting
        _IncludedRouter = ()  # type: ignore
    if _IncludedRouter and isinstance(route, _IncludedRouter):
        try:
            match, _child, inner, ctx = route._match(scope)  # noqa: SLF001
        except Exception:
            return None
        if match != Match.FULL or inner is None:
            return None
        if isinstance(inner, _IncludedRouter):
            return _endpoint_in(inner, scope, depth + 1)
        ep = getattr(inner, "endpoint", None)
        if ep is None and ctx is not None:
            ep = getattr(ctx, "endpoint", None)
        return ep
    try:
        match, _ = route.matches(scope)
    except Exception:
        return None
    if match == Match.FULL:
        return getattr(route, "endpoint", None)
    return None


def find_route_handler(routes: Iterable[BaseRoute], scope: Scope) -> Optional[Callable]:
    """Drop-in for slowapi.middleware._find_route_handler: the endpoint that
    will serve this request (first FULL match, like Starlette), or None —
    None also for EXEMPT_PATHS."""
    if scope.get("type") != "http":
        return None
    if is_exempt_path(scope.get("path", ""), scope.get("method", "GET")):
        return None
    for route in routes:
        ep = _endpoint_in(route, scope)
        if ep is not None:
            return ep
    return None


def install() -> None:
    import slowapi.middleware as sm  # noqa: PLC0415
    sm._find_route_handler = find_route_handler  # noqa: SLF001
