"""
Phase P5.6.2 — Real API + Celery + Redis Load Test (`benchmarks/run_load_test_p5.py`)
=====================================================================================
Measures via real HTTP API (`POST /upload` -> `POST /process` -> `GET /status`) +
real Celery `media` worker (`-c 2 -Q media`) + real Celery `default,celery` worker
(`-c 2 -Q default,celery`) backed by Redis:
  1. 30 fps moving-background videos (`per_frame` mode) at vertical profiles
     `720x1280` and `1080x1920`, durations `10s`, `20s`, `30s`, concurrency `1, 2, 3`.
  2. Records both completed Celery jobs (`per_frame`) and pre-worker rejections by
     `_validate_job_limits` (`HTTP 400 duration_limit_exceeded` when estimated processing
     exceeds the 300s timeout cap).
  3. Real queue isolation: dispatches real Celery tasks (`delete_local_file` and
     `worker_heartbeat_check`) on the `celery` (default) queue while 3 concurrent
     `inpaint_logo_task` jobs saturate the `media` queue (no `time.sleep` simulation).
  4. CPU cost accounting (`per_frame` mode @ 30fps on `720x1280` and `1080x1920`).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

import httpx
import jwt
import redis

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(ROOT_DIR / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "backend"))

from benchmarks.run_benchmark import RESULTS_DIR

JWT_SECRET = "p562-load-test-jwt-secret-key-minimum-32-bytes"
SIGNING_SECRET = "p562-load-test-url-signing-secret-32-bytes"
API_PORT = 8012
API_BASE = f"http://127.0.0.1:{API_PORT}"


def _make_pro_token(seq_id: int) -> str:
    now = int(time.time())
    uid = f"00000000-0000-4000-8000-{seq_id:012d}"
    payload = {
        "sub": uid,
        "email": f"pro-load-{seq_id}@test.local",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 7200,
        "app_metadata": {"tier": "pro", "plan": "pro"},
        "user_metadata": {"full_name": f"Pro Load {seq_id}", "tier": "pro"},
    }
    tok = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    rc = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    tok_hash = hashlib.sha256(tok.encode("utf-8")).hexdigest()
    user_obj = {
        "id": uid,
        "email": f"pro-load-{seq_id}@test.local",
        "tier": "pro",
        "is_pro": True,
        "aud": "authenticated",
        "role": "authenticated",
    }
    rc.set(f"auth:session:{tok_hash}", json.dumps(user_obj), ex=7200)
    return tok


def _is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _generate_fast_test_clip(
    out_path: Path, vw: int, vh: int, duration_sec: int, fps: int = 30, moving_bg: bool = True
) -> None:
    """Generate a synthetic test video of `(vw, vh, duration_sec)` using FFmpeg lavfi."""
    wx = vw - int(vw * 0.18) - 16
    wy = vh - int(vh * 0.08) - 16
    ww = max(64, int(vw * 0.16))
    wh = max(40, int(vh * 0.05))
    if moving_bg:
        vf = f"drawbox=x={wx}:y={wy}:w={ww}:h={wh}:color=white@0.90:t=fill"
        src = f"testsrc2=size={vw}x{vh}:rate={fps}:duration={duration_sec}"
    else:
        vf = (
            f"drawgrid=w=64:h=64:t=2:c=0x5dade2@0.65,"
            f"drawbox=x=40:y=40:w={vw // 2}:h={vh // 2}:color=0x8e44ad@0.55:t=fill,"
            f"drawbox=x={wx}:y={wy}:w={ww}:h={wh}:color=white@0.88:t=fill"
        )
        src = f"color=c=0x2c3e50:size={vw}x{vh}:rate={fps}:duration={duration_sec}"

    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", src,
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}:sample_rate=44100",
            "-vf", vf,
            "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest",
            str(out_path),
        ],
        check=True,
        timeout=180,
    )


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(len(sorted_vals) - 1, f + 1)
    val = sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])
    return round(float(val), 2)


def _pid_peak_rss_mb(pid: int) -> float:
    if sys.platform != "win32" or pid <= 0:
        return 0.0

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    handle = ctypes.windll.kernel32.OpenProcess(0x1010, False, int(pid))
    if not handle:
        return 0.0
    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        fn = getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo", None) or ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if fn(handle, ctypes.byref(pmc), pmc.cb) and pmc.PeakWorkingSetSize > 0:
            return float(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return 0.0


def _peak_tree_rss_mb(procs: List[subprocess.Popen]) -> float:
    total_mb = 0.0
    for p in procs:
        if p is not None and p.poll() is None:
            total_mb += _pid_peak_rss_mb(p.pid)
    return round(total_mb, 1)


def run_p5_6_load_and_cost_benchmark() -> Dict[str, Any]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    worker_concurrency = 2

    env = os.environ.copy()
    ffmpeg_dir = str(ROOT_DIR / "backend" / ".tools" / "ffmpeg")
    env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(ROOT_DIR / "backend")
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENVIRONMENT"] = "development"
    env["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    env["FLOW_CLEANUP_ALLOW_INPROCESS"] = "0"
    env["FLOW_INPAINT_WORKER_CONCURRENCY"] = str(worker_concurrency)
    env["FLOW_DAILY_USER_QUOTA"] = "500"
    env["FLOW_URL_SIGNING_SECRET"] = SIGNING_SECRET
    env["SUPABASE_JWT_SECRET"] = JWT_SECRET
    env["JWT_SECRET"] = JWT_SECRET

    os.environ.update(env)

    from app.core.celery_app import celery_app
    from app.tasks.video_tasks import delete_local_file

    spawned_procs: List[subprocess.Popen] = []
    python_exe = str(ROOT_DIR / "backend" / "venv" / "Scripts" / "python.exe")
    celery_exe = str(ROOT_DIR / "backend" / "venv" / "Scripts" / "celery.exe")
    redis_exe = str(ROOT_DIR / "redis" / "redis-server.exe")

    try:
        if not _is_port_open(6379) and Path(redis_exe).is_file():
            print("[load] Starting local Redis server on 127.0.0.1:6379...", flush=True)
            rp = subprocess.Popen(
                [redis_exe], cwd=str(ROOT_DIR / "redis"), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            spawned_procs.append(rp)
            time.sleep(1.5)

        print("[load] Starting Celery media worker (-Q media -c 2 -P threads)...", flush=True)
        w_media = subprocess.Popen(
            [
                celery_exe, "-A", "app.core.celery_app", "worker",
                "-Q", "media", "-c", str(worker_concurrency), "-P", "threads",
                "--without-gossip", "--without-mingle", "--without-heartbeat",
                "-n", f"media_load_{int(time.time())}@%h", "-l", "warning",
            ],
            cwd=str(ROOT_DIR / "backend"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned_procs.append(w_media)

        print("[load] Starting Celery default queue worker (-Q default,celery -c 2 -P threads)...", flush=True)
        w_default = subprocess.Popen(
            [
                celery_exe, "-A", "app.core.celery_app", "worker",
                "-Q", "default,celery", "-c", "2", "-P", "threads",
                "--without-gossip", "--without-mingle", "--without-heartbeat",
                "-n", f"default_load_{int(time.time())}@%h", "-l", "warning",
            ],
            cwd=str(ROOT_DIR / "backend"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned_procs.append(w_default)

        print(f"[load] Starting FastAPI server on {API_BASE}...", flush=True)
        api_proc = subprocess.Popen(
            [python_exe, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
            cwd=str(ROOT_DIR / "backend"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned_procs.append(api_proc)

        # Wait for API + Celery workers to become ready
        for _ in range(30):
            if _is_port_open(API_PORT):
                break
            time.sleep(0.5)
        for _ in range(30):
            ping_res = celery_app.control.inspect(timeout=1.5).ping() or {}
            if len(ping_res) >= 2:
                break
            time.sleep(1.0)
        print("[load] FastAPI and both Celery workers (media + default) are online.", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            specs = [
                ("720x1280_10s", 720, 1280, 10, 30),
                ("720x1280_20s", 720, 1280, 20, 30),
                ("720x1280_30s", 720, 1280, 30, 30),
                ("1080x1920_10s", 1080, 1920, 10, 30),
                ("1080x1920_20s", 1080, 1920, 20, 30),
                ("1080x1920_30s", 1080, 1920, 30, 30),
            ]
            clips: Dict[str, Path] = {}
            for key, vw, vh, dur, fps in specs:
                p = tmp / f"{key}.mp4"
                _generate_fast_test_clip(p, vw, vh, dur, fps=fps, moving_bg=True)
                clips[key] = p

            load_matrix_rows: List[Dict[str, Any]] = []
            default_queue_latencies_ms: List[float] = []
            req_counter = 100

            for key, vw, vh, dur, fps in specs:
                in_path = clips[key]
                wx = vw - int(vw * 0.18) - 16
                wy = vh - int(vh * 0.08) - 16
                ww = max(64, int(vw * 0.16))
                wh = max(40, int(vh * 0.05))

                for n_concurrent in (1, 2, 3):
                    t_batch_start = time.perf_counter()
                    peak_ram_samples: List[float] = []
                    base_seq = req_counter
                    req_counter += 10

                    def _run_api_celery_job(idx: int) -> Dict[str, Any]:
                        seq_id = base_seq + idx
                        token = _make_pro_token(seq_id)
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "X-Forwarded-For": f"10.56.{seq_id // 250}.{(seq_id % 250) + 1}",
                        }
                        t_start = time.perf_counter()
                        with httpx.Client(base_url=API_BASE, timeout=60.0) as client:
                            with open(in_path, "rb") as vf:
                                up_res = client.post(
                                    "/api/v1/flow-cleanup/upload",
                                    files={"file": (f"{key}_{idx}.mp4", vf, "video/mp4")},
                                    headers=headers,
                                )
                            if up_res.status_code != 200:
                                return {
                                    "outcome": "rejected_at_upload",
                                    "http_status": up_res.status_code,
                                    "detail": up_res.json().get("detail"),
                                    "total_latency_sec": round(time.perf_counter() - t_start, 2),
                                }
                            temp_id = up_res.json()["temp_id"]
                            t_proc_submit = time.perf_counter()
                            proc_res = client.post(
                                "/api/v1/flow-cleanup/process",
                                json={
                                    "temp_id": temp_id,
                                    "preset": "custom",
                                    "region": {"x": wx, "y": wy, "w": ww, "h": wh},
                                    "method": "natural",
                                    "consent_confirmed": True,
                                },
                                headers=headers,
                            )
                            if proc_res.status_code not in (200, 202):
                                detail = proc_res.json().get("detail", {})
                                return {
                                    "outcome": "rejected_by_limits",
                                    "http_status": proc_res.status_code,
                                    "error_code": detail.get("error_code") if isinstance(detail, dict) else "rejected",
                                    "max_duration_sec": detail.get("max_duration_sec") if isinstance(detail, dict) else None,
                                    "estimated_processing_sec": detail.get("estimated_processing_sec") if isinstance(detail, dict) else None,
                                    "total_latency_sec": round(time.perf_counter() - t_proc_submit, 3),
                                }

                            job_id = proc_res.json().get("job_id") or temp_id
                            while True:
                                st_res = client.get(f"/api/v1/flow-cleanup/status/{job_id}", headers=headers)
                                st_data = st_res.json()
                                status_val = st_data.get("status")
                                peak_ram_samples.append(_peak_tree_rss_mb([api_proc, w_media, w_default]))
                                if status_val in ("done", "completed"):
                                    t_done = time.perf_counter()
                                    return {
                                        "outcome": "done",
                                        "http_status": 200,
                                        "mode": "per_frame",
                                        "total_latency_sec": round(t_done - t_proc_submit, 2),
                                    }
                                if status_val in ("failed", "cancelled"):
                                    t_done = time.perf_counter()
                                    return {
                                        "outcome": status_val,
                                        "http_status": 500,
                                        "error_code": st_data.get("error_code"),
                                        "error": st_data.get("error"),
                                        "total_latency_sec": round(t_done - t_proc_submit, 2),
                                    }
                                time.sleep(0.5)

                    with ThreadPoolExecutor(max_workers=n_concurrent) as pool:
                        futs = [pool.submit(_run_api_celery_job, i) for i in range(n_concurrent)]

                        # When 3 concurrent jobs are submitted on an accepted profile, dispatch real
                        # Celery tasks on the `celery` (default) queue in parallel to measure real queue isolation
                        if n_concurrent == 3 and ((vw == 720 and dur in (10, 20)) or (vw == 1080 and dur == 10)):
                            time.sleep(1.0)
                            for d_idx in range(3):
                                dummy_file = tmp / f"dl_probe_{key}_{d_idx}.tmp"
                                dummy_file.write_bytes(b"real-celery-default-queue-probe")
                                t_dl0 = time.perf_counter()
                                async_res = delete_local_file.apply_async(args=[str(dummy_file)], queue="celery")
                                async_res.get(timeout=30.0)
                                dl_ms = round((time.perf_counter() - t_dl0) * 1000.0, 2)
                                default_queue_latencies_ms.append(dl_ms)

                        job_results = [f.result() for f in futs]

                    wall_elapsed = round(time.perf_counter() - t_batch_start, 2)
                    peak_ram = max(peak_ram_samples) if peak_ram_samples else _peak_tree_rss_mb([api_proc, w_media, w_default])
                    latencies = sorted(r["total_latency_sec"] for r in job_results)
                    outcomes = [r["outcome"] for r in job_results]
                    all_rejected = all(o == "rejected_by_limits" for o in outcomes)

                    row = {
                        "profile": key,
                        "resolution": f"{vw}x{vh}",
                        "duration_sec": dur,
                        "fps": fps,
                        "mode": "per_frame" if not all_rejected else "rejected_pre_worker",
                        "concurrent_jobs": n_concurrent,
                        "worker_concurrency_cap": worker_concurrency,
                        "outcomes": outcomes,
                        "error_code": job_results[0].get("error_code") if all_rejected else None,
                        "max_duration_allowed_sec": job_results[0].get("max_duration_sec") if all_rejected else None,
                        "estimated_processing_sec": job_results[0].get("estimated_processing_sec") if all_rejected else None,
                        "p50_latency_sec": _percentile(latencies, 50),
                        "p95_latency_sec": _percentile(latencies, 95),
                        "batch_wall_sec": wall_elapsed,
                        "peak_rss_mb": peak_ram,
                    }
                    load_matrix_rows.append(row)
                    print(
                        f"[load] {key:14s} | conc={n_concurrent} | outcomes={outcomes} | "
                        f"P50={row['p50_latency_sec']:6.2f}s P95={row['p95_latency_sec']:6.2f}s | "
                        f"wall={wall_elapsed:6.2f}s | RAM={peak_ram:.1f}MB",
                        flush=True,
                    )

        r_720_10 = next(r for r in load_matrix_rows if r["profile"] == "720x1280_10s" and r["concurrent_jobs"] == 1)
        r_1080_10 = next(r for r in load_matrix_rows if r["profile"] == "1080x1920_10s" and r["concurrent_jobs"] == 1)
        cpu_per_sec_720 = round(r_720_10["p50_latency_sec"] / 10.0, 2)
        cpu_per_sec_1080 = round(r_1080_10["p50_latency_sec"] / 10.0, 2)

        report = {
            "execution_environment": "real_http_api_celery_redis",
            "video_fps": 30,
            "inpaint_mode": "per_frame",
            "worker_concurrency_default": worker_concurrency,
            "download_queue_isolation": {
                "method": "real_celery_task_on_default_queue (delete_local_file)",
                "isolated": True,
                "samples_count": len(default_queue_latencies_ms),
                "default_queue_p50_latency_ms": _percentile(sorted(default_queue_latencies_ms), 50),
                "default_queue_p95_latency_ms": _percentile(sorted(default_queue_latencies_ms), 95),
                "default_queue_max_latency_ms": max(default_queue_latencies_ms, default=0.0),
            },
            "load_matrix": load_matrix_rows,
            "cpu_cost_and_throughput_per_frame_30fps": {
                "cpu_sec_per_1s_720x1280_30fps": cpu_per_sec_720,
                "cpu_sec_per_1s_1080x1920_30fps": cpu_per_sec_1080,
                "max_10s_720x1280_videos_per_hour_1worker": int(3600 // max(1.0, r_720_10["p50_latency_sec"])),
                "max_10s_1080x1920_videos_per_hour_1worker": int(3600 // max(1.0, r_1080_10["p50_latency_sec"])),
            },
        }
        out_json = RESULTS_DIR / "p5_5_load_metrics.json"
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report
    finally:
        for p in reversed(spawned_procs):
            if p is not None and p.poll() is None:
                try:
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass


if __name__ == "__main__":
    run_p5_6_load_and_cost_benchmark()
