//! The hidden Douyin resolver window (see douyin_local.rs for why).
//!
//! `resolve-douyin-<random>`: invisible, InPrivate, no capability
//! (capabilities/default.json grants the window "main" only), so the Douyin
//! page cannot call the app. The user's saved Douyin cookies are put into its
//! in-memory store, it opens the video page, and Rust evaluates a fixed
//! script that reports the media link through `document.title`. Main-frame
//! navigation is limited to Douyin hosts over https, new windows and
//! downloads are refused, and the window is destroyed on every outcome.
//!
//! WebView2's cookie calls deadlock on the main thread / in sync handlers, so
//! cookies are set from a blocking thread of an async command. Nothing here
//! logs a cookie, a URL or the page's answer.

use crate::cookies::RawCookie;
use crate::douyin_local::{self, PageError, Resolved, Target};
use std::sync::mpsc::{sync_channel, RecvTimeoutError, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::webview::cookie::{time::OffsetDateTime, Cookie};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const LABEL_PREFIX: &str = "resolve-douyin-";
/// Whole resolve, window creation to answer.
const TOTAL: Duration = Duration::from_secs(30);
/// How often the page script is (re-)evaluated; it runs once per document.
const TICK: Duration = Duration::from_secs(2);

#[derive(Debug)]
pub enum Failure {
    /// The window could not be created / driven.
    Window(String),
    Timeout,
    Page(PageError),
}

/// Destroys the window when dropped (success, error, timeout, panic) unless
/// `keep` was set (debug mode after a failure: the user looks at the page).
struct Destroy<R: Runtime> {
    w: WebviewWindow<R>,
    keep: bool,
}
impl<R: Runtime> Drop for Destroy<R> {
    fn drop(&mut self) {
        if !self.keep {
            let _ = self.w.destroy();
        }
    }
}

/// Debug mode (owner test 2026-10-07): the window is shown and waits longer.
const TOTAL_DEBUG: Duration = Duration::from_secs(90);

fn to_cookie(c: &RawCookie) -> Cookie<'static> {
    // Leading dot = a domain cookie (all subdomains), as the stored blob says.
    let mut b = Cookie::build((c.name.clone(), c.value.clone()))
        .domain(format!(".{}", c.domain))
        .path(if c.path.is_empty() { "/".to_string() } else { c.path.clone() })
        .secure(c.secure)
        .http_only(c.http_only);
    if let Some(t) = c.expires.and_then(|e| OffsetDateTime::from_unix_timestamp(e).ok()) {
        b = b.expires(t);
    }
    b.build()
}

/// Opens the hidden window, waits for the answer, destroys the window.
/// MUST be called from an async command (it blocks only inside spawn_blocking).
/// `debug`: show the window (title says so), wait up to 90 s, and leave it
/// open after a failure so the user can see what Douyin showed.
pub async fn resolve<R: Runtime>(app: &AppHandle<R>, target: Target, cookies: Vec<RawCookie>, debug: bool) -> Result<Resolved, Failure> {
    let label = format!("{LABEL_PREFIX}{}", crate::cookies::random_key().map_err(Failure::Window)?);
    // The video id: known now, or read from the short link's redirect.
    let id: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(match &target {
        Target::Video(id) => Some(id.clone()),
        Target::Short(_) => None,
    }));
    let (tx, rx) = sync_channel::<Result<Resolved, PageError>>(1);

    let start = url::Url::parse("about:blank").expect("static URL");
    let id_nav = id.clone();
    let app_nav = app.clone();
    let label_nav = label.clone();
    let id_title = id.clone();
    let tx_title: SyncSender<_> = tx.clone();
    let builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(start))
        // wording: BA review
        .title(if debug { "VidGrab — gỡ lỗi Douyin (cửa sổ này tự đóng khi lấy được video)" } else { "VidGrab" })
        .visible(debug)
        .focused(debug)
        .skip_taskbar(!debug)
        .inner_size(1280.0, 800.0)
        .incognito(true)
        .initialization_script(douyin_local::INIT_SCRIPT)
        .on_navigation(move |u| {
            if !douyin_local::navigation_allowed(u) {
                return false;
            }
            // Short link: the redirect names the video. Open the desktop video
            // page instead of the mobile share page it may redirect to.
            let mut slot = id_nav.lock().unwrap_or_else(|e| e.into_inner());
            if slot.is_none() {
                if let Some(vid) = douyin_local::video_id_of(u) {
                    *slot = Some(vid.clone());
                    let page = douyin_local::video_page(&vid);
                    if u.host_str() != page.host_str() || !u.path().starts_with(page.path()) {
                        let app = app_nav.clone();
                        let lbl = label_nav.clone();
                        tauri::async_runtime::spawn(async move {
                            if let Some(w) = app.get_webview_window(&lbl) {
                                let _ = w.navigate(page);
                            }
                        });
                        return false;
                    }
                }
            }
            true
        })
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .on_download(|_, _| false)
        .on_document_title_changed(move |_w, title| {
            let Some(vid) = id_title.lock().unwrap_or_else(|e| e.into_inner()).clone() else { return };
            if let Some(r) = douyin_local::parse_title(&title, &vid) {
                let _ = tx_title.try_send(r); // first answer wins
            }
        });
    drop(tx);
    let w = builder.build().map_err(|e| Failure::Window(format!("cannot open the hidden Douyin window: {e}")))?;
    let mut guard = Destroy { w: w.clone(), keep: false };

    let first = match &target {
        Target::Video(vid) => douyin_local::video_page(vid),
        Target::Short(u) => u.clone(),
    };
    let w2 = w.clone();
    let id_wait = id.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        for c in &cookies {
            w2.set_cookie(to_cookie(c)).map_err(|e| Failure::Window(format!("cannot set a cookie: {e}")))?;
        }
        w2.navigate(first).map_err(|e| Failure::Window(format!("cannot open the Douyin page: {e}")))?;
        let deadline = Instant::now() + if debug { TOTAL_DEBUG } else { TOTAL };
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(Failure::Timeout);
            }
            match rx.recv_timeout(left.min(TICK)) {
                Ok(r) => return r.map_err(Failure::Page),
                Err(RecvTimeoutError::Disconnected) => return Err(Failure::Window("the hidden Douyin window closed".into())),
                Err(RecvTimeoutError::Timeout) => {
                    let vid = id_wait.lock().unwrap_or_else(|e| e.into_inner()).clone();
                    if let Some(vid) = vid {
                        // The script itself checks it is on this video's page and runs once per document.
                        let _ = w2.eval(douyin_local::page_script(&vid));
                    }
                }
            }
        }
    })
    .await
    .map_err(|e| Failure::Window(e.to_string()))
    .and_then(|r| r);
    guard.keep = debug && outcome.is_err();
    drop(guard);
    outcome
}
