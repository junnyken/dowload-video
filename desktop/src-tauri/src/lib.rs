//! VidGrab desktop, stage C1.
//!
//! The webview can call exactly the commands of docs/desktop/C1-CONTRACT.md
//! (see capabilities/default.json). There is no generic "run a program" or
//! file-system bridge, and nothing goes through a shell: sidecars are started
//! with fixed argument vectors (engine.rs), after a SHA256 check of every
//! sidecar (checksum.rs), in a Job Object so the whole tree dies on
//! pause/cancel/app exit (proc.rs).

mod auth;
mod checksum;
mod engine;
mod error;
mod formats;
mod history;
mod paths;
mod proc;
mod progress;
mod sys;
mod validate;

use error::{CmdResult, Code, CommandError};
use history::{Db, HistoryItem};
use progress::{Line, Stage, Throttle};
use serde::Serialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

const EVENT_PROGRESS: &str = "download://progress";
const EVENT_LOG: &str = "download://log";
const EVENT_DONE: &str = "download://done";
const SIDECARS: &[&str] = &["yt-dlp", "ffmpeg", "ffprobe", "deno"];
const PROBE_TIMEOUT: Duration = Duration::from_secs(90);
const MIN_FREE_BYTES: u64 = 1 << 30; // 1 GiB
const PROGRESS_INTERVAL: Duration = Duration::from_millis(250); // ≤ 4/s
const STDERR_TAIL: usize = 200;

const STOP_NONE: u8 = 0;
const STOP_PAUSE: u8 = 1;
const STOP_CANCEL: u8 = 2;

struct Running {
    tree: proc::ProcessTree,
    stop: AtomicU8,
}

#[derive(Default)]
struct Jobs {
    downloads: Mutex<HashMap<String, Arc<Running>>>,
    probes: Mutex<HashMap<String, Vec<Arc<Running>>>>,
    tool_versions: Mutex<Option<ToolVersions>>,
}

/// None when the DB could not be opened; downloads still work, history and
/// reveal/open report an error.
struct Store(Option<Db>);

impl Store {
    fn db(&self) -> CmdResult<&Db> {
        self.0.as_ref().ok_or_else(|| CommandError::unknown("local database is unavailable"))
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn db_err(e: rusqlite::Error) -> CommandError {
    CommandError::unknown(format!("database error: {e}"))
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProgressEvent<'a> {
    job_id: &'a str,
    stage: Stage,
    percent: Option<f64>,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
    speed_bps: Option<f64>,
    eta_sec: Option<f64>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LogEvent<'a> {
    job_id: &'a str,
    line: &'a str,
}

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct DoneEvent {
    job_id: String,
    state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    file_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    file_size: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_message: Option<String>,
}

#[derive(Clone, Serialize, Default)]
struct ToolVersions {
    ytdlp: Option<String>,
    ffmpeg: Option<String>,
    deno: Option<String>,
}

// ---------------------------------------------------------------- helpers

/// Hashes all sidecars (off the async runtime; ffmpeg alone is ~100 MB).
/// Every spawn goes through this, so a swapped binary is never executed.
async fn verified_sidecars() -> CmdResult<Vec<PathBuf>> {
    tauri::async_runtime::spawn_blocking(|| SIDECARS.iter().map(|n| checksum::verify(n)).collect::<Result<Vec<_>, _>>())
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
}

fn ytdlp_command(ytdlp: &Path, args: Vec<std::ffi::OsString>) -> Command {
    let mut cmd = Command::new(ytdlp);
    cmd.args(args)
        // yt-dlp.exe writes in the console code page when piped; force UTF-8.
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd
}

fn valid_url(raw: &str) -> CmdResult<String> {
    validate::url(raw).map_err(|e| CommandError::new(Code::InvalidUrl, e))
}

fn valid_out_dir(raw: &str) -> CmdResult<PathBuf> {
    let p = PathBuf::from(raw);
    if !p.is_absolute() || !p.is_dir() {
        return Err(CommandError::unknown("output folder does not exist"));
    }
    paths::canonical(&p)
        .map(|c| paths::display_form(&c))
        .map_err(|e| CommandError::unknown(format!("output folder: {e}")))
}

fn lines_of<R: Read>(pipe: R) -> impl Iterator<Item = String> {
    BufReader::new(pipe)
        .split(b'\n')
        .map_while(Result::ok)
        .map(|chunk| String::from_utf8_lossy(&chunk).trim_end_matches('\r').to_string())
}

// ---------------------------------------------------------------- probe

#[tauri::command]
async fn probe(jobs: State<'_, Jobs>, url: String) -> CmdResult<formats::ProbeResult> {
    let url = valid_url(&url)?;
    let paths = verified_sidecars().await?;
    let tools = engine::Tools { ffmpeg: &paths[1], deno: &paths[3] };
    let cmd = ytdlp_command(&paths[0], engine::probe_args(&tools, &url));
    let (mut child, tree) =
        proc::spawn_tree(cmd).map_err(|e| CommandError::new(Code::ToolMissing, format!("cannot start yt-dlp: {e}")))?;
    let running = Arc::new(Running { tree, stop: AtomicU8::new(STOP_NONE) });
    lock(&jobs.probes).entry(url.clone()).or_default().push(running.clone());

    let stdout = child.stdout.take().expect("piped");
    let stderr = child.stderr.take().expect("piped");
    let out_t = std::thread::spawn(move || {
        let mut buf = Vec::new();
        // -J output for a long YouTube video is a few MB; cap at 64 MB.
        let _ = stdout.take(64 << 20).read_to_end(&mut buf);
        buf
    });
    let err_t = std::thread::spawn(move || {
        let mut tail = VecDeque::new();
        for l in lines_of(stderr) {
            if tail.len() == STDERR_TAIL {
                tail.pop_front();
            }
            tail.push_back(l);
        }
        Vec::from(tail)
    });

    let r = running.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + PROBE_TIMEOUT;
        let mut timed_out = false;
        let status = loop {
            match child.try_wait() {
                Ok(Some(s)) => break Some(s),
                Ok(None) if Instant::now() >= deadline => {
                    timed_out = true;
                    let _ = r.tree.kill();
                    break child.wait().ok();
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                Err(_) => break None,
            }
        };
        let _ = r.tree.kill(); // stragglers
        (status, timed_out, out_t.join().unwrap_or_default(), err_t.join().unwrap_or_default())
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()));

    {
        let mut probes = lock(&jobs.probes);
        if let Some(v) = probes.get_mut(&url) {
            v.retain(|x| !Arc::ptr_eq(x, &running));
            if v.is_empty() {
                probes.remove(&url);
            }
        }
    }
    let (status, timed_out, out, err) = result?;

    if running.stop.load(Ordering::SeqCst) == STOP_CANCEL {
        return Err(CommandError::new(Code::Cancelled, "probe cancelled"));
    }
    if timed_out {
        return Err(CommandError::new(Code::Timeout, "yt-dlp did not answer within 90 s"));
    }
    let ok = status.is_some_and(|s| s.success());
    if ok {
        if let Ok(j) = serde_json::from_slice::<serde_json::Value>(&out) {
            if j.is_object() {
                return Ok(formats::map_probe(&url, &j));
            }
        }
    }
    let code = error::classify(&err);
    let msg = error::last_error_line(&err).map(String::from).unwrap_or_else(|| match status.and_then(|s| s.code()) {
        Some(c) => format!("yt-dlp exited with code {c}"),
        None => "yt-dlp failed".into(),
    });
    Err(CommandError::new(code, msg))
}

#[tauri::command]
fn cancel_probe(jobs: State<'_, Jobs>, url: String) -> CmdResult<()> {
    let Ok(url) = validate::url(&url) else { return Ok(()) };
    let list = lock(&jobs.probes).get(&url).cloned().unwrap_or_default();
    for r in list {
        r.stop.store(STOP_CANCEL, Ordering::SeqCst);
        let _ = r.tree.kill();
    }
    Ok(())
}

// ---------------------------------------------------------------- download

/// Shared between the two pipe readers of one job.
struct JobIo {
    job_id: String,
    out_dir: PathBuf,
    throttle: Mutex<Throttle>,
    tracked: Mutex<HashSet<PathBuf>>,
    final_path: Mutex<Option<String>>,
    stderr_tail: Mutex<VecDeque<String>>,
}

fn handle_line(app: &AppHandle, io: &JobIo, line: &str, is_stderr: bool) {
    match progress::parse_line(line) {
        Line::Progress { update, files } => {
            if !files.is_empty() {
                let mut tracked = lock(&io.tracked);
                for f in files {
                    let p = PathBuf::from(f);
                    if tracked.insert(p.clone()) {
                        if let Some(db) = &app.state::<Store>().0 {
                            let _ = db.add_partial(&io.job_id, &io.out_dir, &p);
                        }
                    }
                }
            }
            let Some(stage) = update.stage else { return };
            if !lock(&io.throttle).allow(Instant::now(), &update) {
                return;
            }
            let _ = app.emit(
                EVENT_PROGRESS,
                ProgressEvent {
                    job_id: &io.job_id,
                    stage,
                    percent: update.percent,
                    downloaded_bytes: update.downloaded_bytes,
                    total_bytes: update.total_bytes,
                    speed_bps: update.speed_bps,
                    eta_sec: update.eta_sec,
                },
            );
        }
        Line::FinalPath(p) => *lock(&io.final_path) = Some(p),
        Line::Other => {
            if line.trim().is_empty() {
                return;
            }
            if is_stderr {
                let mut t = lock(&io.stderr_tail);
                if t.len() == STDERR_TAIL {
                    t.pop_front();
                }
                t.push_back(line.to_string());
            }
            let _ = app.emit(EVENT_LOG, LogEvent { job_id: &io.job_id, line });
        }
    }
}

fn pump<R: Read + Send + 'static>(app: AppHandle, io: Arc<JobIo>, pipe: R, is_stderr: bool) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        for line in lines_of(pipe) {
            handle_line(&app, &io, &line, is_stderr);
        }
    })
}

/// Deletes this job's partial files (in memory ∪ recorded in the DB), then
/// forgets them.
fn delete_partials(app: &AppHandle, job_id: &str, out_dir: Option<&Path>, extra: &HashSet<PathBuf>) -> Vec<String> {
    let store = app.state::<Store>();
    let mut tracked: Vec<PathBuf> = extra.iter().cloned().collect();
    let mut dir = out_dir.map(Path::to_path_buf);
    if let Some(db) = &store.0 {
        if let Ok(Some((d, files))) = db.partials(job_id) {
            dir.get_or_insert(d);
            tracked.extend(files);
        }
    }
    let Some(dir) = dir else { return Vec::new() };
    let targets = paths::deletion_targets(&dir, &tracked, |p| store.0.as_ref().is_some_and(|db| db.is_produced(p)));
    let failed = paths::delete_files(&targets);
    if let Some(db) = &store.0 {
        let _ = db.clear_partials(job_id);
    }
    failed.into_iter().map(|(p, e)| format!("could not delete {}: {e}", p.display())).collect()
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn start_download(
    app: AppHandle,
    jobs: State<'_, Jobs>,
    job_id: String,
    url: String,
    out_dir: String,
    format_id: Option<String>,
    audio_only: Option<bool>,
) -> CmdResult<()> {
    if !paths::valid_job_id(&job_id) {
        return Err(CommandError::unknown("invalid job id"));
    }
    let url = valid_url(&url)?;
    let out_dir = valid_out_dir(&out_dir)?;
    if let Some(f) = &format_id {
        validate::format_id(f).map_err(CommandError::unknown)?;
    }
    let audio_only = audio_only.unwrap_or(false);
    if lock(&jobs.downloads).contains_key(&job_id) {
        return Err(CommandError::unknown("this job is already running"));
    }

    let free = sys::disk_free(&out_dir).map_err(|e| CommandError::unknown(format!("cannot read free space: {e}")))?;
    if free < MIN_FREE_BYTES {
        return Err(CommandError::new(
            Code::DiskFull,
            format!("only {} MiB free in the output folder; at least 1 GiB is required", free >> 20),
        ));
    }

    let paths = verified_sidecars().await?;
    let tools = engine::Tools { ffmpeg: &paths[1], deno: &paths[3] };
    let args = engine::download_args(&tools, &url, &out_dir, format_id.as_deref(), audio_only);
    let cmd = ytdlp_command(&paths[0], args);

    // Insert under the lock right after spawning so a concurrent start with
    // the same id cannot slip in between the check above and here.
    let (mut child, running) = {
        let mut map = lock(&jobs.downloads);
        if map.contains_key(&job_id) {
            return Err(CommandError::unknown("this job is already running"));
        }
        let (child, tree) = proc::spawn_tree(cmd)
            .map_err(|e| CommandError::new(Code::ToolMissing, format!("cannot start yt-dlp: {e}")))?;
        let running = Arc::new(Running { tree, stop: AtomicU8::new(STOP_NONE) });
        map.insert(job_id.clone(), running.clone());
        (child, running)
    };

    let io = Arc::new(JobIo {
        job_id: job_id.clone(),
        out_dir: out_dir.clone(),
        throttle: Mutex::new(Throttle::new(PROGRESS_INTERVAL)),
        tracked: Mutex::new(HashSet::new()),
        final_path: Mutex::new(None),
        stderr_tail: Mutex::new(VecDeque::new()),
    });
    let out = pump(app.clone(), io.clone(), child.stdout.take().expect("piped"), false);
    let err = pump(app.clone(), io.clone(), child.stderr.take().expect("piped"), true);

    std::thread::spawn(move || {
        let status = child.wait();
        let _ = out.join();
        let _ = err.join();
        let _ = running.tree.kill(); // stragglers (no-op if the tree is gone)
        lock(&app.state::<Jobs>().downloads).remove(&job_id);

        let success = status.as_ref().is_ok_and(|s| s.success());
        let final_path = lock(&io.final_path).clone();
        let stop = running.stop.load(Ordering::SeqCst);
        let mut ev = DoneEvent { job_id: job_id.clone(), ..Default::default() };

        if success && final_path.is_some() {
            // Finished, even if pause/cancel was pressed in the last instant.
            let raw = PathBuf::from(final_path.unwrap());
            match paths::canonical(&raw) {
                Ok(canon) if canon.is_file() => {
                    let store = app.state::<Store>();
                    if let Some(db) = &store.0 {
                        let _ = db.add_produced(&canon, &job_id);
                        let _ = db.clear_partials(&job_id);
                    }
                    ev.state = "completed";
                    ev.file_size = std::fs::metadata(&canon).ok().map(|m| m.len());
                    ev.file_path = Some(paths::display_form(&canon).to_string_lossy().into_owned());
                }
                _ => {
                    ev.state = "failed";
                    ev.error_code = Some(Code::Unknown.as_str());
                    ev.error_message = Some(format!("yt-dlp reported {} but the file is missing", raw.display()));
                }
            }
        } else if stop == STOP_CANCEL {
            let tracked = lock(&io.tracked).clone();
            let problems = delete_partials(&app, &job_id, Some(&io.out_dir), &tracked);
            for p in &problems {
                let _ = app.emit(EVENT_LOG, LogEvent { job_id: &job_id, line: p });
            }
            ev.state = "cancelled";
        } else if stop == STOP_PAUSE {
            ev.state = "paused";
        } else {
            let tail: Vec<String> = lock(&io.stderr_tail).iter().cloned().collect();
            ev.state = "failed";
            let code = if success { Code::Unknown } else { error::classify(&tail) };
            ev.error_code = Some(code.as_str());
            ev.error_message = Some(if success {
                "yt-dlp finished without reporting an output file".into()
            } else {
                error::last_error_line(&tail).map(String::from).unwrap_or_else(|| match status {
                    Ok(s) => format!("yt-dlp exited with {s}"),
                    Err(e) => format!("yt-dlp wait failed: {e}"),
                })
            });
        }
        let _ = app.emit(EVENT_DONE, ev);
    });

    Ok(())
}

#[tauri::command]
fn pause_download(jobs: State<'_, Jobs>, job_id: String) -> CmdResult<()> {
    let running = lock(&jobs.downloads).get(&job_id).cloned();
    if let Some(r) = running {
        r.stop.store(STOP_PAUSE, Ordering::SeqCst);
        r.tree.kill().map_err(|e| CommandError::unknown(format!("pause failed: {e}")))?;
    }
    Ok(())
}

#[tauri::command]
async fn cancel_download(app: AppHandle, jobs: State<'_, Jobs>, job_id: String) -> CmdResult<()> {
    if !paths::valid_job_id(&job_id) {
        return Err(CommandError::unknown("invalid job id"));
    }
    let running = lock(&jobs.downloads).get(&job_id).cloned();
    match running {
        Some(r) => {
            // The waiter thread deletes the partial files and emits "cancelled".
            r.stop.store(STOP_CANCEL, Ordering::SeqCst);
            r.tree.kill().map_err(|e| CommandError::unknown(format!("cancel failed: {e}")))?;
        }
        None => {
            // Paused, failed, or from an earlier app session.
            let a = app.clone();
            let id = job_id.clone();
            let problems = tauri::async_runtime::spawn_blocking(move || delete_partials(&a, &id, None, &HashSet::new()))
                .await
                .map_err(|e| CommandError::unknown(e.to_string()))?;
            for p in &problems {
                let _ = app.emit(EVENT_LOG, LogEvent { job_id: &job_id, line: p });
            }
            let _ = app.emit(EVENT_DONE, DoneEvent { job_id, state: "cancelled", ..Default::default() });
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- files

/// Native folder picker. Async so the blocking dialog never runs on the main
/// thread. The webview gets no dialog:* permission; only this command.
#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
fn default_download_dir(app: AppHandle) -> CmdResult<String> {
    let base = app
        .path()
        .download_dir()
        .map_err(|e| CommandError::unknown(format!("no Downloads folder: {e}")))?;
    let dir = base.join("VidGrab");
    std::fs::create_dir_all(&dir).map_err(|e| CommandError::unknown(format!("cannot create {}: {e}", dir.display())))?;
    Ok(dir.to_string_lossy().into_owned())
}

#[tauri::command]
fn disk_free(path: String) -> CmdResult<u64> {
    let p = PathBuf::from(&path);
    if !p.is_absolute() {
        return Err(CommandError::unknown("path must be absolute"));
    }
    sys::disk_free(&p).map_err(|e| CommandError::unknown(format!("cannot read free space: {e}")))
}

fn produced_or_refuse(store: &Store, path: &str, need_media: bool) -> CmdResult<PathBuf> {
    let db = store.db()?;
    paths::check_produced(path, need_media, |p| db.is_produced(p)).map_err(|r| match r {
        paths::Refusal::NotFound => CommandError::new(Code::NotFound, "file no longer exists"),
        paths::Refusal::NotProduced => CommandError::unknown("refused: this file was not downloaded by VidGrab"),
        paths::Refusal::NotMedia => CommandError::unknown("refused: not a media file"),
    })
}

#[tauri::command]
fn reveal_path(store: State<'_, Store>, path: String) -> CmdResult<()> {
    let canon = produced_or_refuse(&store, &path, false)?;
    sys::reveal(&paths::display_form(&canon)).map_err(|e| CommandError::unknown(format!("cannot open folder: {e}")))
}

#[tauri::command]
async fn open_path(store: State<'_, Store>, path: String) -> CmdResult<()> {
    let canon = produced_or_refuse(&store, &path, true)?;
    let p = paths::display_form(&canon);
    tauri::async_runtime::spawn_blocking(move || sys::open_default(&p))
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(|e| CommandError::unknown(format!("cannot open file: {e}")))
}

/// Opens one of VidGrab's own web pages in the default browser
/// (validate::OPEN_URL_HOSTS). The webview has no opener/shell permission.
#[tauri::command]
async fn open_url(url: String) -> CmdResult<()> {
    let url = validate::open_url(&url).map_err(|e| CommandError::new(Code::InvalidUrl, e))?;
    tauri::async_runtime::spawn_blocking(move || sys::open_url(&url))
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(|e| CommandError::unknown(format!("cannot open link: {e}")))
}

// ---------------------------------------------------------------- history

#[tauri::command]
fn history_list(
    store: State<'_, Store>,
    limit: Option<u32>,
    offset: Option<u32>,
    query: Option<String>,
) -> CmdResult<Vec<HistoryItem>> {
    store.db()?.list(limit, offset, query.as_deref()).map_err(db_err)
}

#[tauri::command]
fn history_add(store: State<'_, Store>, item: HistoryItem) -> CmdResult<()> {
    item.validate().map_err(CommandError::unknown)?;
    store.db()?.upsert(&item).map_err(db_err)
}

#[tauri::command]
fn history_delete(store: State<'_, Store>, id: String) -> CmdResult<()> {
    store.db()?.delete(&id).map_err(db_err)
}

#[tauri::command]
fn history_clear(store: State<'_, Store>) -> CmdResult<()> {
    store.db()?.clear().map_err(db_err)
}

#[tauri::command]
fn history_mark_synced(store: State<'_, Store>, ids: Vec<String>) -> CmdResult<()> {
    if ids.len() > 10_000 {
        return Err(CommandError::unknown("too many ids"));
    }
    store.db()?.mark_synced(&ids).map_err(db_err)
}

// ---------------------------------------------------------------- auth

#[tauri::command]
async fn auth_save(session: String) -> CmdResult<()> {
    tauri::async_runtime::spawn_blocking(move || auth::save(&session))
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(CommandError::unknown)
}

#[tauri::command]
async fn auth_load() -> CmdResult<Option<String>> {
    tauri::async_runtime::spawn_blocking(auth::load)
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(CommandError::unknown)
}

#[tauri::command]
async fn auth_clear() -> CmdResult<()> {
    tauri::async_runtime::spawn_blocking(auth::clear)
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(CommandError::unknown)
}

// ---------------------------------------------------------------- versions

#[tauri::command]
fn get_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

/// Runs a verified sidecar with a fixed argument and returns its first
/// stdout line (10 s limit).
fn first_line(path: &Path, args: &[&str]) -> Option<String> {
    let mut cmd = Command::new(path);
    cmd.args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    let (mut child, tree) = proc::spawn_tree(cmd).ok()?;
    let stdout = child.stdout.take()?;
    let reader = std::thread::spawn(move || lines_of(stdout).find(|l| !l.trim().is_empty()));
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = tree.kill();
                let _ = child.wait();
                break;
            }
        }
    }
    reader.join().ok().flatten().map(|l| l.trim().to_string())
}

#[tauri::command]
async fn tool_versions(jobs: State<'_, Jobs>) -> CmdResult<ToolVersions> {
    if let Some(v) = lock(&jobs.tool_versions).clone() {
        return Ok(v);
    }
    let paths = verified_sidecars().await?;
    let v = tauri::async_runtime::spawn_blocking(move || ToolVersions {
        ytdlp: first_line(&paths[0], &["--ignore-config", "--no-plugin-dirs", "--version"]),
        ffmpeg: first_line(&paths[1], &["-hide_banner", "-version"]).map(|l| {
            l.strip_prefix("ffmpeg version ").and_then(|r| r.split_whitespace().next()).unwrap_or(&l).to_string()
        }),
        deno: first_line(&paths[3], &["--version"])
            .map(|l| l.strip_prefix("deno ").and_then(|r| r.split_whitespace().next()).unwrap_or(&l).to_string()),
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()))?;
    *lock(&jobs.tool_versions) = Some(v.clone());
    Ok(v)
}

// ---------------------------------------------------------------- app

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Only with `--features updater` (stage C2, needs the key pair).
            // The webview is never granted updater:*; checks run from Rust.
            #[cfg(all(desktop, feature = "updater"))]
            app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;

            let db = app
                .path()
                .app_data_dir()
                .ok()
                .and_then(|d| std::fs::create_dir_all(&d).ok().map(|_| d.join("vidgrab.db")))
                .and_then(|p| Db::open(&p).ok());
            app.manage(Store(db));
            Ok(())
        })
        .manage(Jobs::default())
        .invoke_handler(tauri::generate_handler![
            probe,
            cancel_probe,
            start_download,
            pause_download,
            cancel_download,
            pick_folder,
            default_download_dir,
            disk_free,
            reveal_path,
            open_path,
            open_url,
            history_list,
            history_add,
            history_delete,
            history_clear,
            history_mark_synced,
            auth_save,
            auth_load,
            auth_clear,
            get_version,
            tool_versions
        ])
        .run(tauri::generate_context!())
        .expect("error while running VidGrab");
}
