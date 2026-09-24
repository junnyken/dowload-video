#!/usr/bin/env python3
"""
Find code that exists, imports cleanly, and never runs.

Why this is a script and not a one-off audit
--------------------------------------------
This repo has shipped both of these defects for real, and neither was visible
to tests, to the type checker, or to a reading of the file that contains them:

  • DashboardContent imports LifecycleBanner and calls useLifecycleBanners to
    decide which nudge is due, then never renders <LifecycleBanner>. The whole
    lifecycle nudge system — upgrade prompts, extension prompts, Telegram
    prompts — has never been shown to anyone. The bundler tree-shakes it, so
    even the built output looks consistent.

  • celery_app.py routed trim_video_task, create_gif_task, inpaint_logo_task,
    watermark_task, merge_audio_video_task and render_video_task to a 'media'
    queue while none of those functions existed anywhere. The queue had no
    producer, and the ffmpeg work ran inline in the request handler instead.
    (Recorded in commit 0f1f831.)

Both are the same shape: one end of a connection exists and the other does not,
and nothing in the toolchain objects. Checking it by hand finds it once;
checking it with a script finds it every time.

    python3 scripts/inventory-dead-wiring.py [--json]

Exit code is 0 always — this reports, it does not gate. Judgement about whether
a finding is dead code or work in progress belongs to a person.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
BACKEND = ROOT / "backend" / "app"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ── A. React components imported but never rendered ──────────────────

def unrendered_components() -> list[dict]:
    """A default-exported component that something imports but nothing uses as
    a JSX element. The import keeps the file alive to a reader and dead to the
    browser."""
    out = []
    sources = {p: _read(p) for p in FRONTEND.rglob("*.jsx")}
    sources.update({p: _read(p) for p in FRONTEND.rglob("*.tsx")})
    all_text = "\n".join(sources.values())

    for path, text in sources.items():
        for m in re.finditer(r"^import\s+([A-Z][A-Za-z0-9_]*)\s+from\s+['\"]([^'\"]+)['\"]", text, re.M):
            name, spec = m.group(1), m.group(2)
            if not spec.startswith("."):
                continue  # third-party
            # Rendered anywhere at all, including behind a rename?
            if re.search(rf"<{re.escape(name)}[\s/>]", all_text):
                continue
            # Used as a value (passed as a prop, put in a map of components)?
            # The component's OWN file always mentions its name — excluding only
            # the importing file let every case through, including the known one
            # (LifecycleBanner), which is why _self_check pins it.
            defining = _resolve(path, spec)
            others = "\n".join(
                t for q, t in sources.items() if q != path and q != defining
            )
            if re.search(rf"\b{re.escape(name)}\b", others):
                continue
            out.append({
                "kind": "component_imported_never_rendered",
                "file": str(path.relative_to(ROOT)),
                "name": name,
                "note": "imported here, never used as a JSX element anywhere",
            })
    return out


# ── B. Celery tasks routed or defined with no producer ───────────────

def orphan_celery_tasks() -> list[dict]:
    celery_app = _read(BACKEND / "core" / "celery_app.py")
    task_text = "\n".join(_read(p) for p in (BACKEND / "tasks").rglob("*.py"))
    app_text = "\n".join(_read(p) for p in BACKEND.rglob("*.py"))

    routed = set(re.findall(r"'([a-z_]+)':\s*\{'queue'", celery_app))
    scheduled = set(re.findall(r"'task':\s*'([a-z_]+)'", celery_app))
    defined = set(re.findall(r"@celery_app\.task\(\s*name=[\"']([a-z_]+)[\"']", task_text))

    out = []
    for name in sorted(routed - defined):
        out.append({
            "kind": "task_routed_but_not_defined",
            "name": name,
            "note": "celery_app routes this to a queue, but no function declares it",
        })
    for name in sorted(defined - scheduled):
        # A task with no beat entry is fine if something calls .delay() on it.
        if re.search(rf"\b{re.escape(name)}\b\s*\.\s*delay\b", app_text):
            continue
        if re.search(rf"\b{re.escape(name)}\b\s*\.\s*apply_async\b", app_text):
            continue
        # Passed as a value rather than called directly — media_tasks does
        # `run_on_worker(watermark_task, ...)`, which reaches `.delay()` through
        # a parameter. Reporting those as orphans was a false positive that
        # _self_check now pins.
        outside = "\n".join(
            _read(q) for q in BACKEND.rglob("*.py") if "tasks" not in q.parts
        )
        if re.search(rf"\b{re.escape(name)}\b", outside):
            continue
        out.append({
            "kind": "task_defined_but_never_produced",
            "name": name,
            "note": "defined, but nothing schedules it and nothing calls .delay()/.apply_async()",
        })
    return out


# ── C. Admin/user pages that no route can reach ──────────────────────

def unreachable_pages() -> list[dict]:
    routes_text = "\n".join(
        _read(p) for p in FRONTEND.rglob("*.tsx") if "Routes" in p.name or "App" in p.name
    ) + "\n".join(
        _read(p) for p in FRONTEND.rglob("*.jsx") if "Routes" in p.name or "App" in p.name
    )
    out = []
    for page in sorted(list(FRONTEND.rglob("pages/*.jsx")) + list(FRONTEND.rglob("pages/*.tsx"))):
        stem = page.stem
        if stem in routes_text:
            continue
        out.append({
            "kind": "page_with_no_route",
            "file": str(page.relative_to(ROOT)),
            "name": stem,
            "note": "no Routes/App file mentions it",
        })
    return out


# ── D. Tracked analytics events with no call site ────────────────────

def unfired_events() -> list[dict]:
    track = _read(FRONTEND / "utils" / "trackEvent.js")
    consts = re.findall(r"^\s*([A-Z_]+):\s*'([a-z_]+)'", track, re.M)
    if not consts:
        return []
    callers = "\n".join(
        _read(p) for p in list(FRONTEND.rglob("*.jsx")) + list(FRONTEND.rglob("*.js"))
        if p.name != "trackEvent.js"
    )
    return [
        {"kind": "event_defined_but_never_fired", "name": value,
         "note": f"EVENT.{const} has no call site"}
        for const, value in consts
        if f"EVENT.{const}" not in callers
    ]


def _resolve(importer: Path, spec: str) -> Path | None:
    """Resolve a relative import to the file it points at."""
    base = (importer.parent / spec).resolve()
    for cand in (base, base.with_suffix(".jsx"), base.with_suffix(".tsx"),
                 base / "index.jsx", base / "index.tsx"):
        if cand.is_file():
            return cand
    return None


def _self_check(report: dict) -> list[str]:
    """Hold the scan to cases whose answer is already known.

    A scanner that reports nothing looks identical to a clean repo, so it needs
    at least one finding it MUST produce and one it MUST NOT. Both of these were
    real bugs in the scan itself before they were pinned here.
    """
    problems = []
    comp = {f.get("name") for f in report.get("Component imported but never rendered", [])}
    tasks = {f.get("name") for f in report.get("Celery task with no producer", [])}

    if "LifecycleBanner" not in comp:
        problems.append(
            "MISS: LifecycleBanner is imported by DashboardContent and never "
            "rendered, but the scan did not report it — the check is too lenient"
        )
    for name in ("watermark_task", "merge_audio_video_task"):
        if name in tasks:
            problems.append(
                f"FALSE POSITIVE: {name} is produced via run_on_worker(), which "
                "reaches .delay() through a parameter — the scan should not flag it"
            )
    return problems


CHECKS = [
    ("Component imported but never rendered", unrendered_components),
    ("Celery task with no producer",          orphan_celery_tasks),
    ("Page no route can reach",               unreachable_pages),
    ("Analytics event never fired",           unfired_events),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = {}
    for label, fn in CHECKS:
        try:
            report[label] = fn()
        except Exception as exc:
            report[label] = [{"kind": "check_failed", "note": f"{type(exc).__name__}: {exc}"}]

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    problems = _self_check(report)
    if problems:
        print("⚠️  BỘ QUÉT TỰ KIỂM THẤT BẠI — kết quả dưới đây KHÔNG đáng tin:\n")
        for pr in problems:
            print(f"    {pr}")
        print()

    total = sum(len(v) for v in report.values())
    print(f"Dead-wiring inventory — {total} finding(s)\n")
    for label, findings in report.items():
        print(f"{label}: {len(findings)}")
        for f in findings:
            who = f.get("name") or f.get("file") or "?"
            where = f" ({f['file']})" if f.get("file") and f.get("name") else ""
            print(f"    • {who}{where}")
            print(f"      {f['note']}")
        print()

    print("Reports only — a finding may be work in progress. Check before deleting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
