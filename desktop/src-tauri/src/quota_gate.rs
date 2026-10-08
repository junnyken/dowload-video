//! Rust-side check of the daily allowance before yt-dlp starts (PLAN-32E §5.2 (a),
//! P3, task #6172).
//!
//! The UI claims a download from the server (quota.ts) and passes the signed
//! token it got to `start_download`. Here, BEFORE anything is spawned:
//!
//! 1. no public key embedded (build.rs) → gate off, as in 0.9;
//! 2. a file from our own API host (`/api/v1/download-local`, the server route
//!    the server already counted) → allowed;
//! 3. a token whose signature, expiry, url hash and machine are right, and
//!    whose claim id no OTHER job used this session → allowed;
//! 4. otherwise the signed policy decides. Rust reads it itself
//!    (GET /client/version, 3 s, rustls + bundled Mozilla roots, so a CA the
//!    user installed for a fake server is not trusted) and caches it in
//!    `<app data>/policy.bin` (re-verified on load) for 1 h of freshness:
//!    - policy says no token needed → allowed;
//!    - server answered but without a policy we can verify (no key on the
//!      server, or a key this build does not embed) → allowed, as before P3
//!      (rollback: remove CLIENT_QUOTA_SIGNING_KEY);
//!    - server answered with requireToken → refused `claim_required` (the UI
//!      skipped or faked the claim);
//!    - server NOT reachable → an offline slot from the `quota_grace` table in
//!      vidgrab.db (written only here), at most `policy.grace` (3 without any
//!      policy) per UTC day; the same URL again the same day takes no new
//!      slot (resume / retry). Past the cap → `offline_grace_used`.
//!
//! Limits, said plainly: deleting vidgrab.db resets today's offline slots
//! (and loses the history); a captured requireToken=false policy stays valid
//! for its 24 h; yt-dlp outside the app is never limited (PLAN-32D §5.4).

use crate::claim_token::{self, Keys, Policy};
use crate::history::Db;
use rusqlite::{params, OptionalExtension};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

include!(concat!(env!("OUT_DIR"), "/claim_keys.rs"));

/// Same host as desktop/src/lib/config.ts API_BASE.
pub const API_HOST: &str = "dvid-api.vibe1.tinhgon.xyz";
const VERSION_URL: &str = "https://dvid-api.vibe1.tinhgon.xyz/api/v1/client/version";
const SERVER_FILE_PATH: &str = "/api/v1/download-local";
pub const FETCH_TIMEOUT: Duration = Duration::from_secs(3);
pub const POLICY_FRESH_SEC: i64 = 3600;
/// Offline slots a day when no policy was ever read (fresh install, policy.bin deleted).
pub const DEFAULT_GRACE: u32 = 3;
const GRACE_KEEP_SEC: i64 = 8 * 86_400;
const MAX_VERSION_BODY: u64 = 64 * 1024;

/// What Rust's own GET /client/version gave.
#[derive(Debug, Clone, PartialEq)]
pub enum Fetched {
    /// HTTP 200 with JSON; the `policy` string if there was one.
    Answer(Option<String>),
    /// No answer within 3 s, a network/TLS error or a non-200 status.
    Unreachable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Allowed {
    /// No public key in this build.
    Off,
    /// The server's own file.
    ServerFile,
    Token,
    /// The policy (or the server's lack of one) says no token is needed.
    NotRequired,
    /// One offline slot taken.
    Grace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refused {
    ClaimRequired,
    OfflineGraceUsed,
    /// Offline and the local database could not be used: fail closed.
    NoLedger,
}

pub struct Request<'a> {
    pub job_id: &'a str,
    /// The url string exactly as the UI sent it (trimmed).
    pub url_raw: &'a str,
    /// validate::url's normalised form (what yt-dlp gets).
    pub url_norm: &'a str,
    pub device_hash: &'a str,
    pub token: Option<&'a str>,
}

#[derive(Default)]
struct Cache {
    /// The last verified signed policy string (may be expired; re-checked on use).
    raw: Option<String>,
    /// When Rust last got an Answer from the server.
    answered_at: Option<i64>,
    loaded: bool,
}

pub struct Gate {
    keys: &'static Keys,
    policy_file: Option<PathBuf>,
    cache: Mutex<Cache>,
    /// claim id → job id that used it (this session).
    cids: Mutex<HashMap<String, String>>,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

pub fn now_secs() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// The server-route file (`<API>/api/v1/download-local?...`): counted by the
/// server when it made it, so no claim token.
pub fn is_server_file(url_norm: &str) -> bool {
    url::Url::parse(url_norm).is_ok_and(|u| {
        u.scheme() == "https"
            && u.host_str() == Some(API_HOST)
            && u.port().is_none()
            && u.username().is_empty()
            && u.password().is_none()
            && u.path() == SERVER_FILE_PATH
    })
}

impl Gate {
    pub fn new(keys: &'static Keys, policy_file: Option<PathBuf>) -> Self {
        Gate { keys, policy_file, cache: Mutex::new(Cache::default()), cids: Mutex::new(HashMap::new()) }
    }

    pub fn enabled(&self) -> bool {
        !self.keys.is_empty()
    }

    fn load_file_once(&self, c: &mut Cache, now: i64) {
        if c.loaded {
            return;
        }
        c.loaded = true;
        if let Some(raw) = self.policy_file.as_ref().and_then(|p| std::fs::read_to_string(p).ok()) {
            let raw = raw.trim().to_string();
            if claim_token::check_policy(&raw, self.keys, now).is_some() {
                c.raw = Some(raw);
            }
        }
    }

    fn policy(&self, c: &Cache, now: i64) -> Option<Policy> {
        c.raw.as_deref().and_then(|r| claim_token::check_policy(r, self.keys, now))
    }

    /// Records what the server said. A verified policy replaces the cache
    /// (and policy.bin); an answer without one clears both; Unreachable
    /// keeps what we had.
    fn apply(&self, f: &Fetched, now: i64) {
        let mut c = lock(&self.cache);
        let Fetched::Answer(p) = f else { return };
        c.answered_at = Some(now);
        let verified = p.as_deref().filter(|r| claim_token::check_policy(r, self.keys, now).is_some());
        c.raw = verified.map(str::to_string);
        if let Some(path) = &self.policy_file {
            let _ = match &c.raw {
                Some(r) => std::fs::write(path, r),
                None => std::fs::remove_file(path),
            };
        }
    }

    /// App start: read the policy once in the background.
    pub fn refresh(&self, fetch: &dyn Fn() -> Fetched) {
        if self.enabled() {
            let f = fetch();
            self.apply(&f, now_secs());
        }
    }

    fn take_cid(&self, cid: &str, job_id: &str) -> bool {
        let mut m = lock(&self.cids);
        match m.get(cid) {
            Some(j) => j == job_id, // resume / L0→L1 of the same job reuses its claim
            None => {
                m.insert(cid.to_string(), job_id.to_string());
                true
            }
        }
    }

    pub fn check(&self, req: &Request<'_>, db: Option<&Db>, now: i64, fetch: &dyn Fn() -> Fetched) -> Result<Allowed, Refused> {
        if !self.enabled() {
            return Ok(Allowed::Off);
        }
        if is_server_file(req.url_norm) {
            return Ok(Allowed::ServerFile);
        }
        if let Some(t) = req.token {
            let urls = [req.url_raw, req.url_norm];
            if let Some(tok) = claim_token::check_token(t, self.keys, now, &urls, req.device_hash) {
                if self.take_cid(&tok.cid, req.job_id) {
                    return Ok(Allowed::Token);
                }
            }
        }
        // No usable token: what does the server want?
        let (policy, fresh) = {
            let mut c = lock(&self.cache);
            self.load_file_once(&mut c, now);
            let fresh = c.answered_at.is_some_and(|t| now - t < POLICY_FRESH_SEC);
            (self.policy(&c, now), fresh)
        };
        match policy {
            Some(p) if !p.require_token => return Ok(Allowed::NotRequired),
            None if fresh => return Ok(Allowed::NotRequired), // the server answered < 1 h ago without a policy
            _ => {}
        }
        // A token is (or may be) required: is the server reachable from HERE?
        let fetched = fetch();
        self.apply(&fetched, now);
        match fetched {
            Fetched::Answer(_) => {
                let c = lock(&self.cache);
                match self.policy(&c, now) {
                    Some(p) if p.require_token => Err(Refused::ClaimRequired),
                    _ => Ok(Allowed::NotRequired),
                }
            }
            Fetched::Unreachable => {
                let cap = policy.map(|p| p.grace).unwrap_or(DEFAULT_GRACE);
                let db = db.ok_or(Refused::NoLedger)?;
                match grace_take(db, &claim_token::url_hash(req.url_norm), cap, now) {
                    Ok(true) => Ok(Allowed::Grace),
                    Ok(false) => Err(Refused::OfflineGraceUsed),
                    Err(_) => Err(Refused::NoLedger),
                }
            }
        }
    }
}

/// One offline slot for `url_hash` today (UTC). Same URL again today: no new slot.
pub fn grace_take(db: &Db, url_hash: &str, cap: u32, now: i64) -> rusqlite::Result<bool> {
    let conn = db.c();
    let day: String = conn.query_row("SELECT strftime('%Y-%m-%d', ?1, 'unixepoch')", [now], |r| r.get(0))?;
    let seen: Option<i64> = conn
        .query_row("SELECT used FROM quota_grace WHERE day = ?1 AND url = ?2", params![day, url_hash], |r| r.get(0))
        .optional()?;
    if seen.is_some() {
        return Ok(true);
    }
    let used: i64 = conn.query_row("SELECT COUNT(*) FROM quota_grace WHERE day = ?1", [&day], |r| r.get(0))?;
    if used >= i64::from(cap) {
        return Ok(false);
    }
    conn.execute(
        "INSERT INTO quota_grace (day, used, url, at) VALUES (?1, ?2, ?3, ?4)",
        params![day, used + 1, url_hash, now],
    )?;
    conn.execute("DELETE FROM quota_grace WHERE at < ?1", [now - GRACE_KEEP_SEC])?;
    Ok(true)
}

/// Rust's own GET /client/version (blocking; call off the async runtime).
pub fn fetch_policy(app_version: &str) -> Fetched {
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(FETCH_TIMEOUT))
        .http_status_as_error(false)
        .build()
        .into();
    let resp = agent
        .get(VERSION_URL)
        .header("Accept", "application/json")
        .header("X-VG-Client", app_version)
        .call();
    match resp {
        Ok(mut r) if r.status().as_u16() == 200 => {
            match r.body_mut().with_config().limit(MAX_VERSION_BODY).read_to_string() {
                Ok(s) => parse_version_body(&s),
                Err(_) => Fetched::Unreachable,
            }
        }
        _ => Fetched::Unreachable,
    }
}

pub fn parse_version_body(s: &str) -> Fetched {
    match serde_json::from_str::<serde_json::Value>(s) {
        Ok(v) if v.is_object() => Fetched::Answer(v.get("policy").and_then(|p| p.as_str()).map(str::to_string)),
        _ => Fetched::Unreachable,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine as _;
    use std::cell::Cell;

    struct Fx {
        v: serde_json::Value,
        keys: &'static Keys,
    }

    fn fx() -> Fx {
        let v: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/claim_p3.json")).unwrap();
        let mut keys: Vec<(&'static str, [u8; 32])> = Vec::new();
        for (kid, b64) in v["keys"].as_object().unwrap() {
            let raw = base64::engine::general_purpose::STANDARD.decode(b64.as_str().unwrap()).unwrap();
            keys.push((Box::leak(kid.clone().into_boxed_str()), raw.try_into().unwrap()));
        }
        Fx { v, keys: Box::leak(keys.into_boxed_slice()) }
    }

    impl Fx {
        fn s(&self, k: &str) -> &str {
            self.v[k].as_str().unwrap()
        }
        fn now(&self) -> i64 {
            self.v["now"].as_i64().unwrap()
        }
        fn req<'a>(&'a self, job: &'a str, token: Option<&'a str>) -> Request<'a> {
            Request { job_id: job, url_raw: self.s("url"), url_norm: self.s("url"), device_hash: self.s("device"), token }
        }
    }

    fn db() -> Db {
        Db::open_in_memory().unwrap()
    }

    /// A fetcher that counts its calls.
    fn fetcher(answer: Fetched, calls: &Cell<u32>) -> impl Fn() -> Fetched + '_ {
        move || {
            calls.set(calls.get() + 1);
            answer.clone()
        }
    }

    #[test]
    fn no_keys_is_off() {
        let g = Gate::new(&[], None);
        let f = fx();
        let n = Cell::new(0);
        assert_eq!(g.check(&f.req("j1", None), None, f.now(), &fetcher(Fetched::Unreachable, &n)), Ok(Allowed::Off));
        assert_eq!(n.get(), 0);
    }

    #[test]
    fn valid_token_passes_without_network() {
        let f = fx();
        let g = Gate::new(f.keys, None);
        let n = Cell::new(0);
        let r = g.check(&f.req("j1", Some(f.s("token"))), None, f.now(), &fetcher(Fetched::Unreachable, &n));
        assert_eq!(r, Ok(Allowed::Token));
        assert_eq!(n.get(), 0);
    }

    #[test]
    fn claim_id_reused_by_another_job_is_refused_same_job_ok() {
        let f = fx();
        let g = Gate::new(f.keys, None);
        let n = Cell::new(0);
        let online = Fetched::Answer(Some(f.s("policy").to_string()));
        assert_eq!(g.check(&f.req("j1", Some(f.s("token"))), None, f.now(), &fetcher(online.clone(), &n)), Ok(Allowed::Token));
        // resume of the same job
        assert_eq!(g.check(&f.req("j1", Some(f.s("token"))), None, f.now(), &fetcher(online.clone(), &n)), Ok(Allowed::Token));
        // another job replaying the token
        assert_eq!(
            g.check(&f.req("j2", Some(f.s("token"))), None, f.now(), &fetcher(online, &n)),
            Err(Refused::ClaimRequired)
        );
    }

    #[test]
    fn reachable_server_requiring_token_refuses_missing_or_bad_token() {
        // click-through 11: a fake API told the UI "allowed" without a token
        let f = fx();
        let g = Gate::new(f.keys, None);
        let n = Cell::new(0);
        let online = Fetched::Answer(Some(f.s("policy").to_string()));
        assert_eq!(g.check(&f.req("j1", None), Some(&db()), f.now(), &fetcher(online.clone(), &n)), Err(Refused::ClaimRequired));
        assert_eq!(
            g.check(&f.req("j2", Some(f.s("token_foreign_key"))), Some(&db()), f.now(), &fetcher(online.clone(), &n)),
            Err(Refused::ClaimRequired)
        );
        let other = Request { url_raw: "https://www.youtube.com/watch?v=zzz", url_norm: "https://www.youtube.com/watch?v=zzz", ..f.req("j3", Some(f.s("token"))) };
        assert_eq!(g.check(&other, Some(&db()), f.now(), &fetcher(online, &n)), Err(Refused::ClaimRequired));
    }

    #[test]
    fn server_without_policy_means_old_behaviour() {
        // rollback: CLIENT_QUOTA_SIGNING_KEY removed
        let f = fx();
        let g = Gate::new(f.keys, None);
        let n = Cell::new(0);
        assert_eq!(g.check(&f.req("j1", None), None, f.now(), &fetcher(Fetched::Answer(None), &n)), Ok(Allowed::NotRequired));
        assert_eq!(n.get(), 1);
        // fresh for an hour: no second request
        assert_eq!(g.check(&f.req("j2", None), None, f.now() + 60, &fetcher(Fetched::Answer(None), &n)), Ok(Allowed::NotRequired));
        assert_eq!(n.get(), 1);
        // a policy signed with a key this build does not have = no policy
        let g2 = Gate::new(f.keys, None);
        let foreign = Fetched::Answer(Some(f.s("token_foreign_key").to_string()));
        assert_eq!(g2.check(&f.req("j1", None), None, f.now(), &fetcher(foreign, &n)), Ok(Allowed::NotRequired));
    }

    #[test]
    fn policy_off_needs_no_token() {
        let f = fx();
        let g = Gate::new(f.keys, None);
        let n = Cell::new(0);
        let off = Fetched::Answer(Some(f.s("policy_off").to_string()));
        assert_eq!(g.check(&f.req("j1", None), None, f.now(), &fetcher(off, &n)), Ok(Allowed::NotRequired));
    }

    #[test]
    fn offline_grace_then_refused_and_same_url_free() {
        // click-through 12: no policy.bin, no network → 3 slots, then refused
        let f = fx();
        let g = Gate::new(f.keys, None);
        let d = db();
        let n = Cell::new(0);
        let off = || fetcher(Fetched::Unreachable, &n);
        let url = |i: u32| format!("https://www.youtube.com/watch?v=v{i}");
        for i in 0..DEFAULT_GRACE {
            let u = url(i);
            let r = Request { job_id: "j", url_raw: &u, url_norm: &u, device_hash: f.s("device"), token: None };
            assert_eq!(g.check(&r, Some(&d), f.now(), &off()), Ok(Allowed::Grace), "slot {i}");
        }
        let u = url(99);
        let r = Request { job_id: "j", url_raw: &u, url_norm: &u, device_hash: f.s("device"), token: None };
        assert_eq!(g.check(&r, Some(&d), f.now(), &off()), Err(Refused::OfflineGraceUsed));
        // retry / resume of a URL that already has today's slot
        let u0 = url(0);
        let r0 = Request { job_id: "j", url_raw: &u0, url_norm: &u0, device_hash: f.s("device"), token: None };
        assert_eq!(g.check(&r0, Some(&d), f.now(), &off()), Ok(Allowed::Grace));
        // next UTC day: slots again
        assert_eq!(g.check(&r, Some(&d), f.now() + 86_400, &off()), Ok(Allowed::Grace));
        // no ledger: fail closed
        assert_eq!(g.check(&r, None, f.now(), &off()), Err(Refused::NoLedger));
    }

    #[test]
    fn offline_cap_follows_cached_policy() {
        let f = fx();
        let dir = std::env::temp_dir().join(format!("vg-gate-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("policy.bin");
        let n = Cell::new(0);
        {
            let g = Gate::new(f.keys, Some(file.clone()));
            g.apply(&Fetched::Answer(Some(f.s("policy").to_string())), f.now());
        }
        assert!(file.exists());
        // a new run (app restart) reads policy.bin and is then offline
        let g = Gate::new(f.keys, Some(file.clone()));
        assert_eq!(g.check(&f.req("j1", None), Some(&db()), f.now() + 7200, &fetcher(Fetched::Unreachable, &n)), Ok(Allowed::Grace));
        // a tampered policy.bin is ignored (no policy → default cap, still offline grace)
        std::fs::write(&file, "garbage").unwrap();
        let g = Gate::new(f.keys, Some(file.clone()));
        assert_eq!(g.check(&f.req("j1", None), Some(&db()), f.now(), &fetcher(Fetched::Unreachable, &n)), Ok(Allowed::Grace));
        // a cached policy_off stays in force offline (no slot taken)
        let g = Gate::new(f.keys, Some(file.clone()));
        g.apply(&Fetched::Answer(Some(f.s("policy_off").to_string())), f.now());
        let g = Gate::new(f.keys, Some(file.clone()));
        assert_eq!(g.check(&f.req("j1", None), Some(&db()), f.now(), &fetcher(Fetched::Unreachable, &n)), Ok(Allowed::NotRequired));
        // the server answering without a policy removes policy.bin
        g.apply(&Fetched::Answer(None), f.now());
        assert!(!file.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn server_file_is_exempt_only_on_api_host() {
        assert!(is_server_file("https://dvid-api.vibe1.tinhgon.xyz/api/v1/download-local?file=a.mp4&filename=x.mp4"));
        assert!(!is_server_file("https://dvid-api.vibe1.tinhgon.xyz/api/v1/other?file=a"));
        assert!(!is_server_file("http://dvid-api.vibe1.tinhgon.xyz/api/v1/download-local?file=a"));
        assert!(!is_server_file("https://dvid-api.vibe1.tinhgon.xyz.evil.com/api/v1/download-local"));
        assert!(!is_server_file("https://x@dvid-api.vibe1.tinhgon.xyz/api/v1/download-local"));
        assert!(!is_server_file("https://www.youtube.com/api/v1/download-local"));
    }

    #[test]
    fn version_body_parsing() {
        assert_eq!(parse_version_body(r#"{"latest":"0.10.0"}"#), Fetched::Answer(None));
        assert_eq!(parse_version_body(r#"{"policy":"a.b"}"#), Fetched::Answer(Some("a.b".into())));
        assert_eq!(parse_version_body("<html>"), Fetched::Unreachable);
        assert_eq!(parse_version_body("[]"), Fetched::Unreachable);
    }
}
