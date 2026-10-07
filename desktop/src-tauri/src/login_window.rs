//! In-app login windows (PLAN-32D §3.2 "cách A").
//!
//! `login-<platform>` shows the platform's own page (cookies.rs table, never a
//! URL from the webview). The window has NO capability: capabilities/default.json
//! grants permissions to the window labelled "main" only, and Tauri refuses every
//! command from a remote origin unless a capability with a `remote` entry allows
//! it (tauri webview/mod.rs `on_message`), so the page cannot call the app.
//!
//! The window is InPrivate (`incognito`): its cookies live in memory, apart from
//! the main window's profile, and disappear when the window closes. That is
//! also why "Xoá" never calls `clear_all_browsing_data` on a normal window: on
//! Windows that clears the WebView2 profile the main window shares (its
//! localStorage holds the queue and settings).
//!
//! WebView2's cookie API deadlocks when called from a synchronous command or an
//! event handler, so every cookie read runs inside `spawn_blocking` from an
//! async command.

use crate::cookies::{self, RawCookie};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const LABEL_PREFIX: &str = "login-";

pub fn label(slug: &str) -> String {
    format!("{LABEL_PREFIX}{slug}")
}

/// Pages the login window may navigate to: https (and http, which the page
/// upgrades itself) on any public host. Never the app's own origins.
fn navigation_allowed(u: &url::Url) -> bool {
    if !matches!(u.scheme(), "https" | "http") {
        return false;
    }
    let host = u.host_str().unwrap_or_default().to_ascii_lowercase();
    !(host == "localhost" || host.ends_with(".localhost") || host == "127.0.0.1" || host == "[::1]")
}

/// Opens (or focuses) the login window of one platform.
pub fn open<R: Runtime>(app: &AppHandle<R>, slug: &'static str) -> Result<(), String> {
    let p = cookies::def(slug).ok_or("unknown platform")?;
    let lbl = label(slug);
    if let Some(w) = app.get_webview_window(&lbl) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    let start = url::Url::parse(p.login_url).map_err(|_| "bad login url")?;
    let app_nw = app.clone();
    let lbl_nw = lbl.clone();
    WebviewWindowBuilder::new(app, &lbl, WebviewUrl::External(start))
        // wording: BA review
        .title(format!("VidGrab — kết nối {}", display_name(slug)))
        .inner_size(1000.0, 760.0)
        .center()
        .incognito(true)
        .on_navigation(navigation_allowed)
        // Owner test 2026-10-07: Douyin opens a video in a new tab/window,
        // which the webview refused, so "click a video and play it" did
        // nothing. Open it in THIS window instead (same navigation rule);
        // never a second window. navigate() is dispatched, not called inline.
        .on_new_window(move |url, _features| {
            if navigation_allowed(&url) {
                let app = app_nw.clone();
                let lbl = lbl_nw.clone();
                tauri::async_runtime::spawn(async move {
                    if let Some(w) = app.get_webview_window(&lbl) {
                        let _ = w.navigate(url);
                    }
                });
            }
            NewWindowResponse::Deny
        })
        // A login page has no business saving files on the user's disk.
        .on_download(|_, _| false)
        .build()
        .map(|_| ())
        .map_err(|e| format!("cannot open the login window: {e}"))
}

pub fn window<R: Runtime>(app: &AppHandle<R>, slug: &str) -> Option<WebviewWindow<R>> {
    app.get_webview_window(&label(slug))
}

pub fn display_name(slug: &str) -> &'static str {
    match slug {
        "douyin" => "Douyin",
        "instagram" => "Instagram",
        "facebook" => "Facebook",
        "twitter" => "X (Twitter)",
        "youtube" => "YouTube",
        "bilibili" => "Bilibili",
        "threads" => "Threads",
        "reddit" => "Reddit",
        "pinterest" => "Pinterest",
        "tiktok" => "TikTok",
        "vimeo" => "Vimeo",
        _ => "nền tảng",
    }
}

/// Every cookie of the window's (InPrivate) store, converted. MUST run on a
/// blocking thread (see the module note).
pub fn read_cookies<R: Runtime>(w: &WebviewWindow<R>) -> Result<Vec<RawCookie>, String> {
    let list = w.cookies().map_err(|e| format!("cannot read the login window's cookies: {e}"))?;
    Ok(list
        .into_iter()
        .map(|c| RawCookie {
            name: c.name().to_string(),
            value: c.value().to_string(),
            domain: c.domain().unwrap_or_default().to_string(),
            path: c.path().unwrap_or("/").to_string(),
            secure: c.secure().unwrap_or(false),
            http_only: c.http_only().unwrap_or(false),
            expires: c.expires_datetime().map(|d| d.unix_timestamp()),
        })
        .collect())
}
