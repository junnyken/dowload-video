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
];

fn main() {
    generate_pins();
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
