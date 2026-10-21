//! The hidden channel-listing window (see page_listing.rs for why).
//!
//! Same shape as douyin_window.rs: `list-page-<random>`, InPrivate,
//! no capability (capabilities/default.json grants "main" only, so the page
//! cannot call the app). The user's saved cookies of that platform, if any,
//! go into its in-memory store; it opens the channel page; Rust evaluates
//! page_listing.js every TICK and reads the chunked answer from
//! `document.title`. Main-frame navigation stays on the platform's https
//! hosts, new windows and downloads are refused, and the window is destroyed
//! on every outcome. Nothing here logs a cookie, a URL or the page's answer.
//!
//! 0.16.3: 0.16.2 built the window with `visible(false)`, and on the owner's
//! Windows test TikTok listed nothing ("Không tìm thấy video") while the same
//! script listed 14 videos in Chromium: a hidden WebView2 stops rendering, so
//! the lazy video grid never fills. The window is now shown but placed far off
//! screen, without decorations or a taskbar entry. `show` (the user's
//! "Mở trang để kiểm tra") puts it on screen instead, waits longer, and leaves
//! it open after a failure so the user sees what the platform showed.

use crate::cookies::RawCookie;
use crate::page_listing::{self, Assembler, Target};
use std::sync::mpsc::{sync_channel, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::webview::cookie::{time::OffsetDateTime, Cookie};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const LABEL_PREFIX: &str = "list-page-";
/// Whole listing, window creation to answer: the script's own limit
/// (page_listing::max_ms) plus time to open the page and send the answer.
const SLACK: Duration = Duration::from_secs(20);
const TICK: Duration = Duration::from_secs(2);

#[derive(Debug)]
pub enum Failure {
    Window(String),
    Timeout,
}

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

fn to_cookie(c: &RawCookie) -> Cookie<'static> {
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

/// Opens the hidden window on `target.page`, returns the script's raw answer
/// (JSON text), destroys the window. Call from an async command only.
pub async fn run<R: Runtime>(app: &AppHandle<R>, target: &Target, cookies: Vec<RawCookie>, limit: u32, show: bool) -> Result<String, Failure> {
    let label = format!("{LABEL_PREFIX}{}", crate::cookies::random_key().map_err(Failure::Window)?);
    let (tx, rx) = sync_channel::<String>(1);
    let asm = Arc::new(Mutex::new(Assembler::default()));
    let kind = target.kind;
    let start = url::Url::parse("about:blank").expect("static URL");
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(start))
        .inner_size(1280.0, 860.0)
        .incognito(true);
    builder = if show {
        // wording: BA review
        builder
            .title("VidGrab — đang lấy danh sách video của kênh (xác minh / đăng nhập ngay trong cửa sổ này nếu được hỏi)")
            .visible(true)
            .focused(true)
            .center()
    } else {
        builder.title("VidGrab").visible(true).focused(false).decorations(false).skip_taskbar(true).position(-32000.0, -32000.0)
    };
    let builder = builder
        .on_navigation(move |u| page_listing::navigation_allowed(kind, u))
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .on_download(|_, _| false)
        .on_document_title_changed(move |_w, title| {
            let whole = asm.lock().unwrap_or_else(|e| e.into_inner()).push(&title);
            if let Some(answer) = whole {
                let _ = tx.try_send(answer); // first complete answer wins
            }
        });
    let w = builder.build().map_err(|e| Failure::Window(format!("cannot open the hidden page window: {e}")))?;
    let mut guard = Destroy { w: w.clone(), keep: false };
    let page = target.page.clone();
    let script = page_listing::page_script(kind, limit, show);
    let total = Duration::from_millis(u64::from(page_listing::max_ms(kind, show))) + SLACK;
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        for c in &cookies {
            w.set_cookie(to_cookie(c)).map_err(|e| Failure::Window(format!("cannot set a cookie: {e}")))?;
        }
        w.navigate(page).map_err(|e| Failure::Window(format!("cannot open the page: {e}")))?;
        let deadline = Instant::now() + total;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(Failure::Timeout);
            }
            match rx.recv_timeout(left.min(TICK)) {
                Ok(answer) => return Ok(answer),
                Err(RecvTimeoutError::Disconnected) => return Err(Failure::Window("the hidden page window closed".into())),
                Err(RecvTimeoutError::Timeout) => {
                    let _ = w.eval(&script);
                }
            }
        }
    })
    .await
    .map_err(|e| Failure::Window(e.to_string()))
    .and_then(|r| r);
    // Shown window: stay open when nothing was listed (an answer without items
    // is still Ok here), so the user sees what the platform showed.
    guard.keep = show && outcome.as_ref().map(|a| !a.contains("\"items\":[{")).unwrap_or(true);
    drop(guard);
    outcome
}
