//! Registers this app as the VidGrab extension's native messaging host
//! (app 0.20.0). Runs at every start, per user, no admin:
//!
//! * `%LOCALAPPDATA%\xyz.tinhgon.vidgrab\native-host\`
//!   - `token`: 32 random bytes (hex), new each start; the host must send it
//!     back (ext_bridge.rs `/vidgrab/native`). Anything running as this user
//!     can read it — the user account is the trust boundary, as for the
//!     saved cookies;
//!   - `vidgrab-host-<version>.exe`: a copy of this exe. The browser keeps its
//!     host running, and a running VidGrab.exe would block the updater from
//!     replacing it; older copies are removed once no browser runs them;
//!   - `xyz.tinhgon.vidgrab.json`: the host manifest (ALLOWED_IDS only).
//! * `HKCU\Software\<browser>\NativeMessagingHosts\xyz.tinhgon.vidgrab` =
//!   the manifest path, for Chrome, Chromium, Edge and Brave.

use std::path::{Path, PathBuf};

pub const HOST_NAME: &str = "xyz.tinhgon.vidgrab";
pub const TOKEN_FILE: &str = "token";
const IDENTIFIER: &str = "xyz.tinhgon.vidgrab";

/// Extension ids allowed to start the host. The website build pins its id
/// with the manifest "key" (chrome-extension/manifest.json, 5.22.0); add the
/// Chrome Web Store item's id here once it is published.
pub const ALLOWED_IDS: [&str; 1] = ["gkoepflfmpdmkhmccekmajlfdnfklmgi"];

/// Same folder as Tauri's app_local_data_dir, computed without Tauri (the
/// host process has none).
pub fn native_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from);
    #[cfg(not(windows))]
    let base = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")));
    Some(base?.join(IDENTIFIER).join("native-host"))
}

pub fn host_manifest(exe: &Path) -> String {
    let origins: Vec<String> = ALLOWED_IDS.iter().map(|id| format!("chrome-extension://{id}/")).collect();
    serde_json::to_string_pretty(&serde_json::json!({
        "name": HOST_NAME,
        "description": "VidGrab app link for the VidGrab extension",
        "path": exe.to_string_lossy(),
        "type": "stdio",
        "allowed_origins": origins,
    }))
    .unwrap_or_default()
}

pub fn new_token() -> std::io::Result<String> {
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).map_err(|e| std::io::Error::other(e.to_string()))?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

fn host_exe_name(version: &str) -> String {
    let v: String = version.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-').collect();
    format!("vidgrab-host-{v}.exe")
}

/// Copies `exe` into `dir` as this version's host (once), removes copies of
/// other versions that no browser is running (delete fails while running).
pub fn install_host_exe(dir: &Path, exe: &Path, version: &str) -> std::io::Result<PathBuf> {
    let name = host_exe_name(version);
    let target = dir.join(&name);
    let same = std::fs::metadata(&target).ok().map(|m| m.len()) == std::fs::metadata(exe).ok().map(|m| m.len());
    if !same {
        let tmp = dir.join(format!("{name}.part"));
        std::fs::copy(exe, &tmp)?;
        std::fs::rename(&tmp, &target)?;
    }
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.starts_with("vidgrab-host-") && n != name {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    Ok(target)
}

/// Writes token, host copy and manifest; returns the token.
pub fn prepare(dir: &Path, exe: &Path, version: &str) -> std::io::Result<(String, PathBuf)> {
    std::fs::create_dir_all(dir)?;
    let token = new_token()?;
    let tmp = dir.join(format!("{TOKEN_FILE}.part"));
    std::fs::write(&tmp, &token)?;
    std::fs::rename(&tmp, dir.join(TOKEN_FILE))?;
    let host = install_host_exe(dir, exe, version)?;
    let manifest = dir.join(format!("{HOST_NAME}.json"));
    std::fs::write(&manifest, host_manifest(&host))?;
    Ok((token, manifest))
}

/// Registry roots Chromium browsers read native hosts from (HKCU).
pub const BROWSER_KEYS: [&str; 4] =
    [r"Software\Google\Chrome", r"Software\Chromium", r"Software\Microsoft\Edge", r"Software\BraveSoftware\Brave-Browser"];

/// Everything at app start; returns the token for ext_bridge.
pub fn register(version: &str) -> std::io::Result<String> {
    let dir = native_dir().ok_or_else(|| std::io::Error::other("no local app data folder"))?;
    let exe = std::env::current_exe()?;
    let (token, manifest) = prepare(&dir, &exe, version)?;
    #[cfg(windows)]
    for root in BROWSER_KEYS {
        if let Err(e) = set_default_value(&format!(r"{root}\NativeMessagingHosts\{HOST_NAME}"), &manifest) {
            eprintln!("native host not registered for {root}: {e}");
        }
    }
    #[cfg(not(windows))]
    let _ = manifest;
    Ok(token)
}

#[cfg(windows)]
fn set_default_value(subkey: &str, value: &Path) -> std::io::Result<()> {
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegCreateKeyExW, RegSetValueExW, HKEY, HKEY_CURRENT_USER, KEY_SET_VALUE, REG_OPTION_NON_VOLATILE,
        REG_SZ,
    };
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    let key = wide(subkey);
    let data = wide(&value.to_string_lossy());
    unsafe {
        let mut h: HKEY = std::ptr::null_mut();
        let r = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            0,
            std::ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE,
            std::ptr::null(),
            &mut h,
            std::ptr::null_mut(),
        );
        if r != 0 {
            return Err(std::io::Error::from_raw_os_error(r as i32));
        }
        let r = RegSetValueExW(h, std::ptr::null(), 0, REG_SZ, data.as_ptr() as *const u8, (data.len() * 2) as u32);
        RegCloseKey(h);
        if r != 0 {
            return Err(std::io::Error::from_raw_os_error(r as i32));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("vg-native-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn manifest_names_only_our_extension() {
        let m: serde_json::Value = serde_json::from_str(&host_manifest(Path::new("/x/vidgrab-host-0.20.0.exe"))).unwrap();
        assert_eq!(m["name"], HOST_NAME);
        assert_eq!(m["type"], "stdio");
        assert_eq!(m["allowed_origins"], serde_json::json!(["chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi/"]));
        // Chrome: lowercase alphanumerics, '_' and '.' only
        assert!(HOST_NAME.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '.' || c == '_'));
    }

    #[test]
    fn pinned_id_matches_the_manifest_key() {
        use base64::Engine;
        use sha2::Digest;
        let manifest: serde_json::Value =
            serde_json::from_str(include_str!("../../../chrome-extension/manifest.json")).unwrap();
        let der = base64::engine::general_purpose::STANDARD.decode(manifest["key"].as_str().unwrap()).unwrap();
        let id: String =
            sha2::Sha256::digest(&der)[..16].iter().flat_map(|b| [b >> 4, b & 15]).map(|n| (b'a' + n) as char).collect();
        assert_eq!(id, ALLOWED_IDS[0]);
    }

    #[test]
    fn prepare_writes_token_copy_and_manifest() {
        let d = tmp("prep");
        let exe = d.join("VidGrab.exe");
        std::fs::write(&exe, b"exe v1").unwrap();
        std::fs::write(d.join("vidgrab-host-0.19.9.exe"), b"old").unwrap();
        let (t1, manifest) = prepare(&d, &exe, "0.20.0").unwrap();
        assert_eq!(crate::native_host::read_token(&d).as_deref(), Some(t1.as_str()));
        assert_eq!(std::fs::read(d.join("vidgrab-host-0.20.0.exe")).unwrap(), b"exe v1");
        assert!(!d.join("vidgrab-host-0.19.9.exe").exists(), "old copy removed");
        let m: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(manifest).unwrap()).unwrap();
        assert!(m["path"].as_str().unwrap().ends_with("vidgrab-host-0.20.0.exe"));
        let (t2, _) = prepare(&d, &exe, "0.20.0").unwrap();
        assert_ne!(t1, t2, "new token each start");
    }
}
