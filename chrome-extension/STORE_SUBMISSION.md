# VidGrab Chrome Web Store — Submission Package

Version: **5.1.0** | Manifest V3 | Last updated: 2026-07-07

---

## 1. Store Listing Copy

### Extension Name (max 45 chars)
```
VidGrab - Video Downloader
```

### Short Description (max 132 chars)
```
Download videos from YouTube, TikTok, Instagram, Facebook, Douyin, Threads & Reddit. Batch channel download, MP3 extract.
```
*(121 chars — within limit)*

### Long Description
```
VidGrab lets you download videos from the most popular platforms with one click — no sign-in required for basic use.

★ SUPPORTED PLATFORMS
• YouTube — HD, 4K, MP3 audio extract, subtitles
• TikTok / Douyin — removes watermark automatically
• Instagram — Reels, stories, posts
• Facebook — public videos
• Twitter / X — videos and GIFs
• Reddit — hosted & linked videos
• Threads — public posts
• Spotify — playlist / album to MP3 (via search)
• Pinterest — video pins

★ KEY FEATURES
• Single video download — click the button, file saves to VidGrab/ folder
• Channel / playlist bulk download — scrape a full channel or playlist queue
• Background downloads — download continues even when popup is closed
• Batch progress tracking — see live job stats for bulk downloads
• Format chooser — pick 1080p / 4K / 720p / MP3 per video
• Subtitle download — SRT alongside the video
• No watermark mode — TikTok / Douyin watermark-free copy
• Download history — local log of last 100 downloads
• Archive — save to cloud history when logged in
• Right-click context menu — download any supported link
• Dark / light theme

★ PRIVACY FIRST
VidGrab does not collect personal data. All download processing happens on your own backend server (self-hosted) or the VidGrab cloud instance. No browsing history is tracked or sent anywhere. See our Privacy Policy for details.

★ HOW IT WORKS
The extension sends the current page URL to a VidGrab backend server (configurable in Settings). The server uses yt-dlp to extract and process the video, then the extension downloads the resulting file directly to your computer via Chrome's built-in download manager.

★ SELF-HOSTED FRIENDLY
Point the extension at your own VidGrab server via Settings → Server URL. The backend is open-source and runs on any Docker host.

★ MANUAL INSTALL
If you prefer not to use the Chrome Web Store, install from the GitHub releases page (CRX file) or load unpacked from the source ZIP.
```

### Category
`Productivity`

### Language
Primary: English | Secondary: Vietnamese

---

## 2. Permission Justification Table

| Permission | Why it's needed | Required? |
|---|---|---|
| `activeTab` | Read the URL of the current tab to know what video page the user is on | Required |
| `tabs` | Listen to tab navigation events (badge update); send messages to content scripts; get active tab info | Required |
| `downloads` | Trigger file downloads to the user's Downloads folder; track download progress and state | Required |
| `storage` | Store user settings (API base URL, theme preference, auth token, download history) in sync & local storage | Required |
| `scripting` | Inject content.js into a page when it hasn't loaded yet (user opens popup before page finishes loading) | Required |
| `notifications` | Show a Chrome notification when a background download completes (popup may be closed) | Required |
| `contextMenus` | Add "Download with VidGrab" right-click menu item on supported video pages | Required |
| `alarms` | Keep the service worker alive during long-running background downloads (MV3 SW can be suspended after ~30s idle) | Required |

### Host Permissions Justification

| Host Permission | Why it's needed |
|---|---|
| `*://*.tiktok.com/*` | Detect video/channel pages; inject content script; watermark removal |
| `*://*.youtube.com/*` | Detect video/channel/playlist pages; inject content script |
| `*://*.facebook.com/*` | Detect video pages; inject content script |
| `*://*.douyin.com/*` | Detect channel pages; inject interceptor.js in MAIN world to capture API responses |
| `*://*.instagram.com/*` | Detect Reel/story/post pages; inject content script |
| `*://open.spotify.com/*` | Detect playlist/album pages; inject content script to gather track list |
| `*://*.twitter.com/*` | Detect video tweets; inject content script |
| `*://*.x.com/*` | Twitter/X new domain — same as twitter.com |
| `*://*.reddit.com/*` | Detect video posts; inject content script |
| `*://*.pinterest.com/*` | Detect video pins; inject content script |
| `*://*.threads.com/*` | Detect Threads posts; inject content script |
| `*://*.threads.net/*` | Threads API subdomain — same content script scope |
| `*://*.youtu.be/*` | YouTube short-link domain — same content script scope as youtube.com |
| `*://*.bilibili.com/*` | Detect video pages; inject content script |
| `*://b23.tv/*` | Bilibili short-link domain — same content script scope |
| `*://*.xiaohongshu.com/*` | Detect post/video pages; inject content script |
| `*://*.xhslink.com/*` | Xiaohongshu short-link domain — same content script scope |
| `*://*.lemon8-app.com/*` | Detect post pages; inject content script |
| `*://*.lemon8.app/*` | Lemon8 alternate domain — same content script scope |
| `*://*.snapchat.com/*` | Detect Spotlight/story video pages; inject content script |
| `*://story.snapchat.com/*` | Snapchat story subdomain — same content script scope |
| `*://*.vk.com/*` | Detect video pages; inject content script |
| `*://*.twitch.tv/*` | Detect clip/VOD pages; inject content script |
| `*://*.rumble.com/*` | Detect video pages; inject content script |
| `*://*.odysee.com/*` | Detect video pages; inject content script |
| `*://*.dailymotion.com/*` | Detect video pages; inject content script |
| `*://*.soundcloud.com/*` | Detect track/playlist pages; inject content script (audio download) |

**Note on the backend domain:** the API server (default `https://dowload-video.mk.dev.matbao.ai`, user-configurable in Settings → Server URL) is intentionally **not** declared in `host_permissions`. The service worker's `fetch()` calls to it rely on the backend's own CORS headers rather than a static permission grant, keeping the permission footprint limited to exact platform domains with no wildcard or backend-domain access.

---

## 3. Privacy Policy

*(Host this at a public URL before submitting — e.g. https://dowloadvideo.io.vn/privacy)*

---

**VidGrab Extension — Privacy Policy**
*Effective date: 2026-06-22*

**Data collected by the extension**

VidGrab does not collect, transmit, or sell any personal data.

- **Browsing history**: Not collected. The extension only reads the URL of the active tab when you click the download button or open the popup, and only on supported platforms.
- **Video URLs**: Sent to the configured backend server solely to perform the download you requested. Not stored by the extension itself.
- **Auth token** (optional): If you log in on the VidGrab website, an access token is stored in `chrome.storage.local` on your device only. It is never sent to any third party.
- **Download history**: Stored locally in `chrome.storage.local` on your device. Not synced or transmitted.
- **Settings** (API base URL, theme): Stored in `chrome.storage.sync` so they follow you across Chrome devices. Only your VidGrab server URL is stored — never URLs of pages you visited.

**Content scripts**

Content scripts run on supported video platforms to detect the page type and collect video metadata visible in the page DOM. This data is used only to populate the extension popup and is never transmitted anywhere.

The Douyin interceptor (`interceptor.js`) runs in the MAIN world to capture Douyin's API responses for channel scraping. No data is stored or sent outside the VidGrab backend you configure.

**Backend server**

The VidGrab backend server processes download requests. If you use the default cloud server, it is subject to the VidGrab Terms of Service. If you self-host, you control all data. The extension only communicates with the URL you configure in Settings.

**Third parties**

VidGrab does not share data with any third-party analytics, advertising, or tracking services.

**Contact**

Questions: open an issue at the VidGrab GitHub repository or contact the developer via the Chrome Web Store support page.

---

## 4. Review Notes (paste into "Notes for reviewer" field)

```
This extension downloads publicly accessible videos from supported platforms.

Key implementation details:

1. SERVICE WORKER (background.js):
   - No eval(), no remote code loading.
   - Uses chrome.alarms (1-min heartbeat) to prevent MV3 SW suspension during
     long background downloads.
   - All fetch() calls are to the user-configured backend (same origin as the
     host_permissions domain). No third-party data collection.

2. CONTENT SCRIPTS (content.js, interceptor.js):
   - content.js: runs at document_idle on supported platforms, reads DOM to
     detect page type (video/channel/playlist) and current URL only.
   - interceptor.js: runs at document_start in MAIN world on douyin.com ONLY.
     It patches window.fetch to intercept Douyin's own API responses for
     channel-mode scraping. This is necessary because Douyin's video list API
     is called by its own page JS — intercepting fetch is the only reliable way
     to capture it. No data is exfiltrated; captured video IDs are sent only
     to the user's configured VidGrab backend.

3. chrome.downloads.download():
   - All downloads go to the user's VidGrab/ subfolder inside Downloads.
   - saveAs: false for automated background downloads (standard downloader).
   - saveAs: true only for format-chooser and right-click context menu.

4. PERMISSIONS:
   - Every permission is used actively. The permission table is in the
     submission notes attached to this review.
   - No permission is requested beyond what is actively used.

5. HOST PERMISSIONS:
   - Scoped to exact platform domains. No wildcard (*://*/*).
   - Backend domain is in host_permissions to allow the service worker to
     fetch from it without CORS restriction (user-configurable in Settings).
```

---

## 5. Release & Update Strategy

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
3. Run: `zip -r vidgrab-v4.9.0.zip chrome-extension/ -x "*.DS_Store" -x "*.md"`
4. Upload ZIP to Chrome Web Store Developer Dashboard
5. Fill "What's new" field with changelog
6. Submit for review (typically 1–3 business days)

### Manual Install (backward compat preserved)
Sideloaded CRX / unpacked installs continue to work indefinitely.
The `update_url` in manifest.json points to Google's update service — auto-populated by the Store.

---

## 6. Review Checklist

### Before submitting

**Code**
- [x] Version bumped in `manifest.json` (4.9.0)
- [x] `minimum_chrome_version` set to `"102"`
- [x] `alarms` permission added (for SW keepalive)
- [x] No `eval()`, `new Function()`, or `document.write()` in any JS file
- [x] No remote code loading
- [x] All permissions used and justified in Section 2

**Store assets**
- [ ] Privacy Policy published at a public URL
- [ ] Privacy Policy URL entered in Developer Dashboard
- [ ] Screenshots uploaded (min 3 × 1280×800 PNG) — see Section 6.1
- [ ] Promotional tile uploaded (440×280 PNG)
- [ ] Store listing description filled (copy from Section 1)
- [ ] Category: Productivity
- [ ] Support URL set
- [ ] "Notes for reviewer" filled (copy from Section 4)

### 6.1 Screenshots to capture

| # | What to show | Resolution |
|---|---|---|
| 1 | Popup open on a YouTube video page — quality selector + download button | 1280×800 |
| 2 | Download in progress (status bar + elapsed timer) | 1280×800 |
| 3 | Channel bulk mode — YouTube channel with video count + discovery radar | 1280×800 |
| 4 | Settings panel — Server URL + version info + auth status | 1280×800 |
| 5 | History tab — list of downloaded videos | 640×400 |

### Post-submit watchpoints
- If rejected for `tabs` permission: cite badge update + content script messaging use case
- If rejected for MAIN world script: cite Section 4, point 2 (Douyin API interception)
- If rejected for privacy policy: verify URL is publicly reachable (not behind auth)

---

## 7. Backward Compatibility Notes

### API contract (extension ↔ backend)

| Endpoint | Request shape | Response shape |
|---|---|---|
| `POST /api/v1/fetch-link` | `{url, quality, remove_watermark, download_subs}` | `{success, direct_mp4_url, local_file_path, local_mp3_path, title, thumbnail_url, ...}` |
| `POST /api/v1/bulk-download` | `{urls, quality, channel_mode, max_videos}` | `{success, batch_id}` |
| `GET /api/v1/ping` | — | `{status: "ok"}` |
| `GET /health` | — | `{status, ytdlp_version, disk}` |

If backend changes field names, bump extension MINOR version and update this table.

### Auth bridge
The `connect_extension=1` login flow is opt-in. Extension works without login.

---

## 8. Remaining Debt (pre-public-launch TODO)

| Item | Priority | Notes |
|---|---|---|
| Privacy Policy page not yet live | **HIGH** | Required before store submission; deploy to backend or static page |
| Screenshots not yet captured | **HIGH** | Required for submission |
| Promotional tile not created | Medium | Required for featured placement |
| Download progress setInterval is best-effort in MV3 | Low | Chrome can suspend SW mid-interval; fine progress (500ms) stays as-is — alarm keepalive (1 min) is the practical fix |
| Offscreen document for persistent SW | Low | Alternative to alarms keepalive; adds `offscreen` permission; defer |
