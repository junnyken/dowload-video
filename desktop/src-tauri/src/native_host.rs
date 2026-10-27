//! Native messaging host (app 0.20.0, extension 5.22.0).
//!
//! Chrome / Edge start this program (a per-version copy of VidGrab.exe, see
//! native_reg.rs) when the VidGrab extension calls
//! `chrome.runtime.connectNative("xyz.tinhgon.vidgrab")`, and only for the
//! extension ids in the host manifest — so whatever reaches us on stdin comes
//! from the VidGrab extension in that browser, not from a page or a program
//! that merely sets an `Origin` header.
//!
//! This process is a pipe between the two:
//!
//!   browser  ⇄ stdin/stdout (u32 LE length + JSON, the native messaging frame)
//!   host     ⇄ 127.0.0.1:<PORTS> `GET /vidgrab/native` + `X-VidGrab-Token`
//!              (ext_bridge.rs), then one JSON object per line both ways
//!   app      ext_link.rs
//!
//! The extension keeps the port open, so this runs for as long as the browser
//! does. When the app is not running we tell the extension `{"t":"app","up":
//! false}` and retry every few seconds; the first line on each connection is
//! ours (`{"t":"host", browserExe, origin}`, so the app knows which browser
//! to open links in), then the extension's last `hello` again.
//!
//! No Tauri here: main.rs branches into `run` before the app starts, so the
//! single-instance plugin never sees this process.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::ext_bridge::PORTS;

/// From the browser: Chrome allows 64 MiB, a relay list is a few hundred KB.
pub const MAX_IN: usize = 1024 * 1024;
/// To the browser: Chrome's own limit for host → extension.
pub const MAX_OUT: usize = 1024 * 1024;
const RETRY: Duration = Duration::from_secs(3);
const CONNECT_TIMEOUT: Duration = Duration::from_millis(700);

/// Chrome passes the caller's origin ("chrome-extension://<id>/") as the first
/// argument (Windows adds `--parent-window=<hwnd>`). The app itself never gets
/// such an argument.
pub fn launched_origin<I: IntoIterator<Item = String>>(args: I) -> Option<String> {
    args.into_iter().skip(1).find(|a| a.starts_with("chrome-extension://") && a.len() <= 128)
}

/// One native messaging frame; Ok(None) at a clean end of input.
pub fn read_frame(r: &mut impl Read) -> io::Result<Option<Vec<u8>>> {
    let mut len = [0u8; 4];
    match r.read_exact(&mut len) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    }
    let n = u32::from_le_bytes(len) as usize;
    if n > MAX_IN {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "frame too large"));
    }
    let mut buf = vec![0u8; n];
    r.read_exact(&mut buf)?;
    Ok(Some(buf))
}

pub fn write_frame(w: &mut impl Write, json: &[u8]) -> io::Result<()> {
    if json.len() > MAX_OUT {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "message too large"));
    }
    w.write_all(&(json.len() as u32).to_le_bytes())?;
    w.write_all(json)?;
    w.flush()
}

/// A frame from the extension as one compact line: only JSON objects pass,
/// re-serialised so a raw newline can never split the line protocol.
pub fn to_line(frame: &[u8]) -> Option<(String, serde_json::Value)> {
    let v: serde_json::Value = serde_json::from_slice(frame).ok()?;
    if !v.is_object() {
        return None;
    }
    let mut s = serde_json::to_string(&v).ok()?;
    s.push('\n');
    Some((s, v))
}

/// The token the running app wrote (native_reg.rs); read on every connect,
/// since the app makes a new one each start.
pub fn read_token(dir: &Path) -> Option<String> {
    let t = std::fs::read_to_string(dir.join(crate::native_reg::TOKEN_FILE)).ok()?;
    let t = t.trim();
    (t.len() == 64 && t.bytes().all(|b| b.is_ascii_hexdigit())).then(|| t.to_string())
}

/// Connects and authenticates to the app; returns the stream ready for lines.
pub fn connect_app(token: &str, ports: &[u16]) -> io::Result<TcpStream> {
    let mut last = io::Error::new(io::ErrorKind::NotFound, "app not running");
    for &p in ports {
        let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, p));
        let mut s = match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(s) => s,
            Err(e) => {
                last = e;
                continue;
            }
        };
        let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
        write!(s, "GET /vidgrab/native HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nX-VidGrab-Token: {token}\r\n\r\n")?;
        let mut head = Vec::new();
        let mut b = [0u8; 1];
        while !head.ends_with(b"\r\n\r\n") {
            if s.read(&mut b)? == 0 || head.len() > 4096 {
                break;
            }
            head.push(b[0]);
        }
        if head.starts_with(b"HTTP/1.1 200") {
            s.set_read_timeout(None)?;
            return Ok(s);
        }
        last = io::Error::new(io::ErrorKind::PermissionDenied, "app refused the host");
    }
    Err(last)
}

fn status(up: bool, version: Option<&str>) -> Vec<u8> {
    let mut v = serde_json::json!({ "t": "app", "up": up });
    if let Some(ver) = version {
        v["version"] = ver.into();
    }
    v.to_string().into_bytes()
}

/// The host's own first line on each app connection.
pub fn host_line(origin: &str, browser_exe: Option<&Path>) -> String {
    let mut v = serde_json::json!({ "t": "host", "origin": origin });
    if let Some(p) = browser_exe {
        v["browserExe"] = p.to_string_lossy().into_owned().into();
    }
    format!("{v}\n")
}

/// Runs until the browser closes the port (stdin ends). Exit code for main.
pub fn run(origin: String) -> i32 {
    let Some(dir) = crate::native_reg::native_dir() else { return 2 };
    serve(origin, browser_exe(), &dir, &PORTS, io::stdin(), io::stdout())
}

/// `run` with its inputs injected (tests drive it with pipes and a temp dir).
pub fn serve(
    origin: String,
    browser: Option<PathBuf>,
    dir: &Path,
    ports: &[u16],
    stdin: impl Read + Send + 'static,
    stdout: impl Write + Send + 'static,
) -> i32 {
    let out = Arc::new(Mutex::new(stdout));
    let app: Arc<Mutex<Option<TcpStream>>> = Arc::default();
    let hello: Arc<Mutex<Option<String>>> = Arc::default();

    // browser → app
    let (app_w, hello_w) = (app.clone(), hello.clone());
    let reader = std::thread::spawn(move || {
        let mut stdin = stdin;
        loop {
            let frame = match read_frame(&mut stdin) {
                Ok(Some(f)) => f,
                _ => {
                    // port closed, or a frame we refuse: the host ends (and
                    // the app loop below stops waiting on the socket)
                    if let Some(s) = app_w.lock().unwrap().take() {
                        let _ = s.shutdown(std::net::Shutdown::Both);
                    }
                    return;
                }
            };
            let Some((line, v)) = to_line(&frame) else { continue };
            if v.get("t").and_then(|t| t.as_str()) == Some("hello") {
                *hello_w.lock().unwrap() = Some(line.clone());
            }
            let mut g = app_w.lock().unwrap();
            if let Some(s) = g.as_mut() {
                if s.write_all(line.as_bytes()).is_err() {
                    let _ = s.shutdown(std::net::Shutdown::Both);
                    *g = None;
                }
            } // app not running: dropped; the extension knows from {"t":"app","up":false}
        }
    });

    // app → browser, reconnecting while the browser keeps us
    let host = host_line(&origin, browser.as_deref());
    let mut told_down = false;
    let reader_done = || reader.is_finished();
    loop {
        if reader_done() {
            return 0;
        }
        let conn = read_token(dir).ok_or_else(|| io::Error::other("no token")).and_then(|t| connect_app(&t, ports));
        let Ok(stream) = conn else {
            if !told_down {
                told_down = write_frame(&mut *out.lock().unwrap(), &status(false, None)).is_ok();
            }
            sleep_unless(&reader, RETRY);
            continue;
        };
        let first = format!("{host}{}", hello.lock().unwrap().clone().unwrap_or_default());
        let w = stream.try_clone().and_then(|mut w| w.write_all(first.as_bytes()).map(|_| w));
        let Ok(w) = w else {
            sleep_unless(&reader, RETRY);
            continue;
        };
        *app.lock().unwrap() = Some(w);
        if reader_done() {
            let _ = stream.shutdown(std::net::Shutdown::Both);
            return 0;
        }
        let mut lines = BufReader::new(stream);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match read_line_capped(&mut lines, &mut buf, MAX_OUT) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let Some((line, _)) = to_line(&buf) else { continue };
            if write_frame(&mut *out.lock().unwrap(), line.trim_end().as_bytes()).is_err() {
                return 0; // browser gone
            }
        }
        if let Some(s) = app.lock().unwrap().take() {
            let _ = s.shutdown(std::net::Shutdown::Both);
        }
        told_down = write_frame(&mut *out.lock().unwrap(), &status(false, None)).is_ok();
        sleep_unless(&reader, RETRY);
    }
}

fn sleep_unless(reader: &std::thread::JoinHandle<()>, d: Duration) {
    let step = Duration::from_millis(100);
    let mut left = d;
    while !left.is_zero() && !reader.is_finished() {
        std::thread::sleep(step.min(left));
        left = left.saturating_sub(step);
    }
}

/// One line without the newline, refusing (Err) a line longer than `max`.
pub fn read_line_capped(r: &mut impl BufRead, buf: &mut Vec<u8>, max: usize) -> io::Result<usize> {
    let n = r.by_ref().take(max as u64 + 1).read_until(b'\n', buf)?;
    if buf.ends_with(b"\n") {
        buf.pop();
    } else if n > max {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "line too long"));
    }
    Ok(n)
}

/// Browsers that may start us, by file name (the app opens links with it).
pub const BROWSERS: [&str; 6] = ["chrome.exe", "msedge.exe", "brave.exe", "chromium.exe", "vivaldi.exe", "opera.exe"];

pub fn is_browser_exe(p: &Path) -> bool {
    p.is_absolute()
        && p.file_name().and_then(|n| n.to_str()).is_some_and(|n| BROWSERS.iter().any(|b| b.eq_ignore_ascii_case(n)))
}

/// The browser that started us: Chrome runs hosts through `cmd.exe /c`, so
/// walk a few parents up to the first known browser.
#[cfg(windows)]
fn browser_exe() -> Option<PathBuf> {
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcessId, OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let mut parents = std::collections::HashMap::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE {
            return None;
        }
        let mut e: PROCESSENTRY32W = std::mem::zeroed();
        e.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snap, &mut e) != 0 {
            loop {
                parents.insert(e.th32ProcessID, e.th32ParentProcessID);
                if Process32NextW(snap, &mut e) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    let mut pid = unsafe { GetCurrentProcessId() };
    for _ in 0..5 {
        pid = *parents.get(&pid)?;
        let path = unsafe {
            let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if h.is_null() {
                continue;
            }
            let mut buf = [0u16; 1024];
            let mut len = buf.len() as u32;
            let ok = QueryFullProcessImageNameW(h, 0, buf.as_mut_ptr(), &mut len) != 0;
            CloseHandle(h);
            ok.then(|| PathBuf::from(String::from_utf16_lossy(&buf[..len as usize])))
        };
        if let Some(p) = path.filter(|p| is_browser_exe(p)) {
            return Some(p);
        }
    }
    None
}

#[cfg(not(windows))]
fn browser_exe() -> Option<PathBuf> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn origin_argument_marks_host_mode() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            launched_origin(a(&["VidGrab.exe", "chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi/", "--parent-window=0"])).as_deref(),
            Some("chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi/")
        );
        assert_eq!(launched_origin(a(&["VidGrab.exe", "--minimized"])), None);
        assert_eq!(launched_origin(a(&["chrome-extension://x/"])), None, "argv[0] is the program");
    }

    #[test]
    fn frames_round_trip_and_refuse_oversize() {
        let mut buf = Vec::new();
        write_frame(&mut buf, br#"{"t":"hello"}"#).unwrap();
        write_frame(&mut buf, b"{}").unwrap();
        let mut r = Cursor::new(buf);
        assert_eq!(read_frame(&mut r).unwrap().unwrap(), br#"{"t":"hello"}"#);
        assert_eq!(read_frame(&mut r).unwrap().unwrap(), b"{}");
        assert!(read_frame(&mut r).unwrap().is_none(), "clean end");
        let mut big = ((MAX_IN + 1) as u32).to_le_bytes().to_vec();
        big.extend_from_slice(b"{}");
        assert!(read_frame(&mut Cursor::new(big)).is_err());
        assert!(write_frame(&mut Vec::new(), &vec![b' '; MAX_OUT + 1]).is_err());
    }

    #[test]
    fn only_objects_become_single_lines() {
        let (l, _) = to_line(b"{\"t\":\"x\",\"s\":\"a\\nb\"}").unwrap();
        assert_eq!(l.matches('\n').count(), 1, "{l}");
        assert!(to_line(b"[1]").is_none());
        assert!(to_line(b"nope").is_none());
    }

    #[test]
    fn capped_lines() {
        let mut r = Cursor::new(b"abc\ndefgh\n".to_vec());
        let mut b = Vec::new();
        assert_eq!(read_line_capped(&mut r, &mut b, 4).unwrap(), 4);
        assert_eq!(b, b"abc");
        b.clear();
        assert!(read_line_capped(&mut r, &mut b, 4).is_err());
    }

    #[test]
    fn browser_names() {
        #[cfg(windows)]
        let p = Path::new(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe");
        #[cfg(not(windows))]
        let p = Path::new("/opt/Edge/msedge.exe");
        assert!(is_browser_exe(p));
        assert!(!is_browser_exe(Path::new("msedge.exe")), "absolute only");
        assert!(!is_browser_exe(Path::new("/tmp/evil.exe")));
    }
}
