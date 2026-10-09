use std::{env, fs, path::PathBuf};

/// Sidecars the app checks at runtime. Must match bundle.externalBin.
const SIDECARS: &[&str] = &["yt-dlp", "ffmpeg", "ffprobe", "deno"];

/// Our own commands. Declaring them here makes tauri-build generate
/// `allow-<command>` permissions, so a command is callable from the webview
/// only if capabilities/default.json grants it.
const COMMANDS: &[&str] = &[
    "probe",
    "cancel_probe",
    "start_download",
    "pause_download",
    "cancel_download",
    "pick_folder",
    "default_download_dir",
    "disk_free",
    "reveal_path",
    "open_path",
    "open_url",
    "history_list",
    "history_add",
    "history_delete",
    "history_clear",
    "history_mark_synced",
    "auth_save",
    "auth_load",
    "auth_clear",
    "browser_login",
    "cancel_browser_login",
    "get_version",
    "device_info",
    "tool_versions",
    "channel_fetch",
    "cancel_channel_fetch",
    "channel_save",
    "channel_list",
    "channel_delete",
    "channel_seen_add",
    "channel_seen_list",
    "notify",
    "autostart_get",
    "autostart_set",
    "set_close_to_tray",
    "cookies_login_open",
    "cookies_login_finish",
    "cookies_status",
    "cookies_clear",
    "douyin_resolve_local",
    "update_check",
    "update_install",
];

fn main() {
    generate_pins();
    generate_claim_keys();
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("tauri-build failed");
}

/// Reads ../binaries.lock.json and writes $OUT_DIR/pins.rs with the expected
/// SHA256 of each sidecar for the target being compiled. A pin that is still
/// REPLACE_ME (or missing) becomes `None`, and the app then refuses to run that
/// sidecar instead of trusting an unverified file.
fn generate_pins() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let lock_path = manifest_dir.join("..").join("binaries.lock.json");
    println!("cargo:rerun-if-changed={}", lock_path.display());

    let target = env::var("TARGET").unwrap();
    let lock: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(&lock_path).expect("cannot read binaries.lock.json"),
    )
    .expect("binaries.lock.json is not valid JSON");

    let mut out = String::from("pub const PINS: &[(&str, Option<&str>)] = &[\n");
    for name in SIDECARS {
        let pin = lock["binaries"][name]["targets"][&target]["extractedSha256"]
            .as_str()
            .filter(|h| h.len() == 64 && h.chars().all(|c| c.is_ascii_hexdigit()))
            .map(|h| h.to_ascii_lowercase());
        match &pin {
            Some(h) => out.push_str(&format!("    ({name:?}, Some({h:?})),\n")),
            None => {
                println!("cargo:warning=no SHA256 pin for {name} on {target}; the app will refuse to run it");
                out.push_str(&format!("    ({name:?}, None),\n"));
            }
        }
    }
    out.push_str("];\n");
    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap()).join("pins.rs");
    fs::write(out_path, out).unwrap();
}

const CLAIM_KEYS_ENV: &str = "VIDGRAB_CLAIM_PUBKEYS";
const MAX_CLAIM_KEYS: usize = 3;

/// PLAN-32E P3 (task #6172): the Ed25519 public key(s) the app trusts for
/// claim tokens and the quota policy, embedded as $OUT_DIR/claim_keys.rs.
///
/// VIDGRAB_CLAIM_PUBKEYS = "<kid>:<base64 32-byte public key>[,<kid>:<key>]"
/// (at most 3: current + next during a rotation, docs/desktop/P3-KEY-ROTATION.md).
///
/// Failure policy:
/// - a malformed value always stops the build (a typo must never ship as
///   "no keys");
/// - unset or empty: a RELEASE build stops with an explanation; debug /
///   `cargo test` builds get an empty list and a warning;
/// - "none": an explicit empty list in any profile (that app never asks for
///   a token and grants no offline slots itself, i.e. behaves like 0.9).
fn generate_claim_keys() {
    use base64::Engine as _;
    println!("cargo:rerun-if-env-changed={CLAIM_KEYS_ENV}");
    let raw = env::var(CLAIM_KEYS_ENV).unwrap_or_default();
    let raw = raw.trim();
    let release = env::var("PROFILE").map(|p| p == "release").unwrap_or(false);
    let mut keys: Vec<(String, [u8; 32])> = Vec::new();
    if raw.is_empty() {
        if release {
            panic!(
                "\n\n{CLAIM_KEYS_ENV} is not set. A release build must embed the claim-token public key(s):\n  \
                 {CLAIM_KEYS_ENV}=k1:<base64 public key>   (printed by backend/scripts/gen_claim_signing_key.py)\n\
                 Set {CLAIM_KEYS_ENV}=none to build an app that never checks claim tokens (behaves like 0.9).\n\
                 See docs/desktop/P3-KEY-ROTATION.md.\n"
            );
        }
        println!("cargo:warning={CLAIM_KEYS_ENV} not set: this debug build checks no claim tokens");
    } else if !raw.eq_ignore_ascii_case("none") {
        for part in raw.split(',').map(str::trim).filter(|p| !p.is_empty()) {
            let (kid, b64) = part
                .split_once(':')
                .unwrap_or_else(|| panic!("{CLAIM_KEYS_ENV}: entry {part:?} is not <kid>:<base64 key>"));
            let kid = kid.trim();
            if kid.is_empty() || kid.len() > 16 || !kid.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
                panic!("{CLAIM_KEYS_ENV}: kid {kid:?} must be 1-16 chars of A-Z a-z 0-9 _ -");
            }
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(b64.trim())
                .unwrap_or_else(|e| panic!("{CLAIM_KEYS_ENV}: key {kid:?} is not standard base64: {e}"));
            let key: [u8; 32] = bytes
                .try_into()
                .unwrap_or_else(|b: Vec<u8>| panic!("{CLAIM_KEYS_ENV}: key {kid:?} is {} bytes, expected 32", b.len()));
            if keys.iter().any(|(k, _)| k == kid) {
                panic!("{CLAIM_KEYS_ENV}: kid {kid:?} listed twice");
            }
            keys.push((kid.to_string(), key));
        }
        if keys.is_empty() || keys.len() > MAX_CLAIM_KEYS {
            panic!("{CLAIM_KEYS_ENV}: expected 1 to {MAX_CLAIM_KEYS} keys, got {}", keys.len());
        }
    }
    let mut out = String::from("pub const CLAIM_KEYS: &[(&str, [u8; 32])] = &[\n");
    for (kid, key) in &keys {
        out.push_str(&format!("    ({kid:?}, {key:?}),\n"));
    }
    out.push_str("];\n");
    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap()).join("claim_keys.rs");
    fs::write(out_path, out).unwrap();
}
