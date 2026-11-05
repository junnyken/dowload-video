# Developer Dashboard > Privacy practices: Justifications (v5.23.0)

Paste each block below into the corresponding field in the Chrome Web Store Developer Dashboard under **Privacy practices**.

## Single purpose
```
VidGrab detects video streams played on the web page the user is viewing and saves them to the user's computer.
```

## Permission Justifications

### `activeTab`
```
Reads the URL and title of the active tab when the user clicks the VidGrab toolbar icon so the extension can detect and save the video on that page.
```

### `tabs`
```
Reads tab URL and title to (a) update the toolbar badge when playable media is detected on the current tab, (b) match a user-initiated download to the active tab, and (c) open a helper tab/window during batch channel downloads or account sign-in. Browsing history is never collected or transmitted.
```

### `downloads`
```
Saves the user-selected video or audio file into the user's Downloads folder (subfolder VidGrab/) and tracks download progress and resume state.
```

### `storage`
```
Stores user preferences in chrome.storage.sync, temporary per-tab detected media lists in chrome.storage.session (cleared when the tab closes), and local download history plus optional VidGrab account session token in chrome.storage.local.
```

### `scripting`
```
Injects the extension's bundled content script into the active tab when the user opens the popup so video metadata and page streams can be detected without requiring a page reload. No remote code is used.
```

### `notifications`
```
Displays desktop notifications when a background or batch video download completes or fails while the popup is closed.
```

### `contextMenus`
```
Adds "Download with VidGrab", "Download MP3", and "Download page media" options to the right-click context menu on pages, links, and media elements.
```

### `alarms`
```
Keeps the Manifest V3 service worker responsive during active downloads, polls status for in-progress batch jobs, and refreshes the optional 30-day account session.
```

### `cookies`
```
Used only if the user explicitly turns on "Use browser cookies for videos that require sign-in" in Settings (off by default). When enabled and the user initiates a download from a login-gated social platform (Facebook, Instagram, X, Reddit, Bilibili, Xiaohongshu, Lemon8, or Douyin), the extension reads cookies for that single domain and sends them over HTTPS exclusively to VidGrab's first-party API for that one-time extraction, after which the server immediately deletes them.
```

### `webRequest`
```
Observe-only (non-blocking): watches media response headers (Content-Type and Content-Length for MP4, HLS .m3u8, and DASH .mpd streams) in the active tab to list playable videos in the popup. Captured metadata stays in local session storage and is removed when the tab closes.
```

### `offscreen`
```
Creates a temporary offscreen document (reason BLOBS) only during user-requested actions to merge HLS segments into an MP4 file (using bundled mux.js) or hold a local MediaRecorder stream when recording a tab. Closed immediately when idle.
```

### `nativeMessaging`
```
Communicates with the optional companion VidGrab Windows desktop application installed on the user's own computer to hand off high-resolution or DASH stream downloads locally.
```

### `declarativeNetRequestWithHostAccess`
```
Applies temporary, scoped Referer/Origin headers required by certain social media CDNs (such as Douyin/ByteDance CDN hosts) only while the browser is saving a user-requested video file from those hosts.
```

### `optional_permissions`: `clipboardRead`
```
Requested at runtime only when the user clicks the "Paste" button in the popup to paste a video link from the clipboard.
```

### `optional_permissions`: `tabCapture`
```
Requested at runtime only when the user clicks the "Record tab" button to record a tab's media playback into a local WebM file. Never uploaded anywhere.
```

### `optional_host_permissions`: `*://*/*`
```
Not granted at install. Requested at runtime only when the user clicks "Allow grabbing videos on every site" in the popup, allowing the media sniffer to detect video files served from third-party CDN domains.
```

### `host_permissions`
```
Grants access to supported social media platforms (TikTok, Douyin, Facebook, Instagram, X/Twitter, Reddit, Pinterest, Threads, Bilibili, Xiaohongshu/RedNote, Lemon8, Snapchat, VK, Twitch, Rumble, Odysee, Dailymotion, SoundCloud, Spotify), their media CDNs, and VidGrab's own first-party domains (dowloadvideo.io.vn, dvid.vibe1.tinhgon.xyz, dvid-api.vibe1.tinhgon.xyz) so the extension can detect video streams on the page, download media directly in the browser, and sync user account status.
```

## Remote code
- Select: **No, I am not using remote code.**
- Justification:
```
All JavaScript and CSS assets are bundled inside the extension package, including open-source libraries lib/mux.min.js (Apache-2.0) and lib/qrcode.js (MIT) and local Inter web fonts. No external scripts, eval(), or remote code execution are used.
```

## Data Usage & Certifications
- Check **Authentication information** (for optional account sign-in token, device ID for account security, and opt-in ephemeral cookies for login-gated videos).
- Check **Website content / Web history** (only the specific video page URL when the user clicks download; never browsing history).
- Certify all 3 Limited Use checkboxes:
  1. I do not sell or transfer user data to third parties, outside of the approved use cases.
  2. I do not use or transfer user data for purposes that are unrelated to my item's single purpose.
  3. I do not use or transfer user data to determine creditworthiness or for lending purposes.
- Privacy Policy URL: `https://dowloadvideo.io.vn/en/privacy`
