//! A newer yt-dlp approved by the server, without a new app release
//! (task #6333, Phase 34A-3). Decisions live in ytdlp_choice.rs (pure).
//!
//! * At start and every 12 h (Windows builds only) Rust itself asks
//!   GET /api/v1/client/ytdlp (5 s, rustls + bundled roots, like
//!   quota_gate::fetch_policy). 200 {version, url, sha256} with a version
//!   newer than the bundled one → download the official GitHub asset to
//!   `<app local data>/tools/yt-dlp-<version>.exe.part` (https only, size
//!   capped), hash it while writing, and only when the hash equals the
//!   server's value rename it and record {version, sha256} in
//!   `tools/ytdlp-active.json`. 204, or nothing newer than the bundled one →
//!   the record is removed and the bundled yt-dlp is used again. Server not
//!   reachable → nothing changes.
//! * Before each run `pick` hashes the downloaded file again and uses it only
//!   if it still matches the record; anything else → the bundled yt-dlp,
//!   whose own integrity check (checksum.rs) is unchanged and still runs
//!   first. If the downloaded one cannot be started, lib.rs retries that run
//!   with the bundled one.
//!
//! The webview takes no part: it cannot pass a URL, a version or a hash.

use crate::checksum;
use crate::ytdlp_choice::{self as yc, Active, Offer, Plan};
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

/// Same host as every other app → server call (quota_gate::API_HOST): a
/// domain move is a one-place change.
fn offer_url() -> String {
    format!("https://{}/api/v1/client/ytdlp", crate::quota_gate::API_HOST)
}
const ACTIVE_FILE: &str = "ytdlp-active.json";
pub const CHECK_EVERY: Duration = Duration::from_secs(12 * 3600);
const OFFER_TIMEOUT: Duration = Duration::from_secs(5);
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(10 * 60);
const MAX_OFFER_BODY: u64 = 16 * 1024;
/// yt-dlp.exe is ~18 MB.
const MAX_EXE_BYTES: u64 = 200 * 1024 * 1024;

/// The bundled yt-dlp's version, from the lock file this build was made from.
pub fn bundled_version() -> &'static str {
    static V: OnceLock<String> = OnceLock::new();
    V.get_or_init(|| {
        serde_json::from_str::<serde_json::Value>(include_str!("../../binaries.lock.json"))
            .ok()
            .and_then(|v| v["binaries"]["yt-dlp"]["version"].as_str().map(str::to_string))
            .unwrap_or_default()
    })
}

pub fn read_active(dir: &Path) -> Option<Active> {
    fs::read_to_string(dir.join(ACTIVE_FILE)).ok().and_then(|s| yc::parse_active(&s))
}

fn write_active(dir: &Path, a: &Active) -> io::Result<()> {
    let tmp = dir.join(format!("{ACTIVE_FILE}.tmp"));
    fs::write(&tmp, serde_json::to_vec(a).map_err(io::Error::other)?)?;
    fs::rename(&tmp, dir.join(ACTIVE_FILE))
}

/// Deletes downloaded yt-dlp files other than `keep`. A file still in use by
/// a running download cannot be deleted on Windows: it goes next time.
fn sweep(dir: &Path, keep: Option<&str>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        if name.starts_with("yt-dlp-") && Some(name.as_str()) != keep {
            let _ = fs::remove_file(e.path());
        }
    }
}

/// The downloaded yt-dlp to run, when there is one that is newer than the
/// bundled one and still hashes to the recorded value.
pub fn pick(dir: Option<&Path>) -> Option<(PathBuf, String)> {
    let dir = dir?;
    let active = read_active(dir);
    match yc::choose(bundled_version(), active.as_ref(), |a| {
        checksum::verify_file("yt-dlp (updated)", &dir.join(yc::file_name(&a.version)), &a.sha256).is_ok()
    }) {
        yc::Pick::Downloaded(a) => Some((dir.join(yc::file_name(&a.version)), a.version)),
        yc::Pick::Bundled => None,
    }
}

enum Fetched {
    Offer(Offer),
    /// 204, or a 200 body that is not exactly right.
    NoOffer,
    Unreachable,
}

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(timeout))
        .https_only(true)
        .http_status_as_error(false)
        .build()
        .into()
}

fn fetch_offer(app_version: &str) -> Fetched {
    let resp = agent(OFFER_TIMEOUT)
        .get(&offer_url())
        .header("Accept", "application/json")
        .header("X-VG-Source", "desktop")
        .header("X-VG-Client", app_version)
        .call();
    match resp {
        Ok(r) if r.status().as_u16() == 204 => Fetched::NoOffer,
        Ok(mut r) if r.status().as_u16() == 200 => {
            match r.body_mut().with_config().limit(MAX_OFFER_BODY).read_to_string() {
                Ok(s) => yc::parse_offer(&s).map_or(Fetched::NoOffer, Fetched::Offer),
                Err(_) => Fetched::Unreachable,
            }
        }
        _ => Fetched::Unreachable,
    }
}

/// Copies `src` to `dst` and returns the sha256 of what was written.
fn copy_hashing(mut src: impl Read, dst: &mut File) -> io::Result<String> {
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = src.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        dst.write_all(&buf[..n])?;
    }
    dst.sync_all()?;
    Ok(format!("{:x}", hasher.finalize()))
}

/// Writes `src` to `dir/<file_name(version)>` only if its sha256 is `sha`.
fn install_verified(dir: &Path, offer: &Offer, src: impl Read) -> Result<PathBuf, String> {
    let final_path = dir.join(yc::file_name(&offer.version));
    let part = dir.join(format!("{}.part", yc::file_name(&offer.version)));
    let mut f = File::create(&part).map_err(|e| format!("cannot create {}: {e}", part.display()))?;
    let got = copy_hashing(src, &mut f);
    drop(f);
    let got = got.map_err(|e| {
        let _ = fs::remove_file(&part);
        format!("download failed: {e}")
    })?;
    if !got.eq_ignore_ascii_case(&offer.sha256) {
        let _ = fs::remove_file(&part);
        return Err(format!("sha256 mismatch for yt-dlp {} (expected {}, got {got})", offer.version, offer.sha256));
    }
    let _ = fs::remove_file(&final_path);
    fs::rename(&part, &final_path).map_err(|e| format!("cannot move into place: {e}"))?;
    checksum::verify_file("yt-dlp (updated)", &final_path, &offer.sha256).map_err(|e| e.message)?;
    Ok(final_path)
}

fn download(dir: &Path, offer: &Offer) -> Result<(), String> {
    // Re-checked here: never fetch anything but the official asset.
    if !yc::valid_url(&offer.url, &offer.version) || !yc::valid_sha256(&offer.sha256) {
        return Err("offer rejected".into());
    }
    let resp = agent(DOWNLOAD_TIMEOUT).get(&offer.url).call().map_err(|e| format!("download: {e}"))?;
    if resp.status().as_u16() != 200 {
        return Err(format!("download: HTTP {}", resp.status()));
    }
    let reader = resp.into_body().into_with_config().limit(MAX_EXE_BYTES).reader();
    install_verified(dir, offer, reader)?;
    Ok(())
}

/// One check. Returns true when the yt-dlp in use changed.
pub fn check_once(app_version: &str, dir: &Path) -> bool {
    let offer = match fetch_offer(app_version) {
        Fetched::Unreachable => return false,
        Fetched::NoOffer => None,
        Fetched::Offer(o) => Some(o),
    };
    let active = read_active(dir);
    match yc::plan(bundled_version(), active.as_ref(), offer.as_ref()) {
        Plan::Keep => false,
        Plan::Deactivate => {
            let _ = fs::remove_file(dir.join(ACTIVE_FILE));
            sweep(dir, None);
            true
        }
        Plan::Download(o) => {
            if let Err(e) = fs::create_dir_all(dir) {
                eprintln!("yt-dlp update: {e}");
                return false;
            }
            match download(dir, &o) {
                Ok(()) => {
                    let a = Active { version: o.version.clone(), sha256: o.sha256.clone() };
                    if let Err(e) = write_active(dir, &a) {
                        eprintln!("yt-dlp update: cannot record {}: {e}", a.version);
                        return false;
                    }
                    sweep(dir, Some(&yc::file_name(&a.version)));
                    true
                }
                Err(e) => {
                    // The previous state (bundled, or an older verified download) stays.
                    eprintln!("yt-dlp update: {e}");
                    false
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_dir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("vg-ytdlp-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn offer(sha: &str) -> Offer {
        Offer {
            version: "2099.01.01".into(),
            url: format!("{}2099.01.01/yt-dlp.exe", yc::URL_PREFIX),
            sha256: sha.into(),
        }
    }

    // sha256("abc")
    const ABC: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

    #[test]
    fn bundled_version_comes_from_the_lock_file() {
        assert!(yc::parse_version(bundled_version()).is_some(), "{}", bundled_version());
    }

    #[test]
    fn only_a_matching_download_becomes_active() {
        let d = tmp_dir("install");
        // Wrong hash: nothing installed, no .part left, nothing picked.
        let bad = install_verified(&d, &offer(&"0".repeat(64)), &b"abc"[..]);
        assert!(bad.unwrap_err().contains("sha256 mismatch"));
        assert!(fs::read_dir(&d).unwrap().next().is_none());
        assert_eq!(pick(Some(&d)), None);

        // Right hash: installed; picked once recorded.
        let p = install_verified(&d, &offer(ABC), &b"abc"[..]).unwrap();
        assert_eq!(p, d.join("yt-dlp-2099.01.01.exe"));
        assert_eq!(pick(Some(&d)), None, "not recorded yet");
        write_active(&d, &Active { version: "2099.01.01".into(), sha256: ABC.into() }).unwrap();
        assert_eq!(pick(Some(&d)), Some((p.clone(), "2099.01.01".into())));

        // Changed on disk after it was verified → bundled again.
        fs::write(&p, b"abd").unwrap();
        assert_eq!(pick(Some(&d)), None);
        // Missing file → bundled.
        fs::remove_file(&p).unwrap();
        assert_eq!(pick(Some(&d)), None);
        assert_eq!(pick(None), None);

        // sweep keeps only the named file.
        fs::write(d.join("yt-dlp-2098.01.01.exe"), b"x").unwrap();
        fs::write(d.join("yt-dlp-2099.01.01.exe"), b"y").unwrap();
        sweep(&d, Some("yt-dlp-2099.01.01.exe"));
        assert!(!d.join("yt-dlp-2098.01.01.exe").exists());
        assert!(d.join("yt-dlp-2099.01.01.exe").exists());
        assert!(d.join(ACTIVE_FILE).exists());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn download_refuses_other_urls_before_any_request() {
        let d = tmp_dir("url");
        let mut o = offer(ABC);
        o.url = "https://example.com/yt-dlp.exe".into();
        assert_eq!(download(&d, &o).unwrap_err(), "offer rejected");
        fs::remove_dir_all(&d).unwrap();
    }
}
