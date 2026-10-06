"""
China access layer — environment settings (Phase 32B-1, wave 1).

Every value is read on each call (no import-time snapshot), so tests and
Vibe Host env changes take effect without re-importing. All defaults are the
safe ones: the layer is OFF and no paid provider is eligible.

New names are all CHINA_ACCESS_*: KUAISHOU_ENABLED already exists for the
Kuaishou extractor scaffold, and a default-OFF DOUYIN_ENABLED would read as
"Douyin is disabled" (owner decision #6).
"""
from __future__ import annotations

import os

_TRUE = ("1", "true", "yes", "on")

# Managed-provider modes, lowest privilege first. The env value is the
# ceiling; a runtime (Redis) override may only go lower (plan §7.4).
MANAGED_MODES = ("off", "benchmark", "canary_admin", "on")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def usd_to_micros(usd: float) -> int:
    return int(round(float(usd) * 1_000_000))


# ── master ──────────────────────────────────────────────────────────────────

def master_enabled() -> bool:
    return _bool("CHINA_ACCESS_ENABLED")


def env_global_killswitch() -> bool:
    return _bool("CHINA_ACCESS_GLOBAL_KILL_SWITCH")


def platform_env_enabled(platform: str) -> bool:
    return _bool(f"CHINA_ACCESS_{platform.upper()}_ENABLED")


# ── guardrails ──────────────────────────────────────────────────────────────

def anon_daily_limit() -> int:
    return _int("CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT", 5)


def free_daily_limit() -> int:
    return _int("CHINA_ACCESS_FREE_DAILY_RESOLVE_LIMIT", 20)


def admin_daily_limit() -> int:
    return _int("CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT", 50)


def managed_timeout_sec() -> int:
    return max(10, _int("CHINA_ACCESS_MANAGED_TIMEOUT_SEC", 90))


def managed_max_retries() -> int:
    """Clamped to 0 in wave 1 (plan §13.4: retry_count must be 0)."""
    return 0


def cache_ttl_sec() -> int:
    return max(0, _int("CHINA_ACCESS_CACHE_TTL_SEC", 1800))


def dedupe_ttl_sec() -> int:
    return max(30, _int("CHINA_ACCESS_DEDUPE_TTL_SEC", 1800))


def dedupe_wait_sec() -> float:
    return max(0.0, _float("CHINA_ACCESS_DEDUPE_WAIT_SEC", 20.0))


def managed_probes_enabled() -> bool:
    return _bool("CHINA_ACCESS_MANAGED_PROBES_ENABLED")


def health_fail_threshold() -> int:
    return max(1, _int("CHINA_ACCESS_HEALTH_FAIL_THRESHOLD", 5))


def health_cooldown_sec() -> int:
    return max(30, _int("CHINA_ACCESS_HEALTH_COOLDOWN_SEC", 600))


# ── per platform ────────────────────────────────────────────────────────────

def env_managed_mode(platform: str) -> str:
    mode = _str(f"CHINA_ACCESS_{platform.upper()}_MANAGED_MODE", "off").lower()
    return mode if mode in MANAGED_MODES else "off"


def env_provider_order(platform: str) -> list[str]:
    raw = _str(f"CHINA_ACCESS_{platform.upper()}_PROVIDER_ORDER")
    return [p.strip() for p in raw.split(",") if p.strip()]


# Per-platform paid defaults. Douyin keeps its wave-1 values. Kuaishou and
# Xiaohongshu share the same Apify account ($5/month in total, owner decision
# 2026-10-06), so each gets a small daily cap of its own: 20 calls and
# $0.10/day is ~25x the estimated per-call price of either actor
# (docs/china-access/09). The shared provider-level Apify ceilings below still
# apply on top.
_PLATFORM_MANAGED_DEFAULTS = {
    "douyin": (50, 1.00),
    "kuaishou": (20, 0.10),
    "xiaohongshu": (20, 0.10),
}


def platform_managed_daily_call_limit(platform: str) -> int:
    default = _PLATFORM_MANAGED_DEFAULTS.get(platform, (50, 1.00))[0]
    return max(0, _int(f"CHINA_ACCESS_{platform.upper()}_MANAGED_DAILY_CALL_LIMIT", default))


def platform_managed_daily_spend_micros(platform: str) -> int:
    default = _PLATFORM_MANAGED_DEFAULTS.get(platform, (50, 1.00))[1]
    return usd_to_micros(_float(f"CHINA_ACCESS_{platform.upper()}_MANAGED_DAILY_SPEND_CEILING_USD", default))


def layer_env_on(platform: str) -> bool:
    """Master flag AND the platform flag (env only — no Redis). Used by the
    classifier / normalizer / capability registry so a platform is recognised
    for /fetch-link only while its access-layer flags are on."""
    return master_enabled() and platform_env_enabled(platform)


# ── per provider (budget class) ─────────────────────────────────────────────

def provider_daily_call_limit(budget_class: str) -> int:
    return max(0, _int(f"CHINA_ACCESS_{budget_class.upper()}_DAILY_CALL_LIMIT", 50))


def provider_daily_spend_micros(budget_class: str) -> int:
    return usd_to_micros(_float(f"CHINA_ACCESS_{budget_class.upper()}_DAILY_SPEND_CEILING_USD", 1.00))


def provider_monthly_spend_micros(budget_class: str) -> int:
    return usd_to_micros(_float(f"CHINA_ACCESS_{budget_class.upper()}_MONTHLY_SPEND_CEILING_USD", 5.00))


# ── Apify ───────────────────────────────────────────────────────────────────

def apify_token() -> str:
    """Admin-stored token (Redis, set from the admin panel) > env
    CHINA_ACCESS_APIFY_TOKEN > "". Read on every call.

    Own variable on purpose: setting APIFY_TOKEN would also switch on the
    legacy, unbudgeted Apify path in downloader.py (see docs 01 C6)."""
    from app.services.china_platforms.secret_store import resolve_apify_token  # noqa: PLC0415
    return resolve_apify_token()[0]


def apify_douyin_actor_id() -> str:
    return _str("CHINA_ACCESS_APIFY_DOUYIN_ACTOR_ID", "natanielsantos~douyin-scraper")


def apify_douyin_est_cost_usd() -> float:
    # $0.007/result (FREE tier) + $0.00005 actor-start event, read from the
    # actor's pricingInfos on 2026-10-06.
    return max(0.0, _float("CHINA_ACCESS_APIFY_DOUYIN_EST_COST_USD", 0.0071))


def apify_kuaishou_actor_id() -> str:
    return _str("CHINA_ACCESS_APIFY_KUAISHOU_ACTOR_ID", "natanielsantos~kuaishou-scraper")


def apify_kuaishou_est_cost_usd() -> float:
    # $0.004 per video (apify-default-dataset-item, flat, not tiered) + one
    # $0.00005 actor-start event (256 MB default memory = 1 event), read from
    # the actor's pricingInfos on 2026-10-06 (docs/china-access/09).
    return max(0.0, _float("CHINA_ACCESS_APIFY_KUAISHOU_EST_COST_USD", 0.00405))


def apify_xiaohongshu_actor_id() -> str:
    return _str("CHINA_ACCESS_APIFY_XIAOHONGSHU_ACTOR_ID", "blue_puppy~rednote-video-downloader")


def apify_xiaohongshu_est_cost_usd() -> float:
    # $0.0025 per dataset item (FREE tier; error rows are dataset items too)
    # + one $0.00005 actor-start event (512 MB default memory = 1 event),
    # read from the actor's pricingInfos on 2026-10-06 (docs/china-access/09).
    return max(0.0, _float("CHINA_ACCESS_APIFY_XIAOHONGSHU_EST_COST_USD", 0.00255))


def short_link_timeout_sec() -> float:
    """One free redirect lookup for v.kuaishou.com / xhslink.com share links."""
    return min(15.0, max(1.0, _float("CHINA_ACCESS_SHORT_LINK_TIMEOUT_SEC", 6.0)))


def apify_run_max_charge_usd() -> float:
    return max(0.001, _float("CHINA_ACCESS_APIFY_RUN_MAX_CHARGE_USD", 0.02))


def apify_require_duration() -> bool:
    return _bool("CHINA_ACCESS_APIFY_REQUIRE_DURATION")


# ── benchmark ───────────────────────────────────────────────────────────────

def benchmark_dir() -> str:
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "downloads", "china_benchmark",
    )
    return _str("CHINA_ACCESS_BENCHMARK_DIR", default)


def benchmark_fixtures_file() -> str:
    return _str("CHINA_ACCESS_BENCHMARK_FIXTURES_FILE")


def benchmark_env_urls(platform: str) -> list[str]:
    raw = os.getenv(f"CHINA_ACCESS_BENCHMARK_{platform.upper()}_URLS", "") or ""
    return [u.strip() for u in raw.replace("\n", ",").split(",") if u.strip()]


def secret_values() -> list[str]:
    """Literal secret values the redactor must remove wherever they appear."""
    from app.services.china_platforms.secret_store import admin_token  # noqa: PLC0415
    # APIFY_TOKEN is only READ here, so a legacy token is scrubbed too.
    vals = [admin_token(), _str("CHINA_ACCESS_APIFY_TOKEN"), _str("APIFY_TOKEN")]
    return [v for v in vals if len(v) >= 6]


# ── Phase 32B-2 Stage A (Douyin) ────────────────────────────────────────────

def media_validate_max_bytes() -> int:
    """First range request size for usable-media validation (bounded)."""
    return min(4 * 1024 * 1024, max(64 * 1024, _int("CHINA_ACCESS_MEDIA_VALIDATE_MAX_BYTES", 2 * 1024 * 1024)))


def media_validate_min_bytes() -> int:
    """Below this the media body is treated as trivial (an error page, a stub)."""
    return max(1, _int("CHINA_ACCESS_MEDIA_VALIDATE_MIN_BYTES", 64 * 1024))


def media_validate_timeout_sec() -> float:
    return max(2.0, _float("CHINA_ACCESS_MEDIA_VALIDATE_TIMEOUT_SEC", 20.0))


def router_validate_media() -> bool:
    """Run the usable-media check inside the router after a provider success.
    Default off: it costs up to a few MB of CDN bandwidth per request."""
    return _bool("CHINA_ACCESS_ROUTER_VALIDATE_MEDIA")


def benchmark_validate_media() -> bool:
    return _bool("CHINA_ACCESS_BENCHMARK_VALIDATE_MEDIA", True)


def cost_overrun_flag_ratio() -> float:
    """Flag a managed run when actual > estimate × (1 + pct/100)."""
    return 1.0 + max(0.0, _float("CHINA_ACCESS_COST_OVERRUN_FLAG_PCT", 50.0)) / 100.0


def cost_floor_at_estimate() -> bool:
    """Never settle a managed run below its estimate (see budget_guard.settle:
    Apify's usageTotalUsd left out the per-result charge on 2026-10-06)."""
    return _bool("CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE", True)


def spend_alerts_enabled() -> bool:
    return _bool("CHINA_ACCESS_SPEND_ALERTS_ENABLED", True)


SPEND_ALERT_THRESHOLDS = (50, 80, 100)


def auto_rollback_enabled() -> bool:
    return _bool("CHINA_ACCESS_AUTO_ROLLBACK_ENABLED", True)


def auto_rollback_probe_failures() -> int:
    return max(1, _int("CHINA_ACCESS_AUTO_ROLLBACK_PROBE_FAILURES", 2))


def auto_rollback_window() -> int:
    return max(2, _int("CHINA_ACCESS_AUTO_ROLLBACK_WINDOW", 10))


def auto_rollback_min_usable_rate() -> float:
    return min(1.0, max(0.0, _float("CHINA_ACCESS_AUTO_ROLLBACK_MIN_USABLE_RATE", 0.5)))


def watermark_min_reviewed() -> int:
    return max(1, _int("CHINA_ACCESS_WATERMARK_MIN_REVIEWED", 10))


def watermark_min_free_rate() -> float:
    return min(1.0, max(0.0, _float("CHINA_ACCESS_WATERMARK_MIN_FREE_RATE", 0.9)))


def bench_min_sample() -> int:
    return max(1, _int("CHINA_ACCESS_BENCH_MIN_SAMPLE", 20))


def bench_promote_usable_rate() -> float:
    return _float("CHINA_ACCESS_BENCH_PROMOTE_USABLE_RATE", 0.8)


def bench_reject_usable_rate() -> float:
    return _float("CHINA_ACCESS_BENCH_REJECT_USABLE_RATE", 0.5)


def bench_native_margin() -> float:
    """How far the managed usable rate must beat native to promote."""
    return _float("CHINA_ACCESS_BENCH_NATIVE_MARGIN", 0.2)


def bench_max_cost_per_usable_usd() -> float:
    return _float("CHINA_ACCESS_BENCH_MAX_COST_PER_USABLE_USD", 0.01)


def bench_max_timeout_share() -> float:
    return _float("CHINA_ACCESS_BENCH_MAX_TIMEOUT_SHARE", 0.2)
