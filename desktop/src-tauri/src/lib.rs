//! VidGrab desktop, stage C1.
//!
//! The webview can call exactly the commands of docs/desktop/C1-CONTRACT.md
//! (see capabilities/default.json). There is no generic "run a program" or
//! file-system bridge, and nothing goes through a shell: sidecars are started
//! with fixed argument vectors (engine.rs), after a SHA256 check of every
//! sidecar (checksum.rs), in a Job Object so the whole tree dies on
//! pause/cancel/app exit (proc.rs).
//!
//! §4 adds channel listing/storage, a tray icon (the window hides to the
//! tray on close), start-with-Windows and single-instance. Notification and
//! autostart plugins are driven only from Rust; the webview gets no
//! permission of theirs.

mod auth;
mod browser_login;
mod channels;
mod checksum;
mod cookies;
mod device;
mod engine;
mod error;
mod formats;
mod history;
mod login_window;
mod paths;
mod proc;
mod progress;
mod sys;
mod validate;

use error::{CmdResult, Code, CommandError};
use history::{Db, HistoryItem};
use progress::{Line, Stage, Throttle};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;

const EVENT_PROGRESS: &str = "download://progress";
const EVENT_LOG: &str = "download://log";
const EVENT_DONE: &str = "download://done";
const SIDECARS: &[&str] = &["yt-dlp", "ffmpeg", "ffprobe", "deno"];
const EVENT_CHECK_NOW: &str = "channels://check-now";
const EVENT_QUITTING: &str = "app://quitting";
/// A login window (login_window.rs) was closed; payload `{ platform }`.
const EVENT_LOGIN_CLOSED: &str = "cookies://login-closed";
const MAIN_WINDOW: &str = "main";
const MINIMIZED_ARG: &str = "--minimized";
const PROBE_TIMEOUT: Duration = Duration::from_secs(90);
const CHANNEL_FETCH_TIMEOUT: Duration = Duration::from_secs(180);
/// How long "Thoát" waits for the UI to pause downloads before killing.
const QUIT_GRACE: Duration = Duration::from_secs(3);
/// WebView2 throttles JS timers while the window is hidden in the tray, so
/// Rust also nudges the UI scheduler. The UI only checks channels whose
/// interval has elapsed, so extra ticks are harmless.
const CHECK_TICK: Duration = Duration::from_secs(5 * 60);
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

/// Short-lived yt-dlp runs keyed by URL (several may share one URL).
type Registry = Mutex<HashMap<String, Vec<Arc<Running>>>>;

#[derive(Default)]
struct Jobs {
    downloads: Mutex<HashMap<String, Arc<Running>>>,
    probes: Registry,
    channel_fetches: Registry,
    tool_versions: Mutex<Option<ToolVersions>>,
}

impl Jobs {
    /// Kills every process tree we started (used on quit).
    fn kill_all(&self) {
        for r in lock(&self.downloads).values() {
            r.stop.store(STOP_PAUSE, Ordering::SeqCst);
            let _ = r.tree.kill();
        }
        for reg in [&self.probes, &self.channel_fetches] {
            for r in lock(reg).values().flatten() {
                r.stop.store(STOP_CANCEL, Ordering::SeqCst);
                let _ = r.tree.kill();
            }
        }
    }
}

/// Window/tray behaviour (in memory; the UI re-applies its setting at start).
struct AppFlags {
    close_to_tray: AtomicBool,
    quitting: AtomicBool,
}

impl Default for AppFlags {
    fn default() -> Self {
        AppFlags { close_to_tray: AtomicBool::new(true), quitting: AtomicBool::new(false) }
    }
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
    /// Present (true) when this run used the user's saved cookies.
    #[serde(skip_serializing_if = "Option::is_none")]
    cookies_used: Option<bool>,
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
    // Channel folders (`<default dir>\<channel title>`) are created on the
    // first download; only absolute paths, and only directories.
    if p.is_absolute() && !p.exists() {
        std::fs::create_dir_all(&p).map_err(|e| CommandError::unknown(format!("cannot create output folder: {e}")))?;
    }
    if !p.is_absolute() || !p.is_dir() {
        return Err(CommandError::unknown("output folder does not exist"));
    }
    paths::canonical(&p)
        .map(|c| paths::display_form(&c))
        .map_err(|e| CommandError::unknown(format!("output folder: {e}")))
}

// ---------------------------------------------------------------- cookies (PLAN-32D §3)

/// `<app data>/cookies`: the encrypted blobs (next to vidgrab.db).
fn cookie_dir(app: &AppHandle) -> CmdResult<PathBuf> {
    app.path()
        .app_data_dir()
        .map(|d| d.join("cookies"))
        .map_err(|e| CommandError::unknown(format!("no app data folder: {e}")))
}

/// `<local app data>/tmp`: per-run cookie files, deleted after each run.
fn cookie_tmp_dir(app: &AppHandle) -> CmdResult<PathBuf> {
    app.path()
        .app_local_data_dir()
        .map(|d| d.join("tmp"))
        .map_err(|e| CommandError::unknown(format!("no local app data folder: {e}")))
}

fn valid_platform(raw: &str) -> CmdResult<&'static str> {
    validate::cookie_platform(raw).map_err(CommandError::unknown)
}

/// Cookies for one yt-dlp run. Rust picks the platform from the URL host; the
/// webview only says whether it wants cookies at all. None when the URL is not
/// a cookie platform or nothing is saved for it. A blob that cannot be
/// decrypted is flagged "suspect" and the run goes on without cookies.
struct RunCookies {
    file: cookies::TempCookieFile,
    slug: &'static str,
    dir: PathBuf,
}

impl RunCookies {
    fn path(&self) -> &Path {
        self.file.path()
    }
    fn needle(&self) -> String {
        self.file.needle().to_string()
    }
    /// A login / forbidden failure while using these cookies: flag the blob.
    fn after_failure(&self, code: Code) {
        if matches!(code, Code::PrivateOrLogin | Code::Forbidden) {
            cookies::mark_suspect(&self.dir, self.slug);
        }
    }
}

async fn run_cookies(app: &AppHandle, url: &str, wanted: Option<bool>) -> CmdResult<Option<RunCookies>> {
    if wanted != Some(true) {
        return Ok(None);
    }
    let Some(slug) = cookies::platform_for_url(url) else { return Ok(None) };
    let dir = cookie_dir(app)?;
    let tmp = cookie_tmp_dir(app)?;
    let key = cookies::random_key().map_err(CommandError::unknown)?;
    tauri::async_runtime::spawn_blocking(move || {
        let text = match cookies::load(&dir, slug) {
            Ok(Some(t)) => t,
            Ok(None) => return Ok(None),
            Err(_) => {
                cookies::mark_suspect(&dir, slug);
                return Ok(None);
            }
        };
        let file = cookies::write_temp(&tmp, &key, &text).map_err(CommandError::unknown)?;
        Ok(Some(RunCookies { file, slug, dir }))
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()))?
}

fn lines_of<R: Read>(pipe: R) -> impl Iterator<Item = String> {
    BufReader::new(pipe)
        .split(b'\n')
        .map_while(Result::ok)
        .map(|chunk| String::from_utf8_lossy(&chunk).trim_end_matches('\r').to_string())
}

// ---------------------------------------------------------------- probe

/// Output of a short yt-dlp run (probe, channel listing).
struct Collected {
    status: Option<std::process::ExitStatus>,
    timed_out: bool,
    cancelled: bool,
    out: Vec<u8>,
    err: Vec<String>,
}

impl Collected {
    fn ok(&self) -> bool {
        self.status.is_some_and(|s| s.success())
    }

    fn message(&self) -> String {
        error::last_error_line(&self.err).map(String::from).unwrap_or_else(|| match self.status.and_then(|s| s.code()) {
            Some(c) => format!("yt-dlp exited with code {c}"),
            None => "yt-dlp failed".into(),
        })
    }
}

/// Spawns `cmd` in a killable tree registered under `key` (so a cancel
/// command can find it), collects stdout (≤ 64 MB) and the stderr tail, and
/// kills the tree after `timeout`.
async fn run_collect(
    registry: &Registry,
    key: &str,
    cmd: Command,
    timeout: Duration,
    secret: Option<String>,
) -> CmdResult<Collected> {
    let (mut child, tree) =
        proc::spawn_tree(cmd).map_err(|e| CommandError::new(Code::ToolMissing, format!("cannot start yt-dlp: {e}")))?;
    let running = Arc::new(Running { tree, stop: AtomicU8::new(STOP_NONE) });
    lock(registry).entry(key.to_string()).or_default().push(running.clone());

    let stdout = child.stdout.take().expect("piped");
    let stderr = child.stderr.take().expect("piped");
    let out_t = std::thread::spawn(move || {
        let mut buf = Vec::new();
        // -J output for a long YouTube video is a few MB; a 5000-entry flat
        // channel listing ~10 MB. Cap at 64 MB.
        let _ = stdout.take(64 << 20).read_to_end(&mut buf);
        buf
    });
    let err_t = std::thread::spawn(move || {
        let mut tail = VecDeque::new();
        for l in lines_of(stderr) {
            if cookies::is_secret_line(&l, secret.as_deref()) {
                continue; // names the temp cookie file
            }
            if tail.len() == STDERR_TAIL {
                tail.pop_front();
            }
            tail.push_back(l);
        }
        Vec::from(tail)
    });

    let r = running.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + timeout;
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
        let mut reg = lock(registry);
        if let Some(v) = reg.get_mut(key) {
            v.retain(|x| !Arc::ptr_eq(x, &running));
            if v.is_empty() {
                reg.remove(key);
            }
        }
    }
    let (status, timed_out, out, err) = result?;
    Ok(Collected { status, timed_out, cancelled: running.stop.load(Ordering::SeqCst) == STOP_CANCEL, out, err })
}

fn cancel_in(registry: &Registry, key: &str) {
    let list = lock(registry).get(key).cloned().unwrap_or_default();
    for r in list {
        r.stop.store(STOP_CANCEL, Ordering::SeqCst);
        let _ = r.tree.kill();
    }
}

#[tauri::command]
async fn probe(app: AppHandle, jobs: State<'_, Jobs>, url: String, use_cookies: Option<bool>) -> CmdResult<formats::ProbeResult> {
    let url = valid_url(&url)?;
    let paths = verified_sidecars().await?;
    let tools = engine::Tools { ffmpeg: &paths[1], deno: &paths[3] };
    let ck = run_cookies(&app, &url, use_cookies).await?;
    let cmd = ytdlp_command(&paths[0], engine::probe_args(&tools, &url, ck.as_ref().map(RunCookies::path)));
    let c = run_collect(&jobs.probes, &url, cmd, PROBE_TIMEOUT, ck.as_ref().map(RunCookies::needle)).await?;
    if let Some(k) = ck {
        if !c.ok() && !c.cancelled && !c.timed_out {
            k.after_failure(error::classify(&c.err));
        }
        drop(k); // deletes the temp file
    }

    if c.cancelled {
        return Err(CommandError::new(Code::Cancelled, "probe cancelled"));
    }
    if c.timed_out {
        return Err(CommandError::new(Code::Timeout, "yt-dlp did not answer within 90 s"));
    }
    if c.ok() {
        if let Ok(j) = serde_json::from_slice::<serde_json::Value>(&c.out) {
            if j.is_object() {
                return Ok(formats::map_probe(&url, &j));
            }
        }
    }
    Err(CommandError::new(error::classify(&c.err), c.message()))
}

#[tauri::command]
fn cancel_probe(jobs: State<'_, Jobs>, url: String) -> CmdResult<()> {
    let Ok(url) = validate::url(&url) else { return Ok(()) };
    cancel_in(&jobs.probes, &url);
    Ok(())
}

// ---------------------------------------------------------------- channels

#[tauri::command]
async fn channel_fetch(
    app: AppHandle,
    jobs: State<'_, Jobs>,
    url: String,
    limit: Option<u32>,
    use_cookies: Option<bool>,
) -> CmdResult<channels::ChannelListing> {
    let url = channels::normalize_url(&url).map_err(|e| CommandError::new(Code::InvalidUrl, e))?;
    let limit = channels::clamp_limit(limit);
    let paths = verified_sidecars().await?;
    let tools = engine::Tools { ffmpeg: &paths[1], deno: &paths[3] };
    let ck = run_cookies(&app, &url, use_cookies).await?;
    let cmd = ytdlp_command(&paths[0], engine::channel_fetch_args(&tools, &url, limit, ck.as_ref().map(RunCookies::path)));
    let c = run_collect(&jobs.channel_fetches, &url, cmd, CHANNEL_FETCH_TIMEOUT, ck.as_ref().map(RunCookies::needle)).await?;
    if let Some(k) = ck {
        if !c.ok() && !c.cancelled && !c.timed_out {
            k.after_failure(error::classify(&c.err));
        }
        drop(k); // deletes the temp file
    }

    if c.cancelled {
        return Err(CommandError::new(Code::Cancelled, "channel fetch cancelled"));
    }
    if c.timed_out {
        return Err(CommandError::new(Code::Timeout, "yt-dlp did not list the channel within 180 s"));
    }
    // yt-dlp may print a playlist AND exit 1 when some entries failed; a
    // listing with at least one video is still useful.
    let map_err = match serde_json::from_slice::<serde_json::Value>(&c.out) {
        Ok(j) if j.is_object() => match channels::map_listing(&url, &j, limit) {
            Ok(listing) => return Ok(listing),
            Err(e) => Some(e),
        },
        _ => None,
    };
    let code = channels::failure_code(map_err.as_ref(), c.ok(), &c.err);
    let message = match (&map_err, c.ok()) {
        (Some(channels::MapError::NotAPlaylist), true) => "this URL is a single video, not a channel or playlist".into(),
        (Some(channels::MapError::Empty), true) => "the channel lists no videos".into(),
        _ => c.message(),
    };
    Err(CommandError::new(code, message))
}

#[tauri::command]
fn cancel_channel_fetch(jobs: State<'_, Jobs>, url: String) -> CmdResult<()> {
    let Ok(url) = channels::normalize_url(&url) else { return Ok(()) };
    cancel_in(&jobs.channel_fetches, &url);
    Ok(())
}

#[tauri::command]
fn channel_save(store: State<'_, Store>, channel: channels::Channel) -> CmdResult<()> {
    channel.validate().map_err(CommandError::unknown)?;
    store.db()?.channel_upsert(&channel).map_err(db_err)
}

#[tauri::command]
fn channel_list(store: State<'_, Store>) -> CmdResult<Vec<channels::Channel>> {
    store.db()?.channel_list().map_err(db_err)
}

#[tauri::command]
fn channel_delete(store: State<'_, Store>, id: String) -> CmdResult<()> {
    if !channels::valid_id(&id) {
        return Err(CommandError::unknown("invalid channel id"));
    }
    store.db()?.channel_delete(&id).map_err(db_err)
}

#[tauri::command]
fn channel_seen_add(store: State<'_, Store>, channel_id: String, video_ids: Vec<String>) -> CmdResult<()> {
    channels::validate_seen_ids(&channel_id, &video_ids).map_err(CommandError::unknown)?;
    store.db()?.channel_seen_add(&channel_id, &video_ids).map_err(db_err)
}

#[tauri::command]
fn channel_seen_list(store: State<'_, Store>, channel_id: String) -> CmdResult<Vec<String>> {
    if !channels::valid_id(&channel_id) {
        return Err(CommandError::unknown("invalid channel id"));
    }
    store.db()?.channel_seen_list(&channel_id).map_err(db_err)
}

/// Windows toast. The plugin is used from Rust only; texts are capped.
#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) -> CmdResult<()> {
    use tauri_plugin_notification::NotificationExt;
    let title = channels::cap_chars(title.trim(), channels::NOTIFY_TITLE_MAX);
    let body = channels::cap_chars(body.trim(), channels::NOTIFY_BODY_MAX);
    app.notification()
        .builder()
        .title(if title.is_empty() { "VidGrab".to_string() } else { title })
        .body(body)
        .show()
        .map_err(|e| CommandError::unknown(format!("cannot show notification: {e}")))
}

// ---------------------------------------------------------------- tray, autostart

#[tauri::command]
fn autostart_get(app: AppHandle) -> CmdResult<bool> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().map_err(|e| CommandError::unknown(format!("autostart: {e}")))
}

#[tauri::command]
fn autostart_set(app: AppHandle, enabled: bool) -> CmdResult<()> {
    use tauri_plugin_autostart::ManagerExt;
    let m = app.autolaunch();
    let r = if enabled { m.enable() } else { m.disable() };
    r.map_err(|e| CommandError::unknown(format!("autostart: {e}")))
}

#[tauri::command]
fn set_close_to_tray(flags: State<'_, AppFlags>, enabled: bool) -> CmdResult<()> {
    flags.close_to_tray.store(enabled, Ordering::SeqCst);
    Ok(())
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN_WINDOW) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// "Thoát": tell the UI to pause downloads, give it QUIT_GRACE, then kill
/// whatever is still running and exit. Runs at most once.
fn quit(app: &AppHandle) {
    let flags = app.state::<AppFlags>();
    if flags.quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    let _ = app.emit(EVENT_QUITTING, ());
    let app = app.clone();
    std::thread::spawn(move || {
        let deadline = Instant::now() + QUIT_GRACE;
        let jobs = app.state::<Jobs>();
        while Instant::now() < deadline && !lock(&jobs.downloads).is_empty() {
            std::thread::sleep(Duration::from_millis(100));
        }
        jobs.kill_all();
        app.exit(0);
    });
}

#[cfg(desktop)]
fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{MenuBuilder, MenuItemBuilder};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open = MenuItemBuilder::with_id("open", "Mở VidGrab").build(app)?;
    let check = MenuItemBuilder::with_id("check", "Kiểm tra kênh ngay").build(app)?;
    let exit = MenuItemBuilder::with_id("quit", "Thoát").build(app)?;
    let menu = MenuBuilder::new(app).items(&[&open, &check]).separator().item(&exit).build()?;

    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("VidGrab")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "check" => {
                let _ = app.emit(EVENT_CHECK_NOW, ());
            }
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
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
    /// Lines containing this (the temp cookie file name) are dropped.
    secret: Option<String>,
}

fn handle_line(app: &AppHandle, io: &JobIo, line: &str, is_stderr: bool) {
    if cookies::is_secret_line(line, io.secret.as_deref()) {
        return;
    }
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

/// One extra request header for a direct-link download (validate::download_header).
#[derive(Deserialize)]
struct HeaderArg {
    name: String,
    value: String,
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
    headers: Option<Vec<HeaderArg>>,
    file_title: Option<String>,
    file_id: Option<String>,
    use_cookies: Option<bool>,
) -> CmdResult<()> {
    if !paths::valid_job_id(&job_id) {
        return Err(CommandError::unknown("invalid job id"));
    }
    let url = valid_url(&url)?;
    let headers = headers
        .unwrap_or_default()
        .iter()
        .map(|h| validate::download_header(&h.name, &h.value))
        .collect::<Result<Vec<_>, _>>()
        .map_err(CommandError::unknown)?;
    let out_name = match (&file_title, &file_id) {
        (Some(t), Some(i)) => {
            validate::file_id(i).map_err(CommandError::unknown)?;
            Some(engine::OutName { title: t, id: i })
        }
        _ => None,
    };
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
    // Random file name per run (not the job id): a second start of the same job
    // that is refused below must not delete the file the first run is using.
    let ck = run_cookies(&app, &url, use_cookies).await?;
    let args = engine::download_args(
        &tools,
        &url,
        &out_dir,
        format_id.as_deref(),
        audio_only,
        &headers,
        out_name.as_ref(),
        ck.as_ref().map(RunCookies::path),
    );
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
        secret: ck.as_ref().map(RunCookies::needle),
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
        let mut ev = DoneEvent { job_id: job_id.clone(), cookies_used: ck.as_ref().map(|_| true), ..Default::default() };

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
            if let Some(k) = &ck {
                k.after_failure(code);
            }
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
        drop(ck); // yt-dlp has exited: delete the temp cookie file before telling the UI
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
    // A channel folder may not exist yet: measure the volume it will be on.
    let existing = paths::nearest_existing(&p).ok_or_else(|| CommandError::unknown("no existing parent folder"))?;
    sys::disk_free(existing).map_err(|e| CommandError::unknown(format!("cannot read free space: {e}")))
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

/// The browser sign-in waiting right now, if any (browser_login.rs).
#[derive(Default)]
struct BrowserLogin {
    cancel: Mutex<Option<Arc<AtomicBool>>>,
}

/// "Đăng nhập qua trình duyệt": opens /desktop-login in the default browser
/// and waits (up to 5 minutes) for the one-shot loopback callback. Returns the
/// Supabase refresh token; the UI exchanges it for the app's own session.
/// Starting a new attempt cancels an older one.
#[tauri::command]
async fn browser_login(login: State<'_, BrowserLogin>) -> CmdResult<String> {
    let cancel = Arc::new(AtomicBool::new(false));
    if let Some(old) = lock(&login.cancel).replace(cancel.clone()) {
        old.store(true, Ordering::SeqCst);
    }
    let state = browser_login::new_state().map_err(CommandError::unknown)?;
    let (listener, port) =
        browser_login::bind().map_err(|e| CommandError::unknown(format!("cannot listen on 127.0.0.1: {e}")))?;
    let url = validate::open_url(&browser_login::login_url(port, &state)).map_err(|e| CommandError::new(Code::InvalidUrl, e))?;
    tauri::async_runtime::spawn_blocking(move || sys::open_url(&url))
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(|e| CommandError::unknown(format!("cannot open browser: {e}")))?;
    let c = cancel.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + browser_login::TIMEOUT;
        let r = browser_login::wait_for_callback(&listener, port, &state, deadline, &c);
        drop(listener); // one-shot: the port closes here
        r
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()))?;
    {
        let mut slot = lock(&login.cancel);
        if slot.as_ref().is_some_and(|a| Arc::ptr_eq(a, &cancel)) {
            *slot = None;
        }
    }
    result.map_err(|e| match e {
        browser_login::WaitError::Timeout => CommandError::new(Code::Timeout, "browser sign-in timed out"),
        browser_login::WaitError::Cancelled => CommandError::new(Code::Cancelled, "browser sign-in cancelled"),
        browser_login::WaitError::TooManyRequests => CommandError::unknown("too many unexpected requests on the sign-in port"),
        browser_login::WaitError::Io(m) => CommandError::unknown(format!("sign-in listener failed: {m}")),
    })
}

#[tauri::command]
fn cancel_browser_login(login: State<'_, BrowserLogin>) {
    if let Some(c) = lock(&login.cancel).take() {
        c.store(true, Ordering::SeqCst);
    }
}

#[tauri::command]
async fn auth_clear() -> CmdResult<()> {
    tauri::async_runtime::spawn_blocking(auth::clear)
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))?
        .map_err(CommandError::unknown)
}

// ---------------------------------------------------------------- platform accounts (cookies)

/// Opens the platform's own page in a `login-<platform>` window (no IPC access).
#[tauri::command]
async fn cookies_login_open(app: AppHandle, platform: String) -> CmdResult<()> {
    let slug = valid_platform(&platform)?;
    login_window::open(&app, slug).map_err(CommandError::unknown)
}

/// "Xong": reads the login window's cookies (off the main thread: WebView2
/// deadlocks otherwise), keeps the platform's own domains, stores them
/// encrypted, closes the window. Never returns or logs a cookie.
#[tauri::command]
async fn cookies_login_finish(app: AppHandle, platform: String) -> CmdResult<cookies::Saved> {
    let slug = valid_platform(&platform)?;
    let w = login_window::window(&app, slug)
        .ok_or_else(|| CommandError::new(Code::Cancelled, "the login window is not open"))?;
    let dir = cookie_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let p = cookies::def(slug).ok_or_else(|| CommandError::unknown("unknown platform"))?;
        let all = login_window::read_cookies(&w).map_err(CommandError::unknown)?;
        let kept = cookies::filter_for(p, all);
        let (text, n, _dropped) = cookies::to_netscape(&kept);
        if n == 0 {
            // Keep the window open: the user may still need to sign in / play a video.
            return Err(CommandError::new(Code::CookieRequired, "no cookie of this platform in the login window yet"));
        }
        let saved = cookies::save(&dir, slug, &text, &kept, n).map_err(CommandError::unknown)?;
        let _ = w.close();
        Ok(saved)
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()))?
}

/// One row per platform: saved?, when, maybe expired. No cookie data.
#[tauri::command]
async fn cookies_status(app: AppHandle) -> CmdResult<Vec<cookies::Status>> {
    let dir = cookie_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || cookies::status(&dir, cookies::now_secs()))
        .await
        .map_err(|e| CommandError::unknown(e.to_string()))
}

/// "Xoá": deletes the saved blob; an open login window is wiped and closed.
#[tauri::command]
async fn cookies_clear(app: AppHandle, platform: String) -> CmdResult<()> {
    let slug = valid_platform(&platform)?;
    let dir = cookie_dir(&app)?;
    let w = login_window::window(&app, slug);
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(w) = w {
            // Only on Windows, where the login window's InPrivate profile is its
            // own: on Linux WebKit this would wipe the main window's data too.
            #[cfg(windows)]
            let _ = w.clear_all_browsing_data();
            let _ = w.close();
        }
        cookies::clear(&dir, slug).map_err(CommandError::unknown)
    })
    .await
    .map_err(|e| CommandError::unknown(e.to_string()))?
}

#[derive(Clone, Serialize)]
struct LoginClosed {
    platform: String,
}

// ---------------------------------------------------------------- versions

/// Machine code for quota counting and the Settings card (device.rs).
#[tauri::command]
fn device_info(app: AppHandle) -> device::DeviceInfo {
    let dir = app.path().app_data_dir().ok();
    device::device_info(dir.as_deref())
}

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
    let mut builder = tauri::Builder::default();
    // Must be the first plugin: a second launch hands its args to us and
    // exits; we bring the window up unless it is the autostart launch.
    #[cfg(desktop)]
    {
        builder = builder
            .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
                if !args.iter().any(|a| a == MINIMIZED_ARG) {
                    show_main(app);
                }
            }))
            .plugin(tauri_plugin_autostart::init(
                tauri_plugin_autostart::MacosLauncher::LaunchAgent,
                Some(vec![MINIMIZED_ARG]),
            ));
    }
    builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
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

            // A crash between spawning yt-dlp and deleting its cookie file
            // leaves `ck-*.txt` behind; nothing is running yet, so sweep them.
            if let Ok(tmp) = cookie_tmp_dir(app.handle()) {
                std::thread::spawn(move || cookies::sweep_temp(&tmp));
            }

            #[cfg(desktop)]
            build_tray(app)?;
            let handle = app.handle().clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(CHECK_TICK);
                if handle.state::<AppFlags>().quitting.load(Ordering::SeqCst) {
                    break;
                }
                let _ = handle.emit(EVENT_CHECK_NOW, ());
            });
            // The window is created hidden (tauri.conf.json visible:false) so an
            // autostart launch (--minimized) never flashes it.
            if !std::env::args().any(|a| a == MINIMIZED_ARG) {
                show_main(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                if let Some(slug) = window.label().strip_prefix(login_window::LABEL_PREFIX) {
                    let _ = window.app_handle().emit_to(MAIN_WINDOW, EVENT_LOGIN_CLOSED, LoginClosed { platform: slug.to_string() });
                }
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                let flags = window.app_handle().state::<AppFlags>();
                if window.label() == MAIN_WINDOW
                    && flags.close_to_tray.load(Ordering::SeqCst)
                    && !flags.quitting.load(Ordering::SeqCst)
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .manage(Jobs::default())
        .manage(AppFlags::default())
        .manage(BrowserLogin::default())
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
            browser_login,
            cancel_browser_login,
            get_version,
            device_info,
            tool_versions,
            channel_fetch,
            cancel_channel_fetch,
            channel_save,
            channel_list,
            channel_delete,
            channel_seen_add,
            channel_seen_list,
            notify,
            autostart_get,
            autostart_set,
            set_close_to_tray,
            cookies_login_open,
            cookies_login_finish,
            cookies_status,
            cookies_clear
        ])
        .run(tauri::generate_context!())
        .expect("error while running VidGrab");
}
