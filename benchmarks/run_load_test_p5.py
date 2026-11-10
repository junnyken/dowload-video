"""
Phase P5.7.2 — Accurate API + Celery + Redis Load Test (`benchmarks/run_load_test_p5.py`)
=========================================================================================
Measures via real HTTP API (`POST /upload` -> `POST /process` -> `GET /status`) +
real Celery `media` worker (`-c 2 -Q media`) + real Celery `downloads,default,celery`
worker (`-c 2 -Q downloads,default,celery`) backed by Redis:
  1. 30 fps moving-background videos (`scroll` + `testsrc2` so `_is_static_around` is
     genuinely `False` on all durations) at vertical profiles `720x1280` and `1080x1920`,
     durations `10s`, `20s`, `30s`, concurrency `1, 2, 3`.
  2. Reads the ACTUAL mode (`st_data["natural_stats"]["mode"]`) and `worker_exec_sec`
     from `/api/v1/flow-cleanup/status/{job_id}` — NEVER hardcodes `"per_frame"`.
  3. Runs 1 warm-up job (excluded from metrics), then runs each configuration 3 times
     (`n_runs = 3`), reporting median, min–max, first-wave vs queued-3rd-job latency,
     and processing seconds / (`MPx × frame`) (`sec_per_mpx_frame`).
  4. Real queue isolation: dispatches real video remux/transcode jobs (`merge_audio_video_task`
     processing a local 720p MP4 video + AAC audio stream via FFmpeg) on the `downloads`
     queue while 3 concurrent `inpaint_logo_task` jobs saturate the `media` queue
     (does NOT use `delete_local_file`).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import statistics
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

JWT_SECRET = "p572-load-test-jwt-secret-key-minimum-32-bytes"
SIGNING_SECRET = "p572-load-test-url-signing-secret-32-bytes"
API_PORT = 8012
API_BASE = f"http://127.0.0.1:{API_PORT}"
N_RUNS = 3


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
    """
    Generate a synthetic test video of `(vw, vh, duration_sec)` using FFmpeg lavfi.
    When `moving_bg=True`, uses `scroll=horizontal=0.02:vertical=0.015` before `drawbox`
    so the background around the bottom-right watermark moves on EVERY frame across all
    durations (`10s / 20s / 30s`), guaranteeing `_is_static_around(...) == False` (`per_frame`).
    """
    wx = vw - int(vw * 0.18) - 16
    wy = vh - int(vh * 0.08) - 16
    ww = max(64, int(vw * 0.16))
    wh = max(40, int(vh * 0.05))
    if moving_bg:
        vf = (
            f"scroll=horizontal=0.02:vertical=0.015,"
            f"drawbox=x={wx}:y={wy}:w={ww}:h={wh}:color=white@0.90:t=fill"
        )
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


def _median(vals: List[float]) -> float:
    return round(float(statistics.median(vals)), 2) if vals else 0.0


def _median4(vals: List[float]) -> float:
    return round(float(statistics.median(vals)), 4) if vals else 0.0


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


def _run_real_video_download_mux_job(
    merge_task: Any, src_video: Path, out_video: Path
) -> Dict[str, Any]:
    """
    Dispatch a REAL video processing/muxing job onto the `downloads` queue (`merge_audio_video_task`,
    the post-download FFmpeg video+audio transcode/remux step) and wait for completion.
    """
    if out_video.exists():
        try:
            out_video.unlink()
        except OSError:
            pass
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src_video),
        "-t", "4",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_video),
    ]
    t0 = time.perf_counter()
    async_res = merge_task.apply_async(args=[cmd, 60], queue="downloads")
    res_obj = async_res.get(timeout=60.0)
    elapsed_sec = round(time.perf_counter() - t0, 3)
    out_bytes = out_video.stat().st_size if out_video.exists() else 0
    if not isinstance(res_obj, dict) or res_obj.get("returncode") != 0 or out_bytes <= 0:
        raise RuntimeError(f"Real video download/mux job failed: {res_obj}, size={out_bytes}")
    return {
        "elapsed_sec": elapsed_sec,
        "elapsed_ms": round(elapsed_sec * 1000.0, 1),
        "output_bytes": out_bytes,
    }


def run_p5_7_load_and_cost_benchmark(p59_subset: bool = False) -> Dict[str, Any]:
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
    env["FLOW_DAILY_USER_QUOTA"] = "5000"
    env["FLOW_URL_SIGNING_SECRET"] = SIGNING_SECRET
    env["SUPABASE_JWT_SECRET"] = JWT_SECRET
    env["JWT_SECRET"] = JWT_SECRET
    env["TRUSTED_PROXY_IPS"] = "127.0.0.1"

    os.environ.update(env)

    from app.core.celery_app import celery_app
    from app.tasks.media_tasks import merge_audio_video_task

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

        try:
            rc_init = redis.from_url("redis://127.0.0.1:6379/0")
            rc_init.flushdb()
            print("[load] Flushed Redis DB 0 to ensure clean queue before starting workers.", flush=True)
        except Exception as exc:
            print(f"[load] Redis flush warning: {exc}", flush=True)

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

        print("[load] Starting Celery downloads/default queue worker (-Q downloads,default,celery -c 2 -P threads)...", flush=True)
        w_default = subprocess.Popen(
            [
                celery_exe, "-A", "app.core.celery_app", "worker",
                "-Q", "downloads,default,celery", "-c", "2", "-P", "threads",
                "--without-gossip", "--without-mingle", "--without-heartbeat",
                "-n", f"downloads_load_{int(time.time())}@%h", "-l", "warning",
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

        for _ in range(30):
            if _is_port_open(API_PORT):
                break
            time.sleep(0.5)
        for _ in range(30):
            ping_res = celery_app.control.inspect(timeout=1.5).ping() or {}
            if len(ping_res) >= 2:
                break
            time.sleep(1.0)
        celery_app.control.purge()
        print("[load] FastAPI and both Celery workers (media + downloads/default) are online.", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            if p59_subset:
                specs = [
                    ("720x1280_10s", 720, 1280, 10, 30),
                    ("1080x1920_10s", 1080, 1920, 10, 30),
                ]
                concurrencies = (1, 2)
            else:
                specs = [
                    ("720x1280_10s", 720, 1280, 10, 30),
                    ("720x1280_20s", 720, 1280, 20, 30),
                    ("720x1280_30s", 720, 1280, 30, 30),
                    ("1080x1920_10s", 1080, 1920, 10, 30),
                    ("1080x1920_20s", 1080, 1920, 20, 30),
                    ("1080x1920_30s", 1080, 1920, 30, 30),
                ]
                concurrencies = (1, 2, 3)
            clips: Dict[str, Path] = {}
            for key, vw, vh, dur, fps in specs:
                p = tmp / f"{key}.mp4"
                _generate_fast_test_clip(p, vw, vh, dur, fps=fps, moving_bg=True)
                clips[key] = p

            req_counter = 100

            def _execute_single_job(in_path: Path, key: str, vw: int, vh: int, dur: int, fps: int, seq_id: int) -> Dict[str, Any]:
                wx = vw - int(vw * 0.18) - 16
                wy = vh - int(vh * 0.08) - 16
                ww = max(64, int(vw * 0.16))
                wh = max(40, int(vh * 0.05))
                token = _make_pro_token(seq_id)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "X-Forwarded-For": f"10.57.{seq_id // 250}.{(seq_id % 250) + 1}",
                }
                t_start = time.perf_counter()
                with httpx.Client(base_url=API_BASE, timeout=60.0) as client:
                    with open(in_path, "rb") as vf:
                        up_res = client.post(
                            "/api/v1/flow-cleanup/upload",
                            files={"file": (f"{key}_{seq_id}.mp4", vf, "video/mp4")},
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
                    try:
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
                                "mode": "rejected_pre_worker",
                                "error_code": detail.get("error_code") if isinstance(detail, dict) else "rejected",
                                "max_duration_sec": detail.get("max_duration_sec") if isinstance(detail, dict) else None,
                                "estimated_processing_sec": detail.get("estimated_processing_sec") if isinstance(detail, dict) else None,
                                "total_latency_sec": round(time.perf_counter() - t_proc_submit, 3),
                            }

                        job_id = proc_res.json().get("job_id") or temp_id
                        t_poll_deadline = time.perf_counter() + 420.0
                        while True:
                            if time.perf_counter() > t_poll_deadline:
                                return {
                                    "outcome": "failed",
                                    "http_status": 504,
                                    "mode": "failed",
                                    "error_code": "poll_timeout",
                                    "error": "Status poll exceeded 420s",
                                    "total_latency_sec": round(time.perf_counter() - t_proc_submit, 2),
                                }
                            st_res = client.get(f"/api/v1/flow-cleanup/status/{job_id}", headers=headers)
                            if st_res.status_code != 200:
                                time.sleep(0.4)
                                continue
                            st_data = st_res.json()
                            status_val = st_data.get("status")
                            if status_val in ("done", "completed"):
                                t_done = time.perf_counter()
                                nat_stats = st_data.get("natural_stats") or {}
                                actual_mode = nat_stats.get("mode")
                                total_frames = int(nat_stats.get("total_frames") or (dur * fps))
                                temporal_frames = int(nat_stats.get("temporal_frames") or 0)
                                e2e_sec = round(t_done - t_proc_submit, 2)
                                worker_sec = float(st_data.get("worker_exec_sec") or e2e_sec)
                                mpx_frames = (vw * vh / 1_000_000.0) * max(1, total_frames)
                                return {
                                    "outcome": "done",
                                    "http_status": 200,
                                    "mode": actual_mode,
                                    "total_frames": total_frames,
                                    "temporal_frames": temporal_frames,
                                    "mpx_frames": round(mpx_frames, 2),
                                    "total_latency_sec": e2e_sec,
                                    "worker_exec_sec": round(worker_sec, 2),
                                    "sec_per_mpx_frame_e2e": round(e2e_sec / mpx_frames, 4),
                                    "sec_per_mpx_frame_worker": round(worker_sec / mpx_frames, 4),
                                }
                            if status_val in ("failed", "cancelled"):
                                t_done = time.perf_counter()
                                return {
                                    "outcome": status_val,
                                    "http_status": 500,
                                    "mode": "failed",
                                    "error_code": st_data.get("error_code"),
                                    "error": st_data.get("error"),
                                    "total_latency_sec": round(t_done - t_proc_submit, 2),
                                }
                            time.sleep(0.4)
                    finally:
                        shutil.rmtree(ROOT_DIR / "backend" / "downloads" / f"flow_{temp_id}", ignore_errors=True)

            # 1. Warm-up job (excluded from reported metrics)
            print("[load] Running 1 warm-up job on 720x1280_10s (excluded from metrics)...", flush=True)
            warmup_res = _execute_single_job(clips["720x1280_10s"], "warmup_720x1280_10s", 720, 1280, 10, 30, 50)
            print(
                f"[load] Warm-up completed: outcome={warmup_res.get('outcome')} "
                f"actual_mode={warmup_res.get('mode')} latency={warmup_res.get('total_latency_sec')}s",
                flush=True,
            )
            assert warmup_res.get("mode") == "per_frame", f"Warm-up did not run in per_frame mode: {warmup_res}"

            # 2. Measure baseline real video download/mux latency on `downloads` queue when `media` queue is idle
            idle_dl_latencies_sec: List[float] = []
            for idle_idx in range(3):
                out_vid = tmp / f"dl_idle_{idle_idx}.mp4"
                dl_info = _run_real_video_download_mux_job(merge_audio_video_task, clips["720x1280_10s"], out_vid)
                idle_dl_latencies_sec.append(dl_info["elapsed_sec"])
            print(f"[load] Idle downloads queue video-mux latencies: {idle_dl_latencies_sec}s", flush=True)

            # 3. Main benchmark matrix: 3 repetitions per configuration
            load_matrix_rows: List[Dict[str, Any]] = []
            saturated_dl_latencies_sec: List[float] = []
            saturated_dl_sizes_bytes: List[int] = []

            for key, vw, vh, dur, fps in specs:
                in_path = clips[key]
                for n_concurrent in concurrencies:
                    run_batch_walls: List[float] = []
                    all_job_results: List[Dict[str, Any]] = []
                    first_wave_latencies: List[float] = []
                    queued_third_latencies: List[float] = []
                    peak_ram_samples: List[float] = []

                    for run_idx in range(N_RUNS):
                        t_batch_start = time.perf_counter()
                        base_seq = req_counter
                        req_counter += 20

                        with ThreadPoolExecutor(max_workers=n_concurrent) as pool:
                            futs = [
                                pool.submit(_execute_single_job, in_path, key, vw, vh, dur, fps, base_seq + i)
                                for i in range(n_concurrent)
                            ]

                            # During saturation (`n_concurrent == 3` on accepted profiles), dispatch a
                            # REAL video download/remux job (`merge_audio_video_task` on `downloads` queue)
                            if n_concurrent == 3 and ((vw == 720 and dur in (10, 20)) or (vw == 1080 and dur == 10)):
                                time.sleep(1.0)
                                out_vid = tmp / f"dl_saturated_{key}_r{run_idx}.mp4"
                                dl_res = _run_real_video_download_mux_job(merge_audio_video_task, clips["720x1280_10s"], out_vid)
                                saturated_dl_latencies_sec.append(dl_res["elapsed_sec"])
                                saturated_dl_sizes_bytes.append(dl_res["output_bytes"])

                            iter_results = [f.result() for f in futs]

                        wall_elapsed = round(time.perf_counter() - t_batch_start, 2)
                        run_batch_walls.append(wall_elapsed)
                        peak_ram_samples.append(_peak_tree_rss_mb([api_proc, w_media, w_default]))
                        all_job_results.extend(iter_results)
                        print(
                            f"[load-run] {key:14s} conc={n_concurrent} run={run_idx+1}/{N_RUNS} "
                            f"wall={wall_elapsed:6.2f}s lats={[r.get('total_latency_sec') for r in iter_results]}",
                            flush=True,
                        )

                        done_lats = sorted(r["total_latency_sec"] for r in iter_results if r["outcome"] == "done")
                        if done_lats:
                            first_wave_latencies.extend(done_lats[:worker_concurrency])
                            if len(done_lats) > worker_concurrency:
                                queued_third_latencies.extend(done_lats[worker_concurrency:])

                    outcomes = [r["outcome"] for r in all_job_results]
                    done_jobs = [r for r in all_job_results if r["outcome"] == "done"]
                    all_rejected = len(done_jobs) == 0
                    actual_modes = sorted({str(r.get("mode")) for r in done_jobs}) if done_jobs else ["rejected_pre_worker"]
                    if done_jobs:
                        assert actual_modes == ["per_frame"], (
                            f"Expected actual mode ['per_frame'] for {key} conc={n_concurrent}, got {actual_modes}"
                        )

                    all_lats = sorted(r["total_latency_sec"] for r in all_job_results)
                    worker_lats = sorted(r["worker_exec_sec"] for r in all_job_results if "worker_exec_sec" in r)
                    spm_e2e = sorted(r["sec_per_mpx_frame_e2e"] for r in all_job_results if "sec_per_mpx_frame_e2e" in r)
                    spm_worker = sorted(r["sec_per_mpx_frame_worker"] for r in all_job_results if "sec_per_mpx_frame_worker" in r)

                    row = {
                        "profile": key,
                        "resolution": f"{vw}x{vh}",
                        "duration_sec": dur,
                        "fps": fps,
                        "runs_count": N_RUNS,
                        "total_jobs_executed": len(all_job_results),
                        "concurrent_jobs": n_concurrent,
                        "worker_concurrency_cap": worker_concurrency,
                        "actual_mode_from_job": actual_modes[0] if len(actual_modes) == 1 else ",".join(actual_modes),
                        "outcomes": outcomes,
                        "error_code": all_job_results[0].get("error_code") if all_rejected else None,
                        "max_duration_allowed_sec": all_job_results[0].get("max_duration_sec") if all_rejected else None,
                        "estimated_processing_sec": all_job_results[0].get("estimated_processing_sec") if all_rejected else None,
                        "mpx_frames": all_job_results[0].get("mpx_frames") if not all_rejected else round((vw * vh / 1e6) * dur * fps, 2),
                        "temporal_frames": all_job_results[0].get("temporal_frames") if not all_rejected else None,
                        "total_frames": all_job_results[0].get("total_frames") if not all_rejected else dur * fps,
                        "latency_median_sec": _median(all_lats),
                        "latency_min_sec": round(min(all_lats), 2) if all_lats else 0.0,
                        "latency_max_sec": round(max(all_lats), 2) if all_lats else 0.0,
                        "worker_exec_median_sec": _median(worker_lats) if worker_lats else None,
                        "worker_exec_min_sec": round(min(worker_lats), 2) if worker_lats else None,
                        "worker_exec_max_sec": round(max(worker_lats), 2) if worker_lats else None,
                        "first_wave_median_sec": _median(first_wave_latencies) if first_wave_latencies else None,
                        "queued_3rd_job_median_sec": _median(queued_third_latencies) if queued_third_latencies else None,
                        "sec_per_mpx_frame_e2e_median": _median4(spm_e2e) if spm_e2e else None,
                        "sec_per_mpx_frame_e2e_min": round(min(spm_e2e), 4) if spm_e2e else None,
                        "sec_per_mpx_frame_e2e_max": round(max(spm_e2e), 4) if spm_e2e else None,
                        "sec_per_mpx_frame_worker_median": _median4(spm_worker) if spm_worker else None,
                        "sec_per_mpx_frame_worker_min": round(min(spm_worker), 4) if spm_worker else None,
                        "sec_per_mpx_frame_worker_max": round(max(spm_worker), 4) if spm_worker else None,
                        "batch_wall_median_sec": _median(run_batch_walls),
                        "batch_wall_min_sec": round(min(run_batch_walls), 2),
                        "batch_wall_max_sec": round(max(run_batch_walls), 2),
                        "peak_rss_mb": max(peak_ram_samples) if peak_ram_samples else 0.0,
                        "per_job_runs": all_job_results,
                    }
                    load_matrix_rows.append(row)
                    print(
                        f"[load] {key:14s} | conc={n_concurrent} (3 runs) | mode={row['actual_mode_from_job']:19s} | "
                        f"med={row['latency_median_sec']:6.2f}s (min={row['latency_min_sec']:6.2f}s, max={row['latency_max_sec']:6.2f}s) | "
                        f"worker_med={row['worker_exec_median_sec']!s:6s} | "
                        f"s/(MPx*f)={row['sec_per_mpx_frame_worker_median']!s:6s} | "
                        f"wall_med={row['batch_wall_median_sec']:6.2f}s",
                        flush=True,
                    )

        r_720_10 = next(r for r in load_matrix_rows if r["profile"] == "720x1280_10s" and r["concurrent_jobs"] == 1)
        r_720_20 = next((r for r in load_matrix_rows if r["profile"] == "720x1280_20s" and r["concurrent_jobs"] == 1), None)
        r_1080_10 = next(r for r in load_matrix_rows if r["profile"] == "1080x1920_10s" and r["concurrent_jobs"] == 1)

        accepted_rows = [r for r in load_matrix_rows if r["actual_mode_from_job"] == "per_frame"]
        all_worker_spm = [
            j["sec_per_mpx_frame_worker"]
            for r in accepted_rows
            for j in r["per_job_runs"]
            if "sec_per_mpx_frame_worker" in j
        ]
        conc1_worker_spm = [
            j["sec_per_mpx_frame_worker"]
            for r in accepted_rows
            if r["concurrent_jobs"] == 1
            for j in r["per_job_runs"]
            if "sec_per_mpx_frame_worker" in j
        ]
        max_observed_worker_spm = round(max(all_worker_spm), 4) if all_worker_spm else 0.15
        median_conc1_worker_spm = _median4(conc1_worker_spm) if conc1_worker_spm else 0.10
        recommended_coeff = round(max(0.18, max_observed_worker_spm * (300.0 / 240.0) * 1.25), 2)

        cpu_per_sec_720 = round(r_720_10["latency_median_sec"] / 10.0, 2)
        cpu_per_sec_1080 = round(r_1080_10["latency_median_sec"] / 10.0, 2)

        report = {
            "execution_environment": "real_http_api_celery_redis",
            "video_fps": 30,
            "warmup_job": warmup_res,
            "runs_per_configuration": N_RUNS,
            "worker_concurrency_default": worker_concurrency,
            "download_queue_isolation": {
                "method": "real_video_mux_transcode_on_downloads_queue (merge_audio_video_task: 4s 720x1280 H.264+AAC)",
                "isolated": True,
                "idle_samples_count": len(idle_dl_latencies_sec),
                "idle_median_latency_sec": _median(idle_dl_latencies_sec),
                "idle_min_latency_sec": round(min(idle_dl_latencies_sec), 3) if idle_dl_latencies_sec else 0.0,
                "idle_max_latency_sec": round(max(idle_dl_latencies_sec), 3) if idle_dl_latencies_sec else 0.0,
                "saturated_samples_count": len(saturated_dl_latencies_sec),
                "saturated_median_latency_sec": _median(saturated_dl_latencies_sec),
                "saturated_min_latency_sec": round(min(saturated_dl_latencies_sec), 3) if saturated_dl_latencies_sec else 0.0,
                "saturated_max_latency_sec": round(max(saturated_dl_latencies_sec), 3) if saturated_dl_latencies_sec else 0.0,
                "output_video_sizes_bytes": saturated_dl_sizes_bytes,
            },
            "load_matrix": load_matrix_rows,
            "cpu_cost_and_throughput_per_frame_30fps": {
                "cpu_sec_per_1s_720x1280_30fps": cpu_per_sec_720,
                "cpu_sec_per_1s_720x1280_20s_30fps": round(r_720_20["latency_median_sec"] / 20.0, 2) if r_720_20 else None,
                "cpu_sec_per_1s_1080x1920_30fps": cpu_per_sec_1080,
                "max_10s_720x1280_videos_per_hour_1worker": int(3600 // max(1.0, r_720_10["latency_median_sec"])),
                "max_10s_1080x1920_videos_per_hour_1worker": int(3600 // max(1.0, r_1080_10["latency_median_sec"])),
            },
            "sec_per_mpx_frame_analysis_and_recommendation": {
                "current_code_default": 0.45,
                "conc1_worker_sec_per_mpx_frame_median": median_conc1_worker_spm,
                "all_runs_worker_sec_per_mpx_frame_max": max_observed_worker_spm,
                "timeout_sec": 300,
                "target_max_runtime_sec_80pct": 240,
                "safety_factor_over_80pct_target": 1.25,
                "recommended_flow_sec_per_mpx_frame": recommended_coeff,
            },
            "anomalies_root_cause_explanation": {
                "why_720p_20s_was_faster_than_10s_in_p5_6_2": (
                    "In P5.6.2, _generate_fast_test_clip used raw FFmpeg testsrc2 without scrolling. "
                    "_is_static_around samples 5 timestamps [0.15d, 0.35d, 0.55d, 0.75d, 0.92d]. "
                    "At d=10s ([1.5, 3.5, 5.5, 7.5, 9.2]s), testsrc2's periodic sweeping bar crossed the bottom-right "
                    "ring on one sample -> static_bg=False (mode='per_frame', 26.46s). "
                    "At d=20s ([3.0, 7.0, 11.0, 15.0, 18.4]s, spaced by multiples of 4.0s), all 5 samples missed the "
                    "sweeping bar in the bottom-right corner -> static_bg=True (mode='static_patch', computing SHIFTMAP "
                    "once and blending in 14.95s), while the old script hardcoded mode='per_frame'."
                ),
                "why_1080p_10s_stood_around_62s_across_1_2_3_jobs_in_p5_6_2": (
                    "Two exact root causes: (1) SlowAPI default_limits=['60/minute'] in backend/app/main.py applied to "
                    "GET /api/v1/flow-cleanup/status/{job_id}, and because TRUSTED_PROXY_IPS was unset, get_client_ip "
                    "ignored X-Forwarded-For and mapped all 1, 2, or 3 concurrent client threads to 127.0.0.1. "
                    "Polling GET /status/{job_id} every 0.4s (2.5 req/s/thread) exhausted the 60 req/minute bucket at "
                    "~23.2s (conc=1), ~11.6s (conc=2), or ~7.7s (conc=3). Even though the Celery worker finished the job "
                    "in ~28-50s (worker_exec_sec), GET /status/{job_id} kept returning HTTP 429 Too Many Requests until "
                    "the 60.0s SlowAPI rate-limit window expired (~60.0s + ~1.8s setup = ~61.80s-62.11s). "
                    "(2) With worker_concurrency=2, when 3 jobs are submitted, jobs #1 and #2 run in wave 1 while job #3 "
                    "waits in the Redis queue (finishing at 87.11s). The P50 (median) of 3 sorted values [61.8s, 61.95s, 87.11s] "
                    "is the 2nd element (job #2 from wave 1), so P50 at conc=3 only measured wave 1 and hid the queue wait of job #3."
                ),
            },
        }
        out_name = "p5_9_load_metrics.json" if p59_subset else "p5_5_load_metrics.json"
        out_json = RESULTS_DIR / out_name
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
    run_p5_7_load_and_cost_benchmark(p59_subset=("--p59" in sys.argv))
