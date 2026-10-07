//! Machine code ("Mã máy", PLAN-32D §4): a stable per-PC identifier the server
//! uses to count the guest allowance, and that the user can read to support.
//!
//! hash = lowercase hex SHA-256("vidgrab-device-v1|" + MachineGuid).
//! The raw GUID never leaves the PC. When the registry cannot be read (and on
//! non-Windows dev builds) 32 random bytes are generated ONCE and kept in
//! `<app data dir>/device.id`; that id is reported with source "fallback".

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::OnceLock;

const SALT: &str = "vidgrab-device-v1|";

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DeviceInfo {
    /// 64 lowercase hex.
    pub hash: String,
    /// First 8 hex, uppercase.
    pub code: String,
    /// ASCII only, at most 120 chars ("PC-213 (Windows 11 24H2, build 26100)").
    pub display_name: String,
    /// "machine" | "fallback"
    pub source: &'static str,
}

pub fn hash_guid(guid: &str) -> String {
    let mut h = Sha256::new();
    h.update(SALT.as_bytes());
    h.update(guid.trim().as_bytes());
    to_hex(&h.finalize())
}

pub fn code_of(hash: &str) -> String {
    hash.chars().take(8).collect::<String>().to_ascii_uppercase()
}

fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f'))
}

/// Reads `<dir>/device.id`, or creates it with 32 random bytes (hex). The same
/// value is returned on every later call. Returns the 64-hex fallback hash.
pub fn fallback_hash(dir: &Path) -> Result<String, String> {
    let path = dir.join("device.id");
    if let Ok(s) = std::fs::read_to_string(&path) {
        let s = s.trim().to_ascii_lowercase();
        if is_hex64(&s) {
            return Ok(s);
        }
    }
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).map_err(|e| format!("no secure random source: {e}"))?;
    let id = to_hex(&b);
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    std::fs::write(&path, &id).map_err(|e| e.to_string())?;
    Ok(id)
}

/// Keeps printable ASCII only (control characters and non-ASCII become `?`),
/// trimmed, at most 120 chars; the value travels in an HTTP header.
pub fn ascii_name(raw: &str) -> String {
    let s: String = raw
        .chars()
        .map(|c| if c.is_ascii() && !c.is_ascii_control() { c } else { '?' })
        .collect();
    let s = s.trim();
    s.chars().take(120).collect()
}

pub fn display_name(computer: Option<&str>, os: Option<&str>) -> String {
    let pc = computer.map(str::trim).filter(|s| !s.is_empty()).unwrap_or("PC");
    let os = os.map(str::trim).filter(|s| !s.is_empty()).unwrap_or("Windows");
    ascii_name(&format!("{pc} ({os})"))
}

#[cfg(windows)]
mod reg {
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY, REG_SZ,
    };

    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// HKLM string value, 64-bit registry view. None on any failure.
    pub fn hklm_string(subkey: &str, name: &str) -> Option<String> {
        unsafe {
            let mut key: HKEY = std::ptr::null_mut();
            let sub = wide(subkey);
            if RegOpenKeyExW(HKEY_LOCAL_MACHINE, sub.as_ptr(), 0, KEY_READ | KEY_WOW64_64KEY, &mut key) != 0 {
                return None;
            }
            let n = wide(name);
            let mut ty: u32 = 0;
            let mut buf = [0u16; 256];
            let mut size: u32 = (buf.len() * 2) as u32;
            let rc = RegQueryValueExW(key, n.as_ptr(), std::ptr::null(), &mut ty, buf.as_mut_ptr() as *mut u8, &mut size);
            RegCloseKey(key);
            if rc != 0 || ty != REG_SZ {
                return None;
            }
            let len = ((size as usize) / 2).min(buf.len());
            let s = String::from_utf16_lossy(&buf[..len]);
            let s = s.trim_end_matches('\0').trim().to_string();
            if s.is_empty() { None } else { Some(s) }
        }
    }
}

#[cfg(windows)]
fn machine_guid() -> Option<String> {
    reg::hklm_string(r"SOFTWARE\Microsoft\Cryptography", "MachineGuid")
}
#[cfg(not(windows))]
fn machine_guid() -> Option<String> {
    None
}

#[cfg(windows)]
fn os_label() -> Option<String> {
    let k = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion";
    let build = reg::hklm_string(k, "CurrentBuildNumber")?;
    let n: u32 = build.parse().ok()?;
    let family = if n >= 22000 { "Windows 11" } else { "Windows 10" };
    Some(match reg::hklm_string(k, "DisplayVersion") {
        Some(v) => format!("{family} {v}, build {build}"),
        None => format!("{family}, build {build}"),
    })
}
#[cfg(not(windows))]
fn os_label() -> Option<String> {
    Some(std::env::consts::OS.to_string())
}

static INFO: OnceLock<DeviceInfo> = OnceLock::new();

/// Cached for the life of the process. `app_data` is only used by the fallback.
pub fn device_info(app_data: Option<&Path>) -> DeviceInfo {
    INFO.get_or_init(|| build(machine_guid(), app_data)).clone()
}

fn build(guid: Option<String>, app_data: Option<&Path>) -> DeviceInfo {
    let computer = std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")).ok();
    let name = display_name(computer.as_deref(), os_label().as_deref());
    if let Some(g) = guid {
        let hash = hash_guid(&g);
        return DeviceInfo { code: code_of(&hash), hash, display_name: name, source: "machine" };
    }
    // Fallback: random id kept on disk. If even that fails, an in-memory id
    // for this run only (never a constant: that would merge all such PCs).
    let hash = app_data.and_then(|d| fallback_hash(d).ok()).unwrap_or_else(|| {
        let mut b = [0u8; 32];
        let _ = getrandom::fill(&mut b);
        to_hex(&b)
    });
    DeviceInfo { code: code_of(&hash), hash, display_name: name, source: "fallback" }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_deterministic_and_salted() {
        let a = hash_guid("4c4c4544-0042-3510-8051-b8c04f4b4e32");
        assert_eq!(a, hash_guid("4c4c4544-0042-3510-8051-b8c04f4b4e32"));
        assert_eq!(a, hash_guid(" 4c4c4544-0042-3510-8051-b8c04f4b4e32\n"));
        assert!(is_hex64(&a));
        assert_ne!(a, hash_guid("4c4c4544-0042-3510-8051-b8c04f4b4e33"));
        // Known answer: SHA-256("vidgrab-device-v1|abc")
        let mut h = Sha256::new();
        h.update(b"vidgrab-device-v1|abc");
        assert_eq!(hash_guid("abc"), to_hex(&h.finalize()));
    }

    #[test]
    fn code_is_8_upper_hex() {
        let c = code_of(&hash_guid("x"));
        assert_eq!(c.len(), 8);
        assert!(c.chars().all(|c| matches!(c, '0'..='9' | 'A'..='F')));
        assert_eq!(code_of("abcdef0123456789"), "ABCDEF01");
    }

    #[test]
    fn fallback_is_persisted_and_reused() {
        let dir = std::env::temp_dir().join(format!("vg-device-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let a = fallback_hash(&dir).unwrap();
        assert!(is_hex64(&a));
        assert_eq!(a, fallback_hash(&dir).unwrap());
        assert_eq!(a, std::fs::read_to_string(dir.join("device.id")).unwrap());
        // A damaged file is replaced by a fresh valid id.
        std::fs::write(dir.join("device.id"), "garbage").unwrap();
        let b = fallback_hash(&dir).unwrap();
        assert!(is_hex64(&b) && b != "garbage");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn build_marks_source() {
        let m = build(Some("guid-1".into()), None);
        assert_eq!(m.source, "machine");
        assert_eq!(m.hash, hash_guid("guid-1"));
        assert_eq!(m.code, code_of(&m.hash));
        let dir = std::env::temp_dir().join(format!("vg-device-test2-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let f = build(None, Some(&dir));
        assert_eq!(f.source, "fallback");
        assert_eq!(f.hash, build(None, Some(&dir)).hash);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn display_name_is_ascii_and_bounded() {
        assert_eq!(display_name(Some("PC-213"), Some("Windows 11 24H2, build 26100")), "PC-213 (Windows 11 24H2, build 26100)");
        let n = display_name(Some("MÁY-Triều\r\n"), Some("Windows"));
        assert!(n.is_ascii() && !n.chars().any(|c| c.is_control()));
        assert!(display_name(Some(&"a".repeat(500)), None).len() <= 120);
        assert_eq!(display_name(None, None), "PC (Windows)");
    }
}
