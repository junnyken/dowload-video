//! Browser-extension bridge (app 0.19.0, extension 5.14.0).
//!
//! VidGrab works on the user's own machine first and uses the server only as
//! a fallback. The extension lists the media a page plays; MP4 and HLS it
//! downloads itself, but DASH and HLS whose audio is a separate playlist need
//! yt-dlp + ffmpeg — which this app has. So the extension hands such a link
//! to the app here, and the app downloads it locally like a pasted link.
//!
//!   GET  /vidgrab/ping      -> {"app":"VidGrab","version":"0.19.0","device":"<64 hex>"}
//!                              (0.19.4: the machine id, so the extension and the
//!                              VidGrab site in this browser count as the same
//!                              device as the app — device linking, Free 2 / Pro 5)
//!   POST /vidgrab/download  {"url": "...", "title": "...", "referer": "..."}
//!                           -> {"ok":true}  (the app queues it)
//!
//! Rules of the listener:
//! * bound to 127.0.0.1 only, on the first free port of PORTS (the extension
//!   tries the same list);
//! * `Host` must be 127.0.0.1:<port> or localhost:<port> (DNS-rebinding guard);
//! * a download must come from an extension: `Origin: chrome-extension://…`
//!   (or moz-extension:// / extension:// for Edge/Firefox). A web page cannot
//!   set that header, so a site cannot make the app download anything;
//! * head ≤ 8 KiB, body ≤ 16 KiB, URL http(s) ≤ 4096 chars, one request per
//!   connection, 5 s I/O timeout.
//!
//! No Tauri types here, so the logic is unit-tested on Linux.

use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::sync::OnceLock;
use std::time::Duration;

pub const PORTS: [u16; 3] = [17325, 17326, 17327];
const MAX_HEAD: usize = 8 * 1024;
const MAX_BODY: usize = 16 * 1024;
const MAX_URL: usize = 4096;
const IO_TIMEOUT: Duration = Duration::from_secs(5);

static DEVICE: OnceLock<String> = OnceLock::new();

/// The machine id the ping answers with (set once at start; 64 hex only).
pub fn set_device(hash: &str) {
    if hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit()) {
        let _ = DEVICE.set(hash.to_ascii_lowercase());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Job {
    pub url: String,
    pub title: Option<String>,
    pub referer: Option<String>,
}

/// First free port of PORTS.
pub fn bind() -> io::Result<(TcpListener, u16)> {
    let mut last = io::Error::new(io::ErrorKind::AddrInUse, "no free bridge port");
    for p in PORTS {
        match TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, p))) {
            Ok(l) => return Ok((l, p)),
            Err(e) => last = e,
        }
    }
    Err(last)
}

/// Serve forever; `on_job` gets each accepted download.
pub fn serve(listener: TcpListener, port: u16, version: &str, on_job: impl Fn(Job) + Send + 'static) {
    for stream in listener.incoming() {
        let Ok(mut s) = stream else { continue };
        let _ = s.set_read_timeout(Some(IO_TIMEOUT));
        let _ = s.set_write_timeout(Some(IO_TIMEOUT));
        let (status, origin, body, job) = match read_request(&mut s) {
            Ok(req) => handle(&req, port, version),
            Err(_) => (400, None, err_json("bad request"), None),
        };
        let _ = write_response(&mut s, status, origin.as_deref(), &body);
        if let Some(j) = job {
            on_job(j);
        }
    }
}

#[derive(Debug, Default)]
pub struct Request {
    pub method: String,
    pub path: String,
    pub host: Option<String>,
    pub origin: Option<String>,
    pub content_type: Option<String>,
    pub body: Vec<u8>,
}

fn read_request(s: &mut TcpStream) -> io::Result<Request> {
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 2048];
    let head_end = loop {
        let n = s.read(&mut chunk)?;
        if n == 0 {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "closed"));
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(i) = find(&buf, b"\r\n\r\n") {
            break i;
        }
        if buf.len() > MAX_HEAD {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "head too large"));
        }
    };
    let mut req = parse_head(&buf[..head_end]).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "bad head"))?;
    let len = header_len(&buf[..head_end]).unwrap_or(0);
    if len > MAX_BODY {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "body too large"));
    }
    let mut body = buf[head_end + 4..].to_vec();
    while body.len() < len {
        let n = s.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    body.truncate(len);
    req.body = body;
    Ok(req)
}

fn find(h: &[u8], n: &[u8]) -> Option<usize> {
    h.windows(n.len()).position(|w| w == n)
}

fn header_len(head: &[u8]) -> Option<usize> {
    let text = std::str::from_utf8(head).ok()?;
    text.lines().find_map(|l| {
        let (k, v) = l.split_once(':')?;
        k.trim().eq_ignore_ascii_case("content-length").then(|| v.trim().parse().ok()).flatten()
    })
}

pub fn parse_head(head: &[u8]) -> Option<Request> {
    let text = std::str::from_utf8(head).ok()?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let method = first.next()?.to_string();
    let path = first.next()?.to_string();
    let mut req = Request { method, path, ..Default::default() };
    for l in lines {
        let Some((k, v)) = l.split_once(':') else { continue };
        let v = v.trim().to_string();
        match k.trim().to_ascii_lowercase().as_str() {
            "host" => req.host = Some(v),
            "origin" => req.origin = Some(v),
            "content-type" => req.content_type = Some(v),
            _ => {}
        }
    }
    Some(req)
}

fn is_extension_origin(o: &str) -> bool {
    let rest = o
        .strip_prefix("chrome-extension://")
        .or_else(|| o.strip_prefix("moz-extension://"))
        .or_else(|| o.strip_prefix("extension://"));
    matches!(rest, Some(id) if !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
}

fn host_ok(host: Option<&str>, port: u16) -> bool {
    matches!(host, Some(h) if h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}"))
}

fn err_json(msg: &str) -> String {
    serde_json::json!({ "ok": false, "error": msg }).to_string()
}

fn clean_text(v: Option<&serde_json::Value>, max: usize) -> Option<String> {
    let s = v?.as_str()?.trim();
    if s.is_empty() {
        return None;
    }
    Some(s.chars().filter(|c| !c.is_control()).take(max).collect())
}

pub fn valid_http_url(u: &str) -> bool {
    let u = u.trim();
    u.len() <= MAX_URL
        && (u.starts_with("https://") || u.starts_with("http://"))
        && u.len() > "https://".len()
        && !u.chars().any(|c| c.is_whitespace() || c.is_control())
}

/// (status, origin to allow in CORS, body, accepted job)
pub fn handle(req: &Request, port: u16, version: &str) -> (u16, Option<String>, String, Option<Job>) {
    if !host_ok(req.host.as_deref(), port) {
        return (403, None, err_json("bad host"), None);
    }
    let ext_origin = req.origin.as_deref().filter(|o| is_extension_origin(o)).map(str::to_string);
    let path = req.path.split('?').next().unwrap_or("");
    match (req.method.as_str(), path) {
        ("OPTIONS", "/vidgrab/ping" | "/vidgrab/download") => match ext_origin {
            Some(o) => (204, Some(o), String::new(), None),
            None => (403, None, err_json("extensions only"), None),
        },
        ("GET", "/vidgrab/ping") => {
            // no Origin (an extension's GET) or an extension's Origin; never a web page
            if req.origin.is_some() && ext_origin.is_none() {
                return (403, None, err_json("extensions only"), None);
            }
            let mut body = serde_json::json!({ "app": "VidGrab", "version": version });
            if let Some(d) = DEVICE.get() {
                body["device"] = serde_json::Value::String(d.clone());
            }
            (200, ext_origin, body.to_string(), None)
        }
        ("POST", "/vidgrab/download") => {
            let Some(o) = ext_origin else {
                return (403, None, err_json("extensions only"), None);
            };
            if !req.content_type.as_deref().is_some_and(|c| c.to_ascii_lowercase().starts_with("application/json")) {
                return (415, Some(o), err_json("json only"), None);
            }
            let Ok(v) = serde_json::from_slice::<serde_json::Value>(&req.body) else {
                return (400, Some(o), err_json("bad json"), None);
            };
            let url = v.get("url").and_then(|x| x.as_str()).unwrap_or("").trim().to_string();
            if !valid_http_url(&url) {
                return (400, Some(o), err_json("bad url"), None);
            }
            let referer = clean_text(v.get("referer"), MAX_URL).filter(|r| valid_http_url(r));
            let job = Job { url, title: clean_text(v.get("title"), 200), referer };
            (200, Some(o), serde_json::json!({ "ok": true }).to_string(), Some(job))
        }
        (_, "/vidgrab/ping" | "/vidgrab/download") => (405, ext_origin, err_json("method not allowed"), None),
        _ => (404, None, err_json("not found"), None),
    }
}

fn write_response(s: &mut TcpStream, status: u16, origin: Option<&str>, body: &str) -> io::Result<()> {
    let reason = match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        415 => "Unsupported Media Type",
        _ => "Error",
    };
    let mut head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n",
        body.len()
    );
    if let Some(o) = origin {
        head.push_str(&format!(
            "Access-Control-Allow-Origin: {o}\r\nVary: Origin\r\nAccess-Control-Allow-Methods: GET, POST\r\n\
             Access-Control-Allow-Headers: Content-Type\r\nAccess-Control-Allow-Private-Network: true\r\n"
        ));
    }
    head.push_str("\r\n");
    s.write_all(head.as_bytes())?;
    s.write_all(body.as_bytes())?;
    s.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(method: &str, path: &str, origin: Option<&str>, body: &str) -> Request {
        Request {
            method: method.into(),
            path: path.into(),
            host: Some("127.0.0.1:17325".into()),
            origin: origin.map(Into::into),
            content_type: Some("application/json".into()),
            body: body.as_bytes().to_vec(),
        }
    }
    const EXT: Option<&str> = Some("chrome-extension://abcdefghijklmnopabcdefghijklmnop");

    #[test]
    fn ping_answers_extensions_and_plain_gets_only() {
        let (s, o, b, _) = handle(&req("GET", "/vidgrab/ping", EXT, ""), 17325, "0.19.0");
        assert_eq!((s, o.as_deref()), (200, EXT));
        assert!(b.contains("\"version\":\"0.19.0\""));
        assert_eq!(handle(&req("GET", "/vidgrab/ping", None, ""), 17325, "x").0, 200);
        set_device(&"Ab".repeat(32));
        let (_, _, b, _) = handle(&req("GET", "/vidgrab/ping", None, ""), 17325, "x");
        assert!(b.contains(&format!("\"device\":\"{}\"", "ab".repeat(32))), "{b}");
        assert_eq!(handle(&req("GET", "/vidgrab/ping", Some("https://evil.example"), ""), 17325, "x").0, 403);
    }

    #[test]
    fn download_from_an_extension_is_queued() {
        let body = r#"{"url":"https://cdn.x/a.mpd","title":"Phim\u0007 hay","referer":"https://site.x/watch"}"#;
        let (s, _, _, job) = handle(&req("POST", "/vidgrab/download", EXT, body), 17325, "x");
        assert_eq!(s, 200);
        assert_eq!(
            job,
            Some(Job { url: "https://cdn.x/a.mpd".into(), title: Some("Phim hay".into()), referer: Some("https://site.x/watch".into()) })
        );
    }

    #[test]
    fn web_pages_and_bad_input_are_refused() {
        let ok = r#"{"url":"https://cdn.x/a.mpd"}"#;
        assert_eq!(handle(&req("POST", "/vidgrab/download", None, ok), 17325, "x").0, 403);
        assert_eq!(handle(&req("POST", "/vidgrab/download", Some("https://evil.example"), ok), 17325, "x").0, 403);
        assert_eq!(handle(&req("POST", "/vidgrab/download", Some("chrome-extension://"), ok), 17325, "x").0, 403);
        for bad in [r#"{"url":"file:///etc/passwd"}"#, r#"{"url":"javascript:alert(1)"}"#, r#"{"url":"https://a b"}"#, "nope", "{}"] {
            let (s, _, _, job) = handle(&req("POST", "/vidgrab/download", EXT, bad), 17325, "x");
            assert!(s == 400 && job.is_none(), "{bad}");
        }
        let mut r = req("POST", "/vidgrab/download", EXT, ok);
        r.content_type = Some("text/plain".into());
        assert_eq!(handle(&r, 17325, "x").0, 415);
        let mut r = req("POST", "/vidgrab/download", EXT, ok);
        r.host = Some("evil.example:17325".into());
        assert_eq!(handle(&r, 17325, "x").0, 403, "DNS rebinding");
        assert_eq!(handle(&req("GET", "/vidgrab/download", EXT, ""), 17325, "x").0, 405);
        assert_eq!(handle(&req("GET", "/", EXT, ""), 17325, "x").0, 404);
        let (s, _, _, job) = handle(&req("POST", "/vidgrab/download", EXT, r#"{"url":"https://x/a","referer":"javascript:x"}"#), 17325, "x");
        assert!(s == 200 && job.unwrap().referer.is_none(), "bad referer dropped");
    }

    #[test]
    fn preflight_only_for_extensions() {
        let (s, o, _, _) = handle(&req("OPTIONS", "/vidgrab/download", EXT, ""), 17325, "x");
        assert_eq!((s, o.as_deref()), (204, EXT));
        assert_eq!(handle(&req("OPTIONS", "/vidgrab/download", Some("https://evil.example"), ""), 17325, "x").0, 403);
    }

    #[test]
    fn real_socket_round_trip() {
        use std::sync::mpsc;
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || serve(l, port, "0.19.0", move |j| tx.send(j).unwrap()));
        let body = r#"{"url":"https://cdn.x/v.m3u8","title":"T"}"#;
        let mut c = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(c, "POST /vidgrab/download HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: chrome-extension://abc\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len()).unwrap();
        let mut out = String::new();
        c.read_to_string(&mut out).unwrap();
        assert!(out.starts_with("HTTP/1.1 200"), "{out}");
        assert!(out.contains("Access-Control-Allow-Origin: chrome-extension://abc"));
        assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap().url, "https://cdn.x/v.m3u8");
        // oversize body refused, server keeps serving
        let mut c = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(c, "POST /vidgrab/download HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: chrome-extension://abc\r\nContent-Type: application/json\r\nContent-Length: 999999\r\n\r\n").unwrap();
        let mut out = String::new();
        let _ = c.read_to_string(&mut out);
        assert!(out.starts_with("HTTP/1.1 400"), "{out}");
        let mut c = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(c, "GET /vidgrab/ping HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n").unwrap();
        let mut out = String::new();
        c.read_to_string(&mut out).unwrap();
        assert!(out.starts_with("HTTP/1.1 200") && out.contains("\"app\":\"VidGrab\""), "{out}");
    }
}
