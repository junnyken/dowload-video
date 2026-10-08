# Claim-signing key (PLAN-32E P3, task #6172) — setup, rotation, rollback

The server signs a short-lived token on every allowed claim and a 24 h
policy on `GET /client/version` with an Ed25519 key. App ≥ 0.10.0 embeds the
public key(s) at build time and checks tokens in Rust before yt-dlp starts
(`desktop/src-tauri/src/quota_gate.rs`, `claim_token.rs`; server
`backend/app/core/claim_signing.py`).

## Names

| Where | Variable | Value | Secret? |
|---|---|---|---|
| Backend env | `CLIENT_QUOTA_SIGNING_KEY` | base64 of the 32-byte Ed25519 seed | **yes** — env + owner's password manager only, never in git, never in logs |
| Backend env | `CLIENT_QUOTA_SIGNING_KID` | key id, e.g. `k1` (1–16 chars `A-Za-z0-9_-`, default `k1`) | no |
| App build env | `VIDGRAB_CLAIM_PUBKEYS` | `k1:<base64 public key>` or `k1:<pub>,k2:<pub>` (max 3) | no |

Generate (prints all three lines, writes nothing):

    backend/venv/bin/python backend/scripts/gen_claim_signing_key.py k1

## Build-time policy (build.rs)

* Malformed `VIDGRAB_CLAIM_PUBKEYS` (bad base64, not 32 bytes, bad/duplicate
  kid, > 3 keys) → the build **fails**, in every profile.
* Unset/empty → a **release** build fails with an explanation; a debug /
  `cargo test` build embeds no key (warning) and checks nothing.
* `none` → explicitly no key: that app never asks for a token and grants no
  offline slots itself (0.9 behaviour). Only for test builds.

## First setup (order matters)

1. Generate `k1`. Store the seed in the password manager.
2. Build app 0.10.0 with `VIDGRAB_CLAIM_PUBKEYS=k1:<pub>`; release it.
3. Set `CLIENT_QUOTA_SIGNING_KEY` + `CLIENT_QUOTA_SIGNING_KID=k1` on the
   backend and redeploy (Vibe Host: `set_env` + redeploy; env changes need a
   deploy). App 0.9.x ignores the new fields (`token`, `policy`, `vgToken`).
4. Check: `GET /api/v1/client/version` has `policy`; a claim from the app has
   `token`; Windows click-through 11 and 12 (PLAN-32E §7.3).

Doing 3 before 2 is harmless (old apps ignore the fields). An app built with
a key the server does not use treats the server's policy as "none" and
behaves like 0.9.

## Planned rotation (k1 → k2)

1. Generate `k2`. Build the next app with `VIDGRAB_CLAIM_PUBKEYS=k1:<pub1>,k2:<pub2>`; release.
2. Wait until `DESKTOP_MIN_VERSION` ≥ that version (older apps only know k1).
3. Switch the backend to `CLIENT_QUOTA_SIGNING_KEY=<seed2>`, `_KID=k2`; redeploy.
4. A later app drops k1 from `VIDGRAB_CLAIM_PUBKEYS`.

Apps that do not embed k2 see a policy they cannot verify = "no policy" =
0.9 behaviour (they keep working, just without the check) until updated.

## Leaked private key

Rotate at once (steps 1 and 3 together, server first is fine), then raise
`DESKTOP_MIN_VERSION` to the first version embedding the new key. Until
then apps with only the old key behave like 0.9 (no token check).

## Rollback / emergency

* Stop requiring tokens but keep signing: `CLIENT_QUOTA_MODE=shadow` (or
  `CLIENT_QUOTA_ENABLED=false`) → policy `requireToken:false`; apps follow
  within 1 h (policy freshness) or at the next start.
* Remove signing completely: unset `CLIENT_QUOTA_SIGNING_KEY` → no `policy`,
  no `token`; app 0.10 sees an answer without a policy and downloads as 0.9
  did. Offline (API unreachable from the app) it still allows only
  `policy.grace` / 3 downloads a day — the same cap 0.9 applied in the UI.
* Nothing is stored on the server; no migration to undo.

## What it does not stop (said plainly)

* yt-dlp run outside the app.
* Deleting `vidgrab.db` (resets today's offline slots; also loses history).
* A captured `requireToken:false` policy reused within its 24 h.
* Rust's HTTP client does not use the Windows proxy settings: behind a proxy
  that blocks direct connections Rust counts as "offline" for the policy
  (tokens from the UI still verify offline, so normal downloads are not
  affected).
