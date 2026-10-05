//! Runtime integrity check for the bundled sidecars.
//!
//! build.rs compiles the expected SHA256 of every sidecar (from
//! binaries.lock.json) into PINS. Before each download we hash the files that
//! are actually on disk and refuse to start if any of them differs, so a
//! swapped or tampered yt-dlp/ffmpeg/deno is never executed.
//!
//! Known C0 limit: there is a small window between hashing and spawning
//! (time-of-check/time-of-use). Closing it needs the files to live in a
//! location the user cannot write, which per-user installs do not give us.

use sha2::{Digest, Sha256};
use std::{fs::File, io, path::PathBuf};

include!(concat!(env!("OUT_DIR"), "/pins.rs"));

/// Tauri copies each externalBin next to the main executable, without the
/// target-triple suffix (e.g. `yt-dlp.exe`), both for `tauri dev` and in the
/// installed app.
pub fn sidecar_path(name: &str) -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("cannot locate app executable: {e}"))?;
    let dir = exe.parent().ok_or("app executable has no parent directory")?;
    Ok(dir.join(format!("{name}{}", std::env::consts::EXE_SUFFIX)))
}

/// Hashes the sidecar on disk and compares it with the compiled-in pin.
/// Returns the verified path, or an error that is safe to show to the user.
pub fn verify(name: &str) -> Result<PathBuf, String> {
    let expected = PINS
        .iter()
        .find(|(n, _)| *n == name)
        .and_then(|(_, h)| *h)
        .ok_or_else(|| format!("{name}: no pinned checksum in this build; refusing to run it"))?;

    let path = sidecar_path(name)?;
    let mut file = File::open(&path).map_err(|e| format!("{name}: cannot open {}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    io::copy(&mut file, &mut hasher).map_err(|e| format!("{name}: cannot read: {e}"))?;
    let actual = format!("{:x}", hasher.finalize());

    if actual != expected {
        return Err(format!(
            "{name} failed the integrity check (expected {expected}, found {actual}); \
             refusing to run it. Please reinstall VidGrab."
        ));
    }
    Ok(path)
}
