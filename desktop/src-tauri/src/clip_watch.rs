//! Clipboard link watch (market comparison 14/10: "copy a link, the app
//! offers to download it", as JDownloader does).
//!
//! A background thread reads the clipboard text every POLL. It forwards ONLY
//! a clipboard that is a single http(s) URL — trimmed, no whitespace, at most
//! MAX_LEN chars — to the webview as the `clipboard-link` event. Any other
//! text (passwords, messages, documents) never leaves this module and is
//! never logged. The webview then keeps only links of platforms VidGrab
//! downloads from (src/lib/clipboard-core.ts) and nothing leaves the machine
//! until the user clicks "Tải" or chose automatic mode.
//!
//! Off until the webview calls `clipboard_watch_set(true)` (its setting
//! defaults to "ask"). Whatever is on the clipboard when the watch starts is
//! not reported: only a change after that.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::{AppHandle, Emitter};
use tauri_plugin_clipboard_manager::ClipboardExt;

pub const EVENT: &str = "clipboard-link";
const POLL: Duration = Duration::from_millis(1500);
const MAX_LEN: usize = 2048;

static ENABLED: AtomicBool = AtomicBool::new(false);
static RESET: AtomicBool = AtomicBool::new(true);

pub fn set_enabled(on: bool) {
    if on && !ENABLED.load(Ordering::SeqCst) {
        // Re-baseline: do not report what was already on the clipboard.
        RESET.store(true, Ordering::SeqCst);
    }
    ENABLED.store(on, Ordering::SeqCst);
}

/// The clipboard text as one URL, or None. Pure; unit-tested below.
pub fn single_url(text: &str) -> Option<String> {
    let t = text.trim();
    if t.is_empty() || t.len() > MAX_LEN || t.chars().any(char::is_whitespace) {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    if !(lower.starts_with("https://") || lower.starts_with("http://")) {
        return None;
    }
    let u = url::Url::parse(t).ok()?;
    u.host_str()?;
    Some(t.to_string())
}

fn fingerprint(text: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    text.hash(&mut h);
    h.finish()
}

pub fn start(app: AppHandle) {
    std::thread::spawn(move || {
        let mut last: Option<u64> = None;
        loop {
            std::thread::sleep(POLL);
            if !ENABLED.load(Ordering::SeqCst) {
                continue;
            }
            // Non-text content (image, files) or a busy clipboard: skip.
            let Ok(text) = app.clipboard().read_text() else { continue };
            let fp = fingerprint(&text);
            if RESET.swap(false, Ordering::SeqCst) {
                last = Some(fp);
                continue;
            }
            if last == Some(fp) {
                continue;
            }
            last = Some(fp);
            if let Some(url) = single_url(&text) {
                let _ = app.emit(EVENT, url);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::single_url;

    #[test]
    fn accepts_one_url() {
        assert_eq!(single_url("  https://www.tiktok.com/@a/video/1 \n").as_deref(), Some("https://www.tiktok.com/@a/video/1"));
        assert!(single_url("http://youtu.be/abc").is_some());
    }

    #[test]
    fn rejects_everything_else() {
        assert!(single_url("").is_none());
        assert!(single_url("mật khẩu của tôi").is_none());
        assert!(single_url("xem cái này https://youtu.be/abc").is_none());
        assert!(single_url("https://a.com/x https://b.com/y").is_none());
        assert!(single_url("ftp://youtube.com/x").is_none());
        assert!(single_url("javascript:alert(1)").is_none());
        assert!(single_url(&format!("https://a.com/{}", "x".repeat(3000))).is_none());
        assert!(single_url("https://").is_none());
    }
}
