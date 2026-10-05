# Phase 32C — Design notes (desktop client + delivery routing)

**Tóm tắt cho chủ dự án (VI).** Ghi chú thiết kế cho app desktop Windows (Tauri 2) và việc "định tuyến giao hàng" T0/T1/T2: engine (yt-dlp/ffmpeg đóng gói kèm, kiểm checksum, cập nhật có rollback, kill cả cây tiến trình), luồng nhờ server phân giải (hợp đồng API `/client/resolve`, `/client/version`, `/resolve-link`), đóng gói (khuyến nghị NSIS per-user), bảo mật, telemetry opt-in, cờ rollout và rollback. Mọi thứ **chưa được kiểm chứng** đều gắn nhãn `[UNVERIFIED]` hoặc `[PROPOSAL]`. Đây là tài liệu thiết kế, chưa có code. Suno (32B) đã bị huỷ nên không còn trong phạm vi.

Legend: `[VERIFIED]` = read from code/vendor docs on 2026-10-06. `[PROPOSAL]` = a design choice for the owner to approve. `[UNVERIFIED]` = believed true, not checked. `[PENDING C0]` = depends on experiment results in `C0-RESULTS.md`.

Source plan: `plan32c.md`. Nothing here is implemented.

---

## 1. Delivery routing (T0 / T1 / T2)

### 1.1 Tiers

| Tier | Resolves | Downloads | Merge | Use for |
|---|---|---|---|---|
| T0 server proxy | server | server, then user | server ffmpeg | platforms that work today (cheap/unblocked) |
| T1 direct handoff | server | user's browser from CDN | none (single-stream only) | only platforms with `handoff_ok=true` from C0 |
| T2 local | desktop client | desktop client | local ffmpeg | YouTube, IP-bound platforms, anything needing merge |

### 1.2 Existing backend facts the routing builds on `[VERIFIED]`
- `POST /api/v1/fetch-link` validates the URL with the SSRF guard, enforces tier/quota, then dispatches a server-side download (`backend/app/api/routes.py`). That is T0 today.
- A "cheap" quota bucket exists for `CHEAP_PLATFORMS` (default `tiktok,douyin,threads`, env-overridable) with `GUEST_CHEAP_DAILY=30` and `FREE_CHEAP_DAILY=100` defaults (`backend/app/core/quotas.py`).
- YouTube is gated by `YOUTUBE_ENABLED` (env, with a Redis admin override), a daily proxy-byte ceiling, a circuit breaker and a max proxy height (`backend/app/core/youtube_gate.py`).
- Admission control can reject a platform with reason `platform_disabled` (HTTP 503 `platform_unavailable`) — this is the per-platform kill switch the plan says to reuse (`backend/app/core/admission_control.py`, used from `fetch_link`).
- Related endpoints that already exist and must be reviewed **before** adding `/resolve-link`, to avoid a third overlapping resolver: `POST /api/v1/formats/probe`, `POST /api/v1/resolve-input`, `GET /api/v1/platforms/capabilities`, `GET /api/v1/platform-status` `[VERIFIED to exist; overlap not analysed]`.

### 1.3 Routing rules (from plan, restated)
1. `handoff_ok` per platform is set only from C0 evidence (`C0-RESULTS.md` final table).
2. T1 is never offered for formats that need merging (acceptance criterion 12).
3. T1 403 or expiry → UI offers T2 "open in desktop client" (criterion 11).
4. YouTube: the server returns metadata at most; bytes are downloaded in T2. The existing YouTube gate stays.
5. T0 unchanged for platforms that work today.

### 1.4 Router decision function `[PROPOSAL]`
Evaluated per option, server side, in `/resolve-link`:

```
if option.requiresMerge            -> "local"
elif platform == youtube           -> "local"          # never proxy/handoff bytes
elif DELIVERY_HANDOFF_ENABLED and platform in DELIVERY_HANDOFF_PLATFORMS and option is single-stream
                                   -> "handoff"
else                               -> "proxy"          # T0, unchanged
```
If the platform is killed or the YouTube gate is off, the option is omitted rather than rerouted silently. The client UI shows "open in desktop app" for `local` options when the app is not detected (deep-link detection depends on E5).

### 1.5 `/resolve-link` contract (from plan) `[PROPOSAL, not implemented]`

```
POST /api/v1/resolve-link   body: { "url": string }
 -> { platform, title, thumbnail,
      options: [{ id, label, height, codec, sizeBytes?, hasAudio, requiresMerge,
                  delivery: "proxy" | "handoff" | "local", expiresAt? }] }
```
Notes:
- `expiresAt` only for `handoff`, derived from E2 (`C0-RESULTS.md`), `[PENDING C0]`.
- The response must not contain the CDN URL for `proxy` or `local`. For `handoff` the URL is delivered by a separate short-lived call (open question: inline `directUrl` vs. a follow-up `GET /resolve-link/{id}/url` so the URL is not logged by intermediaries) `[OPEN]`.
- Input URL goes through `app.core.ssrf_guard.assert_safe_url` exactly as `fetch_link` does `[VERIFIED pattern]`.
- Reuse the platform-key detection (`_get_platform_key`) and the same admission/kill switches.
- Anonymous rate limit: reuse slowapi limiter style used by `/fetch-link` (`30/minute`); the number for `/resolve-link` is `[OPEN]`.

---

## 2. Desktop engine (Tauri 2)

### 2.1 Why Tauri
Plan §4: smaller installer, system WebView2, but needs some Rust. If the team has no Rust capacity the plan's fallback is Electron. Keep the Rust surface tiny: spawn, kill tree, folder picker, version `[PROPOSAL from plan]`. The workspace has no Rust toolchain, so nothing in C0 here compiles it; first real build happens on a Windows machine `[VERIFIED constraint]`.

### 2.2 Sidecars
- `yt-dlp`, `ffmpeg`, `ffprobe`, `deno` bundled through `bundle.externalBin` with target-triple suffixed filenames (`yt-dlp-x86_64-pc-windows-msvc.exe`, …) `[plan; naming convention UNVERIFIED against Tauri 2 docs for this exact version — check when scaffold builds]`.
- Pinned versions in a lockfile (`desktop/tools.lock.json` `[PROPOSAL]`): name, version, download URL (official release only), SHA256.
- Build-time: `scripts/fetch-binaries.ts` downloads, verifies SHA256, refuses on mismatch.
- Run-time: before first spawn after install/update, and before every spawn of a binary in a user-writable updated-tools directory, recompute SHA256 and compare to the lock/manifest (acceptance criterion 4: a modified binary is rejected).
- Deno is bundled because current YouTube extraction may need a JS runtime `[plan; flag name verified in E3 setup]`. A PO-token provider is added only if E3 shows it is needed `[PENDING C0]`.
- ffmpeg build source (which static build, license LGPL/GPL implications for redistribution) `[OPEN — needs a licence review before C2]`.

### 2.3 yt-dlp updater with rollback
The server updates yt-dlp daily; mirror that.
1. Check for update at app start and every N hours (`N` `[OPEN]`), only if telemetry/opt-in rules (§7) allow nothing extra to be sent: a plain GET to the official release feed.
2. Download to `tools/staging/`, verify SHA256 against the official `SHA2-256SUMS` file for that release `[VERIFIED the file exists on yt-dlp release pages as of the E3 setup instructions; re-check when implementing]`.
3. Smoke test: run `yt-dlp --version` from staging.
4. Atomic swap: rename `current` → `previous`, staging → `current` (same volume rename). Keep exactly one `previous`.
5. If the next real download fails with a "tool broken" signature right after an update (not a content/network error), roll back to `previous` and pin it until the next release `[PROPOSAL]`.
6. The updater must not replace ffmpeg/deno automatically; those change only with an app release (smaller trust surface) `[PROPOSAL]`.

### 2.4 Process management
- Argument arrays only; no shell, no `cmd /c`, no string concatenation of user input. URLs are passed after `--` so a URL cannot be parsed as an option `[PROPOSAL; standard practice]`.
- Cancel kills the **whole process tree** (yt-dlp spawns ffmpeg). On Windows: create the child in a **Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, or `taskkill /T /F /PID` as fallback. Acceptance criterion 3 requires proof: after cancel during merge, no `ffmpeg.exe` remains and the partial file is deletable `[PROPOSAL; to verify on Windows in C0 scaffold test]`.
- Also kill the tree on app exit/crash (Job Object kill-on-close covers crash).
- Disk space check before start: free space >= expected size x 1.5 + margin when `sizeBytes` is known; otherwise a minimum floor (value `[OPEN]`). Clear error message, no partial file left.
- Expired link mid-transfer: do not resume a stale partial file. Discard the `.part`, re-resolve for a fresh URL, restart (plan WS2.7). Distinguish 403/410 mid-transfer from network drop (a network drop may resume).
- Logs: per-job log file with URLs redacted of query strings; "export logs" button for support.

---

## 3. Server-assisted flow

Some platforms exist only as custom server extractors (Douyin, Bilibili, Xiaohongshu — plan WS3; Suno removed, 32B dropped). The client asks the VidGrab API to resolve and then downloads bytes itself.

### 3.1 Endpoints `[PROPOSAL, from plan; none implemented]`

```
POST /api/v1/client/resolve
  body:  { url, clientVersion, deviceId }
  200:   { platform, title, thumbnail,
           formats: [{ id, label, ext, height, codec, sizeBytes?, directUrl, headers? }],
           expiresAt }
  errors:
    400 invalid url          (SSRF guard rejection reuses existing message path)
    401/403 auth or quota    (reuse ERR_QUOTA_* style used by /fetch-link)
    426 client too old       (clientVersion < CLIENT_MIN_VERSION)       [status code PROPOSAL]
    429 rate limited         (Retry-After)
    503 CLIENT_API_ENABLED=false or platform disabled

GET /api/v1/client/version
  200: { latest, minSupported, notes, downloadUrl }
  (+ channel from CLIENT_UPDATE_CHANNEL: stable | beta)
```
Possible additions to discuss (not in plan): `sha256` of the installer in `/client/version`, so `/install` and the app agree on one source `[OPEN]`.

### 3.2 Authentication options

| Option | How | Pros | Cons |
|---|---|---|---|
| A. Logged-in token | Existing user session token (same one `get_optional_user` reads) | Syncs quota/history; per-user limits | Requires login before first download |
| B. Anonymous device token | Client generates `deviceId` (random UUID, stored locally); server rate-limits by `deviceId` + IP | No friction | `deviceId` is forgeable → IP limit is the real guard; abuse possible |
| C. A + B | Anonymous by default, upgraded when logged in | Matches existing guest/free tiers | Two code paths |

Recommendation `[PROPOSAL]`: **C**, anonymous limits strictly lower than guest web limits until measured. Concrete limits `[OPEN]` — do not invent; derive from observed C1 usage.
The server never trusts `clientVersion` for authorisation, only for upgrade messaging.

### 3.3 Cookies
The client **never sends the user's cookies (or browser cookie files) to the server**. Cookie-gated platforms are resolved locally by the client's own yt-dlp, with cookies read locally only. The privacy text must state this. Server-side extractors that need server-held cookies (pool) continue to use the server's own cookies, never the user's `[plan; consistent with existing platform_probe/cookie-pool design — not re-audited]`.

### 3.4 SSRF rules
Server side `[VERIFIED pattern]`: the submitted page URL passes `assert_safe_url` (resolves host, rejects any non-public address, validates each redirect hop, max 5). Any server-side fetch added for `/client/resolve` must use `safe_stream`/`safe_head` style helpers. Residual DNS-rebinding risk is documented in the module and stays `[VERIFIED statement in code]`.

Client side (new, important because the client runs **inside the user's LAN**) `[PROPOSAL]`:
- `directUrl` must be `https` (http only if a platform proves it needs it, per-platform exception).
- Resolve the host before connecting; reject loopback, RFC1918, link-local, ULA, `169.254.169.254`, reserved ranges — same classes as `ssrf_guard._blocked_reason`.
- Do not follow redirects blindly: follow manually, re-validate each hop, max 5.
- `headers` returned by the server: allowlist of header names (`Referer`, `User-Agent`, `Origin`); drop `Cookie`/`Authorization`.
- A compromised or malicious API response must not be able to point the client at the user's router or intranet.

---

## 4. Packaging (Windows)

### 4.1 NSIS vs MSI — recommendation: **NSIS, per-user (`installMode: currentUser`)**
Why:
- Tauri docs (read 2026-10-06, https://v2.tauri.app/distribute/windows-installer/) state NSIS `currentUser` is the default and installs into `%LOCALAPPDATA%` **without administrator privileges** `[VERIFIED]` — directly satisfies acceptance criterion 1 (no admin prompt) and makes the in-app updater work for non-admin users.
- The same page states MSI packages can only be built **on Windows** (WiX), whereas NSIS supports cross-compilation from Linux/macOS via `cargo-xwin` with limited official support `[VERIFIED statement; not tried]`. This matters because our dev workspace is Linux; we cannot rely on it for C2 because the signing and real tests run on Windows anyway `[PROPOSAL reasoning]`.
- Updater: Tauri updater's Windows `installMode` options are `passive` (default), `basicUi`, `quiet`; `quiet` needs admin **or** a user-wide install `[VERIFIED, https://v2.tauri.app/plugin/updater/]` — per-user NSIS is consistent with silent updates.
- MSI is mainly valuable for enterprise/GPO deployment. If a business customer asks, add MSI later as a separate artifact `[PROPOSAL]`. Not in C0–C3.

### 4.2 WebView2
Tauri offers `downloadBootstrapper` (default, needs internet, no size impact), `embedBootstrapper` (~1.8 MB, internet needed), `offlineInstaller` (~127 MB), `fixedRuntime` (~180 MB), `skip` `[VERIFIED, same URL]`. Recommendation `[PROPOSAL]`: `downloadBootstrapper` for the public installer (smallest), and offer a larger `offlineInstaller` build only if C1 testers report machines without internet during install. Windows 11 and up-to-date Windows 10 normally include WebView2 `[UNVERIFIED]`; test on a clean VM.

### 4.3 Updater keys vs code signing
- Tauri updater signs update artifacts with its **own key pair** generated by `tauri signer generate`; `pubkey` goes into `tauri.conf.json` (content, not a path); the manifest (`version`, `platforms.<target>.url`, `signature`, optional `notes`, `pub_date`) is static JSON `[VERIFIED, updater doc]`.
- These keys are separate from the Windows code-signing certificate `[VERIFIED statement in doc]`. Store the updater private key offline (password-protected), back it up in two places — losing it means existing installs can no longer be updated automatically `[PROPOSAL]`.
- Manifest hosting: the existing API or static hosting; the `/client/version` endpoint carries the human-facing latest/min versions `[plan]`.

### 4.4 Code signing
See `SIGNING-COSTS.md`. C1 ships unsigned (SmartScreen warnings expected `[UNVERIFIED for our build; measure in C1]`); C2 requires signing. The plan says to order the certificate during C1 — validation can take days to weeks (Azure: 1 to 20 business days per Microsoft quickstart; SSL.com: 3–5 days standard, per vendor pages).

### 4.5 Distribution
`/install` page on the website: direct download + SHA256 + system requirements + support doc link. Microsoft Store: optional later `[plan]`.

### 4.6 Build commands
Provided with the scaffold (deliverable 4 of the plan), not repeated here to avoid drift. They run on Windows `[constraint]`.

---

## 5. Security checklist (WS5)

| # | Item | Status |
|---|---|---|
| 1 | Webview may call only an explicit allowlist of Tauri commands: `start_download`, `cancel_download`, `pick_folder`, `get_version` (+ later `open_logs`). No generic shell/exec/fs bridge | `[PROPOSAL]` |
| 2 | Tauri capability/permission files grant only those commands; sidecar execution permission scoped to the four pinned binaries with fixed argument templates — arbitrary `args` not allowed from JS | `[PROPOSAL]`, syntax `[UNVERIFIED]` for the Tauri version used |
| 3 | Strict CSP: `default-src 'self'`; no remote scripts; `connect-src` limited to the VidGrab API host(s); no `unsafe-eval` | `[PROPOSAL]` |
| 4 | Webview loads bundled assets only (no remote URL as app origin) | `[PROPOSAL]` |
| 5 | Host allowlist for server-assisted calls: the app only talks to `dvid-api.vibe1.tinhgon.xyz` (and update host); configurable via build constant, not user input | `[PROPOSAL]` |
| 6 | Client-side SSRF rules for `directUrl` (§3.4) | `[PROPOSAL]` |
| 7 | Checksums of bundled tools verified at build and at run (§2.2); updates verified against vendor checksum and Tauri signature | `[PROPOSAL]` |
| 8 | No shell; argument arrays; `--` before URLs; output path built from sanitised filename, confined to the chosen folder (no `..`, reserved Windows names, no ADS `:`) | `[PROPOSAL]` |
| 9 | `vidgrab://` handler treats input as untrusted; confirmation before download (E5) | `[PROPOSAL]` |
| 10 | Privacy statement: sent = URL, client version, device id (for server-assisted calls); not sent = cookies, local files, download contents | `[plan]` |
| 11 | Secrets: no API keys or server cookies in the app bundle | `[PROPOSAL]` |
| 12 | Security review checklist signed before C2 (acceptance criterion 6) | pending |

---

## 6. Rollout flags and rollback

| Control | Purpose | Notes |
|---|---|---|
| `CLIENT_API_ENABLED` | server accepts `client/resolve` | default false until C1 |
| `CLIENT_MIN_VERSION` | refuse old clients | message shown in the app |
| `CLIENT_UPDATE_CHANNEL` | stable / beta | |
| `DELIVERY_HANDOFF_ENABLED` | master T1 switch | default false |
| `DELIVERY_HANDOFF_PLATFORMS` | comma list | default empty; filled only from `C0-RESULTS.md` |
| Per-platform kill switch | existing web switches | reused `[VERIFIED mechanism exists]` |

Rollback: `CLIENT_API_ENABLED=false` and `DELIVERY_HANDOFF_ENABLED=false` → web app, extension and PWA are unchanged (criterion 8, to be tested). Old clients show a clear message. The existing flags in this codebase are env-driven (YouTube also has a Redis override); whether the new flags also get a Redis/admin override is `[OPEN]` — recommended, so rollback needs no redeploy.

Staged rollout: C1 internal (3–5 testers) → C2 invite-only signed beta → C3 public + `/install`. T1 per platform is enabled one platform at a time, with success/403 rates watched (telemetry opt-in, §7) and a one-line revert (remove from `DELIVERY_HANDOFF_PLATFORMS`).

Container deploy note (workspace policy): any server container deployed for this phase needs the `mb.env` / `mb.owner` labels and the restart command documented in `docs/DEPLOY.md` `[org policy]`; nothing is deployed by C0.

---

## 7. Telemetry (opt-in)

- Off by default; explicit toggle; acceptance criterion 9 tests it (no network call to the telemetry endpoint before opt-in).
- Fields (plan): client version, OS version, crash count, update adoption. Admin panel: version distribution and download share by tier (T0/T1/T2).
- T1/T2 outcome events (success / 403 / expired / fallback-to-T2) are what we need to validate `handoff_ok` in production; they require an additional opt-in-covered field set `[PROPOSAL]` — list them in the privacy text.
- No URLs, titles or file names in telemetry `[PROPOSAL]`.
- Server-side tier share (T0 from existing logs, T1/T2 from `/client/*` calls) can be computed without client telemetry for T0; T2 completion needs the client `[observation]`.

---

## 8. Open questions for the owner

1. Is a Rust-capable person available, or do we plan Electron as the fallback?
2. Who owns the legal entity that will buy the signing certificate, and in which country (affects eligibility, see `SIGNING-COSTS.md`)?
3. Approve proposals: E2 `expiresAt = 50%` rule; E4 threshold = 50% of measured max; one `previous` yt-dlp version kept.
4. Which ffmpeg build to redistribute (licence review).
5. Anonymous device-token limits for `/client/resolve`.
6. Does `/resolve-link` extend `/formats/probe` / `/resolve-input` rather than being a third resolver?
