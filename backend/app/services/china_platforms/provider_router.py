"""
Guarded provider router (plan §8). The check order is fixed:

   1 identify platform + canonicalize      →  unsupported_url
   2 platform in registry, routable         →  platform_disabled
   3 master flag + global kill switch       →  platform_disabled
   4 platform flag + platform kill switch   →  platform_disabled
   5 user/IP quota          ┐ read-only pre-check, paid path only;
   6 platform call/spend     │ a denial makes the paid path fail with
   7 provider daily/monthly ┘ budget_exceeded (no fallback after it)
   8 cache, then same-URL dedupe (SET NX EX)
   9 health + effective order
  10 providers in order, ONCE each; paid ones: kill switch re-check →
     atomic budget reserve → once-per-URL paid marker → dispatch → settle
  11 success / aggregated failure
  12 audit record, counters, cache, release dedupe

Fallback to the next provider only when the failure category is in
policy.fallback_categories. budget_exceeded / platform_disabled stop at once.
No retry loop anywhere: CHINA_ACCESS_MANAGED_MAX_RETRIES is clamped to 0.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from app.services.china_platforms import apify_pool, budget_guard, cost_metrics, registry, request_cache, settings
from app.services.china_platforms import media_validation, provider_health, rollout_guard, watermark
from app.services.china_platforms.errors import (
    AlreadyProcessing,
    ChinaAccessFailure,
    ProviderFailure,
    ProviderFailureError,
    make_failure,
    redact,
)
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaResult,
    RequestContext,
    current_context,
)

logger = logging.getLogger("app.china_access")

ATTEMPTS_KEY = "china:attempts:{platform}"
STATS_KEY = "china:stats:{day}"
_ATTEMPTS_CAP = 200
_ATTEMPTS_TTL = 7 * 86400
_STATS_TTL = 35 * 86400

ProvidersFactory = Callable[[str], dict]


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def record_attempt(attempt: dict) -> None:
    """Scrubbed attempt → Redis ring + counters + one JSON log line. Never raises."""
    line = redact(json.dumps(attempt, ensure_ascii=False, default=str))
    logger.info("china_access attempt %s", line)
    try:
        r = _r()
        k = ATTEMPTS_KEY.format(platform=attempt.get("platform"))
        r.lpush(k, line)
        r.ltrim(k, 0, _ATTEMPTS_CAP - 1)
        r.expire(k, _ATTEMPTS_TTL)
        sk = STATS_KEY.format(day=budget_guard.day_key())
        field = f"{attempt.get('platform')}|{attempt.get('provider')}|" + (
            "ok" if attempt.get("outcome") == "success"
            else f"{attempt.get('outcome')}|{attempt.get('normalized_failure_category')}")
        r.hincrby(sk, field, 1)
        r.expire(sk, _STATS_TTL)
    except Exception:  # noqa: BLE001
        pass


def recent_attempts(platform: str, limit: int = _ATTEMPTS_CAP) -> list[dict]:
    try:
        rows = _r().lrange(ATTEMPTS_KEY.format(platform=platform), 0, max(0, limit - 1)) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for raw in rows:
        try:
            out.append(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
        except Exception:  # noqa: BLE001
            continue
    return out


class ProviderRouter:
    """One instance per resolve is fine; `trace` and `attempts` describe the
    last resolve() (used by tests and the benchmark harness)."""

    def __init__(self, providers_factory: Optional[ProvidersFactory] = None, *, poll_interval: float = 0.5):
        self._factory = providers_factory
        self._poll = poll_interval
        self.trace: list[str] = []
        self.attempts: list[dict] = []

    # ── helpers ─────────────────────────────────────────────────────────────

    def _providers(self, platform: str) -> dict:
        if self._factory is not None:
            return self._factory(platform)
        adapter = registry.get_adapter(platform)
        return adapter.build_providers() if adapter else {}

    def _fail(self, platform: str, category: str, detail: str, failures=None, stop="guard"):
        f = make_failure(platform, "router", category, detail)
        return ChinaAccessFailure(platform, category, list(failures or []) + [f], stop_reason=stop)

    def _attempt(self, *, request, platform, provider_name, mode, h, ctx, outcome, category=None,
                 latency_ms=0, result: Optional[NormalizedMediaResult] = None, est_usd=0.0,
                 actual_usd=None, cost_source="none", health_state=None, budget_check="n/a",
                 duration_missing=False, expiry=None, media: Optional[dict] = None,
                 cost: Optional[dict] = None, pool_entry: Optional[str] = None,
                 pool_refusals: Optional[list] = None) -> dict:
        a = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "platform": platform,
            "operation": request.operation,
            "provider": provider_name,
            "route_mode": mode,
            "request_id": request.request_id,
            "canonical_url_hash": h,
            "origin": ctx.origin,
            "outcome": outcome,
            "normalized_failure_category": category,
            "latency_ms": int(latency_ms),
            "metadata_success": bool(result and result.title),
            "usable_media_url": bool(result and result.primary_video_url()),
            "media_url_expiry_if_known": expiry,
            "watermark_state": result.watermark_state if result else "unknown",
            "proxy_bytes_if_used": 0,
            "estimated_cost_usd": round(float(est_usd or 0), 6),
            "actual_cost_usd": None if actual_usd is None else round(float(actual_usd), 6),
            "cost_source": cost_source,
            "retry_count": settings.managed_max_retries(),
            "duration_missing": bool(duration_missing),
            "cache_hit": bool(result and result.cache_hit),
            "health_state": health_state,
            "budget_check_result": budget_check,
            # 32B-2 Stage A
            "media_url_present": bool(result and result.primary_video_url()),
            "cost_delta_usd": (cost or {}).get("cost_delta_usd"),
            "cost_flag": (cost or {}).get("cost_flag"),
            # task #6036: which pool entry paid (id only, never the token)
            "apify_entry": pool_entry,
            "apify_refusals": pool_refusals,
        }
        m = media or media_validation.not_run()
        for k in media_validation.RESULT_FIELDS:
            if k == "usable_media_url" and m.get("media_check") == "not_run":
                continue    # not validated: keep "a media URL is present"
            if k == "media_url_expiry_if_known" and m.get(k) is None:
                continue
            a[k] = m.get(k)
        self.attempts.append(a)
        record_attempt(a)
        return a

    # ── main entry ──────────────────────────────────────────────────────────

    async def resolve(self, request: ChinaResolveRequest, ctx: Optional[RequestContext] = None, *,
                      use_cache: bool = True, only_provider: Optional[str] = None) -> NormalizedMediaResult:
        ctx = ctx or current_context()
        self.trace, self.attempts = [], []
        op = request.operation

        # 1. identify + canonicalize
        self.trace.append("identify")
        platform = registry.identify_platform(request.url)
        if not platform:
            raise self._fail("unknown", "unsupported_url", "not a registered China platform")
        adapter = registry.get_adapter(platform)

        # 2. registry
        self.trace.append("registry")
        policy = registry.get_policy(platform)
        if policy is None or not policy.routable_in_wave1:
            raise self._fail(platform, "platform_disabled", "platform not routable in wave 1")
        if op not in policy.supported_operations:
            raise self._fail(platform, "unsupported_url", f"operation {op} not supported")

        # 3. master + global kill switch
        self.trace.append("master")
        if not settings.master_enabled():
            raise self._fail(platform, "platform_disabled", "CHINA_ACCESS_ENABLED off")
        try:
            if budget_guard.global_killswitch_on():
                raise self._fail(platform, "platform_disabled", "global kill switch")
        except ChinaAccessFailure:
            raise
        except Exception:  # noqa: BLE001
            raise self._fail(platform, "platform_disabled", "kill switch unreadable (fail closed)")

        # 4. platform flag + kill switch
        self.trace.append("platform")
        if not settings.platform_env_enabled(platform):
            raise self._fail(platform, "platform_disabled", f"CHINA_ACCESS_{platform.upper()}_ENABLED off")
        try:
            if budget_guard.platform_killswitch_on(platform):
                raise self._fail(platform, "platform_disabled", "platform kill switch")
        except ChinaAccessFailure:
            raise
        except Exception:  # noqa: BLE001
            raise self._fail(platform, "platform_disabled", "kill switch unreadable (fail closed)")

        # Step 1's network part (short-link redirect, free) runs only after the
        # flags pass, so a disabled layer never makes a request.
        canonical = await adapter.resolve_canonical(request.url)
        h = request_cache.url_hash(canonical)
        req = request.model_copy(update={"url": canonical})

        order = registry.effective_order(policy, op)
        if only_provider is not None:
            order = [p for p in order if p == only_provider]
        providers = self._providers(platform)
        mode = registry.effective_managed_mode(platform)
        managed_ok = registry.managed_allowed_for(mode, ctx)

        candidates: list[budget_guard.PaidCandidate] = []
        for name in order:
            prov = providers.get(name)
            if prov is not None and prov.paid and managed_ok and prov.is_configured():
                est = await prov.estimate_cost_usd(req)
                candidates.append(budget_guard.PaidCandidate(
                    name, prov.budget_class, settings.usd_to_micros(float(est))))

        # 5-7. quota / platform budget / provider budget (read-only, in order)
        self.trace.append("budget_precheck")
        denial = budget_guard.precheck(platform, ctx.requester_key, candidates)

        # 8. cache, then dedupe
        cacheable = use_cache and not ctx.private
        self.trace.append("cache")
        if cacheable:
            hit = request_cache.get_cached(platform, op, h)
            if hit is not None:
                self._attempt(request=req, platform=platform, provider_name=hit.provider_name,
                              mode=hit.provider_mode, h=h, ctx=ctx, outcome="success", result=hit)
                cost_metrics.record_cache_hit(platform, hit.provider_name, hit.provider_mode)
                return hit

        self.trace.append("dedupe")
        token = None
        if cacheable:   # a private (own-cookie) request is user-specific: no shared dedupe
            token = request_cache.acquire_dedupe(platform, op, h)
            if token is None:
                waited = 0.0
                while waited < settings.dedupe_wait_sec():
                    await asyncio.sleep(self._poll)
                    waited += self._poll
                    hit = request_cache.get_cached(platform, op, h)
                    if hit is not None:
                        cost_metrics.record_cache_hit(platform, hit.provider_name, hit.provider_mode)
                        return hit
                raise AlreadyProcessing(platform, h)

        try:
            return await self._run_providers(req, ctx, platform, policy, order, providers, mode,
                                             managed_ok, denial, h, cacheable)
        finally:
            if token:
                request_cache.release_dedupe(platform, op, h, token)

    async def _run_providers(self, req, ctx, platform, policy, order, providers, mode, managed_ok,
                             denial, h, cacheable) -> NormalizedMediaResult:
        # 9. health + order
        self.trace.append("health")
        failures: list[ProviderFailure] = []
        attempted: set[str] = set()

        # 10. once each
        for name in order:
            if name in attempted:
                continue
            prov = providers.get(name)
            if prov is None or not await prov.can_handle(req, policy):
                continue
            health = provider_health.snapshot(name, platform)
            if not health.eligible:
                f = make_failure(platform, name, "provider_unavailable", f"health {health.state}")
                failures.append(f)
                self._attempt(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h,
                              ctx=ctx, outcome="skipped", category="provider_unavailable",
                              health_state=health.state)
                continue

            reservation = None
            est_usd = 0.0
            if prov.paid:
                if not managed_ok or not prov.is_configured():
                    self._attempt(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h,
                                  ctx=ctx, outcome="skipped", category=None,
                                  budget_check=("mode_" + mode) if not managed_ok else "not_configured")
                    continue
                if denial is not None:
                    self.trace.append(f"budget_denied:{name}")
                    f = make_failure(platform, name, "budget_exceeded", f"{denial.level}: {denial.detail}")
                    self._attempt(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h,
                                  ctx=ctx, outcome="skipped", category="budget_exceeded",
                                  budget_check=denial.level)
                    raise ChinaAccessFailure(platform, "budget_exceeded", failures + [f], stop_reason="budget")
                # Kill switch re-check right before money moves.
                try:
                    killed = budget_guard.global_killswitch_on() or budget_guard.platform_killswitch_on(platform)
                except Exception:  # noqa: BLE001
                    killed = True
                if killed:
                    f = make_failure(platform, name, "platform_disabled", "kill switch before dispatch")
                    raise ChinaAccessFailure(platform, "platform_disabled", failures + [f], stop_reason="killswitch")
                est_usd = float(await prov.estimate_cost_usd(req))
                cand = budget_guard.PaidCandidate(name, prov.budget_class, settings.usd_to_micros(est_usd))
                self.trace.append(f"reserve:{name}")
                try:
                    reservation = budget_guard.reserve(platform, ctx.requester_key, cand)
                except budget_guard.BudgetDenied as bd:
                    f = make_failure(platform, name, "budget_exceeded", f"{bd.level}: {bd.detail}")
                    self._attempt(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h,
                                  ctx=ctx, outcome="skipped", category="budget_exceeded", budget_check=bd.level)
                    raise ChinaAccessFailure(platform, "budget_exceeded", failures + [f], stop_reason="budget")
                if not request_cache.claim_paid_attempt(name, h):
                    budget_guard.rollback(reservation)
                    f = make_failure(platform, name, "provider_unavailable",
                                     "paid attempt already made for this URL within the dedupe TTL")
                    failures.append(f)
                    self._attempt(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h,
                                  ctx=ctx, outcome="skipped", category="provider_unavailable",
                                  budget_check="paid_marker_exists", est_usd=0)
                    continue

            attempted.add(name)
            self.trace.append(f"provider:{name}")
            t0 = time.monotonic()
            result: Optional[NormalizedMediaResult] = None
            failure: Optional[ProviderFailure] = None
            try:
                result = await prov.resolve(req, ctx)
            except ProviderFailureError as exc:
                failure = exc.failure
            except Exception as exc:  # noqa: BLE001
                failure = make_failure(platform, name, "unknown", f"{type(exc).__name__}: {exc}")
            latency = int((time.monotonic() - t0) * 1000)

            run = getattr(prov, "last_run", None)
            if prov.paid and reservation is not None and not (run and run.run_started):
                # Nothing was billed: let the next request for this URL try again.
                request_cache.release_paid_attempt(name, h)
            actual_usd, cost_source = None, "none"
            cost: Optional[dict] = None
            if reservation is not None:
                cost = budget_guard.reconcile(reservation, run.actual_cost_usd if run else None,
                                              bool(run and run.run_started))
                if run and run.run_started and run.actual_cost_usd is not None:
                    actual_usd, cost_source = cost["actual_cost_usd"], "actual"
                elif run and run.run_started:
                    actual_usd, cost_source = cost["estimated_cost_usd"], "estimated"
                else:
                    actual_usd, cost_source = 0.0, "none"
                budget_guard.check_spend_alerts(prov.budget_class)
                lease = getattr(run, "pool_lease", None) if run else None
                if lease is not None:
                    # Same recorded figure (floor at the estimate) on the token entry.
                    apify_pool.settle(lease, reservation.recorded_micros or 0,
                                      dispatched=bool(run.run_started))

            media: Optional[dict] = None
            if result is not None:
                result = result.model_copy(update={"watermark_state": watermark.gated_state(platform, name)})
                if settings.router_validate_media():
                    media = await media_validation.validate_media_url(result.primary_video_url(),
                                                                      platform=platform)
                    if media.get("media_check") == "unusable":
                        failure = make_failure(platform, name, "parse_failed",
                                               f"media url unusable: {media.get('media_check_reason')}")
                        result = None

            common = dict(request=req, platform=platform, provider_name=name, mode=prov.mode, h=h, ctx=ctx,
                          latency_ms=latency, est_usd=est_usd, actual_usd=actual_usd, cost_source=cost_source,
                          health_state=health.state, budget_check="reserved" if reservation else "n/a",
                          duration_missing=bool(run and run.duration_missing),
                          expiry=run.media_url_expiry if run else None, media=media, cost=cost,
                          pool_entry=getattr(run, "pool_entry_id", None) if run else None,
                          pool_refusals=(getattr(run, "pool_refusals", None) or None) if run else None)
            if reservation is not None:
                usable = (media or {}).get("usable_media_url")    # True / False / None = not checked
                cost_metrics.record_paid(platform, success=result is not None,
                                         usable=None if usable is None else bool(usable),
                                         recorded_micros=reservation.recorded_micros or 0)
            if prov.paid:
                rollout_guard.record_managed_outcome(
                    platform, name, ctx.origin, success=result is not None,
                    usable=(media or {}).get("usable_media_url"),
                    category=failure.category if failure else None)
            if result is not None:
                provider_health.record_success(name, platform)
                result = result.model_copy(update={"resolution_time_ms": latency, "cache_hit": False})
                self._attempt(outcome="success", result=result, **common)
                if cacheable:
                    request_cache.set_cached(result, req.operation, h, policy.cache_ttl_sec)
                return result

            provider_health.record_failure(name, platform, failure.category)
            self._attempt(outcome="failure", category=failure.category, **common)
            failures.append(failure)
            if failure.category not in policy.fallback_categories:
                raise ChinaAccessFailure(platform, failure.category, failures, stop_reason="not_fallback_eligible")

        # 11. aggregated failure
        if not failures:
            f = make_failure(platform, "router", "provider_unavailable", "no eligible provider")
            raise ChinaAccessFailure(platform, "provider_unavailable", [f], stop_reason="no_provider")
        raise ChinaAccessFailure(platform, failures[-1].category, failures, stop_reason="exhausted")
