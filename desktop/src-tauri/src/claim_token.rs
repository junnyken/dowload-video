//! Signed claim tokens and quota policy from the VidGrab API (PLAN-32E §5.2 (a),
//! P3, task #6172). Pure functions; the decision lives in quota_gate.rs.
//!
//! Both are `b64url(payload JSON) + "." + b64url(Ed25519 signature)` where the
//! signature covers the ASCII of the encoded payload (backend
//! app/core/claim_signing.py). The public keys are embedded at build time
//! (build.rs, VIDGRAB_CLAIM_PUBKEYS); the payload's `kid` picks one.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use ed25519_dalek::{Signature, VerifyingKey};
use serde::Deserialize;
use sha2::{Digest, Sha256};

pub type Keys = [(&'static str, [u8; 32])];

/// Signature right for the payload's kid, `v == 1`, `exp > now`. Returns the payload.
pub fn verify_signed(signed: &str, keys: &Keys, now: i64) -> Option<serde_json::Value> {
    if signed.len() > 8192 {
        return None;
    }
    let (body, sig) = signed.split_once('.')?;
    if sig.contains('.') {
        return None;
    }
    let payload: serde_json::Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(body).ok()?).ok()?;
    if payload.get("v")?.as_i64()? != 1 {
        return None;
    }
    let kid = payload.get("kid")?.as_str()?;
    let (_, pub_bytes) = keys.iter().find(|(k, _)| *k == kid)?;
    let key = VerifyingKey::from_bytes(pub_bytes).ok()?;
    let sig: [u8; 64] = URL_SAFE_NO_PAD.decode(sig).ok()?.try_into().ok()?;
    key.verify_strict(body.as_bytes(), &Signature::from_bytes(&sig)).ok()?;
    if payload.get("exp")?.as_i64()? <= now {
        return None;
    }
    Some(payload)
}

/// First 32 hex of sha256(url) — what the server put in `uh`.
pub fn url_hash(url: &str) -> String {
    let d = Sha256::digest(url.as_bytes());
    d.iter().take(16).map(|b| format!("{b:02x}")).collect()
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Token {
    pub cid: String,
    pub uh: Vec<String>,
    pub dev: String,
    pub exp: i64,
}

/// A verified token whose `uh` lists one of `urls` (the exact string the UI
/// sent, and our normalised form) and whose `dev` is this machine.
pub fn check_token(signed: &str, keys: &Keys, now: i64, urls: &[&str], device_hash: &str) -> Option<Token> {
    let t: Token = serde_json::from_value(verify_signed(signed, keys, now)?).ok()?;
    let dev = device_hash.to_ascii_lowercase();
    if t.cid.is_empty() || dev.len() < 32 || t.dev != dev[..32] {
        return None;
    }
    let want: Vec<String> = urls.iter().map(|u| url_hash(u)).collect();
    t.uh.iter().any(|h| want.contains(h)).then_some(t)
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Policy {
    pub require_token: bool,
    pub grace: u32,
    pub exp: i64,
}

/// A verified, unexpired policy.
pub fn check_policy(signed: &str, keys: &Keys, now: i64) -> Option<Policy> {
    serde_json::from_value(verify_signed(signed, keys, now)?).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Made by backend/scripts/make_claim_p3_fixture.py with throwaway keys:
    /// proves a token from the Python signer verifies here.
    struct Fx {
        v: serde_json::Value,
        keys: Vec<(&'static str, [u8; 32])>,
    }

    fn fixture() -> Fx {
        let v: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/claim_p3.json")).unwrap();
        let mut keys = Vec::new();
        for (kid, b64) in v["keys"].as_object().unwrap() {
            let raw = base64::engine::general_purpose::STANDARD.decode(b64.as_str().unwrap()).unwrap();
            keys.push((&*Box::leak(kid.clone().into_boxed_str()), raw.try_into().unwrap()));
        }
        Fx { v, keys }
    }

    impl Fx {
        fn s(&self, k: &str) -> &str {
            self.v[k].as_str().unwrap()
        }
        fn now(&self) -> i64 {
            self.v["now"].as_i64().unwrap()
        }
        fn only(&self, kid: &str) -> Vec<(&'static str, [u8; 32])> {
            self.keys.iter().filter(|(k, _)| *k == kid).cloned().collect()
        }
    }

    #[test]
    fn python_token_verifies_and_binds() {
        let f = fixture();
        let t = check_token(f.s("token"), &f.keys, f.now() + 60, &[f.s("url")], f.s("device")).expect("valid");
        assert_eq!(t.cid, f.s("cid"));
        assert_eq!(t.uh, vec![url_hash(f.s("url"))]);
    }

    #[test]
    fn next_key_of_a_rotation_verifies() {
        let f = fixture();
        assert!(check_token(f.s("token_next_key"), &f.keys, f.now(), &[f.s("url")], f.s("device")).is_some());
        // an app that only embeds t1 does not accept the t2 token
        assert!(check_token(f.s("token_next_key"), &f.only("t1"), f.now(), &[f.s("url")], f.s("device")).is_none());
    }

    #[test]
    fn foreign_key_refused() {
        let f = fixture();
        assert!(verify_signed(f.s("token_foreign_key"), &f.keys, f.now()).is_none());
    }

    #[test]
    fn tampered_refused() {
        let f = fixture();
        let tok = f.s("token");
        let (body, sig) = tok.split_once('.').unwrap();
        for i in [0, 7, body.len() - 3] {
            let mut b = body.as_bytes().to_vec();
            b[i] = if b[i] == b'A' { b'B' } else { b'A' };
            let t = format!("{}.{sig}", String::from_utf8(b).unwrap());
            assert!(verify_signed(&t, &f.keys, f.now()).is_none(), "byte {i}");
        }
        let mut s = sig.as_bytes().to_vec();
        s[5] = if s[5] == b'A' { b'B' } else { b'A' };
        let t = format!("{body}.{}", String::from_utf8(s).unwrap());
        assert!(verify_signed(&t, &f.keys, f.now()).is_none());
        assert!(verify_signed("x", &f.keys, f.now()).is_none());
        assert!(verify_signed(&format!("{tok}.x"), &f.keys, f.now()).is_none());
    }

    #[test]
    fn expired_refused() {
        let f = fixture();
        let exp = f.now() + 2 * 3600;
        assert!(verify_signed(f.s("token"), &f.keys, exp - 1).is_some());
        assert!(verify_signed(f.s("token"), &f.keys, exp).is_none());
    }

    #[test]
    fn wrong_url_or_machine_refused() {
        let f = fixture();
        let other_dev = "f".repeat(64);
        assert!(check_token(f.s("token"), &f.keys, f.now(), &["https://www.youtube.com/watch?v=other"], f.s("device")).is_none());
        assert!(check_token(f.s("token"), &f.keys, f.now(), &[f.s("url")], &other_dev).is_none());
        assert!(check_token(f.s("token"), &f.keys, f.now(), &[f.s("url")], "short").is_none());
        // either the raw or the normalised url may match
        assert!(check_token(f.s("token"), &f.keys, f.now(), &["https://x.invalid/", f.s("url")], f.s("device")).is_some());
    }

    #[test]
    fn policy_from_python_signer() {
        let f = fixture();
        let p = check_policy(f.s("policy"), &f.keys, f.now()).unwrap();
        assert!(p.require_token);
        assert_eq!(p.grace, 3);
        assert!(!check_policy(f.s("policy_off"), &f.keys, f.now()).unwrap().require_token);
        assert!(check_policy(f.s("policy"), &f.keys, f.now() + 24 * 3600).is_none()); // expired
        assert!(check_policy(f.s("policy"), &[], f.now()).is_none()); // no keys embedded
    }

    #[test]
    fn url_hash_matches_python() {
        // python: hashlib.sha256(b"abc").hexdigest()[:32]
        assert_eq!(url_hash("abc"), "ba7816bf8f01cfea414140de5dae2223");
    }
}
