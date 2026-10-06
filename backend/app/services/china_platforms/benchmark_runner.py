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

CLI:  python -m app.services.china_platforms.benchmark_runner --platform douyin [--include-managed] [--max-urls 30]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from typing import Optional

from app.services.china_platforms import registry, settings
from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure
from app.services.china_platforms.normalized_models import ChinaResolveRequest, RequestContext
from app.services.china_platforms.provider_router import ProviderRouter

CSV_FIELDS = [
    "platform", "operation", "provider", "route_mode", "request_id", "case", "canonical_url_hash",
    "outcome", "normalized_failure_category", "latency_ms", "metadata_success", "usable_media_url",
    "media_url_expiry_if_known", "watermark_state", "proxy_bytes_if_used", "estimated_cost_usd",
    "actual_cost_usd", "cost_source", "retry_count", "duration_missing",
]

LAST_SUMMARY_KEY = "china:benchmark:last:{platform}"
LOCK_KEY = "china:benchmark:lock:{platform}"


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


def summarize(attempts: list[dict]) -> dict:
    out: dict = {}
    for a in attempts:
        s = out.setdefault(a["provider"], {
            "attempts": 0, "successes": 0, "skipped": 0, "latencies": [], "cost_total_usd": 0.0,
            "error_mix": {}, "duration_missing": 0, "watermark_validated": 0,
        })
        if a["outcome"] == "skipped":
            s["skipped"] += 1
            continue
        s["attempts"] += 1
        s["latencies"].append(int(a.get("latency_ms") or 0))
        s["cost_total_usd"] += float(a.get("actual_cost_usd") or 0)
        if a["outcome"] == "success":
            s["successes"] += 1
        else:
            cat = a.get("normalized_failure_category") or "unknown"
            s["error_mix"][cat] = s["error_mix"].get(cat, 0) + 1
        if a.get("duration_missing"):
            s["duration_missing"] += 1
    for s in out.values():
        lat = s.pop("latencies")
        n, ok = s["attempts"], s["successes"]
        s["success_rate"] = round(ok / n, 4) if n else None
        s["p50_latency_ms"] = _pct(lat, 0.50)
        s["p95_latency_ms"] = _pct(lat, 0.95)
        s["cost_total_usd"] = round(s["cost_total_usd"], 6)
        s["cost_per_success_usd"] = round(s["cost_total_usd"] / ok, 6) if ok else None
        s["cost_per_attempt_usd"] = round(s["cost_total_usd"] / n, 6) if n else None
    return out


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
                        write: bool = True, router_factory=ProviderRouter) -> dict:
    policy = registry.get_policy(platform)
    if policy is None:
        raise ValueError(f"unknown platform {platform}")
    fixtures = (fixtures if fixtures is not None else load_fixtures(platform))[:max(0, int(max_urls))]
    order = registry.effective_order(policy, "single_media")
    adapter = registry.get_adapter(platform)
    paid_names = {n for n, p in (adapter.build_providers() if adapter else {}).items() if getattr(p, "paid", False)}
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
            try:
                await router.resolve(req, ctx, use_cache=False, only_provider=name)
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
            for a in router.attempts:
                a["case"] = fx.get("case")
                attempts.append(a)

    report = {
        "platform": platform,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "include_managed": include_managed,
        "fixture_count": len(fixtures),
        "attempts": attempts,
        "summary": summarize(attempts),
    }
    if write:
        report["files"] = _write(platform, report, out_dir or settings.benchmark_dir())
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        slim = {k: v for k, v in report.items() if k != "attempts"}
        get_redis().set(LAST_SUMMARY_KEY.format(platform=platform),
                        json.dumps(slim, ensure_ascii=False, default=str), ex=30 * 86400)
    except Exception:  # noqa: BLE001
        pass
    return report


def _main() -> None:
    ap = argparse.ArgumentParser(description="China access benchmark (no paid calls unless --include-managed)")
    ap.add_argument("--platform", default="douyin")
    ap.add_argument("--include-managed", action="store_true")
    ap.add_argument("--max-urls", type=int, default=30)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = asyncio.run(run_benchmark(args.platform, include_managed=args.include_managed,
                                       max_urls=args.max_urls, out_dir=args.out))
    print(json.dumps({k: v for k, v in report.items() if k != "attempts"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
