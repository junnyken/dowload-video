"""
Benchmark harness (plan §13; docs/china-access/05-BENCHMARK-PLAN.md).

Fixture URLs come from config only (CHINA_ACCESS_BENCHMARK_<P>_URLS or the
JSON file in CHINA_ACCESS_BENCHMARK_FIXTURES_FILE) — none are hardcoded.
Every provider call goes through the router's guarded single-provider path,
so kill switches, budget reserve, the once-per-URL paid marker and health
apply exactly as in production. The cache is bypassed (real measurements).

Managed (paid) calls run only when include_managed=True AND the effective
managed mode is ≥ "benchmark" AND the budget reserve succeeds. After the first
budget_exceeded the run stops calling paid providers.

Phase 32B-2 Stage A: every successful attempt's media URL is checked with
media_validation (bounded range fetch + ffprobe; `usable_media_url` becomes
True / False / None = not_checked), and the summary carries per-route
aggregates plus a recommendation computed by `recommend()` (rules documented
there and in docs/china-access/05-BENCHMARK-PLAN.md).

CLI:  python -m app.services.china_platforms.benchmark_runner --platform douyin [--include-managed] [--max-urls 30]
      python -m app.services.china_platforms.benchmark_runner --platform douyin --report   (recompute last run)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from typing import Optional

from app.services.china_platforms import media_validation, registry, settings, watermark
from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure, redact
from app.services.china_platforms.normalized_models import ChinaResolveRequest, RequestContext
from app.services.china_platforms.provider_router import ProviderRouter

CSV_FIELDS = [
    "platform", "operation", "provider", "route_mode", "request_id", "case", "canonical_url_hash",
    "outcome", "normalized_failure_category", "latency_ms", "metadata_success", "usable_media_url",
    "media_url_expiry_if_known", "watermark_state", "proxy_bytes_if_used", "estimated_cost_usd",
    "actual_cost_usd", "cost_source", "retry_count", "duration_missing",
    # 32B-2 Stage A (appended; the columns above keep their order)
    "media_url_present", "media_check", "media_check_reason", "media_url_http_status",
    "media_content_type", "media_bytes_read", "media_duration_sec", "cost_delta_usd", "cost_flag",
]

LAST_SUMMARY_KEY = "china:benchmark:last:{platform}"
LOCK_KEY = "china:benchmark:lock:{platform}"
LAST_ATTEMPTS_KEY = "china:benchmark:last_attempts:{platform}"

RECOMMENDATIONS = ("promote_canary_admin", "keep_benchmark", "native_only", "reject")
_VALIDATED = ("usable", "unusable", "not_checked")


def load_fixtures(platform: str) -> list[dict]:
    out: list[dict] = [{"url": u, "case": "env"} for u in settings.benchmark_env_urls(platform)]
    path = settings.benchmark_fixtures_file()
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for row in (data.get(platform) or []):
            if isinstance(row, str):
                out.append({"url": row, "case": "file"})
            elif isinstance(row, dict) and row.get("url"):
                out.append({"url": row["url"], "case": row.get("case") or "file"})
    seen, uniq = set(), []
    for row in out:
        if row["url"] not in seen:
            seen.add(row["url"])
            uniq.append(row)
    return uniq


def _pct(values: list[int], p: float) -> Optional[int]:
    if not values:
        return None
    v = sorted(values)
    idx = min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))
    return v[idx]


def summarize(attempts: list[dict], *, paid_routes: Optional[set] = None,
              watermark_reviews: Optional[dict] = None) -> dict:
    """Per-route aggregates. Rates use non-skipped attempts as the denominator.

    usable_media_rate        attempts whose media check said "usable" / attempts
                             (None when no success was validated at all)
    cost_per_usable_success  total cost / usable successes
    watermark_distribution   manual reviews of THIS run's successful items
                             (watermark_free | watermarked | unknown | not_reviewed)
    """
    paid_routes = paid_routes or set()
    watermark_reviews = watermark_reviews or {}
    out: dict = {}
    for a in attempts:
        s = out.setdefault(a["provider"], {
            "route_mode": "managed" if a["provider"] in paid_routes else (a.get("route_mode") or "native"),
            "attempts": 0, "successes": 0, "skipped": 0, "latencies": [], "cost_total_usd": 0.0,
            "error_mix": {}, "duration_missing": 0, "watermark_validated": 0,
            "media_usable": 0, "media_unusable": 0, "media_not_checked": 0, "media_reasons": {},
            "cost_flags": {}, "skip_mix": {}, "_hashes": [],
        })
        if a["outcome"] == "skipped":
            s["skipped"] += 1
            cat = a.get("normalized_failure_category") or a.get("budget_check_result") or "skipped"
            s["skip_mix"][cat] = s["skip_mix"].get(cat, 0) + 1
            continue
        s["attempts"] += 1
        s["latencies"].append(int(a.get("latency_ms") or 0))
        s["cost_total_usd"] += float(a.get("actual_cost_usd") or 0)
        if a.get("cost_flag"):
            s["cost_flags"][a["cost_flag"]] = s["cost_flags"].get(a["cost_flag"], 0) + 1
        if a["outcome"] == "success":
            s["successes"] += 1
            if a.get("canonical_url_hash"):
                s["_hashes"].append(a["canonical_url_hash"])
        else:
            cat = a.get("normalized_failure_category") or "unknown"
            s["error_mix"][cat] = s["error_mix"].get(cat, 0) + 1
        mc = a.get("media_check")
        if mc == "usable":
            s["media_usable"] += 1
        elif mc == "unusable":
            s["media_unusable"] += 1
        elif mc == "not_checked":
            s["media_not_checked"] += 1
        if mc in ("unusable", "not_checked") and a.get("media_check_reason"):
            r = a["media_check_reason"]
            s["media_reasons"][r] = s["media_reasons"].get(r, 0) + 1
        if a.get("duration_missing"):
            s["duration_missing"] += 1
    for name, s in out.items():
        lat = s.pop("latencies")
        hashes = s.pop("_hashes")
        n, ok, usable = s["attempts"], s["successes"], s["media_usable"]
        checked = s["media_usable"] + s["media_unusable"]
        s["sample_size"] = n
        s["success_rate"] = round(ok / n, 4) if n else None
        s["usable_media_rate"] = round(usable / n, 4) if n and checked else None
        s["p50_latency_ms"] = _pct(lat, 0.50)
        s["p95_latency_ms"] = _pct(lat, 0.95)
        s["cost_total_usd"] = round(s["cost_total_usd"], 6)
        s["cost_per_success_usd"] = round(s["cost_total_usd"] / ok, 6) if ok else None
        s["cost_per_attempt_usd"] = round(s["cost_total_usd"] / n, 6) if n else None
        s["cost_per_usable_success_usd"] = round(s["cost_total_usd"] / usable, 6) if usable else None
        s["failure_category_mix"] = dict(s["error_mix"])
        revs = watermark_reviews.get(name) or {}
        dist = {"watermark_free": 0, "watermarked": 0, "unknown": 0, "not_reviewed": 0}
        for h in hashes:
            st = (revs.get(h) or {}).get("state")
            dist[st if st in dist else "not_reviewed"] += 1
        s["watermark_distribution"] = dist
        s["watermark_validated"] = dist["watermark_free"] + dist["watermarked"]
    for name, s in out.items():
        s["recommendation"], s["recommendation_reasons"] = recommend(s, out)
    return out


def recommend(route: dict, all_routes: dict) -> tuple[str, list[str]]:
    """Explicit rules, evaluated top to bottom; the first that matches wins.

    Every route:
      R1  sample_size < CHINA_ACCESS_BENCH_MIN_SAMPLE (20)        → keep_benchmark
      R2  any success whose media was not validated
          (media_not_checked > 0, e.g. ffprobe missing)            → keep_benchmark
    Native route:
      N1  usable_media_rate ≥ PROMOTE (0.8)                        → native_only
      N2  usable_media_rate < REJECT (0.5)                         → reject
      N3  otherwise                                                → keep_benchmark
    Managed route:
      M1  usable_media_rate < REJECT (0.5)                         → reject
      M2  provider_timeout share ≥ MAX_TIMEOUT_SHARE (0.2)         → reject
      M3  cost_per_usable_success > MAX_COST_PER_USABLE ($0.01)    → reject
      M4  some native route already qualifies native_only          → native_only
      M5  any cost_flag (over_estimate / actual_missing)           → keep_benchmark
      M6  usable_media_rate ≥ PROMOTE (0.8) and ≥ best native
          usable rate + NATIVE_MARGIN (0.2)                        → promote_canary_admin
      M7  otherwise                                                → keep_benchmark
    """
    n = route.get("sample_size") or 0
    min_n = settings.bench_min_sample()
    if n < min_n:
        return "keep_benchmark", [f"R1 sample {n} < {min_n}"]
    if route.get("media_not_checked"):
        return "keep_benchmark", [f"R2 {route['media_not_checked']} success(es) with media not validated"]
    rate = route.get("usable_media_rate") or 0.0
    promote, reject = settings.bench_promote_usable_rate(), settings.bench_reject_usable_rate()
    if route.get("route_mode") != "managed":
        if rate >= promote:
            return "native_only", [f"N1 usable {rate:.2f} >= {promote:.2f}"]
        if rate < reject:
            return "reject", [f"N2 usable {rate:.2f} < {reject:.2f}"]
        return "keep_benchmark", ["N3 between thresholds"]
    if rate < reject:
        return "reject", [f"M1 usable {rate:.2f} < {reject:.2f}"]
    timeouts = (route.get("error_mix") or {}).get("provider_timeout", 0)
    if n and timeouts / n >= settings.bench_max_timeout_share():
        return "reject", [f"M2 timeouts {timeouts}/{n} >= {settings.bench_max_timeout_share():.2f}"]
    cpu = route.get("cost_per_usable_success_usd")
    if cpu is not None and cpu > settings.bench_max_cost_per_usable_usd():
        return "reject", [f"M3 cost/usable ${cpu:.4f} > ${settings.bench_max_cost_per_usable_usd():.4f}"]
    natives = [r for r in all_routes.values() if r.get("route_mode") != "managed"]
    for r in natives:
        if (r.get("sample_size") or 0) >= min_n and not r.get("media_not_checked") \
                and (r.get("usable_media_rate") or 0.0) >= promote:
            return "native_only", ["M4 a native route qualifies native_only"]
    if route.get("cost_flags"):
        return "keep_benchmark", [f"M5 cost flags {route['cost_flags']}"]
    best_native = max([(r.get("usable_media_rate") or 0.0) for r in natives] or [0.0])
    margin = settings.bench_native_margin()
    if rate >= promote and rate >= best_native + margin:
        return "promote_canary_admin", [f"M6 usable {rate:.2f} >= {promote:.2f} and >= native {best_native:.2f}+{margin:.2f}"]
    return "keep_benchmark", ["M7 below promotion thresholds"]


def overall_recommendation(summary: dict) -> dict:
    """Platform-level: native_only wins; else the best managed route's verdict
    (promote > keep_benchmark > reject); no managed route → keep_benchmark."""
    recs = {name: s.get("recommendation") for name, s in summary.items()}
    if any(s.get("route_mode") != "managed" and s.get("recommendation") == "native_only" for s in summary.values()):
        return {"recommendation": "native_only", "routes": recs}
    managed = [s.get("recommendation") for s in summary.values() if s.get("route_mode") == "managed"]
    for r in ("promote_canary_admin", "keep_benchmark", "reject"):
        if r in managed:
            return {"recommendation": r, "routes": recs}
    return {"recommendation": "keep_benchmark", "routes": recs}


def _sanitize(a: dict) -> dict:
    return {k: (redact(v) if isinstance(v, str) else v) for k, v in a.items()}


def _reviews_for(platform: str, providers) -> dict:
    return {p: watermark.reviews(platform, p) for p in providers}


def build_summary(platform: str, attempts: list[dict], paid_routes: set) -> dict:
    providers = {a["provider"] for a in attempts}
    summary = summarize(attempts, paid_routes=paid_routes, watermark_reviews=_reviews_for(platform, providers))
    return summary


def _write(platform: str, report: dict, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(out_dir, f"china_benchmark_{platform}_{stamp}")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    with open(base + ".csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for a in report["attempts"]:
            w.writerow(a)
    return {"json": os.path.basename(base + ".json"), "csv": os.path.basename(base + ".csv")}


async def run_benchmark(platform: str, *, include_managed: bool = False, max_urls: int = 30,
                        fixtures: Optional[list[dict]] = None, out_dir: Optional[str] = None,
                        write: bool = True, router_factory=ProviderRouter,
                        validate_media: Optional[bool] = None, media_validator=None) -> dict:
    policy = registry.get_policy(platform)
    if policy is None:
        raise ValueError(f"unknown platform {platform}")
    fixtures = (fixtures if fixtures is not None else load_fixtures(platform))[:max(0, int(max_urls))]
    order = registry.effective_order(policy, "single_media")
    adapter = registry.get_adapter(platform)
    paid_names = {n for n, p in (adapter.build_providers() if adapter else {}).items() if getattr(p, "paid", False)}
    validate = settings.benchmark_validate_media() if validate_media is None else validate_media
    validator = media_validator or media_validation.validate_media_url
    ctx = RequestContext(requester_key="admin", origin="benchmark", allow_managed_benchmark=include_managed)

    started = datetime.now(timezone.utc).isoformat()
    attempts: list[dict] = []
    managed_stopped = False
    for fx in fixtures:
        for name in order:
            paid = name in paid_names
            if paid and (not include_managed or managed_stopped):
                continue
            router = router_factory()
            req = ChinaResolveRequest(url=fx["url"], operation="single_media")
            result = None
            try:
                result = await router.resolve(req, ctx, use_cache=False, only_provider=name)
            except ChinaAccessFailure as exc:
                if paid and exc.category == "budget_exceeded":
                    managed_stopped = True
                if not router.attempts:
                    attempts.append({
                        "platform": platform, "operation": "single_media", "provider": name,
                        "case": fx.get("case"),
                        "route_mode": "managed" if paid else "native", "request_id": req.request_id,
                        "canonical_url_hash": None, "outcome": "skipped",
                        "normalized_failure_category": exc.category, "latency_ms": 0,
                        "metadata_success": False, "usable_media_url": False, "proxy_bytes_if_used": 0,
                        "estimated_cost_usd": 0.0, "actual_cost_usd": None, "cost_source": "none",
                        "retry_count": 0, "duration_missing": False, "watermark_state": "unknown",
                    })
            except AlreadyProcessing:
                pass
            if result is not None and router.attempts:
                last = router.attempts[-1]
                if validate and last.get("outcome") == "success" and last.get("media_check") not in _VALIDATED:
                    media = await validator(result.primary_video_url(), platform=platform)
                    for k in media_validation.RESULT_FIELDS:
                        if k == "media_url_expiry_if_known" and media.get(k) is None:
                            continue
                        last[k] = media.get(k)
            for a in router.attempts:
                a["case"] = fx.get("case")
                attempts.append(_sanitize(a))

    watermark.register_items(platform, [(a["provider"], a["canonical_url_hash"]) for a in attempts
                                        if a.get("outcome") == "success" and a.get("canonical_url_hash")])
    summary = build_summary(platform, attempts, paid_names)

    report = {
        "platform": platform,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "include_managed": include_managed,
        "fixture_count": len(fixtures),
        "media_validation": {"enabled": validate, "ffprobe_available": media_validation.ffprobe_available()},
        "attempts": attempts,
        "summary": summary,
        "overall": overall_recommendation(summary),
    }
    if write:
        report["files"] = _write(platform, report, out_dir or settings.benchmark_dir())
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        slim = {k: v for k, v in report.items() if k != "attempts"}
        r = get_redis()
        r.set(LAST_SUMMARY_KEY.format(platform=platform),
              json.dumps(slim, ensure_ascii=False, default=str), ex=30 * 86400)
        r.set(LAST_ATTEMPTS_KEY.format(platform=platform),
              json.dumps({"paid_routes": sorted(paid_names), "started_at": started, "attempts": attempts},
                         ensure_ascii=False, default=str), ex=30 * 86400)
    except Exception:  # noqa: BLE001
        pass
    return report


def report_from_last(platform: str) -> Optional[dict]:
    """Recompute the last run's aggregates with the CURRENT watermark reviews
    (reviews are made after the run). Attempts are the sanitized copies."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    raw = get_redis().get(LAST_ATTEMPTS_KEY.format(platform=platform))
    if not raw:
        return None
    data = json.loads(raw)
    attempts = data.get("attempts") or []
    summary = build_summary(platform, attempts, set(data.get("paid_routes") or []))
    return {"platform": platform, "started_at": data.get("started_at"), "attempt_count": len(attempts),
            "summary": summary, "overall": overall_recommendation(summary),
            "watermark_routes": {p: watermark.aggregate(platform, p) for p in summary}}


def _main() -> None:
    ap = argparse.ArgumentParser(description="China access benchmark (no paid calls unless --include-managed)")
    ap.add_argument("--platform", default="douyin")
    ap.add_argument("--include-managed", action="store_true")
    ap.add_argument("--max-urls", type=int, default=30)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", action="store_true", help="recompute the last run's report; no provider calls")
    args = ap.parse_args()
    if args.report:
        print(json.dumps(report_from_last(args.platform), ensure_ascii=False, indent=2, default=str))
        return
    report = asyncio.run(run_benchmark(args.platform, include_managed=args.include_managed,
                                       max_urls=args.max_urls, out_dir=args.out))
    print(json.dumps({k: v for k, v in report.items() if k != "attempts"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
