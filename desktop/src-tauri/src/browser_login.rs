//! "Đăng nhập qua trình duyệt" (task #6039, app 0.4.0).
//!
//! Sign-up and sign-in are protected by a captcha on the website, which a
//! direct password sign-in from the app cannot pass. So the app never sees the
//! password: it listens once on http://127.0.0.1:<random port>/callback,
//! opens https://dvid.vibe1.tinhgon.xyz/desktop-login?port=<port>&state=<state>
//! in the default browser, and the page (after a normal sign-in there) sends
//! the browser to
//!     http://127.0.0.1:<port>/callback?state=<state>&refresh_token=<token>
//! The refresh token travels in the query string because a URL fragment is
//! never sent to a server; the app swaps it for its own session right away
//! (refresh tokens are single-use), and the success page scrubs the query from
//! the address bar / history with history.replaceState.
//!
//! Rules of the listener:
//! * bound to 127.0.0.1 only, port chosen by the OS;
//! * `state` = 32 bytes from the OS CSPRNG, hex; compared in constant time;
//! * only `GET /callback` with `Host: 127.0.0.1:<port>` (DNS-rebinding guard),
//!   exactly one `state` and one `refresh_token`, is accepted;
//! * anything else gets 404/405/400 and the listener keeps waiting (a stale tab
//!   or a stray request must not end the sign-in), up to MAX_REQUESTS;
//! * the first valid callback ends it: the caller drops the listener, so no
//!   second request is ever served; also ends on timeout or cancel.
//!
//! No Tauri types here, so the logic is unit-tested on Linux.

use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub const LOGIN_PAGE: &str = "https://dvid.vibe1.tinhgon.xyz/desktop-login";
pub const TIMEOUT: Duration = Duration::from_secs(5 * 60);
pub const MAX_REQUESTS: usize = 32;
const MAX_HEAD: usize = 8 * 1024;
const POLL: Duration = Duration::from_millis(50);
const IO_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, PartialEq, Eq)]
pub enum WaitError {
    Timeout,
    Cancelled,
    TooManyRequests,
    Io(String),
}

#[derive(Debug, PartialEq, Eq)]
pub enum Reject {
    NotFound,
    MethodNotAllowed,
    BadRequest,
}

/// 32 random bytes, lower-case hex (64 chars). The web page accepts exactly this.
pub fn new_state() -> Result<String, String> {
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).map_err(|e| format!("no secure random source: {e}"))?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

/// Listener on 127.0.0.1 with an OS-chosen port.
pub fn bind() -> io::Result<(TcpListener, u16)> {
    let l = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))?;
    let port = l.local_addr()?.port();
    Ok((l, port))
}

pub fn login_url(port: u16, state: &str) -> String {
    format!("{LOGIN_PAGE}?port={port}&state={state}")
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn valid_refresh_token(t: &str) -> bool {
    (8..=512).contains(&t.len()) && t.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

/// Checks one request head (everything before the blank line). Returns the
/// refresh token when it is the expected callback.
pub fn check_request(head: &str, port: u16, state: &str) -> Result<String, Reject> {
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or("").split(' ');
    let (method, target, version) = (first.next().unwrap_or(""), first.next().unwrap_or(""), first.next().unwrap_or(""));
    if first.next().is_some() || !version.starts_with("HTTP/1.") {
        return Err(Reject::BadRequest);
    }
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if path != "/callback" {
        return Err(Reject::NotFound);
    }
    if method != "GET" {
        return Err(Reject::MethodNotAllowed);
    }
    let want_host = format!("127.0.0.1:{port}");
    let mut hosts = lines
        .filter_map(|l| l.split_once(':'))
        .filter(|(k, _)| k.trim().eq_ignore_ascii_case("host"))
        .map(|(_, v)| v.trim());
    if hosts.next() != Some(want_host.as_str()) || hosts.next().is_some() {
        return Err(Reject::BadRequest);
    }
    let (mut got_state, mut token) = (None, None);
    for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
        let slot = match k.as_ref() {
            "state" => &mut got_state,
            "refresh_token" => &mut token,
            _ => continue,
        };
        if slot.replace(v.into_owned()).is_some() {
            return Err(Reject::BadRequest); // duplicated parameter
        }
    }
    let (Some(s), Some(t)) = (got_state, token) else { return Err(Reject::BadRequest) };
    if !ct_eq(s.as_bytes(), state.as_bytes()) || !valid_refresh_token(&t) {
        return Err(Reject::BadRequest);
    }
    Ok(t)
}

/// Inline script of the success page; allowed by its hash only (SCRIPT_HASH).
pub const SCRIPT: &str = "history.replaceState(null,'','/callback')";
/// base64(sha256(SCRIPT)); the unit test recomputes it.
pub const SCRIPT_HASH: &str = "sha256-xkdJM4MHx6LS828wJ7zbBxnKPgGHCoFsvuWrs5+VHX8=";

const STYLE: &str = "body{font-family:Segoe UI,system-ui,sans-serif;background:#FAFAF9;color:#1C1917;display:flex;\
min-height:100vh;margin:0;align-items:center;justify-content:center;text-align:center}\
main{max-width:420px;padding:24px}h1{font-size:20px}p{color:#57534E;font-size:14px;line-height:1.6}";

fn page(title: &str, body: &str, with_script: bool) -> String {
    let script = if with_script { format!("<script>{SCRIPT}</script>") } else { String::new() };
    format!(
        "<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" \
         content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>{STYLE}</style></head>\
         <body><main><h1>{title}</h1><p>{body}</p></main>{script}</body></html>"
    )
}

fn respond(stream: &mut TcpStream, status: &str, html: &str) {
    let csp = format!("default-src 'none'; style-src 'unsafe-inline'; script-src '{SCRIPT_HASH}'");
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\
         Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n\
         X-Frame-Options: DENY\r\nContent-Security-Policy: {csp}\r\nConnection: close\r\n\r\n",
        html.len()
    );
    let _ = stream.write_all(head.as_bytes()).and_then(|_| stream.write_all(html.as_bytes())).and_then(|_| stream.flush());
}

/// Reads the request head (up to the blank line, at most MAX_HEAD bytes).
fn read_head(stream: &mut TcpStream) -> Option<String> {
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    while buf.len() < MAX_HEAD {
        let n = stream.read(&mut chunk).ok()?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(end) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            buf.truncate(end);
            return String::from_utf8(buf).ok();
        }
    }
    None
}

/// Serves one connection. Some(token) only for the valid callback.
fn handle(mut stream: TcpStream, port: u16, state: &str) -> Option<String> {
    let _ = stream.set_nonblocking(false);
    let _ = stream.set_read_timeout(Some(IO_TIMEOUT));
    let _ = stream.set_write_timeout(Some(IO_TIMEOUT));
    let Some(head) = read_head(&mut stream) else {
        respond(&mut stream, "400 Bad Request", &page("Yêu cầu không hợp lệ", "Hãy quay lại ứng dụng VidGrab.", false));
        return None;
    };
    match check_request(&head, port, state) {
        Ok(token) => {
            respond(
                &mut stream,
                "200 OK",
                &page("Đã đăng nhập, quay lại ứng dụng", "Bạn đã đăng nhập VidGrab. Hãy quay lại ứng dụng VidGrab; có thể đóng thẻ này.", true),
            );
            Some(token)
        }
        Err(Reject::NotFound) => {
            respond(&mut stream, "404 Not Found", &page("Không tìm thấy", "Hãy quay lại ứng dụng VidGrab.", false));
            None
        }
        Err(Reject::MethodNotAllowed) => {
            respond(&mut stream, "405 Method Not Allowed", &page("Yêu cầu không hợp lệ", "Hãy quay lại ứng dụng VidGrab.", false));
            None
        }
        Err(Reject::BadRequest) => {
            respond(
                &mut stream,
                "400 Bad Request",
                &page(
                    "Liên kết đăng nhập không khớp",
                    "Liên kết này không thuộc lần đăng nhập đang chờ trong ứng dụng. Hãy bấm “Đăng nhập qua trình duyệt” trong ứng dụng VidGrab lần nữa.",
                    false,
                ),
            );
            None
        }
    }
}

/// Waits for the one valid callback. The caller drops `listener` afterwards,
/// which closes the port.
pub fn wait_for_callback(
    listener: &TcpListener,
    port: u16,
    state: &str,
    deadline: Instant,
    cancel: &AtomicBool,
) -> Result<String, WaitError> {
    listener.set_nonblocking(true).map_err(|e| WaitError::Io(e.to_string()))?;
    let mut served = 0usize;
    loop {
        if cancel.load(Ordering::SeqCst) {
            return Err(WaitError::Cancelled);
        }
        if Instant::now() >= deadline {
            return Err(WaitError::Timeout);
        }
        match listener.accept() {
            Ok((stream, peer)) => {
                served += 1;
                if peer.ip().is_loopback() {
                    if let Some(token) = handle(stream, port, state) {
                        return Ok(token);
                    }
                }
                if served >= MAX_REQUESTS {
                    return Err(WaitError::TooManyRequests);
                }
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::Interrupted => {
                std::thread::sleep(POLL)
            }
            Err(e) => return Err(WaitError::Io(e.to_string())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn b64(data: &[u8]) -> String {
        const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        for c in data.chunks(3) {
            let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
            for i in 0..4 {
                if i <= c.len() {
                    out.push(T[(n >> (18 - 6 * i) & 63) as usize] as char);
                } else {
                    out.push('=');
                }
            }
        }
        out
    }

    #[test]
    fn script_hash_matches_script() {
        use sha2::{Digest, Sha256};
        assert_eq!(SCRIPT_HASH, format!("sha256-{}", b64(&Sha256::digest(SCRIPT.as_bytes()))));
    }

    #[test]
    fn state_is_64_hex_and_random() {
        let a = new_state().unwrap();
        let b = new_state().unwrap();
        assert_eq!(a.len(), 64);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
        assert_ne!(a, b);
        assert_eq!(
            login_url(53682, &a),
            format!("https://dvid.vibe1.tinhgon.xyz/desktop-login?port=53682&state={a}")
        );
        assert!(crate::validate::open_url(&login_url(53682, &a)).is_ok());
    }

    const S: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    fn req(target: &str, host: &str) -> String {
        format!("GET {target} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: test")
    }

    #[test]
    fn request_checks() {
        let ok = req(&format!("/callback?state={S}&refresh_token=abcDEF123_-x"), "127.0.0.1:5000");
        assert_eq!(check_request(&ok, 5000, S), Ok("abcDEF123_-x".into()));
        // state mismatch / missing / duplicated
        let other = S.replace('0', "1");
        assert_eq!(check_request(&req(&format!("/callback?state={other}&refresh_token=abcdefgh"), "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req("/callback?refresh_token=abcdefgh", "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req(&format!("/callback?state={S}&state={S}&refresh_token=abcdefgh"), "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req(&format!("/callback?state={S}"), "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        // wrong path / method
        assert_eq!(check_request(&req(&format!("/?state={S}&refresh_token=abcdefgh"), "127.0.0.1:5000"), 5000, S), Err(Reject::NotFound));
        assert_eq!(check_request(&req("/favicon.ico", "127.0.0.1:5000"), 5000, S), Err(Reject::NotFound));
        assert_eq!(check_request(&req(&format!("/callbackx?state={S}&refresh_token=abcdefgh"), "127.0.0.1:5000"), 5000, S), Err(Reject::NotFound));
        let post = ok.replacen("GET", "POST", 1);
        assert_eq!(check_request(&post, 5000, S), Err(Reject::MethodNotAllowed));
        // Host header (DNS rebinding) and port
        assert_eq!(check_request(&req(&format!("/callback?state={S}&refresh_token=abcdefgh"), "evil.com:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req(&format!("/callback?state={S}&refresh_token=abcdefgh"), "localhost:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req(&format!("/callback?state={S}&refresh_token=abcdefgh"), "127.0.0.1:5001"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&format!("GET /callback?state={S}&refresh_token=abcdefgh HTTP/1.1\r\nUser-Agent: x"), 5000, S), Err(Reject::BadRequest));
        // token format
        assert_eq!(check_request(&req(&format!("/callback?state={S}&refresh_token=short"), "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request(&req(&format!("/callback?state={S}&refresh_token=abc%3Cscript%3Exyz"), "127.0.0.1:5000"), 5000, S), Err(Reject::BadRequest));
        // garbage
        assert_eq!(check_request("", 5000, S), Err(Reject::BadRequest));
        assert_eq!(check_request("GET /callback", 5000, S), Err(Reject::BadRequest));
    }

    fn send(port: u16, raw: &str) -> String {
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        s.write_all(raw.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        out
    }

    fn start(state: &'static str) -> (u16, Arc<AtomicBool>, std::thread::JoinHandle<Result<String, WaitError>>) {
        let (l, port) = bind().unwrap();
        let cancel = Arc::new(AtomicBool::new(false));
        let c = cancel.clone();
        let h = std::thread::spawn(move || {
            let r = wait_for_callback(&l, port, state, Instant::now() + Duration::from_secs(10), &c);
            drop(l);
            r
        });
        (port, cancel, h)
    }

    #[test]
    fn listener_rejects_then_accepts_once_then_closes() {
        let (port, _c, h) = start(S);
        let host = format!("Host: 127.0.0.1:{port}");
        // wrong path
        let r = send(port, &format!("GET /favicon.ico HTTP/1.1\r\n{host}\r\n\r\n"));
        assert!(r.starts_with("HTTP/1.1 404"), "{r}");
        // state mismatch
        let bad = S.replace('a', "b");
        let r = send(port, &format!("GET /callback?state={bad}&refresh_token=tok12345 HTTP/1.1\r\n{host}\r\n\r\n"));
        assert!(r.starts_with("HTTP/1.1 400"), "{r}");
        assert!(!r.contains("tok12345"));
        // the valid callback
        let r = send(port, &format!("GET /callback?state={S}&refresh_token=tok12345 HTTP/1.1\r\n{host}\r\n\r\n"));
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        assert!(r.contains("Đã đăng nhập, quay lại ứng dụng"));
        assert!(r.contains("Cache-Control: no-store"));
        assert!(!r.contains("tok12345"), "token must not be echoed");
        assert_eq!(h.join().unwrap(), Ok("tok12345".into()));
        // one-shot: the port is closed now
        assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
    }

    #[test]
    fn listener_cancel_and_timeout_and_cap() {
        let (port, cancel, h) = start(S);
        let _ = send(port, "GET / HTTP/1.1\r\n\r\n");
        cancel.store(true, Ordering::SeqCst);
        assert_eq!(h.join().unwrap(), Err(WaitError::Cancelled));

        let (l, port) = bind().unwrap();
        let never = AtomicBool::new(false);
        let r = wait_for_callback(&l, port, S, Instant::now() + Duration::from_millis(200), &never);
        assert_eq!(r, Err(WaitError::Timeout));

        let (port, _c, h) = start(S);
        for _ in 0..MAX_REQUESTS {
            let _ = send(port, "GET /x HTTP/1.1\r\n\r\n");
        }
        assert_eq!(h.join().unwrap(), Err(WaitError::TooManyRequests));
    }

    #[test]
    fn bound_to_loopback_only() {
        let (l, _) = bind().unwrap();
        assert!(l.local_addr().unwrap().ip().is_loopback());
    }
}
