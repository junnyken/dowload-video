//! VidGrab desktop, stage C0 spike.
//!
//! The webview can call exactly four commands (see capabilities/default.json):
//! start_download, cancel_download, pick_folder, get_version. There is no
//! generic "run a program" bridge, and nothing goes through a shell: the
//! yt-dlp sidecar is started with a fixed argument vector built here.

mod checksum;
mod proc;
mod validate;

use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

const EVENT_LOG: &str = "download://log";
const EVENT_DONE: &str = "download://done";
const SIDECARS: &[&str] = &["yt-dlp", "ffmpeg", "ffprobe", "deno"];

struct Running {
    tree: proc::ProcessTree,
    cancelled: AtomicBool,
}

#[derive(Default)]
struct Jobs {
    next_id: AtomicU64,
    running: Mutex<HashMap<String, Arc<Running>>>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LogEvent<'a> {
    job_id: &'a str,
    stream: &'a str,
    line: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DoneEvent {
    job_id: String,
    exit_code: Option<i32>,
    cancelled: bool,
}

/// Emits each line of a child pipe as a `download://log` event.
fn pump<R: Read + Send + 'static>(
    app: AppHandle,
    job_id: String,
    stream: &'static str,
    pipe: R,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        for chunk in BufReader::new(pipe).split(b'\n').map_while(Result::ok) {
            let line = String::from_utf8_lossy(&chunk).trim_end_matches('\r').to_string();
            let _ = app.emit(EVENT_LOG, LogEvent { job_id: &job_id, stream, line });
        }
    })
}

#[tauri::command]
async fn start_download(
    app: AppHandle,
    jobs: State<'_, Jobs>,
    url: String,
    out_dir: String,
    format_id: Option<String>,
) -> Result<String, String> {
    let url = validate::url(&url)?;
    let out_dir = PathBuf::from(out_dir);
    if !out_dir.is_absolute() || !out_dir.is_dir() {
        return Err("output folder does not exist".into());
    }
    if let Some(f) = &format_id {
        validate::format_id(f)?;
    }

    // Hash all sidecars off the async runtime (ffmpeg is ~100 MB).
    let paths = tauri::async_runtime::spawn_blocking(|| {
        SIDECARS.iter().map(|n| checksum::verify(n)).collect::<Result<Vec<_>, _>>()
    })
    .await
    .map_err(|e| e.to_string())??;
    let (ytdlp, ffmpeg, deno) = (&paths[0], &paths[1], &paths[3]);

    // Fixed argv. Config files and plugins are ignored so that a yt-dlp.conf
    // dropped next to the exe (or in the user profile) cannot add --exec.
    let mut cmd = Command::new(ytdlp);
    cmd.arg("--ignore-config")
        .arg("--no-plugin-dirs")
        .arg("--newline")
        .arg("--ffmpeg-location")
        .arg(ffmpeg) // ffprobe sits in the same directory
        .arg("--no-js-runtimes")
        .arg("--js-runtimes")
        .arg(format!("deno:{}", deno.display()))
        .arg("-P")
        .arg(&out_dir)
        .arg("-o")
        .arg("%(title).150B [%(id)s].%(ext)s");
    if let Some(f) = &format_id {
        cmd.arg("-f").arg(f);
    }
    cmd.arg("--").arg(&url);
    // yt-dlp.exe writes in the console code page when piped; force UTF-8.
    cmd.env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let (mut child, tree) = proc::spawn_tree(cmd).map_err(|e| format!("cannot start yt-dlp: {e}"))?;
    let job_id = format!("job-{}", jobs.next_id.fetch_add(1, Ordering::Relaxed) + 1);
    let running = Arc::new(Running { tree, cancelled: AtomicBool::new(false) });
    jobs.running.lock().unwrap().insert(job_id.clone(), running.clone());

    let out = pump(app.clone(), job_id.clone(), "stdout", child.stdout.take().unwrap());
    let err = pump(app.clone(), job_id.clone(), "stderr", child.stderr.take().unwrap());

    let id = job_id.clone();
    std::thread::spawn(move || {
        let status = child.wait();
        let _ = out.join();
        let _ = err.join();
        // Removing the entry drops the ProcessTree; on Windows that closes the
        // job handle, which also kills any straggler left in the tree.
        app.state::<Jobs>().running.lock().unwrap().remove(&id);
        let _ = app.emit(
            EVENT_DONE,
            DoneEvent {
                job_id: id,
                exit_code: status.ok().and_then(|s| s.code()),
                cancelled: running.cancelled.load(Ordering::SeqCst),
            },
        );
    });

    Ok(job_id)
}

#[tauri::command]
fn cancel_download(jobs: State<'_, Jobs>, job_id: String) -> Result<bool, String> {
    let running = jobs.running.lock().unwrap().get(&job_id).cloned();
    match running {
        Some(r) => {
            r.cancelled.store(true, Ordering::SeqCst);
            r.tree.kill().map_err(|e| format!("cancel failed: {e}"))?;
            Ok(true)
        }
        None => Ok(false), // already finished
    }
}

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
fn get_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Registered for C2. The webview is NOT granted updater:* in C0;
            // checks will be driven from Rust once the key pair exists.
            #[cfg(desktop)]
            app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            Ok(())
        })
        .manage(Jobs::default())
        .invoke_handler(tauri::generate_handler![
            start_download,
            cancel_download,
            pick_folder,
            get_version
        ])
        .run(tauri::generate_context!())
        .expect("error while running VidGrab");
}
