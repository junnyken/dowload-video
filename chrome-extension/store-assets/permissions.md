# Developer Dashboard > Privacy practices: justifications (build 5.20.0, store build)

Paste each block into the matching field. Verified against chrome-extension/manifest.json, background.js, popup.js,
media-sniffer.js, recorder.js. "INFERRED" marks reasoning not directly checkable in code.
Store build = `python3 scripts/package-extension.py --store` (same manifest minus `*.youtube.com` / `*.youtu.be` hosts and matches).

## Single purpose
VidGrab finds the video the page the user is viewing plays and saves it to the user's computer.

## Permissions

activeTab
: Reads the URL/title of the tab the user opens the popup on, to know which page to look for a video on. Does not read page content of other tabs and does nothing in the background.

tabs
: Reads URL/title of tabs to (a) set the toolbar badge for the tab that has videos, (b) match a download request to the tab that plays it, (c) open the VidGrab website (login, QR page) and a helper tab/window for a channel download. Not used to collect browsing history; nothing about tabs is sent to a server except the page URL the user asks to download.

downloads
: Saves the file the user chose into the Downloads folder (subfolder VidGrab/) and follows progress/interrupted state for resume. Does not download anything on its own.

storage
: Keeps settings, the list of videos found on a tab (chrome.storage.session, deleted with the tab), a local history, and the VidGrab session token (chrome.storage.local). Settings only in storage.sync.

scripting
: Injects the extension's own bundled content script into the page the user opened the popup on, when it has not loaded yet. No remote code.

notifications
: Shows "download finished/failed" notices, also when the popup is closed.

contextMenus
: Right-click items "Download with VidGrab" / "Download MP3" / "Download page media" on a page, link or video.

alarms
: Keeps the service worker alive while a download/queue runs, polls a bulk job (1 min) and renews the extension's 30-day session (every 12 h).

cookies
: Only for sign-in-gated sites. When the user downloads from instagram.com, twitter.com, x.com, facebook.com, reddit.com, bilibili.com, xiaohongshu.com, rednote.com, lemon8-app.com or douyin.com, background.js `getEphemeralCookiesB64` reads `chrome.cookies.getAll({domain})` for THAT one site, and sends them (Netscape format, base64, field `user_cookies_b64`) in the same request to VidGrab's own API (POST /api/v1/fetch-link). `isCookieTrustedBase` makes the send happen only if the API base is https on dvid-api.vibe1.tinhgon.xyz / dvid.vibe1.tinhgon.xyz (or a localhost developer base); with any other server URL the cookies are not read. The server uses them for that one extraction and deletes them (stated in the public privacy policy; server code not re-verified here, see privacy-checklist.md). Not read for other domains, not sent to anyone else, not stored by the extension, not sold.

webRequest
: Observe-only (no webRequestBlocking, no declarativeNetRequest): media-sniffer.js watches response headers of the page the user is on, to list the video/audio files it loads (MP4, HLS .m3u8, DASH .mpd; content-type, size, URL). The list is in chrome.storage.session and removed with the tab. It never blocks, redirects or modifies a request, and does not send the list anywhere by itself. A URL goes to VidGrab's server only if the user presses a "via VidGrab server" button.

offscreen
: Creates a hidden extension page (reason BLOBS) used for two things while the user asks for them: join HLS segments into one MP4 (bundled mux.js 6.3.0, Apache-2.0), and hold the MediaRecorder for "Quay tab". It is closed when idle. Local processing only.

tabCapture
: Used only after the user presses "Quay tab" (recording mode, last resort when a page has no downloadable file). background.js `vgRecStart` calls `chrome.tabCapture.getMediaStreamId` for that tab, the offscreen page records it to WebM, and the user gets the file via chrome.downloads. The recording is never uploaded. DRM players give a black picture; nothing is done to avoid that.

optional: clipboardRead
: Asked at runtime (chrome.permissions.request, popup.js) only if the user presses the paste button, to fill the URL box. Not granted at install.

optional host permission `*://*/*`
: Not granted at install. The popup button "Allow grabbing videos on every site" calls `chrome.permissions.request({origins:['*://*/*']})` inside the user's click. Needed because the media of a page comes from CDN hosts different from the page host (measured 22/10/2026: Chrome only shows webRequest events for hosts the extension can access). The user can revoke on chrome://extensions. Without it the extension still works on the listed sites.

## Host permissions (granted at install)

Platform hosts: tiktok.com, facebook.com, douyin.com, instagram.com, open.spotify.com, twitter.com, x.com, reddit.com, pinterest.com, threads.com, threads.net, bilibili.com, b23.tv, xiaohongshu.com, rednote.com, xhslink.com, lemon8-app.com, lemon8.app, snapchat.com, story.snapchat.com, vk.com, twitch.tv, rumble.com, odysee.com, dailymotion.com, soundcloud.com (all `*://*.host/*`).
: Why: on these pages the extension (1) runs content.js to detect the page type (video/profile/channel) and the video on screen, (2) lets the service worker fetch the media file in the user's session (with credentials) to save it, (3) lets background read that site's cookies (list above) and lets media-sniffer see the page's media responses. The MAIN-world interceptor.js runs on douyin, facebook, instagram, tiktok, twitter/x, threads, bilibili, xiaohongshu/rednote, lemon8 only to read the page's own video-list API responses; captured data goes to content.js/popup in the same browser, not to a third party. No ads, no analytics.

VidGrab hosts: dvid.vibe1.tinhgon.xyz, dvid-api.vibe1.tinhgon.xyz
: Own website and API. web-bridge.js (content script on the website only) receives the sign-in token after login; the service worker calls the API.

localhost, 127.0.0.1
: (a) developer API base; (b) pings and hands a DASH/split-audio URL to the VidGrab Windows app on the SAME computer (ports 17325-17327, `/vidgrab/ping`, `/vidgrab/download`, only if the user has installed the app). No remote host.

Note for the store build: there are NO YouTube host permissions. The Dashboard may show the extension as "all sites" because of the optional permission; that is optional only.

## Remote code
Answer: **No, I am not using remote code.**
Verified: grep for `eval(`, `new Function`, remote `<script src>` in chrome-extension/*.js, *.html: none (only `importScripts('media-sniffer.js','direct-download.js')`, local files). lib/mux.min.js (mux.js 6.3.0, Apache-2.0) and lib/qrcode.js (MIT, QR generator) are bundled with LICENSE files. Minor: popup.html has `@import url('https://fonts.googleapis.com/...')` (CSS font only, not code); see TODO in privacy-checklist.md.

## Data usage certification
Check the three boxes (no sale, no unrelated use, no creditworthiness use): see privacy-checklist.md.
