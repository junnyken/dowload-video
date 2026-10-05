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
use crate::error::{Code, CommandError};
use std::{
    fs::File,
    io,
    path::{Path, PathBuf},
};

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
/// Returns the verified path, or `tool_missing` (no pin / file absent or
/// unreadable) / `tool_tampered` (hash differs).
pub fn verify(name: &str) -> Result<PathBuf, CommandError> {
    let expected = PINS
        .iter()
        .find(|(n, _)| *n == name)
        .and_then(|(_, h)| *h)
        .ok_or_else(|| {
            CommandError::new(Code::ToolMissing, format!("{name}: no pinned checksum in this build; refusing to run it"))
        })?;
    let path = sidecar_path(name).map_err(|e| CommandError::new(Code::ToolMissing, e))?;
    verify_file(name, &path, expected)?;
    Ok(path)
}

pub fn verify_file(name: &str, path: &Path, expected: &str) -> Result<(), CommandError> {
    let mut file = File::open(path)
        .map_err(|e| CommandError::new(Code::ToolMissing, format!("{name}: cannot open {}: {e}", path.display())))?;
    let mut hasher = Sha256::new();
    io::copy(&mut file, &mut hasher).map_err(|e| CommandError::new(Code::ToolMissing, format!("{name}: cannot read: {e}")))?;
    let actual = format!("{:x}", hasher.finalize());
    if !actual.eq_ignore_ascii_case(expected) {
        return Err(CommandError::new(
            Code::ToolTampered,
            format!(
                "{name} failed the integrity check (expected {expected}, found {actual}); \
                 refusing to run it. Please reinstall VidGrab."
            ),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_tampering_and_missing() {
        let p = std::env::temp_dir().join(format!("vg-sum-{}", std::process::id()));
        std::fs::write(&p, b"abc").unwrap();
        // sha256("abc")
        let good = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        assert!(verify_file("t", &p, good).is_ok());
        std::fs::write(&p, b"abd").unwrap();
        assert_eq!(verify_file("t", &p, good).unwrap_err().code, Code::ToolTampered);
        std::fs::remove_file(&p).unwrap();
        assert_eq!(verify_file("t", &p, good).unwrap_err().code, Code::ToolMissing);
    }
}
