# Dashboard "Privacy practices" data-usage questionnaire (build 5.20.0)

Derived from `fetch(` calls in background.js, popup.js, media-sniffer.js, hls-downloader.js (grep 23/10/2026). API base = https://dvid-api.vibe1.tinhgon.xyz (user may change it in Settings; cookies are then not sent).

## 1. Network endpoints and what is sent
| Endpoint (VidGrab API) | Sent | Trigger |
|---|---|---|
| POST /api/v1/fetch-link | page/video URL, quality, flags, `user_cookies_b64` for cookie-gated sites, bearer token if signed in | server extraction/fallback |
| POST /api/v1/client/quota/claim, /settle | URL, route `local`, `ext-<version>`, claimId, outcome; bearer if signed in | every browser download counted against the daily allowance |
| POST/DELETE /api/v1/extension/session | web sign-in token -> receives the `vgx_` 30-day token | sign-in, renew every 12 h, sign-out |
| POST /api/v1/bulk-download, GET /api/v1/jobs/{id}, POST .../cancel | list of video URLs | channel/bulk through server |
| GET /api/v1/watch/status, POST /api/v1/watch/sources | channel URL (follow channel, signed-in only) | user presses follow |
| POST/GET/DELETE /api/v1/archive | video URL/title for the cloud history | signed-in, user action |
| POST /api/v1/analyze-media, GET /api/v1/analyze/{id} | URL + server file ref | user presses AI suggestions |
| POST /api/v1/fetch-spotify | Spotify playlist URL | user action |
| GET /api/v1/user/usage, /extension/version, /ping, /health | bearer only | popup status |
| VG_API_FETCH proxy | restricted to API origin (checked in code) | content script/popup |
| http://127.0.0.1:17325-17327/vidgrab/* | media URL, title, referer to the Windows app on the same PC | DASH handoff |
| fetch(media URL) in browser | to the video's CDN (HEAD/Range for size, segments for HLS), `credentials: include` | the user's download/preview |
Third parties from extension code: none, except popup.html `@import` of Google Fonts (Google receives the IP when the popup opens).
No analytics/Sentry/ads code in the extension (grep found none).

## 2. Local only (never transmitted)
List of media found on a tab (chrome.storage.session), local history, settings (storage.sync, Google sync), tab recording (WebM), QR (generated locally), HLS/MP4 bytes.

## 3. Data type checkboxes (answer, with reason)
- Personally identifiable information: YES (email is shown/kept with the extension session: `vg_auth_email` in storage; account is the VidGrab account). INFERRED.
- Health: no. Financial/payment: no (Stripe is on the website only).
- Authentication information: YES (session token `vgx_...`; and the site's login cookies for cookie-gated sites, sent to VidGrab's own server).
- Personal communications: no.
- Location: no.
- Web history: YES, limited: URL of the page/video the user asks to download, plus channel URLs the user follows. Not passive browsing history (sniffed list stays local).
- User activity: YES, limited (download claims, history if signed in).
- Website content: YES (video URL/title/thumbnail metadata; media found on a page). Tab recording stays local.

## 4. Certifications (check all three)
- I do not sell or transfer user data to third parties outside approved use cases. TRUE as coded; the policy states no sale (s6 "noSale"). Note: server-side "intermediary download services (Apify, ScraperAPI)" and AI services receive video URLs; the policy discloses it. Owner must confirm this fits "approved use cases" (providing the single purpose).
- I do not use or transfer user data for purposes unrelated to the single purpose. TRUE.
- I do not use data to determine creditworthiness or for lending. TRUE.

## 5. TODO for the owner: gaps in the privacy policy (frontend/src/locales/*/privacy.json, last updated 13/10/2026)
The policy s4 covers only: activeTab/tabs/scripting, cookies, downloads/storage/notifications/contextMenus/alarms. NOT covered, and added in 5.12-5.20:
1. webRequest / media list in storage.session (local only, deleted with tab).
2. tabCapture + offscreen + recording mode (local only, never uploaded).
3. Optional all-sites permission and clipboardRead (optional).
4. Extension 30-day session (`vgx_` token, email in local storage, renewal, sign-out/revocation).
5. Quota claim/settle: every browser download sends URL + client version to the server for counting.
6. Handoff to the Windows app on 127.0.0.1 (same computer).
7. Which domains' cookies are read (the list in permissions.md); the policy says "the video site itself" only.
8. Google Fonts in the popup (IP to Google) - or bundle the font and drop the @import.
9. Chrome Web Store "Limited Use" sentence: "The use of information received from Chrome APIs will adhere to the Chrome Web Store User Data Policy, including the Limited Use requirements." Not present; add to both languages.
10. Retention of cookies server-side is stated ("delete immediately"): owner must confirm the backend really does (backend `routes.py` /fetch-link, not re-checked here).
11. The English page /en/privacy exists in en/privacy.json (translationNotice may still be set). Check the live URL returns 200 before submitting.
12. Policy "updated" date (13/10/2026) predates 5.18-5.20; bump when edited.
Editing the policy is outside this task (no code was changed).
