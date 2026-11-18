//! Supabase session storage in Windows Credential Manager.
//!
//! Uses the `keyring` crate (v4, `v1` API → keyring-core with the
//! windows-native store). Service name "VidGrab".
//!
//! Credential Manager caps one generic credential's blob at 2560 bytes
//! (CRED_MAX_CREDENTIAL_BLOB_SIZE). A Supabase session (access JWT + refresh
//! token + user object) is often larger, so the JSON is split into chunks:
//! `session.meta` holds "v1:<n>", `session.0` .. `session.<n-1>` hold the
//! bytes. A save writes the chunks first and the meta last, then removes
//! chunks left over from a longer previous session.

pub const SERVICE: &str = "VidGrab";
pub const CHUNK: usize = 2048;
pub const MAX_CHUNKS: usize = 32; // 64 KiB, far above any real session
pub const MAX_SESSION_BYTES: usize = CHUNK * MAX_CHUNKS;

pub fn split(session: &str) -> Result<Vec<&[u8]>, String> {
    let b = session.as_bytes();
    if b.is_empty() {
        return Err("empty session".into());
    }
    if b.len() > MAX_SESSION_BYTES {
        return Err(format!("session too large ({} bytes)", b.len()));
    }
    Ok(b.chunks(CHUNK).collect())
}

pub fn parse_meta(meta: &[u8]) -> Option<usize> {
    std::str::from_utf8(meta).ok()?.strip_prefix("v1:")?.parse().ok().filter(|n| *n >= 1 && *n <= MAX_CHUNKS)
}

/// The few operations the chunking needs; implemented by Credential Manager
/// on Windows and by an in-memory map in tests.
pub trait SecretStore {
    fn get(&self, user: &str) -> Result<Option<Vec<u8>>, String>;
    fn set(&self, user: &str, secret: &[u8]) -> Result<(), String>;
    /// Ok(false) when there was nothing to delete.
    fn delete(&self, user: &str) -> Result<bool, String>;
}

const META: &str = "session.meta";

fn chunk_name(i: usize) -> String {
    format!("session.{i}")
}

pub fn save_with(store: &impl SecretStore, session: &str) -> Result<(), String> {
    let chunks = split(session)?;
    for (i, c) in chunks.iter().enumerate() {
        store.set(&chunk_name(i), c)?;
    }
    store.set(META, format!("v1:{}", chunks.len()).as_bytes())?;
    // Stale chunks from a longer previous session.
    for i in chunks.len()..MAX_CHUNKS {
        store.delete(&chunk_name(i))?;
    }
    Ok(())
}

pub fn load_with(store: &impl SecretStore) -> Result<Option<String>, String> {
    let Some(meta) = store.get(META)? else { return Ok(None) };
    let Some(n) = parse_meta(&meta) else { return Ok(None) };
    let mut buf = Vec::new();
    for i in 0..n {
        match store.get(&chunk_name(i))? {
            Some(c) => buf.extend_from_slice(&c),
            // Half-written or partly deleted: treat as signed out.
            None => return Ok(None),
        }
    }
    Ok(String::from_utf8(buf).ok())
}

pub fn clear_with(store: &impl SecretStore) -> Result<(), String> {
    let mut first_err = None;
    for user in std::iter::once(META.to_string()).chain((0..MAX_CHUNKS).map(chunk_name)) {
        if let Err(e) = store.delete(&user) {
            first_err.get_or_insert(e);
        }
    }
    first_err.map_or(Ok(()), Err)
}

#[cfg(windows)]
mod store {
    use super::*;
    use keyring::{Entry, Error};

    pub struct CredentialManager;

    fn entry(user: &str) -> Result<Entry, String> {
        Entry::new(SERVICE, user).map_err(|e| format!("credential store unavailable: {e}"))
    }

    impl SecretStore for CredentialManager {
        fn get(&self, user: &str) -> Result<Option<Vec<u8>>, String> {
            match entry(user)?.get_secret() {
                Ok(v) => Ok(Some(v)),
                Err(Error::NoEntry) => Ok(None),
                Err(e) => Err(format!("cannot read session: {e}")),
            }
        }
        fn set(&self, user: &str, secret: &[u8]) -> Result<(), String> {
            entry(user)?.set_secret(secret).map_err(|e| format!("cannot save session: {e}"))
        }
        fn delete(&self, user: &str) -> Result<bool, String> {
            match entry(user)?.delete_credential() {
                Ok(()) => Ok(true),
                Err(Error::NoEntry) => Ok(false),
                Err(e) => Err(format!("cannot delete session part: {e}")),
            }
        }
    }

    pub fn save(session: &str) -> Result<(), String> {
        save_with(&CredentialManager, session)
    }
    pub fn load() -> Result<Option<String>, String> {
        load_with(&CredentialManager)
    }
    pub fn clear() -> Result<(), String> {
        clear_with(&CredentialManager)
    }
}

#[cfg(not(windows))]
mod store {
    //! File-backed 0600 SecretStore for macOS and Linux desktop builds.
    use super::*;
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
    use std::path::PathBuf;

    const IDENTIFIER: &str = "xyz.tinhgon.vidgrab";

    fn auth_dir() -> Result<PathBuf, String> {
        #[cfg(target_os = "macos")]
        let base = std::env::var_os("HOME")
            .map(|h| PathBuf::from(h).join("Library").join("Application Support"))
            .ok_or_else(|| "HOME is not set".to_string())?;
        #[cfg(not(target_os = "macos"))]
        let base = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("share")))
            .ok_or_else(|| "cannot locate user data directory".to_string())?;
        Ok(base.join(IDENTIFIER).join("auth"))
    }

    pub struct FileSecretStore {
        dir: PathBuf,
    }

    impl FileSecretStore {
        pub fn new(dir: PathBuf) -> Self {
            Self { dir }
        }

        fn key_path(&self, user: &str) -> Result<PathBuf, String> {
            if user.is_empty()
                || user == "."
                || user == ".."
                || !user.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
            {
                return Err("invalid session key".into());
            }
            Ok(self.dir.join(user))
        }
    }

    impl SecretStore for FileSecretStore {
        fn get(&self, user: &str) -> Result<Option<Vec<u8>>, String> {
            let p = self.key_path(user)?;
            match std::fs::read(&p) {
                Ok(b) => Ok(Some(b)),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
                Err(e) => Err(format!("cannot read session: {e}")),
            }
        }

        fn set(&self, user: &str, secret: &[u8]) -> Result<(), String> {
            let p = self.key_path(user)?;
            std::fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&self.dir)
                .map_err(|e| format!("cannot create auth folder: {e}"))?;
            let tmp = self.dir.join(format!("{user}.tmp-{}", std::process::id()));
            {
                use std::io::Write;
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .truncate(true)
                    .write(true)
                    .mode(0o600)
                    .open(&tmp)
                    .map_err(|e| format!("cannot save session: {e}"))?;
                f.write_all(secret).map_err(|e| format!("cannot save session: {e}"))?;
            }
            std::fs::rename(&tmp, &p).map_err(|e| format!("cannot save session: {e}"))
        }

        fn delete(&self, user: &str) -> Result<bool, String> {
            let p = self.key_path(user)?;
            match std::fs::remove_file(&p) {
                Ok(()) => Ok(true),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
                Err(e) => Err(format!("cannot delete session part: {e}")),
            }
        }
    }

    pub fn save(session: &str) -> Result<(), String> {
        let store = FileSecretStore::new(auth_dir()?);
        save_with(&store, session)
    }

    pub fn load() -> Result<Option<String>, String> {
        let store = FileSecretStore::new(auth_dir()?);
        load_with(&store)
    }

    pub fn clear() -> Result<(), String> {
        let store = FileSecretStore::new(auth_dir()?);
        clear_with(&store)
    }
}

pub use store::{clear, load, save};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunking_round_trip_and_limits() {
        let s = "é".repeat(3000); // 6000 bytes, multi-byte chars split across chunks
        let parts = split(&s).unwrap();
        assert_eq!(parts.len(), 3);
        assert!(parts.iter().all(|p| p.len() <= CHUNK && p.len() <= 2560));
        let joined: Vec<u8> = parts.concat();
        assert_eq!(String::from_utf8(joined).unwrap(), s);
        assert!(split("").is_err());
        assert!(split(&"a".repeat(MAX_SESSION_BYTES + 1)).is_err());
        assert_eq!(split(&"a".repeat(MAX_SESSION_BYTES)).unwrap().len(), MAX_CHUNKS);
    }

    use std::cell::RefCell;
    use std::collections::HashMap;

    /// In-memory stand-in for Credential Manager that enforces its 2560-byte
    /// blob limit.
    #[derive(Default)]
    struct Mock(RefCell<HashMap<String, Vec<u8>>>);
    impl SecretStore for Mock {
        fn get(&self, u: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.0.borrow().get(u).cloned())
        }
        fn set(&self, u: &str, s: &[u8]) -> Result<(), String> {
            if s.len() > 2560 {
                return Err("TooLong".into());
            }
            self.0.borrow_mut().insert(u.into(), s.to_vec());
            Ok(())
        }
        fn delete(&self, u: &str) -> Result<bool, String> {
            Ok(self.0.borrow_mut().remove(u).is_some())
        }
    }

    #[test]
    fn store_round_trip_stale_chunks_and_clear() {
        let m = Mock::default();
        assert_eq!(load_with(&m).unwrap(), None);
        // A realistic large session: ~7 KB, more than two Credential Manager blobs.
        let big = format!("{{\"access_token\":\"{}\",\"user\":{{\"name\":\"Nguyễn\"}}}}", "x".repeat(7000));
        save_with(&m, &big).unwrap();
        assert_eq!(m.0.borrow().len(), 1 + big.len().div_ceil(CHUNK));
        assert_eq!(load_with(&m).unwrap().as_deref(), Some(big.as_str()));
        // A shorter session removes the stale chunks.
        save_with(&m, "{\"a\":1}").unwrap();
        assert_eq!(m.0.borrow().len(), 2);
        assert_eq!(load_with(&m).unwrap().as_deref(), Some("{\"a\":1}"));
        // A missing chunk reads as signed out, not as a truncated session.
        save_with(&m, &big).unwrap();
        m.0.borrow_mut().remove("session.1");
        assert_eq!(load_with(&m).unwrap(), None);
        clear_with(&m).unwrap();
        assert!(m.0.borrow().is_empty());
        assert_eq!(load_with(&m).unwrap(), None);
    }

    #[test]
    fn meta_parsing() {
        assert_eq!(parse_meta(b"v1:3"), Some(3));
        assert_eq!(parse_meta(b"v1:0"), None);
        assert_eq!(parse_meta(b"v1:999"), None);
        assert_eq!(parse_meta(b"v2:3"), None);
        assert_eq!(parse_meta(&[0xff, 0xfe]), None);
    }

    #[cfg(not(windows))]
    #[test]
    fn file_store_round_trip() {
        let dir = std::env::temp_dir().join(format!("vg-auth-file-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let s = store::FileSecretStore::new(dir.clone());
        assert_eq!(load_with(&s).unwrap(), None);
        let payload = format!("{{\"access_token\":\"{}\"}}", "a".repeat(3000));
        save_with(&s, &payload).unwrap();
        assert_eq!(load_with(&s).unwrap().as_deref(), Some(payload.as_str()));
        clear_with(&s).unwrap();
        assert_eq!(load_with(&s).unwrap(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
