"""
Regression tests for docker-compose.yml environment variable pass-through.

Services in docker-compose.yml declare an explicit `environment:` list without
`env_file:`. Any runtime secret or config variable omitted from `environment:`
never reaches the container under `docker compose`, while any `${VAR}` written
without a `:-` default causes Vibe Host's static compose scanner to block
deploys with `ENV_REQUIRED`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, Set

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
BACKEND_ROOT = REPO_ROOT / "backend"


def _load_compose() -> dict:
    with COMPOSE_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict) and "services" in data
    return data


def _env_map(service_cfg: dict) -> Dict[str, str]:
    raw = service_cfg.get("environment") or []
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    parsed: Dict[str, str] = {}
    for item in raw:
        entry = str(item)
        if "=" in entry:
            k, v = entry.split("=", 1)
            parsed[k.strip()] = v.strip()
        else:
            parsed[entry.strip()] = ""
    return parsed


def _nodes_import_flow_cleanup(nodes: list[ast.AST]) -> bool:
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.api.flow_cleanup" or alias.name.endswith(".flow_cleanup"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "app.api.flow_cleanup" or mod.endswith(".flow_cleanup"):
                return True
            if mod in ("app.api", "api"):
                for alias in node.names:
                    if alias.name == "flow_cleanup":
                        return True
    return False


def _module_imports_flow_cleanup(py_file: Path, top_level_only: bool = False) -> bool:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    if top_level_only:
        top_stmts = [
            stmt for stmt in tree.body if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        all_subnodes: list[ast.AST] = []
        for stmt in top_stmts:
            all_subnodes.extend(ast.walk(stmt))
        return _nodes_import_flow_cleanup(all_subnodes)
    return _nodes_import_flow_cleanup(list(ast.walk(tree)))


def _task_names_importing_flow_cleanup(py_file: Path) -> Set[str]:
    """Return Celery task names in `py_file` whose body (or local helpers they call) imports flow_cleanup."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    funcs: Dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    task_name_by_func: Dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and "task" in ast.unparse(dec.func):
                    tname = node.name
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            tname = kw.value.value
                    task_name_by_func[node.name] = tname

    direct_importers = {
        fname for fname, fnode in funcs.items() if _nodes_import_flow_cleanup(list(ast.walk(fnode)))
    }
    # Propagate 2 hops across module-local helper calls
    importers = set(direct_importers)
    for _ in range(2):
        for fname, fnode in funcs.items():
            called = {
                n.func.id
                for n in ast.walk(fnode)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if called & importers:
                importers.add(fname)

    return {task_name_by_func[fname] for fname in importers if fname in task_name_by_func}


def _celery_included_modules() -> list[str]:
    from app.core.celery_app import celery_app

    return list(celery_app.conf.include or [])


def _queues_for_command(cmd: str) -> Set[str]:
    match = re.search(r"-Q\s+([^\s]+)", cmd or "")
    if not match:
        return set()
    return {q.strip() for q in match.group(1).split(",") if q.strip()}


def _discover_services_importing_flow_cleanup(compose: dict) -> tuple[Set[str], Set[str]]:
    """
    Returns (startup_importers, runtime_importers) among docker-compose services
    built from ./backend by inspecting AST imports of app/main.py and Celery task modules.
    """
    startup_importers: Set[str] = set()
    runtime_importers: Set[str] = set()

    main_py = BACKEND_ROOT / "app" / "main.py"
    main_imports_at_startup = _module_imports_flow_cleanup(main_py, top_level_only=True)

    included_mods = _celery_included_modules()
    celery_top_level_imports = False
    queue_importers: Dict[str, Set[str]] = {}

    # Map task module -> queues its tasks run on (default queue in Celery is 'celery')
    from app.core.celery_app import celery_app

    routes = celery_app.conf.task_routes or {}
    for mod_name in included_mods:
        mod_path = BACKEND_ROOT / Path(*mod_name.split(".")).with_suffix(".py")
        if not mod_path.exists():
            continue
        if _module_imports_flow_cleanup(mod_path, top_level_only=True):
            celery_top_level_imports = True
        flow_tasks = _task_names_importing_flow_cleanup(mod_path)
        if flow_tasks:
            mod_queues: Set[str] = set()
            for tname in flow_tasks:
                route_cfg = routes.get(tname) or {}
                mod_queues.add(route_cfg.get("queue", "celery"))
            queue_importers[mod_name] = mod_queues

    flow_queues: Set[str] = set()
    for qs in queue_importers.values():
        flow_queues.update(qs)

    for svc_name, svc_cfg in (compose.get("services") or {}).items():
        build = svc_cfg.get("build") or {}
        context = build if isinstance(build, str) else build.get("context", "")
        if context not in ("./backend", "backend"):
            continue

        cmd = svc_cfg.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)

        if not cmd:
            # Uses backend/Dockerfile default CMD (./docker-entrypoint.sh -> uvicorn app.main:app + embedded worker)
            if main_imports_at_startup:
                startup_importers.add(svc_name)
            entrypoint = (BACKEND_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
            for q in _queues_for_command(entrypoint):
                if q in flow_queues:
                    runtime_importers.add(svc_name)
        elif "celery" in cmd and "worker" in cmd:
            if celery_top_level_imports:
                startup_importers.add(svc_name)
            svc_queues = _queues_for_command(cmd)
            if svc_queues & flow_queues:
                runtime_importers.add(svc_name)
        elif "celery" in cmd and "beat" in cmd:
            if celery_top_level_imports:
                startup_importers.add(svc_name)

    return startup_importers, runtime_importers


def test_every_service_importing_flow_cleanup_passes_signing_secret():
    compose = _load_compose()
    services = compose["services"]

    startup_importers, runtime_importers = _discover_services_importing_flow_cleanup(compose)
    assert "backend" in startup_importers
    assert {"backend", "celery-media", "celery-light"}.issubset(startup_importers | runtime_importers)

    all_flow_services = startup_importers | runtime_importers
    for svc_name in sorted(all_flow_services):
        env = _env_map(services[svc_name])
        assert "FLOW_URL_SIGNING_SECRET" in env, (
            f"Service '{svc_name}' imports app.api.flow_cleanup but is missing "
            f"FLOW_URL_SIGNING_SECRET in docker-compose.yml environment"
        )
        assert "${FLOW_URL_SIGNING_SECRET" in env["FLOW_URL_SIGNING_SECRET"], (
            f"Service '{svc_name}' must interpolate FLOW_URL_SIGNING_SECRET from host environment"
        )
        assert env["FLOW_URL_SIGNING_SECRET"] == "${FLOW_URL_SIGNING_SECRET}", (
            f"Service '{svc_name}' must require FLOW_URL_SIGNING_SECRET without a fallback default"
        )


def test_only_expected_mandatory_vars_lack_defaults_in_compose():
    """
    Vibe Host scans docker-compose.yml and raises ENV_REQUIRED for any `${VAR}`
    without a `:-` default. Only true secrets/endpoints may omit defaults; all
    optional FLOW_* and TELEGRAM_* knobs must use `${VAR:-<default matching code>}`.
    """
    raw_text = COMPOSE_PATH.read_text(encoding="utf-8")
    # Match ${VAR_NAME} or ${VAR_NAME:-default} (ignore $$HOSTNAME escaped shell vars)
    exprs = re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-)([^}]*))?\}", raw_text)
    assert exprs, "Expected variable interpolations in docker-compose.yml"

    no_default_vars = {name for name, sep, _ in exprs if sep != ":-"}
    expected_mandatory = {
        "FLOW_URL_SIGNING_SECRET",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "TELEGRAM_DIST_BOT_TOKEN",
        "VITE_SUPABASE_URL",
        "VITE_SUPABASE_ANON_KEY",
    }
    assert no_default_vars == expected_mandatory, (
        f"Unexpected mandatory vars without defaults in docker-compose.yml: "
        f"extra={sorted(no_default_vars - expected_mandatory)}, "
        f"missing={sorted(expected_mandatory - no_default_vars)}"
    )


def test_compose_defaults_match_code_and_do_not_crash_startup(monkeypatch):
    """
    Ensure every `${VAR:-default}` on backend / worker services evaluates to a
    valid value (never an empty string on int()/float()-parsed FLOW_* vars) so
    importing app.api.flow_cleanup in production mode succeeds when optional
    vars are unset on the host.
    """
    import importlib
    import app.api.flow_cleanup as fc

    compose = _load_compose()
    services = compose["services"]

    mandatory_host_env = {
        "FLOW_URL_SIGNING_SECRET": "a" * 64,
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test-key",
    }

    def _resolve_expr(val: str) -> str:
        def _sub(match: re.Match) -> str:
            var_name = match.group(1)
            has_def = match.group(2) == ":-"
            default_val = match.group(3) or ""
            host_val = mandatory_host_env.get(var_name, "")
            if host_val:
                return host_val
            if has_def:
                return default_val
            raise AssertionError(f"Unset mandatory var ${{{var_name}}} in compose simulation")

        return re.sub(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-)([^}]*))?\}", _sub, val)

    for svc_name in ("backend", "celery-light", "celery-media"):
        env_cfg = _env_map(services[svc_name])
        with monkeypatch.context() as mp:
            # Clear all FLOW_* and TELEGRAM_* from test process first
            for k in list(fc.os.environ.keys()):
                if k.startswith("FLOW_") or k.startswith("TELEGRAM_"):
                    mp.delenv(k, raising=False)
            for k, raw_v in env_cfg.items():
                mp.setenv(k, _resolve_expr(raw_v))
            reloaded = importlib.reload(fc)
            assert reloaded._WORKER_CONCURRENCY == 2
            assert reloaded._DAILY_USER_QUOTA == 20
            assert reloaded._JOB_TIMEOUT_SEC == 300
            assert reloaded._USER_LOCK_TTL_SEC == 1020
            assert reloaded._FLOW_TTL_SEC == 1200

    # Restore module with test-suite environment
    importlib.reload(fc)


def test_flow_runtime_and_telegram_alert_vars_passed_to_app_services():
    compose = _load_compose()
    services = compose["services"]

    # Manual-review alerts (payments/webhooks) run in backend; operational alerts run in workers
    for svc_name in ("backend", "celery", "celery-light", "celery-media", "celery-analysis"):
        env = _env_map(services[svc_name])
        assert "TELEGRAM_BOT_TOKEN" in env, f"{svc_name} missing TELEGRAM_BOT_TOKEN"
        assert "TELEGRAM_CHAT_ID" in env, f"{svc_name} missing TELEGRAM_CHAT_ID"

    required_flow_runtime_vars = {
        "FLOW_URL_SIGNING_SECRET",
        "FLOW_INPAINT_WORKER_CONCURRENCY",
        "FLOW_DAILY_USER_QUOTA",
        "FLOW_MAX_USER_CONCURRENT_JOBS",
        "FLOW_JOB_TIMEOUT_SEC",
        "FLOW_TRACK_TIMEOUT_SEC",
        "FLOW_PREVIEW_CLIP_TIMEOUT_SEC",
        "FLOW_SEC_PER_MPX_FRAME",
        "FLOW_CLEANUP_TTL_SEC",
        "FLOW_SIGNED_URL_TTL_SEC",
        "FLOW_USER_LOCK_TTL_SEC",
        "FLOW_MAX_VIDEO_WIDTH",
        "FLOW_MAX_VIDEO_HEIGHT",
        "FLOW_MAX_VIDEO_PIXELS",
        "FLOW_MAX_DURATION_SEC",
        "FLOW_MAX_INPAINT_DURATION_SEC",
        "FLOW_MAX_RETRY_COUNT",
        "FLOW_MAX_KEYFRAME_CACHE_FILES",
        "FLOW_REDIS_URL",
        "FLOW_REDIS_NAMESPACE",
        "FLOW_CLEANUP_ALLOW_INPROCESS",
    }
    for svc_name in ("backend", "celery-media"):
        env = _env_map(services[svc_name])
        missing = required_flow_runtime_vars - set(env.keys())
        assert not missing, f"{svc_name} missing runtime FLOW_* vars: {sorted(missing)}"

    light_env = _env_map(services["celery-light"])
    assert "FLOW_CLEANUP_TTL_SEC" in light_env
    assert "ENV" in light_env
