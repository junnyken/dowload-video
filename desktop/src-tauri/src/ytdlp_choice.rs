//! Which yt-dlp to run (task #6333, Phase 34A-3) — pure logic only: no Tauri,
//! no network, no file system, so it is unit-tested on its own.
//!
//! The installer bundles a pinned yt-dlp (binaries.lock.json, checked by
//! checksum.rs). An admin may approve a NEWER official yt-dlp.exe on the
//! server (GET /api/v1/client/ytdlp → {version, url, sha256}, 204 = none);
//! ytdlp_update.rs downloads it, and this module decides:
//!
//! * `plan`   — after asking the server: keep what we have, download the
//!              offered version, or go back to the bundled one (server said
//!              204, or offers nothing newer than the bundled copy);
//! * `choose` — before each run: the downloaded one only when it is newer
//!              than the bundled one AND its file still hashes to the value
//!              recorded when it was verified; otherwise the bundled one.

use serde::{Deserialize, Serialize};

pub const URL_PREFIX: &str = "https://github.com/yt-dlp/yt-dlp/releases/download/";
pub const ASSET: &str = "yt-dlp.exe";

/// yt-dlp versions: `YYYY.MM.DD` or `YYYY.MM.DD.N` (nightly / hotfix).
pub fn parse_version(s: &str) -> Option<[u32; 4]> {
    let parts: Vec<&str> = s.split('.').collect();
    if !(parts.len() == 3 || parts.len() == 4) {
        return None;
    }
    let widths: [(usize, usize); 4] = [(4, 4), (2, 2), (2, 2), (1, 6)];
    let mut out = [0u32; 4];
    for (i, p) in parts.iter().enumerate() {
        let (lo, hi) = widths[i];
        if p.len() < lo || p.len() > hi || !p.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        out[i] = p.parse().ok()?;
    }
    if !(1..=12).contains(&out[1]) || !(1..=31).contains(&out[2]) {
        return None;
    }
    Some(out)
}

/// `a` is strictly newer than `b`. False when either does not parse.
pub fn is_newer(a: &str, b: &str) -> bool {
    match (parse_version(a), parse_version(b)) {
        (Some(x), Some(y)) => x > y,
        _ => false,
    }
}

/// The only URL we ever download from: the official release asset of THAT
/// version (so it starts with URL_PREFIX and ends with `/yt-dlp.exe`).
pub fn valid_url(url: &str, version: &str) -> bool {
    parse_version(version).is_some()
        && url.starts_with(URL_PREFIX)
        && url.ends_with("/yt-dlp.exe")
        && url == format!("{URL_PREFIX}{version}/{ASSET}")
}

pub fn valid_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// File name in `<app local data>/tools`; the version was validated, so it
/// cannot contain a path separator.
pub fn file_name(version: &str) -> String {
    format!("yt-dlp-{version}.exe")
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Offer {
    pub version: String,
    pub url: String,
    pub sha256: String,
}

/// The server's 200 body; None when anything in it is not exactly right.
pub fn parse_offer(body: &str) -> Option<Offer> {
    let mut o: Offer = serde_json::from_str(body).ok()?;
    o.version = o.version.trim().to_string();
    o.sha256 = o.sha256.trim().to_ascii_lowercase();
    (valid_url(&o.url, &o.version) && valid_sha256(&o.sha256)).then_some(o)
}

/// `<app local data>/tools/ytdlp-active.json`: the downloaded version that
/// passed the sha256 check, and that hash.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Active {
    pub version: String,
    pub sha256: String,
}

pub fn parse_active(body: &str) -> Option<Active> {
    let a: Active = serde_json::from_str(body).ok()?;
    (parse_version(&a.version).is_some() && valid_sha256(&a.sha256)).then_some(a)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    Keep,
    Download(Offer),
    /// Forget the downloaded one; the bundled yt-dlp is used again.
    Deactivate,
}

/// `offer` None = the server answered 204 (feature off). An unreachable
/// server is not a reason to change anything: the caller does not call this.
pub fn plan(bundled: &str, active: Option<&Active>, offer: Option<&Offer>) -> Plan {
    let fallback = if active.is_some() { Plan::Deactivate } else { Plan::Keep };
    let Some(o) = offer else { return fallback };
    if !is_newer(&o.version, bundled) {
        return fallback;
    }
    match active {
        Some(a) if a.version == o.version && a.sha256.eq_ignore_ascii_case(&o.sha256) => Plan::Keep,
        _ => Plan::Download(o.clone()),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pick {
    Bundled,
    Downloaded(Active),
}

/// `hash_matches` is asked only when the downloaded one would otherwise win
/// (it hashes the file on disk against `Active::sha256`).
pub fn choose(bundled: &str, active: Option<&Active>, hash_matches: impl FnOnce(&Active) -> bool) -> Pick {
    match active {
        Some(a) if is_newer(&a.version, bundled) && hash_matches(a) => Pick::Downloaded(a.clone()),
        _ => Pick::Bundled,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const SHB: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    fn offer(v: &str, sha: &str) -> Offer {
        Offer { version: v.into(), url: format!("{URL_PREFIX}{v}/yt-dlp.exe"), sha256: sha.into() }
    }

    fn active(v: &str, sha: &str) -> Active {
        Active { version: v.into(), sha256: sha.into() }
    }

    #[test]
    fn versions_parse_and_compare() {
        assert_eq!(parse_version("2026.08.19"), Some([2026, 8, 19, 0]));
        assert_eq!(parse_version("2026.10.10.1"), Some([2026, 10, 10, 1]));
        for bad in ["", "2026.8.19", "26.08.19", "2026.13.01", "2026.00.10", "2026.10.32", "2026.10.10.", "2026.10.10.1.2",
            "2026.10.10a", "../2026.10.10", "2026.10.10.1234567", "+2026.10.10"] {
            assert_eq!(parse_version(bad), None, "{bad}");
        }
        assert!(is_newer("2026.10.10", "2026.08.19"));
        assert!(is_newer("2026.08.19.1", "2026.08.19"));
        assert!(is_newer("2027.01.01", "2026.12.31"));
        assert!(!is_newer("2026.08.19", "2026.08.19"));
        assert!(!is_newer("2026.08.18", "2026.08.19"));
        assert!(!is_newer("garbage", "2026.08.19"));
        assert!(!is_newer("2026.10.10", "garbage"));
    }

    #[test]
    fn only_the_official_asset_url() {
        let v = "2026.10.10";
        assert!(valid_url("https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe", v));
        for bad in [
            "http://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe",
            "https://github.com/evil/yt-dlp/releases/download/2026.10.10/yt-dlp.exe",
            "https://github.com.evil.io/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe",
            "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.09/yt-dlp.exe",
            "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp_x86.exe",
            "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/../x/yt-dlp.exe",
            "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe?x=/yt-dlp.exe",
        ] {
            assert!(!valid_url(bad, v), "{bad}");
        }
        assert!(!valid_url("https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp.exe", "x"));
    }

    #[test]
    fn offer_body_is_checked() {
        let body = format!(r#"{{"version":"2026.10.10","url":"{URL_PREFIX}2026.10.10/yt-dlp.exe","sha256":"{}"}}"#, SHA.to_uppercase());
        assert_eq!(parse_offer(&body), Some(offer("2026.10.10", SHA)));
        let bad_sha = format!(r#"{{"version":"2026.10.10","url":"{URL_PREFIX}2026.10.10/yt-dlp.exe","sha256":"abc"}}"#);
        assert_eq!(parse_offer(&bad_sha), None);
        let other_ver = format!(r#"{{"version":"2026.10.11","url":"{URL_PREFIX}2026.10.10/yt-dlp.exe","sha256":"{SHA}"}}"#);
        assert_eq!(parse_offer(&other_ver), None);
        assert_eq!(parse_offer("not json"), None);
        assert_eq!(parse_active(&format!(r#"{{"version":"2026.10.10","sha256":"{SHA}"}}"#)), Some(active("2026.10.10", SHA)));
        assert_eq!(parse_active(r#"{"version":"..\\x","sha256":"aa"}"#), None);
    }

    #[test]
    fn plan_follows_the_server() {
        let b = "2026.08.19";
        // Nothing offered (204): nothing to do, or go back to bundled.
        assert_eq!(plan(b, None, None), Plan::Keep);
        assert_eq!(plan(b, Some(&active("2026.10.10", SHA)), None), Plan::Deactivate);
        // Newer offered: download unless that exact file is already active.
        let o = offer("2026.10.10", SHA);
        assert_eq!(plan(b, None, Some(&o)), Plan::Download(o.clone()));
        assert_eq!(plan(b, Some(&active("2026.10.10", SHA)), Some(&o)), Plan::Keep);
        assert_eq!(plan(b, Some(&active("2026.10.10", SHB)), Some(&o)), Plan::Download(o.clone()));
        // Admin rolled back to an older (still newer than bundled) version.
        let older = offer("2026.09.01", SHB);
        assert_eq!(plan(b, Some(&active("2026.10.10", SHA)), Some(&older)), Plan::Download(older.clone()));
        // Offered version not newer than the bundled one (app was updated).
        assert_eq!(plan(b, None, Some(&offer("2026.08.19", SHA))), Plan::Keep);
        assert_eq!(plan(b, Some(&active("2026.10.10", SHA)), Some(&offer("2026.07.01", SHA))), Plan::Deactivate);
    }

    #[test]
    fn choose_needs_newer_and_matching_hash() {
        let b = "2026.08.19";
        let a = active("2026.10.10", SHA);
        assert_eq!(choose(b, None, |_| panic!("not asked")), Pick::Bundled);
        assert_eq!(choose(b, Some(&a), |x| x.sha256 == SHA), Pick::Downloaded(a.clone()));
        // File changed on disk since it was verified.
        assert_eq!(choose(b, Some(&a), |_| false), Pick::Bundled);
        // The app now bundles the same or a newer yt-dlp.
        assert_eq!(choose("2026.10.10", Some(&a), |_| panic!("not asked")), Pick::Bundled);
        assert_eq!(choose("2026.11.01", Some(&a), |_| panic!("not asked")), Pick::Bundled);
    }
}
