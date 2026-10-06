"""
Platform capability registry: URL → platform, policy lookup, effective
provider order and managed mode.

Runtime overrides (Redis) can only REDUCE what the deployment env allows
(plan §7.4): a lower managed mode or a kill switch. Expanding needs an env
change on Vibe Host (no code deploy).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.china_platforms import settings
from app.services.china_platforms.adapters.base import PlatformAdapter
from app.services.china_platforms.adapters.bilibili import BilibiliAdapter
from app.services.china_platforms.adapters.douyin import DouyinAdapter
from app.services.china_platforms.adapters.kuaishou import KuaishouAdapter
from app.services.china_platforms.adapters.lemon8 import Lemon8Adapter
from app.services.china_platforms.adapters.xiaohongshu import XiaohongshuAdapter
from app.services.china_platforms.policy import CHINA_PLATFORM_POLICIES, ChinaPlatformPolicy

logger = logging.getLogger("app.china_access")

ADAPTERS: dict[str, PlatformAdapter] = {
    a.platform: a for a in (
        DouyinAdapter(), KuaishouAdapter(), XiaohongshuAdapter(), BilibiliAdapter(), Lemon8Adapter(),
    )
}

MODE_OVERRIDE_KEY = "china:mode:{platform}"


def identify_platform(url: str) -> Optional[str]:
    for name, adapter in ADAPTERS.items():
        if adapter.matches(url):
            return name
    return None


def get_policy(platform: str) -> Optional[ChinaPlatformPolicy]:
    return CHINA_PLATFORM_POLICIES.get(platform)


def get_adapter(platform: str) -> Optional[PlatformAdapter]:
    return ADAPTERS.get(platform)


def effective_order(policy: ChinaPlatformPolicy, operation: str) -> list[str]:
    """Policy order, optionally re-ordered by CHINA_ACCESS_<P>_PROVIDER_ORDER
    (owner correction #3: Douyin may go managed-first after the benchmark).
    Only providers the policy allows survive; duplicates are dropped so a
    provider can appear at most once per job."""
    base = list(policy.provider_order.get(operation, ()))
    override = settings.env_provider_order(policy.platform)
    order = override if override else base
    out: list[str] = []
    for name in order:
        if name not in policy.allowed_providers:
            logger.warning("china_access: provider %s not allowed for %s — ignored", name, policy.platform)
            continue
        if name not in out:
            out.append(name)
    return out


def _rank(mode: str) -> int:
    try:
        return settings.MANAGED_MODES.index(mode)
    except ValueError:
        return 0


def mode_override(platform: str) -> Optional[str]:
    """Raises on Redis failure (callers decide)."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    v = get_redis().get(MODE_OVERRIDE_KEY.format(platform=platform))
    if isinstance(v, bytes):
        v = v.decode()
    return v if v in settings.MANAGED_MODES else None


def effective_managed_mode(platform: str) -> str:
    policy = get_policy(platform)
    if not policy or not policy.managed_provider_allowed:
        return "off"
    env_mode = settings.env_managed_mode(platform)
    if env_mode == "off":
        return "off"
    try:
        override = mode_override(platform)
    except Exception:  # noqa: BLE001
        return "off"   # cannot read the override → no paid calls
    if override is None:
        return env_mode
    return override if _rank(override) < _rank(env_mode) else env_mode


def managed_allowed_for(mode: str, ctx) -> bool:
    """Is a managed (paid) provider eligible for this request context?"""
    if mode == "off":
        return False
    if ctx.origin == "probe" and not settings.managed_probes_enabled():
        return False
    if ctx.private:
        # A user's own cookie is for the native path only; never send that
        # request to a paid actor on their behalf.
        return False
    if ctx.origin == "benchmark":
        return ctx.allow_managed_benchmark and _rank(mode) >= _rank("benchmark")
    if mode == "canary_admin":
        return bool(ctx.is_admin)
    return mode == "on"


def rank(mode: str) -> int:
    return _rank(mode)
