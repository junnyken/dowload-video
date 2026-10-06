"""
Probe registration skeleton (owner correction #9: reuse
app/core/platform_probe.py + app/tasks/probe_tasks.py, no new scheduler).

The existing probe calls extract_video_info_sync — the same path users take —
so once CHINA_ACCESS_DOUYIN_ENABLED is on, a configured Douyin probe target
goes through the access layer. Plan §14.1: a probe must not invoke an
expensive managed provider by default. platform_probe.probe_once therefore
runs the extraction via run_as_probe(), which marks the context
origin="probe"; registry.managed_allowed_for() refuses paid providers for that
origin unless CHINA_ACCESS_MANAGED_PROBES_ENABLED is on.

Managed probes (budgeted, N/day) are wave 2: they would be a separate
probe:targets entry routed with only_provider="apify_douyin".
"""
from __future__ import annotations

import contextvars

from app.services.china_platforms.normalized_models import RequestContext, set_context

PROBE_CONTEXT = RequestContext(requester_key="probe", origin="probe")


def run_as_probe(fn, *args, **kwargs):
    """Run fn in a copy of the current context marked as a probe. Safe to
    pass to ThreadPoolExecutor.submit (which does not copy contextvars)."""
    ctx = contextvars.copy_context()
    ctx.run(set_context, PROBE_CONTEXT)
    return ctx.run(fn, *args, **kwargs)
