//! In-app update of the app itself (task #6205, docs/desktop/UPDATER.md).
//!
//! The webview gets no `updater:*` / `process:*` permission. It may call two
//! commands of ours:
//!
//! * `update_check` asks the endpoint in tauri.conf.json
//!   (`/api/v1/client/update/{target}/{arch}/{current_version}`) and keeps the
//!   answer here; the webview only sees the version and notes.
//! * `update_install` downloads THAT update (progress on `update://progress`),
//!   checks its minisign signature against `plugins.updater.pubkey` (the
//!   plugin refuses anything unsigned or signed by another key), pauses and
//!   kills our downloads like "Thoát", then runs the NSIS installer in passive
//!   mode with `/R`: Windows exits this process, the installer updates the
//!   per-user install in place (app data is untouched) and starts the new
//!   version.
//!
//! The webview cannot pass a URL, signature or version: everything comes from
//! the endpoint answer Rust fetched itself. Built without the `updater` feature
//! both commands answer `updater_unavailable` and the UI keeps the manual
//! download link.
#![cfg_attr(not(all(desktop, feature = "updater")), allow(dead_code))]

use crate::error::{CmdResult, Code, CommandError};
use serde::Serialize;

pub const EVENT_PROGRESS: &str = "update://progress";

/// What `update_check` tells the webview.
#[derive(Debug, Clone, Serialize, Default, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct UpdateOffer {
    pub available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct UpdateProgress {
    pub downloaded: u64,
    pub total: Option<u64>,
    /// 0..=100 when the size is known.
    pub percent: Option<u8>,
}

impl UpdateProgress {
    pub fn new(downloaded: u64, total: Option<u64>) -> Self {
        let percent = total
            .filter(|t| *t > 0)
            .map(|t| ((downloaded.min(t) as u128 * 100) / t as u128) as u8);
        UpdateProgress { downloaded, total, percent }
    }
}

#[cfg_attr(all(desktop, feature = "updater"), allow(dead_code))]
fn unavailable() -> CommandError {
    CommandError::new(Code::UpdaterUnavailable, "this build has no in-app updater")
}

// ---------------------------------------------------------------- updater on

#[cfg(all(desktop, feature = "updater"))]
mod imp {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Mutex;
    use std::time::{Duration, Instant};
    use tauri::{AppHandle, Emitter, Runtime};
    use tauri_plugin_updater::{Update, UpdaterExt};

    /// The endpoint answer; the installer download gets its own, longer limit.
    const CHECK_TIMEOUT: Duration = Duration::from_secs(30);
    const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30 * 60);
    const PROGRESS_INTERVAL: Duration = Duration::from_millis(200);

    /// The update found by the last `update_check`; `update_install` installs
    /// only this one.
    #[derive(Default)]
    pub struct Pending {
        update: Mutex<Option<Update>>,
        installing: AtomicBool,
    }

    fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
        m.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn map_err(e: tauri_plugin_updater::Error) -> CommandError {
        use tauri_plugin_updater::Error as E;
        let code = match &e {
            E::Minisign(_)
            | E::Base64(_)
            | E::SignatureUtf8(_)
            | E::SignedVersionMismatch { .. }
            | E::MissingSignedVersion => Code::UpdateBadSignature,
            E::Reqwest(_) | E::Network(_) => Code::Network,
            _ => Code::Unknown,
        };
        CommandError::new(code, e.to_string())
    }

    pub fn plugin<R: Runtime>() -> tauri::plugin::TauriPlugin<R, tauri_plugin_updater::Config> {
        tauri_plugin_updater::Builder::new().build()
    }

    pub async fn check<R: Runtime>(app: &AppHandle<R>, pending: &Pending) -> CmdResult<UpdateOffer> {
        let updater = app.updater_builder().timeout(CHECK_TIMEOUT).build().map_err(map_err)?;
        let mut found = updater.check().await.map_err(map_err)?;
        if let Some(u) = found.as_mut() {
            u.timeout = Some(DOWNLOAD_TIMEOUT);
        }
        let offer = match &found {
            Some(u) => UpdateOffer {
                available: true,
                version: Some(u.version.clone()),
                notes: u.body.clone().filter(|b| !b.trim().is_empty()),
            },
            None => UpdateOffer::default(),
        };
        *lock(&pending.update) = found;
        Ok(offer)
    }

    /// `before_install` runs after the download is verified and before the
    /// installer starts (it pauses and kills our own downloads). If the
    /// installer then cannot start, those downloads stay paused (resumable).
    pub async fn install<R: Runtime>(
        app: &AppHandle<R>,
        pending: &Pending,
        before_install: impl FnOnce() + Send + 'static,
    ) -> CmdResult<()> {
        let Some(update) = lock(&pending.update).clone() else {
            return Err(CommandError::unknown("no update to install; check first"));
        };
        if pending.installing.swap(true, Ordering::SeqCst) {
            return Err(CommandError::unknown("an update is already being installed"));
        }
        let result = run(app, &update, before_install).await;
        pending.installing.store(false, Ordering::SeqCst);
        result
    }

    async fn run<R: Runtime>(
        app: &AppHandle<R>,
        update: &Update,
        before_install: impl FnOnce() + Send + 'static,
    ) -> CmdResult<()> {
        let mut downloaded: u64 = 0;
        let mut last: Option<Instant> = None;
        // `download` verifies the signature before it returns the bytes.
        let bytes = update
            .download(
                |chunk, total| {
                    downloaded += chunk as u64;
                    if last.is_none_or(|t| t.elapsed() >= PROGRESS_INTERVAL) {
                        last = Some(Instant::now());
                        let _ = app.emit(EVENT_PROGRESS, UpdateProgress::new(downloaded, total));
                    }
                },
                || {},
            )
            .await
            .map_err(map_err)?;
        let len = bytes.len() as u64;
        let _ = app.emit(EVENT_PROGRESS, UpdateProgress::new(len, Some(len)));
        tauri::async_runtime::spawn_blocking(before_install)
            .await
            .map_err(|e| CommandError::unknown(e.to_string()))?;
        // Windows: starts the installer and exits this process.
        update.install(bytes).map_err(map_err)?;
        // Other platforms (not shipped): start the new binary ourselves.
        app.restart()
    }
}

#[cfg(all(desktop, feature = "updater"))]
pub use imp::{check, install, plugin, Pending};

/// Built without the `updater` feature.
#[cfg(not(all(desktop, feature = "updater")))]
pub fn not_enabled<T>() -> CmdResult<T> {
    Err(unavailable())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percent_is_bounded_and_needs_a_size() {
        assert_eq!(UpdateProgress::new(0, Some(200)).percent, Some(0));
        assert_eq!(UpdateProgress::new(50, Some(200)).percent, Some(25));
        assert_eq!(UpdateProgress::new(250, Some(200)).percent, Some(100));
        assert_eq!(UpdateProgress::new(50, None).percent, None);
        assert_eq!(UpdateProgress::new(50, Some(0)).percent, None);
    }

    #[test]
    fn offer_serializes_without_empty_fields() {
        let none = serde_json::to_value(UpdateOffer::default()).unwrap();
        assert_eq!(none, serde_json::json!({ "available": false }));
        let some = UpdateOffer { available: true, version: Some("0.11.1".into()), notes: None };
        assert_eq!(serde_json::to_value(some).unwrap(), serde_json::json!({ "available": true, "version": "0.11.1" }));
    }

    #[test]
    fn unavailable_has_its_own_code() {
        assert_eq!(unavailable().code.as_str(), "updater_unavailable");
    }
}
