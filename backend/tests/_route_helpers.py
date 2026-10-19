"""Route walking shared by tests that override `verify_admin`.

fastapi 0.141 (pinned 2026-09-04) wraps every include_router in an
`_IncludedRouter` with no `.dependant`; its routes sit under
`.original_router.routes`. The walkers looked only at the top level, found no
`verify_admin`, overrode nothing — and 41 admin tests saw 401 and failed
for that reason alone (triage 2026-10-19)."""


def iter_routes(routes):
    for r in routes:
        sub = getattr(r, "original_router", None)
        if sub is not None:
            yield from iter_routes(sub.routes)
        else:
            yield r


def admin_dependencies(app_obj):
    """Every verify_admin callable the mounted routes actually depend on.

    test_admin_security reloads app.api.admin, after which the module's
    `verify_admin` is a new object while the routes still hold the original —
    so override what the routes hold, not the module attribute."""
    found = set()
    for route in iter_routes(app_obj.routes):
        dep = getattr(route, "dependant", None)
        stack = list(getattr(dep, "dependencies", []) or [])
        while stack:
            d = stack.pop()
            if getattr(d.call, "__name__", "") == "verify_admin":
                found.add(d.call)
            stack.extend(d.dependencies or [])
    return found
