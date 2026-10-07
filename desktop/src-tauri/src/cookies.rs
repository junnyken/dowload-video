//! The user's own platform cookies (PLAN-32D §3, P1).
//!
//! Path of a cookie: in-app login window (login_window.rs) -> filtered to the
//! platform's own domains here -> Netscape text -> encrypted blob on disk
//! (DPAPI on Windows) -> per-job temp file handed to yt-dlp with `--cookies`
//! -> deleted when yt-dlp exits. Nothing here ever logs a cookie name, value
//! or the path of a cookie file, and nothing sends a cookie anywhere: yt-dlp
//! itself only sends each cookie to the domain it belongs to.
//!
//! Everything except the DPAPI calls is plain Rust so `cargo test` covers it
//! on Linux.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// One platform the app may use the user's cookies for.
pub struct PlatformDef {
    /// Slug shared with the server (`CLIENT_COOKIES_PLATFORMS`) and the UI.
    pub slug: &'static str,
    /// Cookies kept for this platform: exactly these domains and their subdomains.
    pub cookie_domains: &'static [&'static str],
    /// Hosts (and their subdomains) whose URLs belong to this platform.
    pub url_hosts: &'static [&'static str],
    /// The only page the login window opens.
    pub login_url: &'static str,
}

pub const PLATFORMS: &[PlatformDef] = &[
    PlatformDef {
        slug: "douyin",
        cookie_domains: &["douyin.com", "iesdouyin.com"],
        url_hosts: &["douyin.com", "iesdouyin.com"],
        // No sign-in needed: playing one video sets the signature cookies.
        login_url: "https://www.douyin.com/",
    },
    PlatformDef {
        slug: "instagram",
        cookie_domains: &["instagram.com"],
        url_hosts: &["instagram.com", "instagr.am"],
        login_url: "https://www.instagram.com/accounts/login/",
    },
    PlatformDef {
        slug: "facebook",
        cookie_domains: &["facebook.com"],
        url_hosts: &["facebook.com", "fb.watch", "fb.com"],
        login_url: "https://www.facebook.com/login/",
    },
    PlatformDef {
        slug: "twitter",
        cookie_domains: &["x.com", "twitter.com"],
        url_hosts: &["x.com", "twitter.com"],
        login_url: "https://x.com/i/flow/login",
    },
    PlatformDef {
        slug: "youtube",
        cookie_domains: &["youtube.com", "google.com"],
        url_hosts: &["youtube.com", "youtu.be"],
        login_url: "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F",
    },
    PlatformDef {
        slug: "bilibili",
        cookie_domains: &["bilibili.com"],
        url_hosts: &["bilibili.com", "b23.tv"],
        login_url: "https://passport.bilibili.com/login",
    },
    PlatformDef {
        slug: "threads",
        cookie_domains: &["threads.net", "threads.com", "instagram.com"],
        url_hosts: &["threads.net", "threads.com"],
        login_url: "https://www.threads.com/login",
    },
    PlatformDef {
        slug: "reddit",
        cookie_domains: &["reddit.com"],
        url_hosts: &["reddit.com", "redd.it"],
        login_url: "https://www.reddit.com/login/",
    },
    PlatformDef {
        slug: "pinterest",
        cookie_domains: &["pinterest.com"],
        url_hosts: &["pinterest.com", "pin.it"],
        login_url: "https://www.pinterest.com/login/",
    },
    PlatformDef {
        slug: "tiktok",
        cookie_domains: &["tiktok.com"],
        url_hosts: &["tiktok.com"],
        login_url: "https://www.tiktok.com/login",
    },
    PlatformDef {
        slug: "vimeo",
        cookie_domains: &["vimeo.com"],
        url_hosts: &["vimeo.com"],
        login_url: "https://vimeo.com/log_in",
    },
];

pub fn def(slug: &str) -> Option<&'static PlatformDef> {
    PLATFORMS.iter().find(|p| p.slug == slug)
}

/// `host` is `domain` or a subdomain of it. Case-insensitive, a trailing dot
/// on either side is ignored. "evil-douyin.com" and "douyin.com.evil.com" do
/// NOT match "douyin.com".
pub fn domain_matches(host: &str, domain: &str) -> bool {
    let h = host.trim().trim_end_matches('.').to_ascii_lowercase();
    let d = domain.trim().trim_start_matches('.').trim_end_matches('.').to_ascii_lowercase();
    if h.is_empty() || d.is_empty() || !d.contains('.') {
        return false;
    }
    h == d || (h.len() > d.len() && h.ends_with(&d) && h.as_bytes()[h.len() - d.len() - 1] == b'.')
}

/// Which cookie platform a download URL belongs to, decided from its host only
/// (Rust never trusts the webview for this). http(s) only; no userinfo.
pub fn platform_for_url(raw: &str) -> Option<&'static str> {
    let u = url::Url::parse(raw.trim()).ok()?;
    if !matches!(u.scheme(), "http" | "https") || !u.username().is_empty() || u.password().is_some() {
        return None;
    }
    let host = u.host_str()?;
    PLATFORMS.iter().find(|p| p.url_hosts.iter().any(|d| domain_matches(host, d))).map(|p| p.slug)
}

/// A cookie as read from the webview, independent of the webview crate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawCookie {
    pub name: String,
    pub value: String,
    /// Without the leading dot (the `cookie` crate strips it).
    pub domain: String,
    pub path: String,
    pub secure: bool,
    pub http_only: bool,
    /// Unix seconds; None = session cookie.
    pub expires: Option<i64>,
}

/// Keeps the cookies whose domain is one of the platform's cookie domains (or a
/// subdomain of one). Duplicates (same domain/path/name) keep the last one.
pub fn filter_for(p: &PlatformDef, cookies: Vec<RawCookie>) -> Vec<RawCookie> {
    let mut out: Vec<RawCookie> = Vec::new();
    for c in cookies {
        let d = c.domain.trim().trim_start_matches('.').to_ascii_lowercase();
        if !p.cookie_domains.iter().any(|a| domain_matches(&d, a)) {
            continue;
        }
        out.retain(|o| !(o.domain.eq_ignore_ascii_case(&c.domain) && o.path == c.path && o.name == c.name));
        out.push(c);
    }
    out
}

pub const NETSCAPE_HEADER: &str = "# Netscape HTTP Cookie File";
const HTTPONLY_PREFIX: &str = "#HttpOnly_";

fn clean_field(s: &str) -> bool {
    !s.chars().any(|c| c.is_control()) // tabs, CR, LF and every other control char
}

fn clean_domain(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 253
        && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
        && !s.starts_with('-')
}

/// Netscape cookies.txt for yt-dlp. A cookie yt-dlp could not parse would be
/// echoed verbatim to stderr by yt-dlp's loader, so anything with a control
/// character (tab/newline) in a field, an empty name, a name starting with
/// '#', or a non-hostname domain is dropped (counted, never printed).
/// Domain cookies are written as `.domain` + TRUE (the webview API does not
/// tell host-only from domain cookies; TRUE only widens a cookie to the
/// subdomains of a domain it already belongs to). Session cookies get expiry 0,
/// which yt-dlp reads as a session cookie. Returns (text, kept, dropped).
pub fn to_netscape(cookies: &[RawCookie]) -> (String, usize, usize) {
    let mut s = String::from(NETSCAPE_HEADER);
    s.push('\n');
    let (mut kept, mut dropped) = (0, 0);
    for c in cookies {
        let domain = c.domain.trim().trim_start_matches('.').to_ascii_lowercase();
        let path = if c.path.is_empty() { "/" } else { c.path.as_str() };
        let ok = clean_domain(&domain)
            && !c.name.is_empty()
            && !c.name.starts_with('#')
            && clean_field(&c.name)
            && clean_field(&c.value)
            && clean_field(path)
            && path.starts_with('/')
            && c.name.len() + c.value.len() <= 8192;
        if !ok {
            dropped += 1;
            continue;
        }
        let expires = c.expires.filter(|e| *e > 0).unwrap_or(0);
        if c.http_only {
            s.push_str(HTTPONLY_PREFIX);
        }
        s.push_str(&format!(
            ".{domain}\tTRUE\t{path}\t{}\t{expires}\t{}\t{}\n",
            if c.secure { "TRUE" } else { "FALSE" },
            c.name,
            c.value
        ));
        kept += 1;
    }
    (s, kept, dropped)
}

// ---------------------------------------------------------------- store

/// What the UI may know about a saved blob. Never a cookie name or value.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq, Default)]
#[serde(rename_all = "camelCase")]
pub struct Meta {
    /// Unix seconds.
    pub saved_at: u64,
    pub cookie_count: usize,
    /// Latest expiry among the persistent cookies (unix seconds), if any.
    pub latest_expiry: Option<i64>,
    /// A download with these cookies failed with a login / forbidden error.
    pub suspect_expired: bool,
}

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub platform: &'static str,
    pub saved: bool,
    /// Unix seconds.
    pub saved_at: Option<u64>,
    pub suspect_expired: bool,
}

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Saved {
    pub platform: &'static str,
    pub cookie_count: usize,
    pub saved_at: u64,
}

pub fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn blob_path(dir: &Path, slug: &str) -> PathBuf {
    dir.join(format!("{slug}.bin"))
}

fn meta_path(dir: &Path, slug: &str) -> PathBuf {
    dir.join(format!("{slug}.json"))
}

/// Writes `bytes` to `path` via a temp file + rename; 0600 on unix.
fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = path.with_extension("part");
    {
        let mut o = std::fs::OpenOptions::new();
        o.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        let mut f = o.open(&tmp)?;
        std::io::Write::write_all(&mut f, bytes)?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp, path)
}

/// Encrypts and stores the Netscape text of one platform, with its meta.
pub fn save(dir: &Path, slug: &str, netscape: &str, cookies: &[RawCookie], kept: usize) -> Result<Saved, String> {
    let p = def(slug).ok_or("unknown platform")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("cannot create the cookie folder: {}", e.kind()))?;
    let blob = protect(netscape.as_bytes())?;
    write_private(&blob_path(dir, slug), &blob).map_err(|e| format!("cannot write the cookie store: {}", e.kind()))?;
    let meta = Meta {
        saved_at: now_secs(),
        cookie_count: kept,
        latest_expiry: cookies.iter().filter_map(|c| c.expires).filter(|e| *e > 0).max(),
        suspect_expired: false,
    };
    write_meta(dir, slug, &meta)?;
    Ok(Saved { platform: p.slug, cookie_count: kept, saved_at: meta.saved_at })
}

fn write_meta(dir: &Path, slug: &str, meta: &Meta) -> Result<(), String> {
    let json = serde_json::to_vec(meta).map_err(|e| e.to_string())?;
    write_private(&meta_path(dir, slug), &json).map_err(|e| format!("cannot write the cookie store: {}", e.kind()))
}

fn read_meta(dir: &Path, slug: &str) -> Option<Meta> {
    serde_json::from_slice(&std::fs::read(meta_path(dir, slug)).ok()?).ok()
}

pub fn has_blob(dir: &Path, slug: &str) -> bool {
    blob_path(dir, slug).is_file()
}

/// The decrypted Netscape text, or None when nothing is saved.
pub fn load(dir: &Path, slug: &str) -> Result<Option<String>, String> {
    let bytes = match std::fs::read(blob_path(dir, slug)) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("cannot read the cookie store: {}", e.kind())),
    };
    let plain = unprotect(&bytes)?;
    String::from_utf8(plain).map(Some).map_err(|_| "the cookie store is damaged".into())
}

/// Deletes the blob and its meta. Ok when there was nothing to delete.
pub fn clear(dir: &Path, slug: &str) -> Result<(), String> {
    for p in [blob_path(dir, slug), meta_path(dir, slug)] {
        match std::fs::remove_file(&p) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("cannot delete the cookie store: {}", e.kind())),
        }
    }
    Ok(())
}

/// Flags a blob whose last use failed with a login / forbidden error.
pub fn mark_suspect(dir: &Path, slug: &str) {
    if !has_blob(dir, slug) {
        return;
    }
    let mut m = read_meta(dir, slug).unwrap_or_default();
    if !m.suspect_expired {
        m.suspect_expired = true;
        let _ = write_meta(dir, slug, &m);
    }
}

/// One row per known platform. "Suspect" also when every persistent cookie
/// has passed its expiry.
pub fn status(dir: &Path, now: u64) -> Vec<Status> {
    PLATFORMS
        .iter()
        .map(|p| {
            let saved = has_blob(dir, p.slug);
            let meta = if saved { read_meta(dir, p.slug) } else { None };
            let expired = meta.as_ref().and_then(|m| m.latest_expiry).is_some_and(|e| e > 0 && (e as u64) <= now);
            Status {
                platform: p.slug,
                saved,
                saved_at: meta.as_ref().map(|m| m.saved_at),
                suspect_expired: saved && (meta.as_ref().is_some_and(|m| m.suspect_expired) || expired),
            }
        })
        .collect()
}

// ---------------------------------------------------------------- per-job temp file

const TEMP_PREFIX: &str = "ck-";

/// `ck-<key>.txt`; deleted when dropped (after yt-dlp has exited).
pub struct TempCookieFile {
    path: PathBuf,
    needle: String,
}

impl TempCookieFile {
    pub fn path(&self) -> &Path {
        &self.path
    }
    /// Log lines containing this are dropped (they would show the file path).
    pub fn needle(&self) -> &str {
        &self.needle
    }
}

impl Drop for TempCookieFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Keys are random hex (random_key); anything outside [A-Za-z0-9_-]{1,64} is refused.
pub fn write_temp(tmp_dir: &Path, key: &str, netscape: &str) -> Result<TempCookieFile, String> {
    if key.is_empty() || key.len() > 64 || !key.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err("invalid temp key".into());
    }
    std::fs::create_dir_all(tmp_dir).map_err(|e| format!("cannot create the temp folder: {}", e.kind()))?;
    let name = format!("{TEMP_PREFIX}{key}.txt");
    let path = tmp_dir.join(&name);
    let f = TempCookieFile { path, needle: name };
    write_private(&f.path, netscape.as_bytes()).map_err(|e| format!("cannot write a temp file: {}", e.kind()))?;
    Ok(f)
}

/// Random key: one temp file per yt-dlp run (download, probe, channel listing).
pub fn random_key() -> Result<String, String> {
    let mut b = [0u8; 12];
    getrandom::fill(&mut b).map_err(|e| format!("no secure random source: {e}"))?;
    Ok(format!("r{}", b.iter().map(|x| format!("{x:02x}")).collect::<String>()))
}

/// Deletes leftover `ck-*.txt` (a crash between spawn and cleanup). App start only.
pub fn sweep_temp(tmp_dir: &Path) -> usize {
    let Ok(rd) = std::fs::read_dir(tmp_dir) else { return 0 };
    let mut n = 0;
    for e in rd.flatten() {
        let name = e.file_name();
        let name = name.to_string_lossy();
        if (name.starts_with(TEMP_PREFIX) && (name.ends_with(".txt") || name.ends_with(".part")))
            && e.file_type().is_ok_and(|t| t.is_file())
            && std::fs::remove_file(e.path()).is_ok()
        {
            n += 1;
        }
    }
    n
}

/// True when a log line must not be shown or kept (it names the temp cookie file).
pub fn is_secret_line(line: &str, needle: Option<&str>) -> bool {
    needle.is_some_and(|n| !n.is_empty() && line.contains(n))
}

// ---------------------------------------------------------------- encryption

/// Constant extra entropy: another program running as the same Windows user
/// must also know this to call CryptUnprotectData on our blobs (a speed bump,
/// not a secret).
#[cfg(windows)]
const ENTROPY: &[u8] = b"VidGrab/xyz.tinhgon.vidgrab/cookies/v1";

#[cfg(windows)]
pub fn protect(plain: &[u8]) -> Result<Vec<u8>, String> {
    dpapi::run(plain, true)
}

#[cfg(windows)]
pub fn unprotect(blob: &[u8]) -> Result<Vec<u8>, String> {
    dpapi::run(blob, false)
}

#[cfg(windows)]
mod dpapi {
    use super::ENTROPY;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    pub fn run(input: &[u8], encrypt: bool) -> Result<Vec<u8>, String> {
        let len = u32::try_from(input.len()).map_err(|_| "cookie data is too large".to_string())?;
        let data_in = CRYPT_INTEGER_BLOB { cbData: len, pbData: input.as_ptr() as *mut u8 };
        let entropy = CRYPT_INTEGER_BLOB { cbData: ENTROPY.len() as u32, pbData: ENTROPY.as_ptr() as *mut u8 };
        let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
        // SAFETY: every pointer is valid for the call; DPAPI only reads
        // data_in/entropy; `out` is allocated by DPAPI with LocalAlloc and
        // freed below with LocalFree after copying.
        let ok = unsafe {
            if encrypt {
                CryptProtectData(
                    &data_in,
                    std::ptr::null(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut out,
                )
            } else {
                CryptUnprotectData(
                    &data_in,
                    std::ptr::null_mut(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut out,
                )
            }
        };
        if ok == 0 || out.pbData.is_null() {
            let code = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
            return Err(if encrypt {
                format!("Windows could not encrypt the cookies (error {code})")
            } else {
                format!("Windows could not decrypt the saved cookies (error {code}); connect the account again")
            });
        }
        // SAFETY: DPAPI returned cbData bytes at pbData.
        let v = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize) }.to_vec();
        // SAFETY: pbData was allocated by DPAPI with LocalAlloc.
        unsafe {
            LocalFree(out.pbData as _);
        }
        Ok(v)
    }
}

/// Dev / test builds only (Linux): the file is 0600, not encrypted.
#[cfg(not(windows))]
pub fn protect(plain: &[u8]) -> Result<Vec<u8>, String> {
    let mut v = b"VGPLAIN1".to_vec();
    v.extend_from_slice(plain);
    Ok(v)
}

#[cfg(not(windows))]
pub fn unprotect(blob: &[u8]) -> Result<Vec<u8>, String> {
    blob.strip_prefix(b"VGPLAIN1".as_slice()).map(<[u8]>::to_vec).ok_or_else(|| "the cookie store is damaged".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ck(name: &str, value: &str, domain: &str) -> RawCookie {
        RawCookie {
            name: name.into(),
            value: value.into(),
            domain: domain.into(),
            path: "/".into(),
            secure: true,
            http_only: false,
            expires: Some(1_900_000_000),
        }
    }

    fn tmpdir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("vg-ck-test-{tag}-{}", random_key().unwrap()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn domain_suffix_is_strict() {
        assert!(domain_matches("douyin.com", "douyin.com"));
        assert!(domain_matches("www.douyin.com", "douyin.com"));
        assert!(domain_matches("V.DOUYIN.COM.", "douyin.com"));
        assert!(domain_matches("www.douyin.com", ".douyin.com"));
        for evil in ["evil-douyin.com", "douyin.com.evil.com", "xdouyin.com", ".evil-douyin.com", "com", "", "douyin.co"] {
            assert!(!domain_matches(evil, "douyin.com"), "{evil}");
        }
        // A bare TLD is never a cookie domain.
        assert!(!domain_matches("douyin.com", "com"));
    }

    #[test]
    fn platform_from_url_host() {
        assert_eq!(platform_for_url("https://www.douyin.com/video/7311"), Some("douyin"));
        assert_eq!(platform_for_url("https://v.douyin.com/abc/"), Some("douyin"));
        assert_eq!(platform_for_url("https://www.iesdouyin.com/share/video/1"), Some("douyin"));
        assert_eq!(platform_for_url("https://youtu.be/x"), Some("youtube"));
        assert_eq!(platform_for_url("https://m.youtube.com/watch?v=x"), Some("youtube"));
        assert_eq!(platform_for_url("https://x.com/a/status/1"), Some("twitter"));
        assert_eq!(platform_for_url("https://www.threads.net/@a/post/1"), Some("threads"));
        assert_eq!(platform_for_url("https://www.instagram.com/reel/1"), Some("instagram"));
        for none in [
            "https://douyin.com.evil.com/video/1",
            "https://evil-douyin.com/video/1",
            "https://www.douyin.com@evil.com/",
            "https://user@www.douyin.com/",
            "ftp://www.douyin.com/",
            "https://example.com/?u=https://www.douyin.com/",
            "https://v26-web.douyinvod.com/x.mp4",
            "not a url",
        ] {
            assert_eq!(platform_for_url(none), None, "{none}");
        }
    }

    #[test]
    fn filter_keeps_only_platform_domains() {
        let p = def("douyin").unwrap();
        let all = vec![
            ck("a", "1", "douyin.com"),
            ck("b", "2", "www.douyin.com"),
            ck("c", "3", "iesdouyin.com"),
            ck("d", "4", "evil-douyin.com"),
            ck("e", "5", "douyin.com.evil.com"),
            ck("f", "6", "google.com"),
            ck("a", "7", "douyin.com"), // duplicate: last one wins
        ];
        let kept = filter_for(p, all);
        let names: Vec<_> = kept.iter().map(|c| format!("{}={}", c.name, c.value)).collect();
        assert_eq!(names, vec!["b=2", "c=3", "a=7"]);
        // youtube keeps google.com, threads keeps instagram.com
        assert_eq!(filter_for(def("youtube").unwrap(), vec![ck("SID", "x", "google.com"), ck("t", "y", "tiktok.com")]).len(), 1);
        assert_eq!(filter_for(def("threads").unwrap(), vec![ck("sessionid", "x", "instagram.com")]).len(), 1);
    }

    #[test]
    fn every_platform_has_an_https_login_page_on_its_own_domain() {
        for p in PLATFORMS {
            let u = url::Url::parse(p.login_url).unwrap();
            assert_eq!(u.scheme(), "https", "{}", p.slug);
            let h = u.host_str().unwrap();
            assert!(p.cookie_domains.iter().any(|d| domain_matches(h, d)), "{} -> {h}", p.slug);
        }
    }

    #[test]
    fn netscape_output_and_sanitising() {
        let mut http_only = ck("sessionid", "abc", "www.douyin.com");
        http_only.http_only = true;
        let mut session = ck("ttwid", "x y", "douyin.com");
        session.expires = None;
        session.secure = false;
        session.path = String::new();
        let bad = vec![
            ck("t\tab", "v", "douyin.com"),
            ck("nl", "v\nX", "douyin.com"),
            ck("cr", "v\r", "douyin.com"),
            ck("", "v", "douyin.com"),
            ck("#c", "v", "douyin.com"),
            ck("dom", "v", "douyin.com\tTRUE"),
            ck("dom2", "v", "bad domain.com"),
        ];
        let mut all = vec![http_only, session];
        all.extend(bad);
        let (text, kept, dropped) = to_netscape(&all);
        assert_eq!((kept, dropped), (2, 7));
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines[0], NETSCAPE_HEADER);
        assert_eq!(lines[1], "#HttpOnly_.www.douyin.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\tabc");
        assert_eq!(lines[2], ".douyin.com\tTRUE\t/\tFALSE\t0\tttwid\tx y");
        assert_eq!(lines.len(), 3);
        for l in &lines[1..] {
            assert_eq!(l.trim_start_matches(HTTPONLY_PREFIX).split('\t').count(), 7);
        }
        // Nothing of a dropped cookie reaches the text.
        assert!(!text.contains("v\n") && !text.contains("#c") && !text.contains("dom"));
    }

    #[test]
    fn store_round_trip_status_and_clear() {
        let dir = tmpdir("store");
        let cookies = vec![ck("a", "1", "douyin.com")];
        let (text, kept, _) = to_netscape(&cookies);
        assert_eq!(load(&dir, "douyin").unwrap(), None);
        let saved = save(&dir, "douyin", &text, &cookies, kept).unwrap();
        assert_eq!(saved.platform, "douyin");
        assert_eq!(saved.cookie_count, 1);
        assert_eq!(load(&dir, "douyin").unwrap().as_deref(), Some(text.as_str()));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.join("douyin.bin")).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
        let st = status(&dir, now_secs());
        let d = st.iter().find(|s| s.platform == "douyin").unwrap();
        assert!(d.saved && !d.suspect_expired && d.saved_at.is_some());
        assert!(st.iter().filter(|s| s.platform != "douyin").all(|s| !s.saved));
        // status never carries cookie data
        let json = serde_json::to_string(&st).unwrap();
        assert!(!json.contains("\"a\"") && json.contains("suspectExpired"));

        mark_suspect(&dir, "douyin");
        assert!(status(&dir, now_secs()).iter().find(|s| s.platform == "douyin").unwrap().suspect_expired);
        // every persistent cookie past its expiry -> suspect too
        save(&dir, "douyin", &text, &cookies, kept).unwrap();
        assert!(!status(&dir, 1_800_000_000).iter().find(|s| s.platform == "douyin").unwrap().suspect_expired);
        assert!(status(&dir, 1_900_000_001).iter().find(|s| s.platform == "douyin").unwrap().suspect_expired);

        clear(&dir, "douyin").unwrap();
        clear(&dir, "douyin").unwrap(); // idempotent
        assert_eq!(load(&dir, "douyin").unwrap(), None);
        mark_suspect(&dir, "douyin"); // no blob: no meta resurrected
        assert!(!dir.join("douyin.json").exists());
        assert!(save(&dir, "nope", &text, &cookies, kept).is_err());
        // A damaged blob is an error, not garbage text.
        std::fs::write(dir.join("douyin.bin"), b"garbage").unwrap();
        assert!(load(&dir, "douyin").is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn temp_file_lifecycle_and_sweep() {
        let dir = tmpdir("tmp");
        let f = write_temp(&dir, "job-1", "x").unwrap();
        let p = f.path().to_path_buf();
        assert!(p.is_file());
        assert_eq!(f.needle(), "ck-job-1.txt");
        assert!(is_secret_line(&format!("[debug] reading {}", p.display()), Some(f.needle())));
        assert!(!is_secret_line("[download] 50%", Some(f.needle())));
        assert!(!is_secret_line("anything", None));
        drop(f);
        assert!(!p.exists());
        assert!(write_temp(&dir, "../x", "x").is_err());
        assert!(write_temp(&dir, "", "x").is_err());
        // leftovers from a crash are swept, other files are not
        std::fs::write(dir.join("ck-old.txt"), "x").unwrap();
        std::fs::write(dir.join("keep.txt"), "x").unwrap();
        let leaked = write_temp(&dir, &random_key().unwrap(), "x").unwrap();
        std::mem::forget(leaked);
        assert_eq!(sweep_temp(&dir), 2);
        assert!(dir.join("keep.txt").exists());
        assert_eq!(sweep_temp(&dir.join("missing")), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
