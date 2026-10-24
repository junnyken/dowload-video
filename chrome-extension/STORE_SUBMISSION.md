# VidGrab Chrome Web Store - Submission Package

Version: **5.20.0** | Manifest V3 | Last updated: 2026-10-23

Listing texts and answers live in `store-assets/`:
- `listing-en.md`, `listing-vi.md`: name, summary, description, category, single purpose
- `permissions.md`: justification for every permission / host permission / remote code
- `privacy-checklist.md`: data-usage questionnaire, every endpoint, policy gaps (TODO)

The older text in this file (5.1.x listing, permission table) is superseded by those files.

---

## 1. Owner checklist (step by step)

1. **Developer account**: https://chrome.google.com/webstore/devcenter , sign in with the Google account that will own the extension, pay the one-time US$5 registration fee, verify the contact email (and the trader/non-trader declaration in Account; INFERRED: required for EU visibility).
2. **Fix the privacy policy first** (see `privacy-checklist.md` section 5): at least the Limited Use sentence, tabCapture/recording, webRequest, cookies domain list, 30-day session. Then check https://dvid.vibe1.tinhgon.xyz/privacy and /en/privacy return 200 without login.
3. **Build the store zip** (from the repo root):
   `python3 scripts/package-extension.py --store`
   Output: `chrome-extension/store-assets/VidGrab-store-v5.20.0.zip` (manifest without YouTube hosts/matches; the script verifies it). Bump `version` in manifest.json before every resubmission; the store rejects a repeated version.
   **Upload only `VidGrab-store-v<version>.zip`.** Never upload `backend/extension/VidGrab-extension.zip` (website build, has YouTube) or the old store zips (5.14.1 ... 5.19.0).
4. Dashboard > **Add new item** > upload the zip.
5. **Store listing** tab: paste name, summary, description from `listing-en.md` (default language) and add Vietnamese from `listing-vi.md`; category Productivity; upload screenshots (section 3), promo tile `store-assets/promo-tile-440x280.png` (small promo tile 440x280; check it has no YouTube and matches 5.20.0 before using, it dates from 11/08/2026); support URL / website: https://dvid.vibe1.tinhgon.xyz .
6. **Privacy practices** tab: single purpose, each permission justification, remote code = No, data usage boxes, certifications, privacy policy URL `https://dvid.vibe1.tinhgon.xyz/privacy` (all from `permissions.md` and `privacy-checklist.md`).
7. **Distribution**: Public (or Unlisted for a first test), all regions, free.
8. "Notes for reviewer" (section 5 below). Submit for review. Typical wait 1-3 business days, longer with broad host permissions (INFERRED).
9. After approval: copy the store item ID into the website "install" button and the extension page.

## 2. Packaging facts (verified in scripts/package-extension.py)
- `--store` keeps the same files as the website build (excludes `store-assets/`, `tests/`, `STORE_SUBMISSION.md`, dotfiles) and only rewrites manifest.json: removes `*youtube.com` / `*youtu.be` from `host_permissions` and from every `content_scripts[].matches`. It refuses to finish if a YouTube host remains.
- Source files are NOT changed for the store build. YouTube strings remain in popup.js/content.js (the popup shows a "use the VidGrab website" note and refuses the link). **Risk**: a reviewer who greps the code finds YouTube names. Mitigation: it is refusal logic only, and the listing never mentions YouTube. If a reviewer objects, strip those branches from the store build.

## 2b. Changes in 5.20.1 for the review (24/10/2026)
- `tabCapture` moved to **optional_permissions**: asked only when the person presses "Record tab" (no install-time warning for it).
- Sign-in cookies: a **switch in Settings**, **off by default in the Web Store build** (on by default only in the website build). Update the `cookies` justification: "used only if the user turns on 'Use the browser’s cookies for videos that need sign-in'".
- YouTube: the store build refuses YouTube on every path (popup, list, right-click menu, server fallback) and no longer offers "open on the VidGrab website" for a YouTube link. The remaining "YouTube" strings in the code are this refusal logic.
- Checked by `chrome-extension/tests/test_store_build.py` (builds and loads the real store zip).

## 3. Screenshots to capture (up to 5, 1280x800 or 640x400, PNG/JPEG, no alpha)
**Done (24/10/2026):** `store-assets/screenshots-5.20/1-video-on-page.png`, `2-preview.png`, `3-channel-list.png`, `4-progress.png`, `5-send-to-phone.png` — 1280x800 PNG, English UI of 5.20.0, own sample pages/files only, no YouTube, no platform logos. Upload these five; do not upload anything from the old `screenshots/` folder.

The existing `store-assets/screenshots/` (11/08/2026) are outdated: `01-youtube-quality.png` shows YouTube and MUST NOT be used. Capture after the English UI lands, on a clean profile, no personal data, no YouTube anywhere (tabs, bookmarks).
1. **Video on the page**: popup on a page with an MP4 and an HLS stream listed (quality, length), "download to this computer" button visible.
2. **Preview before download**: preview/size of the file, file name template visible.
3. **Channel scan**: Douyin/TikTok channel, scan with Stop, list with checkboxes, "download N videos to this computer".
4. **Download in progress / HLS joined into MP4** (progress bar) plus finished notification.
5. **Account and settings**: signed-in state (30-day session), QR code to the phone, DRM/live marked as unsupported (shows the honest limits).
Use content you own or public-domain/test videos (e.g. a sample MP4 you host); avoid brand logos of platforms in a way that implies endorsement (INFERRED).

## 4. What reviewers usually reject for downloaders, and how this build avoids it
| Rejection reason (INFERRED from Web Store policy and common feedback) | This build |
|---|---|
| YouTube downloading (explicitly forbidden) | store zip has no YouTube host/matches; listing says it does not support YouTube |
| Circumventing DRM / "bypass" language | DRM and live are detected, marked and never downloaded; no such wording; recording mode does not avoid DRM (black picture) |
| Misleading/keyword-stuffed listing, unverifiable claims | no numbers, no "best/fastest", platform list limited to what the manifest covers |
| Excessive permissions | each permission justified; all-sites is optional, asked at runtime; `tabCapture`/`cookies` are the ones most likely to be questioned (section 6) |
| Remote code | none; mux.js and qrcode.js bundled with licences |
| Privacy policy missing/not matching data use | policy URL live; update it per `privacy-checklist.md` before submit |
| Single purpose unclear | one sentence, all features serve "find the page's video and save it" |
| Copyright | content-rights statement in the listing; takedown channel exists on the website |

## 5. Notes for reviewer (paste)
```
VidGrab detects the video the current page plays and saves it to the user's computer. Test: open any page with a public MP4 (e.g. a sample file), click the icon, the list shows the file; press the download button.
- Store build has NO YouTube support.
- DRM/encrypted and live streams are detected and refused.
- No remote code. Bundled: lib/mux.min.js (mux.js, Apache-2.0), lib/qrcode.js (MIT).
- webRequest is observe-only (no blocking). The list is kept in chrome.storage.session.
- *://*/* is optional; requested only when the user clicks "Allow grabbing videos on every site".
- tabCapture is used only after the user presses "Quay tab" (recording a tab to a local WebM file).
- cookies: only for the sign-in-gated site the user is downloading from; sent only to VidGrab's own API (https, first-party hosts) for that one extraction.
- interceptor.js (MAIN world) runs only on the listed social sites to read the page's own video-list responses; nothing is sent to third parties.
- Sign-in with the VidGrab website is optional. Test account on request: <owner to add credentials if the reviewer needs them>.
```

## 6. Policy risks to watch (flagged for the owner)
1. **cookies + facebook/instagram/x/reddit hosts**: sending the user's session cookies to our server is the most sensitive item (Web Store treats authentication data as sensitive). It is limited to first-party hosts and one-time use; the policy must state it exactly. If review pushes back, make it an explicit opt-in toggle (code change, not done here).
2. **tabCapture** and **MAIN-world scripts on social sites**: likely to trigger extra review; justification ready.
3. **Optional `*://*/*`**: may lengthen review; keep optional.
4. Granting host permissions for 26 platforms at install: expect a "broad host" warning in the install dialog.
5. Policy page older than the code (13/10/2026) - see TODOs.
6. popup.html imports Google Fonts: third party contact not covered by the policy.

---

## 7. Release & Update Strategy

### Version Numbering
`MAJOR.MINOR.PATCH` — e.g. `4.9.0`

| Bump | When |
|---|---|
| PATCH | Bug fixes, copy changes |
| MINOR | New platform support, UI improvements, new features |
| MAJOR | Breaking backend API changes, manifest format changes |

### Backend Compatibility Indicator
The extension Settings panel shows:
- Extension version (from `chrome.runtime.getManifest().version`)
- Backend yt-dlp version (from `GET /health`)
- Backend status: green/offline

### Update Flow
1. Bump version in `manifest.json`
2. Update this file's version header
3. Run `python3 scripts/package-extension.py --store` and upload the store zip (section 2)
4. Upload ZIP to Chrome Web Store Developer Dashboard
5. Fill "What's new" field with changelog
6. Submit for review (typically 1–3 business days)
7. **Regenerate the self-hosted download ZIPs too** (easy to forget — found
   stuck at v4.9.5 on 2026-08-13 while the source had moved to v5.1.7, so
   the "Tải Extension (.ZIP)" button on `/extension` and the Telegram bot
   delivery were both serving a many-versions-stale build for who knows how
   long): from `chrome-extension/`, run
   `zip -r VidGrab-extension.zip . -x "*.md" -x "*.DS_Store" -x "*.git*" -x "store-assets/*" -x "tests/*"`
   and overwrite both `backend/extension/VidGrab-extension.zip` (served by
   `GET /api/v1/extension/download`, checked first at the mounted path
   `/app/extension/VidGrab-extension.zip`) and
   `telegram-bot/extension/VidGrab-extension.zip`. Neither is auto-built —
   both are static files that only change when someone manually redoes
   this step.

   Those two paths are now the ONLY packages in the tree, and every
   docker-compose mount points at one of them, so redoing this step is
   sufficient. It did not used to be: `docker-compose.small.yml` (twice) and
   `docker-compose.enterprise.yml` mounted `./chrome_extension_v4.9.5.zip`
   from the repo root over `/app/extension/VidGrab-extension.zip` — a third
   copy, outside this checklist, holding a build from two versions earlier
   than its own filename claimed. Anyone deploying from those files served
   that stale build no matter how carefully this step was followed. The root
   zips have been deleted; do not reintroduce one. If you add a new deploy
   target, mount `backend/extension/VidGrab-extension.zip` rather than
   copying a zip somewhere new.

### Manual Install (backward compat preserved)
Sideloaded CRX / unpacked installs continue to work indefinitely.
The `update_url` in manifest.json points to Google's update service — auto-populated by the Store.

---


---

## 8. Backward Compatibility Notes

### API contract (extension ↔ backend)

| Endpoint | Request shape | Response shape |
|---|---|---|
| `POST /api/v1/fetch-link` | `{url, quality, remove_watermark, download_subs}` | `{success, direct_mp4_url, local_file_path, local_mp3_path, title, thumbnail_url, ...}` |
| `POST /api/v1/bulk-download` | `{urls, quality, channel_mode, max_videos}` | `{success, batch_id}` |
| `GET /api/v1/jobs/{batch_id}` | — | batch job status (polled by the background batch tracker, see below) |
| `POST /api/v1/analyze-media` | `{url, media_path, analyses[], duration_hint_s}` | `{job_id, status}` |
| `GET /api/v1/analyze/{job_id}` | — | `{status, trim_suggestions[], clip_suggestions[], gif_suggestions[], ...}` |
| `GET /api/v1/ping` | — | `{status: "ok"}` |
| `GET /health` | — | `{status, ytdlp_version, disk}` |

If backend changes field names, bump extension MINOR version and update this table.

### AI Smart Suggestions (added 5.1.2)
After a single download that produced a server-local file (`local_file_path` starting with `/app/downloads/`), the popup's "🤖 AI Gợi ý" button calls `analyze-media` and polls `analyze/{job_id}` (same contract the web app's `SmartActionsPanel` uses) to show top trim/highlight/GIF suggestions inline. The button stays hidden for stream-only downloads (no local file to `ffprobe`) — those still work via the "Xem chi tiết & áp dụng trên Web →" link, which opens the full web app. A separate "💬 Dịch phụ đề" button opens the web app's `/transcript-translate` page directly (`chrome.tabs.create`, no new host_permission needed — `tabs` already covers it).

### Batch tracking (added 5.1.1 → hardened in 5.1.2 lineage)
The background service worker tracks in-flight `bulk-download` batches (`chrome.storage.local`, keyed by `batch_id`) and polls `GET /api/v1/jobs/{batch_id}` on a `chrome.alarms` 1-minute tick, firing a `chrome.notifications` toast when a batch reaches a terminal state — this works even if the popup is closed. `popup.js`'s own foreground poller uses adaptive backoff (2s → 5s after 30s → 10s after 2min) instead of a fixed interval to reduce request volume on long batches.

### Auth bridge
The `connect_extension=1` login flow is opt-in. Extension works without login.

---

