# VidGrab security review, 13/10/2026 (Task #6398, Phase 34B-4..7)

**Scope:**
- **A.** Admin hardening.
- **B.** Dependency and container scan.
- **C.** Abuse controls.
- **D.** Privacy policy compared with the data the code actually stores.

**Baseline:** branch `feat/34b4-sechardening`, from `github/main` 2178005.

**Method:**
- The code and the existing tests were read.
- The admin routes were audited with an AST scan (auth dependency and audit call per mutating route).
- Dependencies were checked with `pip-audit`, `npm audit --omit=dev` and `cargo audit`.

No external calls were made in tests, and this document contains no secret values.

**Severity:**
- **HIGH:** an attacker can exploit it with little effort, or it exposes admin or user data.
- **MEDIUM:** it weakens a control or needs a precondition.
- **LOW:** hardening or hygiene.

## Findings

| # | Area | Finding | Severity | Status | File:line |
|---|------|---------|----------|--------|-----------|
| A1 | Admin | Partner API key `ip_allowlist` compared `request.client.host`. Under `uvicorn --forwarded-allow-ips '*'` that is the first X-Forwarded-For entry, which the caller chooses. So a leaked `vgp_` key plus `X-Forwarded-For: <allowed ip>` bypassed the allowlist. It now uses `get_client_ip`. | HIGH | **fixed** | backend/app/core/partner_auth.py:187 |
| A2 | Admin / infra | `docker-compose.cobalt.yml` published Redis on `0.0.0.0:6379` with no password, and Cobalt on `0.0.0.0:9000`. Redis holds admin sessions and the cookie pool, so anyone reaching that port gets admin. Both ports are now bound to 127.0.0.1. I could not confirm whether this file is used on any server. | HIGH (if deployed) | **fixed** | docker-compose.cobalt.yml:7,22 |
| A3 | Admin | Admin session tokens were stored raw as Redis key names (`admin:session:<token>`). A Redis read, KEYS dump or backup gave a working admin credential. They are now stored as `sha256(token)`. Sessions issued before this change are accepted once and migrated, so nobody is logged out. | MEDIUM | **fixed** | backend/app/api/admin.py:65, 102 |
| A4 | Admin | Sessions had only a sliding 8h idle TTL. An open dashboard (SSE every 5s) kept a token alive forever. An absolute cap of 7 days now applies, counted from login. | MEDIUM | **fixed** | backend/app/api/admin.py:53, 102 |
| A5 | Admin | Logout only cleared localStorage, so the token stayed valid until its idle TTL expired. A new `POST /admin/logout` revokes the presented session, and the dashboard calls it. | MEDIUM | **fixed** | backend/app/api/admin.py:288; frontend/src/admin/hooks/useAdminAuth.ts |
| A6 | Admin | State-changing admin routes that wrote no audit entry: `POST /backup/run`, `/cookies/check-expiry`, `/scraperapi/rotate`, `DELETE /cache/spotify-resolve`, `/cache/spotify-artist/{id}`, intelligence `POST /anomalies/{id}/resolve`, `/auto-tune/reset`. They now call `log_admin_action`. A test fails if a new admin mutation has no audit call. | MEDIUM | **fixed** | backend/app/api/admin.py, intelligence.py; tests/test_security_hardening_6398.py |
| A7 | Admin | Still not logged, by choice: read-only probes (`/po-token/test`, `/cookies/test`, `/debug-youtube`, `/scraperapi/refresh-credits`) and the machine ingest `POST /throwaway/accounts` (one row per script attempt). They are listed as exemptions in the test. | LOW | open (by design) | backend/app/api/admin.py |
| A8 | Admin | There are no roles on the server. One shared `ADMIN_PASSWORD` gives full admin. The frontend `navConfig` `minRole` (viewer/operator/admin) is cosmetic: `useAdminAuth` hard-codes `role: 'admin'`, and no backend route checks a role. The audit log records `actor_email="admin"`, so it cannot tell who acted. | MEDIUM | **open – owner decision** (needs per-admin accounts) | frontend/src/admin/hooks/useAdminAuth.ts:77; backend/app/core/audit.py:94 |
| A9 | Admin | `X-Admin-Token` still accepts the raw admin password, as well as session tokens, on every admin route. `scripts/refresh-cookie.sh` and `tools/create_throwaway.py` depend on this. Guesses through it share the login lockout. | MEDIUM | open – owner decision (move scripts to a session or a dedicated scoped key, then drop the password path) | backend/app/api/admin.py:210 |
| A10 | Admin | The lockout is per IP: 3 bad attempts lock that IP for 15 min. There is no global limit, so an attacker spread over many IPs is limited only by how many IPs they have. Nothing checks password strength or length at startup. | LOW | open | backend/app/api/admin.py:55 |
| A11 | Admin | The session token is kept in `localStorage`, so any XSS on the admin origin can read it. | LOW | open | frontend/src/admin/hooks/useAdminAuth.ts:81 |
| A12 | Admin | The `ADMIN_ALLOWED_IPS` allowlist exists but is empty by default, so every IP is allowed. Its strength depends on the proxy topology described in C1. | LOW | open – owner decision (set it) | backend/app/api/admin.py:42 |
| A13 | Admin | No hard-coded admin password. `ADMIN_PASSWORD` has no default, and the compare is constant-time (`secrets.compare_digest`). | – | verified | backend/app/api/admin.py:37, 80 |
| C1 | Abuse | `get_client_ip` takes the last X-Forwarded-For hop, which is correct behind exactly one proxy. Two cases break it. If the backend port is reachable directly, XFF is attacker-set. If a CDN sits in front of the proxy, every user shares the CDN IP. Uvicorn also trusts `*`. | MEDIUM | open – deploy check (keep the backend unexposed; set `--forwarded-allow-ips` to the proxy IP) | backend/app/core/client_ip.py; backend/docker-entrypoint.sh:86 |
| C2 | Abuse | Partner key `rate_limit_per_min` was stored, editable and shown on `/usage`, but nothing enforced it. It is now enforced per key with a Redis counter per minute window, returning 429 with Retry-After. It fails open if Redis is down. | MEDIUM | **fixed** | backend/app/core/partner_auth.py:265 |
| C3 | Abuse | Feedback put `request.client.host`, which can be spoofed, into the Telegram message. It now uses `get_client_ip`. | LOW | **fixed** | backend/app/api/feedback.py:22 |
| C4 | Abuse | Partner daily cap has four problems:<br>- It is skipped for enterprise plans (`monthly_cap == -1`).<br>- It fails open on a DB error.<br>- It is check-then-increment, so concurrent calls can race past it.<br>- It applies only to submit-job. | MEDIUM | open (product rule for enterprise) | backend/app/api/partner.py:128-149 |
| C5 | Abuse | Partner key with an unparseable `expires_at` never expires (fail open). A key with null `scopes` gets every scope. | LOW | open | backend/app/core/partner_auth.py:163-167, 250 |
| C6 | Abuse | `DailyIPQuotaMiddleware` is off by default (`DAILY_QUOTA_PER_IP=0`), fails open, and opens a synchronous Redis client per request. The real guest controls are the per-device and per-IP caps in `quotas.py`, and `ip_cap_exceeded` is applied on the claim, douyin and fetch-link paths. | LOW | open | backend/app/main.py:73-110 |
| C7 | Abuse | A new random device id gets a fresh quota bucket. The per-IP cap (guest limit × 3) bounds this, but IPv6 is keyed per /128, so a /64 lets a client rotate addresses freely. | LOW | open (key IPv6 by /64) | backend/app/api/client_quota.py:264 |
| C8 | Abuse | `/client/quota` settle is a non-atomic read-check-write, so two concurrent settles can both refund. The daily refund cap bounds it. | LOW | open | backend/app/api/client_quota.py:441 |
| C9 | Abuse | Claim tokens (Ed25519) are correctly signed, carry a `kid`, expire after 2h and are bound to the URL and device. They are verified only by the Windows app; there is no server-side redemption, so a token can be replayed for 2h on the same device and URL. This follows from the design (a client-side gate). | LOW | open (by design) | backend/app/core/claim_signing.py:169 |
| C10 | Abuse | Webhook SSRF guard checks pass:<br>- https only.<br>- Every resolved IP must be public, including IPv4-mapped IPv6 and the metadata IPs.<br>- The connection is pinned to the validated IP, with SNI for the original host.<br>- No redirects.<br>- The check runs at save and at send.<br>Any port on a public host is allowed (blind). | LOW | verified / open (port) | backend/app/core/webhook_guard.py; backend/app/tasks/webhook_tasks.py |
| C11 | Abuse | Webhook `secret_hash` is also the HMAC signing key, so a DB leak lets an attacker forge signatures. | LOW | open | backend/app/tasks/webhook_tasks.py:43 |
| C12 | Abuse | The takedown list is capped at 1000 and evicts the oldest entry. An attacker rotating IPs (limit 5/h per IP) can push genuine legal requests out. Status updates use `lset` by an index read earlier, so they race with new requests. | MEDIUM | open – owner decision (durable table + global limit) | backend/app/api/takedown.py:155, 218 |
| C13 | Abuse | Takedown IP hash salt defaults to a public constant when `TAKEDOWN_IP_SALT` is unset. | LOW | open – set the env | backend/app/api/takedown.py:52 |
| C14 | Abuse | Watch has caps per user and tier, per IP per day, a global free cap, poll floors and an admin-only kill switch, so it is well bounded. Two small gaps: the cap check is not atomic with the insert, and channel resolve makes an outbound call before the per-IP daily cap is checked. | LOW | open | backend/app/services/channel_watch.py:1270 |
| B1 | Deps | `python-multipart==0.0.12` has 7 advisories: PYSEC-2026-1851, PYSEC-2026-1852, PYSEC-2026-3036 to PYSEC-2026-3040 (multipart/urlencoded DoS, Content-Length bound, CR/LF skipping). Bumped to 0.0.31, a patch bump within 0.0.x. pip-audit now reports none. | HIGH | **fixed** | backend/requirements.txt |
| B2 | Deps | `react-router` 7.18.1 has GHSA-qwww-vcr4-c8h2 (CSRF in RSC mode, HIGH). VidGrab is a Vite SPA without RSC, so it was not exploitable here. Bumped `react-router-dom` from ^7.18.1 to ^7.18.4, a patch bump. `npm audit` now reports 0. | HIGH (n/a here) | **fixed** | frontend/package.json |
| B3 | Deps | `desktop/` npm audit: 0 vulnerabilities. `cargo audit` on `desktop/src-tauri`: 0 vulnerabilities, 2 warnings, both transitive through Tauri/GTK: `proc-macro-error` (unmaintained) and `glib` (unsound). | LOW | open (upstream) | desktop/src-tauri/Cargo.lock |
| B4 | Container | The backend image is `python:3.12-slim`, pinned to a tag but not a digest.<br>- The app runs as uid 10001 after the entrypoint fixes volume ownership.<br>- PhantomJS is pinned by SHA-256.<br>- Deno v2.4.1 and the bgutil tarball are pinned by version but have no checksum.<br>- The build runs a yt-dlp warm-up against YouTube.<br>- PhantomJS 2.1.1 is abandoned and bundles OpenSSL 1.0, which is reachable through iq.com extraction. | LOW | open (add digest + checksums; owner decides on PhantomJS) | backend/Dockerfile |
| D1 | Privacy | The privacy page did not match the code. It claimed:<br>- the extension does not read cookies (it has the `cookies` permission and sends site cookies with a download);<br>- IPs are "anonymised" (they are stored raw in `audit_logs`, `desktop_devices.last_ip` and Redis counters);<br>- technical logs are deleted after 30 days (no purge job exists);<br>- no video is stored on the server (temporary files are kept up to about 2h);<br>- only Supabase and Stripe receive data (Apify, ScraperAPI, AI vendors, Telegram, Sentry, Google Fonts and push services are also used).<br>It also omitted desktop device data, the takedown form and 14-day backups. The page is rewritten to match the code. | MEDIUM | **fixed** (text; BA review) | frontend/src/pages/PrivacyPolicy.jsx |
| D2 | Privacy | No retention job exists for `audit_logs` (raw IP and user agent) or for `analytics_events`. The page now says so instead of claiming 30 days. | MEDIUM | open – owner decision (add purge, then update the page) | backend/app/core/audit.py |
| D3 | Privacy | Takedown requests (name, email, URL, salted IP hash) live in Redis with no TTL and no backup. Retention is only the 1000-entry cap. | LOW | open | backend/app/api/takedown.py |
| D4 | Privacy | The server cookie pool is admin-supplied accounts only. It is stored in Redis as base64, not encrypted at rest. End-user cookies sent by the extension (`user_cookies_b64`) go to a temporary file that is deleted after the job. Desktop app cookies stay on the PC, encrypted with DPAPI. | MEDIUM | open – owner decision (encrypt the pool at rest) | backend/app/core/cookie_pool.py:258; backend/app/api/routes.py:277; desktop/src-tauri/src/cookies.rs:1-8 |
| D5 | Privacy | There is no self-service account deletion; deletion is by email, as the page states. | LOW | open | – |

## Verification

**Backend:**
- `pytest tests/`: 3460 passed, 33 skipped.
- Run against python-multipart 0.0.31, which was put on `PYTHONPATH` from a scratch dir because the shared venv still has 0.0.12.
- New tests are in `backend/tests/test_security_hardening_6398.py` (13 tests).
- Negative control: reverting the A1 fix makes `test_partner_allowlist_uses_last_xff_hop` fail.

**Frontend:** `vite build` OK with react-router 7.18.4.

**Desktop:** not changed.

**Deploy note:** after this deploys, the shared venv and the image must install `python-multipart==0.0.31`. The image does this from `requirements.txt`.

## Open items needing owner decision

1. Per-admin accounts and real roles (A8). This also fixes the anonymous audit actor.
2. Retire the raw password in `X-Admin-Token` once the two scripts use a session or a scoped key (A9).
3. Pin `--forwarded-allow-ips` to the proxy address and set `ADMIN_ALLOWED_IPS` (C1, A12).
4. Partner daily cap for enterprise plans, and make it atomic (C4).
5. Durable storage for takedown requests (C12, D3), and set `TAKEDOWN_IP_SALT` (C13).
6. Retention purge for `audit_logs` and `analytics_events` (D2), then update the privacy page.
7. Encrypt the cookie pool at rest (D4).
8. Confirm whether `docker-compose.cobalt.yml` was ever run on a public host (A2). If it was, revoke all admin sessions and rotate the cookie pool.
