//! The app's end of the extension link (app 0.20.0, extension 5.22.0).
//!
//! Each browser that runs the VidGrab extension keeps a native messaging host
//! (native_host.rs) connected here through ext_bridge.rs `/vidgrab/native`.
//! Unlike `POST /vidgrab/download` (the extension asks, the app answers),
//! this is two-way: the app knows which browsers have the extension, opens
//! links in the right one, and asks the extension to do things:
//!
//!   app → ext  {"t":"app","up":true,"version"}            on connect
//!   app → ext  {"t":"req","id","op":"open_relay","url"}  scroll a channel
//!   ext → app  {"t":"res","id","ok","data"|"error"}       the answer
//!   ext → app  {"t":"evt","id","data":{items,channelTitle,done,skipped,loginWall}}
//!   ext → app  {"t":"hello","ext":"5.22.0","brand"}       who it is
//!   ext → app  {"t":"download","url","title","referer"}  like POST /download
//!
//! A channel relay this way needs no server: the extension opens the channel
//! in its own browser (the one signed in), scrolls it, and the list comes
//! straight back; the app polls `relay_poll` like it polls the server's
//! /channel-relay, with the same document shape (channel_relay.py).
//!
//! No Tauri types here, so the logic is unit-tested on Linux.

use std::collections::HashMap;
use std::io::{BufReader, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::{json, Value};

use crate::ext_bridge::{clean_text, valid_http_url, Job, MAX_URL};
use crate::native_host::{is_browser_exe, read_line_capped, MAX_IN};

const WRITE_TIMEOUT: Duration = Duration::from_secs(5);
const RELAY_TTL: Duration = Duration::from_secs(30 * 60);
const MAX_ITEMS: usize = 2000;

#[derive(Debug, Clone, Default, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LinkInfo {
    /// "Microsoft Edge", "Google Chrome", … (the extension's own word), else the exe name
    pub browser: String,
    pub browser_exe: Option<String>,
    pub ext_version: Option<String>,
    /// unix seconds
    pub connected_at: u64,
}

struct Link {
    id: u64,
    writer: Mutex<TcpStream>,
    info: Mutex<LinkInfo>,
}

impl Link {
    fn send(&self, v: &Value) -> std::io::Result<()> {
        let mut line = v.to_string();
        line.push('\n');
        self.writer.lock().unwrap().write_all(line.as_bytes())
    }
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayItem {
    pub id: String,
    pub url: String,
    pub title: Option<String>,
}

/// Same shape as GET /api/v1/channel-relay/{token}.
#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayDoc {
    pub status: &'static str,
    pub channel_url: String,
    pub platform: &'static str,
    pub channel_title: Option<String>,
    pub items: Vec<RelayItem>,
    pub skipped: u64,
}

struct Relay {
    link: u64,
    started: Instant,
    doc: RelayDoc,
}

#[derive(Default)]
struct State {
    links: Mutex<Vec<Arc<Link>>>,
    pending: Mutex<HashMap<u64, mpsc::Sender<Value>>>,
    relays: Mutex<HashMap<u64, Relay>>,
    next: AtomicU64,
}

static STATE: OnceLock<State> = OnceLock::new();
static ON_JOB: OnceLock<Box<dyn Fn(Job) + Send + Sync>> = OnceLock::new();

fn state() -> &'static State {
    STATE.get_or_init(State::default)
}

/// Where a `download` from the extension goes (set once at start).
pub fn set_on_job(f: impl Fn(Job) + Send + Sync + 'static) {
    let _ = ON_JOB.set(Box::new(f));
}

fn now_s() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// A host that passed ext_bridge's token check. Blocks (run it on its own thread).
pub fn accept(stream: TcpStream, app_version: &str) {
    let _ = stream.set_write_timeout(Some(WRITE_TIMEOUT));
    let Ok(w) = stream.try_clone() else { return };
    let mut lines = BufReader::new(stream);
    let mut buf = Vec::new();
    // the host's own first line: which browser started it
    if read_line_capped(&mut lines, &mut buf, MAX_IN).unwrap_or(0) == 0 {
        return;
    }
    let first: Value = serde_json::from_slice(&buf).unwrap_or(Value::Null);
    if first.get("t").and_then(Value::as_str) != Some("host") {
        return;
    }
    let exe = first
        .get("browserExe")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .filter(|p| is_browser_exe(p));
    let st = state();
    let link = Arc::new(Link {
        id: st.next.fetch_add(1, Ordering::Relaxed) + 1,
        writer: Mutex::new(w),
        info: Mutex::new(LinkInfo {
            browser: exe.as_ref().and_then(|p| p.file_name()).map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
            browser_exe: exe.map(|p| p.to_string_lossy().into_owned()),
            ext_version: None,
            connected_at: now_s(),
        }),
    });
    if link.send(&json!({ "t": "app", "up": true, "version": app_version })).is_err() {
        return;
    }
    st.links.lock().unwrap().push(link.clone());
    loop {
        buf.clear();
        match read_line_capped(&mut lines, &mut buf, MAX_IN) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        if let Ok(v) = serde_json::from_slice::<Value>(&buf) {
            dispatch(&link, &v);
        }
    }
    st.links.lock().unwrap().retain(|l| l.id != link.id);
}

fn dispatch(link: &Link, v: &Value) {
    let st = state();
    match v.get("t").and_then(Value::as_str) {
        Some("hello") => {
            let mut info = link.info.lock().unwrap();
            if let Some(b) = clean_text(v.get("brand"), 60) {
                info.browser = b;
            }
            info.ext_version = clean_text(v.get("ext"), 20);
        }
        Some("download") => {
            let url = v.get("url").and_then(Value::as_str).unwrap_or("").trim().to_string();
            if !valid_http_url(&url) {
                return;
            }
            let referer = clean_text(v.get("referer"), MAX_URL).filter(|r| valid_http_url(r));
            let job = Job { url, title: clean_text(v.get("title"), 200), referer };
            if let Some(f) = ON_JOB.get() {
                f(job);
            }
        }
        Some("res") => {
            let Some(id) = v.get("id").and_then(Value::as_u64) else { return };
            if let Some(tx) = st.pending.lock().unwrap().remove(&id) {
                let _ = tx.send(v.clone());
            }
        }
        Some("evt") => {
            let Some(id) = v.get("id").and_then(Value::as_u64) else { return };
            let mut relays = st.relays.lock().unwrap();
            if let Some(r) = relays.get_mut(&id).filter(|r| r.link == link.id) {
                apply_relay(&mut r.doc, v.get("data").unwrap_or(&Value::Null));
            }
        }
        _ => {}
    }
}

fn request(link: &Link, id: u64, op: &str, args: Value, timeout: Duration) -> Result<Value, String> {
    let st = state();
    let (tx, rx) = mpsc::channel();
    st.pending.lock().unwrap().insert(id, tx);
    let mut msg = json!({ "t": "req", "id": id, "op": op });
    if let (Some(m), Some(a)) = (msg.as_object_mut(), args.as_object()) {
        m.extend(a.clone());
    }
    if let Err(e) = link.send(&msg) {
        st.pending.lock().unwrap().remove(&id);
        return Err(format!("extension link lost: {e}"));
    }
    let r = rx.recv_timeout(timeout);
    st.pending.lock().unwrap().remove(&id);
    let v = r.map_err(|_| "the extension did not answer".to_string())?;
    if v.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(v.get("data").cloned().unwrap_or(Value::Null))
    } else {
        Err(clean_text(v.get("error"), 200).unwrap_or_else(|| "refused".into()))
    }
}

/// The browser to use: the most recently connected one.
fn preferred() -> Option<Arc<Link>> {
    state().links.lock().unwrap().last().cloned()
}

/// Browsers with the extension connected now (oldest first).
pub fn status() -> Vec<LinkInfo> {
    state().links.lock().unwrap().iter().map(|l| l.info.lock().unwrap().clone()).collect()
}

/// The exe of a browser that has the extension, to open links in.
pub fn browser_exe() -> Option<PathBuf> {
    let links = state().links.lock().unwrap();
    links.iter().rev().find_map(|l| l.info.lock().unwrap().browser_exe.clone()).map(PathBuf::from)
}

#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayStarted {
    pub id: u64,
    pub browser: String,
}

/// Asks the extension to open and scroll the channel. Ok(None): no browser is
/// linked, or this link is not one the local relay takes — use the server's.
pub fn relay_start(url: &str) -> Result<Option<RelayStarted>, String> {
    let Some(channel) = clean_profile(url) else { return Ok(None) };
    let Some(link) = preferred() else { return Ok(None) };
    let st = state();
    let id = st.next.fetch_add(1, Ordering::Relaxed) + 1;
    {
        let mut relays = st.relays.lock().unwrap();
        relays.retain(|_, r| r.started.elapsed() < RELAY_TTL);
        let doc = RelayDoc {
            status: "waiting",
            platform: platform(&channel),
            channel_url: channel.clone(),
            channel_title: None,
            items: Vec::new(),
            skipped: 0,
        };
        relays.insert(id, Relay { link: link.id, started: Instant::now(), doc });
    }
    match request(&link, id, "open_relay", json!({ "url": channel }), Duration::from_secs(8)) {
        Ok(data) => {
            if data.get("signedIn").and_then(Value::as_bool) == Some(false) {
                if let Some(r) = st.relays.lock().unwrap().get_mut(&id) {
                    if r.doc.status == "waiting" {
                        r.doc.status = "login";
                    }
                }
            }
            let browser = link.info.lock().unwrap().browser.clone();
            Ok(Some(RelayStarted { id, browser }))
        }
        Err(e) => {
            st.relays.lock().unwrap().remove(&id);
            eprintln!("local relay not started: {e}");
            Ok(None)
        }
    }
}

/// None = unknown or expired (like the server's 404).
pub fn relay_poll(id: u64) -> Option<RelayDoc> {
    let relays = state().relays.lock().unwrap();
    relays.get(&id).filter(|r| r.started.elapsed() < RELAY_TTL).map(|r| r.doc.clone())
}

// ---------------------------------------------------------------- relay rules
// A port of backend/app/api/channel_relay.py (_clean_profile without the
// share-text step — such links go to the server — and _clean_item).

fn re(cell: &'static OnceLock<Regex>, src: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(src).expect("relay regex"))
}

const FB_RESERVED: [&str; 17] = [
    "reel", "reels", "watch", "groups", "share", "events", "marketplace", "pages", "gaming", "login", "profile.php",
    "story.php", "photo", "photos", "video", "videos", "hashtag",
];

pub fn clean_profile(raw: &str) -> Option<String> {
    static DY: OnceLock<Regex> = OnceLock::new();
    static FB_NAMED: OnceLock<Regex> = OnceLock::new();
    static FB_ID: OnceLock<Regex> = OnceLock::new();
    static FB_PEOPLE: OnceLock<Regex> = OnceLock::new();
    let mut url = raw.trim().replacen("http://", "https://", 1);
    if let Some(rest) = url.strip_prefix("https://douyin.com/") {
        url = format!("https://www.douyin.com/{rest}");
    }
    if let Some(m) = re(&DY, r"^https://www\.douyin\.com/user/(MS4w[A-Za-z0-9_-]{10,120})(?:[/?#].*)?$").captures(&url) {
        return Some(format!("https://www.douyin.com/user/{}", &m[1]));
    }
    if let Some(rest) = url.strip_prefix("https://facebook.com/") {
        url = format!("https://www.facebook.com/{rest}");
    }
    let id = re(&FB_ID, r"^https://(?:www\.|web\.|m\.)?facebook\.com/profile\.php\?(?:[^#]*&)?id=(\d{5,25})")
        .captures(&url)
        .or_else(|| {
            re(&FB_PEOPLE, r"^https://(?:www\.|web\.|m\.)?facebook\.com/people/[^/?#]{1,120}/(\d{5,25})/?(?:[?#].*)?$")
                .captures(&url)
        });
    if let Some(m) = id {
        return Some(format!("https://www.facebook.com/profile.php?id={}&sk=reels_tab", &m[1]));
    }
    let m = re(&FB_NAMED, r"^https://(?:www\.|web\.|m\.)?facebook\.com/([A-Za-z0-9.\-]{1,80})(?:/(reels|videos))?/?(?:[?#].*)?$")
        .captures(&url)?;
    if FB_RESERVED.contains(&m[1].to_ascii_lowercase().as_str()) {
        return None;
    }
    Some(format!("https://www.facebook.com/{}/{}/", &m[1], m.get(2).map_or("reels", |g| g.as_str())))
}

fn platform(channel: &str) -> &'static str {
    if channel.contains("facebook.com/") {
        "facebook"
    } else {
        "douyin"
    }
}

/// (id, canonical url) of a video of the channel's platform.
pub fn clean_item(platform: &str, raw: &str) -> Option<(String, String)> {
    static FB_ITEM: OnceLock<Regex> = OnceLock::new();
    static DY_VIDEO: OnceLock<Regex> = OnceLock::new();
    let u = raw.trim();
    if platform == "facebook" {
        let m = re(&FB_ITEM, r"^https://www\.facebook\.com/(?:reel/(\d{6,25})|watch/\?v=(\d{6,25}))$").captures(u)?;
        return Some(match (m.get(1), m.get(2)) {
            (Some(r), _) => (r.as_str().into(), format!("https://www.facebook.com/reel/{}", r.as_str())),
            (_, Some(w)) => (w.as_str().into(), format!("https://www.facebook.com/watch/?v={}", w.as_str())),
            _ => return None,
        });
    }
    let m = re(&DY_VIDEO, r"^https://www\.douyin\.com/video/(\d{15,25})$").captures(u.split('?').next()?)?;
    Some((m[1].to_string(), format!("https://www.douyin.com/video/{}", &m[1])))
}

fn squash(s: &str, max: usize) -> Option<String> {
    let s: String = s.split_whitespace().collect::<Vec<_>>().join(" ").chars().filter(|c| !c.is_control()).take(max).collect();
    (!s.is_empty()).then_some(s)
}

/// The extension's progress / final list into the document (PUT /channel-relay).
pub fn apply_relay(doc: &mut RelayDoc, data: &Value) {
    let mut seen = std::collections::HashSet::new();
    let mut items = Vec::new();
    for it in data.get("items").and_then(Value::as_array).into_iter().flatten().take(MAX_ITEMS) {
        let Some(raw) = it.get("url").and_then(Value::as_str).filter(|u| u.len() <= 300) else { continue };
        let Some((id, url)) = clean_item(doc.platform, raw) else { continue };
        if !seen.insert(id.clone()) {
            continue;
        }
        let title = it.get("title").and_then(Value::as_str).and_then(|t| squash(t, 300));
        items.push(RelayItem { id, url, title });
    }
    let flag = |k: &str| data.get(k).and_then(Value::as_bool).unwrap_or(false);
    doc.status = if flag("done") {
        "done"
    } else if flag("loginWall") {
        "login"
    } else {
        "scrolling"
    };
    doc.items = items;
    doc.skipped = data.get("skipped").and_then(Value::as_u64).unwrap_or(0).min(100_000);
    if let Some(t) = data.get("channelTitle").and_then(Value::as_str).and_then(|t| squash(t, 300)) {
        doc.channel_title = Some(t);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiles_like_the_server() {
        let c = |u: &str| clean_profile(u);
        assert_eq!(
            c("https://douyin.com/user/MS4wLjABAAAAabcdefghij?from=x").as_deref(),
            Some("https://www.douyin.com/user/MS4wLjABAAAAabcdefghij")
        );
        assert_eq!(
            c("https://m.facebook.com/profile.php?sk=x&id=1000123456").as_deref(),
            Some("https://www.facebook.com/profile.php?id=1000123456&sk=reels_tab")
        );
        assert_eq!(
            c("https://www.facebook.com/people/Ten-Ai/61550000000000/?sk=reels_tab").as_deref(),
            Some("https://www.facebook.com/profile.php?id=61550000000000&sk=reels_tab")
        );
        assert_eq!(c("https://facebook.com/NASA").as_deref(), Some("https://www.facebook.com/NASA/reels/"));
        assert_eq!(c("https://www.facebook.com/NASA/videos/").as_deref(), Some("https://www.facebook.com/NASA/videos/"));
        assert_eq!(c("https://www.facebook.com/watch"), None, "reserved");
        assert_eq!(c("https://www.tiktok.com/@x"), None);
        assert_eq!(c("Xem kênh https://v.douyin.com/abc/"), None, "share text goes to the server");
    }

    #[test]
    fn items_like_the_server() {
        assert_eq!(
            clean_item("douyin", "https://www.douyin.com/video/7412345678901234567?modal=1"),
            Some(("7412345678901234567".into(), "https://www.douyin.com/video/7412345678901234567".into()))
        );
        assert_eq!(clean_item("douyin", "https://www.douyin.com/note/7412345678901234567"), None);
        assert_eq!(
            clean_item("facebook", "https://www.facebook.com/watch/?v=1234567"),
            Some(("1234567".into(), "https://www.facebook.com/watch/?v=1234567".into()))
        );
        assert_eq!(clean_item("facebook", "https://evil.example/reel/1234567"), None);
    }

    #[test]
    fn relay_document_follows_the_extension() {
        let mut doc = RelayDoc {
            status: "waiting",
            channel_url: "https://www.douyin.com/user/MS4wX".into(),
            platform: "douyin",
            channel_title: None,
            items: vec![],
            skipped: 0,
        };
        let v = "https://www.douyin.com/video/7412345678901234567";
        apply_relay(&mut doc, &json!({ "items": [{ "url": v, "title": "  Một \n video " }, { "url": v }, { "url": "javascript:x" }], "channelTitle": "Kênh", "skipped": 2 }));
        assert_eq!(doc.status, "scrolling");
        assert_eq!(doc.items, vec![RelayItem { id: "7412345678901234567".into(), url: v.into(), title: Some("Một video".into()) }]);
        assert_eq!((doc.skipped, doc.channel_title.as_deref()), (2, Some("Kênh")));
        apply_relay(&mut doc, &json!({ "items": [], "loginWall": true }));
        assert_eq!((doc.status, doc.channel_title.as_deref()), ("login", Some("Kênh")));
        apply_relay(&mut doc, &json!({ "items": [{ "url": v }], "done": true }));
        assert_eq!(doc.status, "done");
    }

    /// A fake extension on the host's stdin/stdout, the real host, the real
    /// bridge listener and this module: the whole path of a local relay.
    #[test]
    fn extension_host_app_round_trip() {
        use crate::native_host::{read_frame, write_frame};
        use std::net::TcpListener;

        let dir = std::env::temp_dir().join(format!("vg-link-e2e-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let token = "7f".repeat(32); // the same token as ext_bridge's handshake test (one process-wide value)
        std::fs::write(dir.join(crate::native_reg::TOKEN_FILE), &token).unwrap();
        crate::ext_bridge::set_native_token(&token);
        let (jobs_tx, jobs_rx) = mpsc::channel();
        let jobs_tx = Mutex::new(jobs_tx);
        set_on_job(move |j| jobs_tx.lock().unwrap().send(j).unwrap());

        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            crate::ext_bridge::serve(l, port, "0.20.0", |_| {}, |s| {
                std::thread::spawn(move || accept(s, "0.20.0"));
            })
        });

        let (host_in, mut ext_out) = std::io::pipe().unwrap();
        let (mut ext_in, host_out) = std::io::pipe().unwrap();
        #[cfg(windows)]
        let browser = PathBuf::from(r"C:\Edge\msedge.exe");
        #[cfg(not(windows))]
        let browser = PathBuf::from("/opt/edge/msedge.exe");
        let d = dir.clone();
        let host = std::thread::spawn(move || {
            crate::native_host::serve("chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi/".into(), Some(browser), &d, &[port], host_in, host_out)
        });
        let next = |r: &mut std::io::PipeReader| -> Value { serde_json::from_slice(&read_frame(r).unwrap().unwrap()).unwrap() };

        write_frame(&mut ext_out, br#"{"t":"hello","ext":"5.22.0","brand":"Microsoft Edge"}"#).unwrap();
        let up = next(&mut ext_in);
        assert_eq!((up["t"].as_str(), up["up"].as_bool(), up["version"].as_str()), (Some("app"), Some(true), Some("0.20.0")), "{up}");
        let t0 = Instant::now();
        while status().first().and_then(|i| i.ext_version.clone()).is_none() {
            assert!(t0.elapsed() < Duration::from_secs(5), "hello never arrived");
            std::thread::sleep(Duration::from_millis(20));
        }
        let info = status().remove(0);
        assert_eq!(info.browser, "Microsoft Edge");
        assert!(info.browser_exe.as_deref().unwrap().ends_with("msedge.exe"));
        assert!(browser_exe().is_some());

        // the app asks for a channel; the extension opens it and scrolls
        let starter = std::thread::spawn(|| relay_start("https://www.douyin.com/user/MS4wLjABAAAAabcdefghij?x=1"));
        let req = next(&mut ext_in);
        assert_eq!(req["op"], "open_relay");
        assert_eq!(req["url"], "https://www.douyin.com/user/MS4wLjABAAAAabcdefghij");
        let id = req["id"].as_u64().unwrap();
        write_frame(&mut ext_out, json!({ "t": "res", "id": id, "ok": true, "data": { "signedIn": false } }).to_string().as_bytes()).unwrap();
        let started = starter.join().unwrap().unwrap().expect("local relay");
        assert_eq!((started.id, started.browser.as_str()), (id, "Microsoft Edge"));
        assert_eq!(relay_poll(id).unwrap().status, "login", "not signed in yet");
        let v = "https://www.douyin.com/video/7412345678901234567";
        write_frame(&mut ext_out, json!({ "t": "evt", "id": id, "data": { "items": [{ "url": v, "title": "A" }], "done": true, "channelTitle": "Kênh" } }).to_string().as_bytes()).unwrap();
        let t0 = Instant::now();
        while relay_poll(id).unwrap().status != "done" {
            assert!(t0.elapsed() < Duration::from_secs(5), "relay never finished");
            std::thread::sleep(Duration::from_millis(20));
        }
        let doc = relay_poll(id).unwrap();
        assert_eq!((doc.items.len(), doc.channel_title.as_deref()), (1, Some("Kênh")));
        assert!(relay_poll(id + 999).is_none());

        // a download handed over the link, and one the app refuses
        write_frame(&mut ext_out, br#"{"t":"download","url":"file:///etc/passwd"}"#).unwrap();
        write_frame(&mut ext_out, br#"{"t":"download","url":"https://cdn.x/a.mpd","title":"T"}"#).unwrap();
        assert_eq!(jobs_rx.recv_timeout(Duration::from_secs(5)).unwrap().url, "https://cdn.x/a.mpd");

        // the browser closes the port: the host ends, the link goes away
        drop(ext_out);
        assert_eq!(host.join().unwrap(), 0);
        let t0 = Instant::now();
        while !status().is_empty() {
            assert!(t0.elapsed() < Duration::from_secs(5), "link not dropped");
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(relay_start("https://www.douyin.com/user/MS4wLjABAAAAabcdefghij").unwrap().is_none(), "no browser: server relay");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
