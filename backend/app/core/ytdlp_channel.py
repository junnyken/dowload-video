"""
yt-dlp channel — try a newer yt-dlp safely, keep it, roll it back.
==================================================================
Added 2026-10-19. When a platform changes its page, the fix is almost always
a newer yt-dlp — often only in a nightly for weeks. Before this:

  * the daily `pip install --upgrade yt-dlp` ran as appuser against a
    site-packages owned by root (Dockerfile installs as root), so it could
    never write and only printed "pip upgrade failed";
  * even when it could, uvicorn (/fetch-link) kept the yt-dlp it had already
    imported, and Celery's pool children are forked from a parent holding the
    old one — nothing ran the new version until the next deploy;
  * nightlies were never considered, and nothing tested a version first.

How it works now
----------------
OVERRIDE_DIR (/app/ytdlp_override, owned by appuser) is first on PYTHONPATH
(docker-entrypoint.sh), so a yt-dlp installed there with `pip --target`
shadows the image's copy. A candidate version is installed into a sibling
directory and judged by running the app's own platform probe in a SUBPROCESS
whose PYTHONPATH starts with that directory — the real extraction path,
the candidate yt-dlp, nothing in the running service touched. It is promoted
only if it fixes a broken platform (or is the daily stable update) AND every
platform that passes today still passes. Promotion swaps the directory
(previous kept for rollback), records the version in Redis — the entrypoint
reinstalls it after a redeploy (`python -m app.core.ytdlp_channel boot`) —
and restarts the processes: SIGHUP to uvicorn's supervisor (fresh worker
processes), SIGTERM to the Celery worker (the entrypoint loop starts it again).

Everything here fails safe: any error leaves the running version in place.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from typing import Dict, Iterable, List, Optional

OVERRIDE_DIR = os.getenv("YTDLP_OVERRIDE_DIR", "/app/ytdlp_override")
STATE_KEY = "ytdlp:channel"            # json: pin + history
LOCK_KEY = "ytdlp:channel:lock"
LOCK_TTL = 45 * 60
HISTORY_MAX = 30
PIP_TIMEOUT = 240
PROBE_TIMEOUT = 150                    # per platform, inside the subprocess run
# Probed for "nothing that works today breaks": the most-used platforms whose
# probe is currently ok. Bounded so one run stays within a few minutes.
REGRESSION_SET = ("youtube", "tiktok", "douyin", "facebook", "instagram", "twitter",
                  "threads", "bilibili", "soundcloud")
PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


# ── small helpers ─────────────────────────────────────────────────────

def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _v(s: Optional[str]):
    from packaging.version import Version  # noqa: PLC0415
    try:
        return Version(str(s))
    except Exception:
        return None


def same(a: Optional[str], b: Optional[str]) -> bool:
    """Same release, ignoring PyPI's .devN suffix: a nightly is published as
    2026.10.10.233141.dev0 but its yt_dlp/version.py says 2026.10.10.233141
    (found installing the real nightly on 2026-10-19)."""
    import re  # noqa: PLC0415
    norm = lambda s: re.sub(r"\.dev\d+$", "", str(s or "").strip())  # noqa: E731
    return bool(a and b and norm(a) == norm(b))


def newer(a: Optional[str], b: Optional[str]) -> bool:
    """a > b, both yt-dlp versions (2026.10.10.233141.dev0 style allowed).
    The same release with and without .devN is not newer either way."""
    if same(a, b):
        return False
    va, vb = _v(a), _v(b)
    return bool(va and vb and va > vb)


def active_version() -> Optional[str]:
    """The yt-dlp this process imports (override dir first on sys.path)."""
    try:
        from yt_dlp.version import __version__  # noqa: PLC0415
        return __version__
    except Exception:
        return None


def dir_version(path: str) -> Optional[str]:
    try:
        with open(os.path.join(path, "yt_dlp", "version.py"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        return None
    return None


def image_version() -> Optional[str]:
    """The copy baked into the image (ignoring the override dir)."""
    env = _env_without_override()
    try:
        out = subprocess.run([sys.executable, "-c", "from yt_dlp.version import __version__ as v;print(v)"],
                             capture_output=True, text=True, timeout=60, env=env)
        return out.stdout.strip() or None
    except Exception:
        return None


def _env_without_override(extra_first: Optional[str] = None) -> dict:
    env = dict(os.environ)
    parts = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep)
             if p and os.path.abspath(p) != os.path.abspath(OVERRIDE_DIR)]
    if extra_first:
        parts.insert(0, extra_first)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def latest_versions(timeout: int = 20) -> Dict[str, Optional[str]]:
    """{'stable': x, 'nightly': y} from PyPI (nightlies are .devN releases)."""
    out: Dict[str, Optional[str]] = {"stable": None, "nightly": None}
    try:
        req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "vidgrab-ytdlp-channel"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        out["stable"] = data.get("info", {}).get("version")
        best = None
        for ver, files in (data.get("releases") or {}).items():
            if not files or "dev" not in ver:
                continue
            if best is None or newer(ver, best):
                best = ver
        out["nightly"] = best
    except Exception:
        pass
    return out


# ── state ─────────────────────────────────────────────────────────────

def get_state() -> dict:
    try:
        raw = _r().get(STATE_KEY)
        st = json.loads(raw.decode() if isinstance(raw, bytes) else raw) if raw else {}
    except Exception:
        st = {}
    st.setdefault("pinned", None)          # {"version", "source", "at"} or None
    st.setdefault("history", [])
    return st


def _save_state(st: dict) -> None:
    try:
        st["history"] = st.get("history", [])[-HISTORY_MAX:]
        _r().set(STATE_KEY, json.dumps(st))
    except Exception:
        pass


def _log(action: str, **fields) -> dict:
    st = get_state()
    entry = {"at": int(time.time()), "action": action, **fields}
    st["history"].append(entry)
    _save_state(st)
    return entry


def status() -> dict:
    """For the admin: what runs, what is pinned, what exists, what happened."""
    st = get_state()
    return {
        "active": active_version(),
        "override": dir_version(OVERRIDE_DIR),
        "previous": dir_version(OVERRIDE_DIR + ".prev"),
        "pinned": st.get("pinned"),
        "latest": latest_versions(),
        "history": list(reversed(st.get("history", [])))[:HISTORY_MAX],
    }


# ── install / probe / promote ─────────────────────────────────────────

def install(version: str, target: str) -> Optional[str]:
    """pip-install yt-dlp==version into `target` (replaced). Returns an
    error string, None on success. --no-deps: the image already has yt-dlp's
    dependencies (curl_cffi is pinned there on purpose)."""
    shutil.rmtree(target, ignore_errors=True)
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-cache-dir",
             "--disable-pip-version-check", "--target", target, f"yt-dlp=={version}"],
            capture_output=True, text=True, timeout=PIP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "pip timeout"
    except Exception as exc:
        return f"pip error: {exc}"
    if p.returncode != 0:
        return (p.stderr or p.stdout or "pip failed").strip()[-300:]
    got = dir_version(target)
    if not same(got, version):
        return f"installed {got!r}, wanted {version}"
    return None


def probe_with(path: Optional[str], platforms: Iterable[str]) -> Dict[str, dict]:
    """Run the app's platform probe for `platforms` in a subprocess that
    imports yt-dlp from `path` (None = the image copy). {platform: {ok, reason}}."""
    plats = [p for p in platforms]
    if not plats:
        return {}
    env = _env_without_override(path)
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        p = subprocess.run([sys.executable, "-m", "app.core.ytdlp_channel", "probe", *plats],
                           capture_output=True, text=True, env=env, cwd=cwd,
                           timeout=PROBE_TIMEOUT * len(plats) + 60)
        for line in reversed((p.stdout or "").splitlines()):
            if line.startswith("{"):
                return json.loads(line)
        return {pl: {"ok": False, "reason": f"probe run gave no result: {(p.stderr or '')[-200:]}"}
                for pl in plats}
    except subprocess.TimeoutExpired:
        return {pl: {"ok": False, "reason": "probe run timed out"} for pl in plats}
    except Exception as exc:
        return {pl: {"ok": False, "reason": f"probe run error: {exc}"} for pl in plats}


def _probe_main(platforms: List[str]) -> None:
    """Subprocess side of probe_with: print one JSON line."""
    from app.core import platform_probe as pp  # noqa: PLC0415
    from yt_dlp.version import __version__  # noqa: PLC0415
    targets = pp.get_targets()
    out = {"_version": {"ok": True, "reason": __version__}}
    for pl in platforms:
        url = targets.get(pl)
        if not url:
            out[pl] = {"ok": False, "reason": "no_target"}
            continue
        res = pp.probe_once(url, timeout=PROBE_TIMEOUT, quality=pp.probe_quality(pl))
        out[pl] = {"ok": bool(res.get("ok")), "reason": res.get("reason")}
    print(json.dumps(out))


def currently_ok_platforms() -> List[str]:
    """Platforms of REGRESSION_SET whose live probe is ok right now."""
    try:
        from app.core import platform_probe as pp  # noqa: PLC0415
        states = pp.get_probe_states()
        return [p for p in REGRESSION_SET if (states.get(p) or {}).get("status") == pp.OK]
    except Exception:
        return []


def promote(candidate_dir: str, version: str, source: str, reason: str) -> None:
    prev = OVERRIDE_DIR + ".prev"
    shutil.rmtree(prev, ignore_errors=True)
    if os.path.isdir(OVERRIDE_DIR) and dir_version(OVERRIDE_DIR):
        os.replace(OVERRIDE_DIR, prev)
    else:
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    os.replace(candidate_dir, OVERRIDE_DIR)
    st = get_state()
    st["pinned"] = {"version": version, "source": source, "at": int(time.time())}
    _save_state(st)
    _log("promoted", version=version, source=source, reason=reason[:200])


def rollback(reason: str = "admin") -> dict:
    """Back to the previous override, or to the image copy when none."""
    prev = OVERRIDE_DIR + ".prev"
    before = dir_version(OVERRIDE_DIR)
    if os.path.isdir(prev) and dir_version(prev):
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        os.replace(prev, OVERRIDE_DIR)
        now = dir_version(OVERRIDE_DIR)
        st = get_state()
        st["pinned"] = {"version": now, "source": "rollback", "at": int(time.time())}
        _save_state(st)
    else:
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        st = get_state()
        st["pinned"] = None
        _save_state(st)
        now = image_version()
    entry = _log("rolled_back", **{"from": before, "to": now, "reason": reason[:200]})
    restart_processes()
    return entry


# ── process restart ───────────────────────────────────────────────────

def _procs() -> List[dict]:
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            out.append({"pid": int(pid), "ppid": ppid, "argv": argv, "cmd": " ".join(argv)})
        except Exception:
            continue
    return out


def restart_processes() -> Dict[str, List[int]]:
    """SIGHUP the uvicorn supervisor (it respawns its workers — fresh
    interpreters, so they import the new yt-dlp) and SIGTERM the Celery
    worker's main process (warm shutdown: running tasks finish; the
    entrypoint's loop starts it again). Never signals itself."""
    procs = _procs()
    by_pid = {p["pid"]: p for p in procs}
    me = os.getpid()
    done: Dict[str, List[int]] = {"uvicorn": [], "celery": []}

    def program(p) -> List[str]:
        """argv of the PROGRAM itself: [uvicorn, ...] or [python, -m, uvicorn,
        ...] → from the uvicorn/celery word on; [] for anything else. A
        matching substring is not enough: the entrypoint's `sh -c "uvicorn
        app.main:app ..."` and its celery `while true` loop contain the same
        words, and SIGHUP to that shell would take the container down (the
        first live run of this function signalled the calling bash)."""
        argv = p.get("argv") or p.get("cmd", "").split()
        if not argv:
            return []
        base = os.path.basename(argv[0])
        if base in ("uvicorn", "celery"):
            return [base] + argv[1:]
        if base.startswith("python"):
            rest = argv[1:]
            if rest[:1] == ["-m"]:
                rest = rest[1:]
            if rest and os.path.basename(rest[0]) in ("uvicorn", "celery"):
                return [os.path.basename(rest[0])] + rest[1:]
        return []

    def is_uvicorn(p):
        a = program(p)
        return a[:1] == ["uvicorn"] and "app.main:app" in a

    def is_celery_worker(p):
        a = program(p)
        return a[:1] == ["celery"] and "worker" in a

    for p in procs:
        parent = by_pid.get(p["ppid"])
        if is_uvicorn(p) and not (parent and is_uvicorn(parent)):
            try:
                os.kill(p["pid"], signal.SIGHUP)
                done["uvicorn"].append(p["pid"])
            except Exception:
                pass
        elif is_celery_worker(p) and not (parent and is_celery_worker(parent)) and p["pid"] != me:
            try:
                os.kill(p["pid"], signal.SIGTERM)
                done["celery"].append(p["pid"])
            except Exception:
                pass
    _log("restarted", uvicorn=done["uvicorn"], celery=done["celery"])
    return done


# ── the two entry points the tasks call ───────────────────────────────

def _lock() -> bool:
    try:
        return bool(_r().set(LOCK_KEY, str(int(time.time())), nx=True, ex=LOCK_TTL))
    except Exception:
        return False


def _unlock() -> None:
    try:
        _r().delete(LOCK_KEY)
    except Exception:
        pass


def try_candidates(broken: List[str], candidates: List[tuple], reason: str) -> dict:
    """Install each (version, source) in turn; promote the first that fixes
    at least one of `broken` (any, when `broken` is empty) without breaking a
    platform that passes today. Returns a summary dict."""
    keep = [p for p in currently_ok_platforms() if p not in broken]
    tried = []
    for version, source in candidates:
        cand = f"{OVERRIDE_DIR}.cand"
        err = install(version, cand)
        if err:
            tried.append({"version": version, "source": source, "result": "install_failed", "detail": err[:200]})
            continue
        res = probe_with(cand, list(broken) + keep)
        fixed = [p for p in broken if res.get(p, {}).get("ok")]
        regressed = [p for p in keep if not res.get(p, {}).get("ok")]
        row = {"version": version, "source": source, "fixed": fixed, "regressed": regressed,
               "still_broken": [p for p in broken if p not in fixed]}
        if regressed or (broken and not fixed):
            row["result"] = "rejected"
            tried.append(row)
            shutil.rmtree(cand, ignore_errors=True)
            continue
        promote(cand, version, source, reason)
        row["result"] = "promoted"
        tried.append(row)
        return {"promoted": row, "tried": tried, "kept_ok": keep}
    return {"promoted": None, "tried": tried, "kept_ok": keep}


def remediate(platforms: List[str], reason: str) -> dict:
    """A platform broke: try the latest stable, then the latest nightly,
    whichever is newer than what runs."""
    if not _lock():
        return {"skipped": "another run in progress"}
    try:
        act = active_version()
        latest = latest_versions()
        cands = []
        for source in ("stable", "nightly"):
            v = latest.get(source)
            if v and newer(v, act) and v not in [c[0] for c in cands]:
                cands.append((v, source))
        if not cands:
            out = {"promoted": None, "tried": [], "note": f"no version newer than {act}"}
        else:
            out = try_candidates(list(platforms), cands, f"remediate {','.join(platforms)}: {reason}")
        _log("remediate", platforms=list(platforms), active=act,
             result=(out.get("promoted") or {}).get("version") or "none",
             tried=[{k: t.get(k) for k in ("version", "source", "result", "fixed", "regressed")}
                    for t in out.get("tried", [])])
        if out.get("promoted"):
            restart_processes()
        return out
    finally:
        _unlock()


def daily_update() -> dict:
    """Daily: move to a newer STABLE if it breaks nothing that works. A
    nightly pin is left alone until a stable at least as new exists."""
    if not _lock():
        return {"skipped": "another run in progress"}
    try:
        act = active_version()
        stable = latest_versions().get("stable")
        if not stable or not newer(stable, act):
            _log("daily_check", active=act, stable=stable, result="up_to_date")
            return {"promoted": None, "note": f"up to date ({act})"}
        out = try_candidates([], [(stable, "stable")], "daily stable update")
        _log("daily_check", active=act, stable=stable,
             result=(out.get("promoted") or {}).get("version") or "rejected",
             tried=[{k: t.get(k) for k in ("version", "result", "regressed", "detail")}
                    for t in out.get("tried", [])])
        if out.get("promoted"):
            restart_processes()
        return out
    finally:
        _unlock()


def boot() -> str:
    """Container start (entrypoint, before the services): reinstall the
    pinned version a redeploy wiped, or drop the pin when the image already
    ships something newer. Prints what it did; never fails the boot."""
    try:
        pin = (get_state().get("pinned") or {})
        want = pin.get("version")
        if not want:
            return "no pin"
        img = image_version()
        if img and not newer(want, img):
            st = get_state()
            st["pinned"] = None
            _save_state(st)
            shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
            _log("boot_unpinned", pinned=want, image=img)
            return f"image {img} >= pin {want}: pin dropped"
        have = dir_version(OVERRIDE_DIR)
        if same(have, want):
            return f"pin {want} present"
        err = install(want, OVERRIDE_DIR + ".cand")
        if err:
            _log("boot_install_failed", version=want, detail=err[:200])
            return f"pin {want} install failed: {err[:120]}"
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        os.replace(OVERRIDE_DIR + ".cand", OVERRIDE_DIR)
        _log("boot_reinstalled", version=want)
        return f"pin {want} reinstalled"
    except Exception as exc:
        return f"boot error: {exc}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "probe":
        _probe_main(sys.argv[2:])
    elif cmd == "boot":
        print(f"[ytdlp-channel] {boot()}")
    elif cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
    else:
        print("usage: python -m app.core.ytdlp_channel probe <platform...> | boot | status")
