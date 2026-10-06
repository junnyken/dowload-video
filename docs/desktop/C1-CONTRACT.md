# VidGrab Desktop C1 — interface contract (Rust ⇄ UI ⇄ server)

Owner request 2026-10-06: full Windows UX/UI + login + history sync with the web
(task #5982, due 2026-10-10). Three parts are built in parallel against this
contract; change it only through the main session.

## 1. Tauri commands (Rust, `desktop/src-tauri`) — called with `invoke()`

All errors are returned as `{ code: string, message: string }` (serialized
`CommandError`); `message` is English for logs, the UI maps `code` to Vietnamese.

| Command | Args | Returns | Notes |
|---|---|---|---|
| `probe` | `{ url }` | `ProbeResult` | Runs `yt-dlp -J --no-playlist`, timeout 90 s, killable via `cancel_probe`. |
| `cancel_probe` | `{ url }` | `null` | |
| `start_download` | `{ jobId, url, outDir, formatId?, audioOnly? }` | `null` | Emits events below. Same `jobId` may be started again to resume (yt-dlp continues `.part`). |
| `pause_download` | `{ jobId }` | `null` | Kills the process tree, keeps partial files. Emits `download://done` with `state:"paused"`. |
| `cancel_download` | `{ jobId }` | `null` | Kills the tree, deletes this job's partial files. `state:"cancelled"`. |
| `pick_folder` | `{}` | `string \| null` | |
| `default_download_dir` | `{}` | `string` | `%USERPROFILE%\Downloads\VidGrab` (created if missing). |
| `disk_free` | `{ path }` | `number` (bytes) | |
| `reveal_path` | `{ path }` | `null` | Explorer with the file selected. Only paths this app produced (see below). |
| `open_path` | `{ path }` | `null` | Default app. Only produced files AND media extensions (mp4, mkv, webm, mov, m4a, mp3, opus, ogg, wav, flac, aac, jpg, png, webp, srt, vtt). |
| `open_url` | `{ url }` | `null` | Opens the default browser. Only `https` on exactly `dvid.vibe1.tinhgon.xyz` or `dvid-api.vibe1.tinhgon.xyz` (no userinfo/port); else `invalid_url`. For sign-up / password reset / installer download. No opener permission is granted to the webview. |
| `history_list` | `{ limit?, offset?, query? }` | `HistoryItem[]` | Local SQLite (rusqlite, bundled) in the app data dir. |
| `history_add` | `{ item: HistoryItem }` | `null` | Upsert by `id`. |
| `history_delete` | `{ id }` | `null` | Deletes the record only, never the file. |
| `history_clear` | `{}` | `null` | |
| `history_mark_synced` | `{ ids: string[] }` | `null` | |
| `auth_save` | `{ session: string }` | `null` | Stores the Supabase session JSON in Windows Credential Manager (`keyring` crate, service `VidGrab`). |
| `auth_load` | `{}` | `string \| null` | |
| `auth_clear` | `{}` | `null` | |
| `get_version` | `{}` | `string` | App version. |
| `tool_versions` | `{}` | `{ ytdlp, ffmpeg, deno }` | |

"Produced paths": every final file path reported by a finished download is
remembered (in the history DB); `reveal_path`/`open_path` refuse anything else.

### Events (Rust → UI)

- `download://progress` → `{ jobId, stage: "downloading"|"merging"|"processing", percent: number|null, downloadedBytes: number|null, totalBytes: number|null, speedBps: number|null, etaSec: number|null }`
  (yt-dlp `--progress-template` with a JSON line per update; throttle to ≤ 4/s per job)
- `download://log` → `{ jobId, line }` (raw, for the log drawer)
- `download://done` → `{ jobId, state: "completed"|"failed"|"paused"|"cancelled", filePath?: string, fileSize?: number, errorCode?: string, errorMessage?: string }`

### Types

```ts
type ProbeFormat = { id: string; label: string; height: number|null; ext: string;
  vcodec: string|null; acodec: string|null; fps: number|null;
  filesize: number|null; requiresMerge: boolean; audioOnly: boolean };
type ProbeResult = { url: string; platform: string; title: string; thumbnail: string|null;
  duration: number|null; uploader: string|null; formats: ProbeFormat[] };
type HistoryItem = { id: string; url: string; title: string; platform: string;
  formatLabel: string; filePath: string|null; fileSize: number|null;
  state: "completed"|"failed"; errorCode: string|null;
  createdAt: string; finishedAt: string|null; synced: boolean };
```

Error codes the UI must handle: `invalid_url`, `unsupported`, `private_or_login`,
`geo_blocked`, `not_found`, `network`, `disk_full`, `tool_missing`,
`tool_tampered` (checksum mismatch), `timeout`, `cancelled`, `unknown`.

## 2. Server API (backend, deployed to dvid-api) — auth: Supabase JWT `Authorization: Bearer`

| Method + path | Body / query | Response |
|---|---|---|
| `POST /api/v1/client/history` | `{ deviceId, clientVersion, items: [{ clientId, url, title, platform, formatLabel, fileSize, state, errorCode, finishedAt }] }` (≤ 100 items) | `{ accepted: n, ids: [clientId…] }` — upsert on (user, clientId). NO local file paths are ever sent. |
| `GET /api/v1/client/history?limit=&before=` | | `{ items: [...same fields + id, createdAt] }` own rows only |
| `GET /api/v1/history?limit=&offset=` (existing) | | the user's web/server downloads — app shows them in a "Trên web" tab |
| `GET /api/v1/client/version` | | `{ latest, minSupported, notes, downloadUrl }` from env; no auth |

Flag `CLIENT_API_ENABLED` (default false) → 503 `client_api_disabled` on the
two `/client/history` routes. Rate limit like other authenticated routes.

## 3. UI (desktop/src, React + TS + Tailwind v4)

Sign-in uses `@supabase/supabase-js` directly (same project as the web:
`https://wtwnbagcqyedindtcadv.supabase.co`, public anon key taken from the web
build — it is the same public key the website ships). Session persisted only via
`auth_save`/`auth_load` (custom storage adapter), never localStorage.

CSP (`tauri.conf.json`, owned by the Rust part): `connect-src` adds the
Supabase URL; `img-src 'self' data: https:` for thumbnails.

## 4. Channels (owner request 2026-10-06: "tải toàn bộ video kênh riêng")

Owner decisions: per-channel mode (auto-download OR notify only); app keeps
running in the system tray when the window is closed, optional start with
Windows (off by default); first add shows the video list to pick from, later
checks only take videos not seen before.

### Rust commands (added to section 1)

| Command | Args | Returns | Notes |
|---|---|---|---|
| `channel_fetch` | `{ url, limit? }` | `ChannelListing` | `yt-dlp --flat-playlist -J --playlist-end <limit, default 200, max 5000>`; YouTube channel root URLs get `/videos` appended; timeout 180 s; `cancel_channel_fetch { url }`. |
| `cancel_channel_fetch` | `{ url }` | `null` | |
| `channel_save` | `{ channel: Channel }` | `null` | Upsert by `id`. |
| `channel_list` | `{}` | `Channel[]` | |
| `channel_delete` | `{ id }` | `null` | Deletes channel + its seen list; never files. |
| `channel_seen_add` | `{ channelId, videoIds: string[] }` | `null` | Videos already downloaded/queued/dismissed. |
| `channel_seen_list` | `{ channelId }` | `string[]` | |
| `notify` | `{ title, body }` | `null` | Windows toast (notification plugin used from Rust only). |
| `autostart_get` / `autostart_set` | `{}` / `{ enabled }` | `boolean` / `null` | tauri-plugin-autostart; launched with `--minimized` → start hidden in tray. |
| `set_close_to_tray` | `{ enabled }` | `null` | Window close hides instead of quitting when enabled (default true). |

Tray icon menu: "Mở VidGrab", "Kiểm tra kênh ngay" (emits `channels://check-now`),
"Thoát" (real quit; running downloads are paused first via `app://quitting` event
+ 3 s grace). Single instance: a second launch focuses the existing window.

### Types

```ts
type ChannelVideo = { id: string; url: string; title: string; duration: number|null;
  uploadDate: string|null /* YYYYMMDD */; thumbnail: string|null };
type ChannelListing = { channelId: string; url: string; title: string; platform: string;
  uploader: string|null; thumbnail: string|null; videos: ChannelVideo[]; truncated: boolean };
type Channel = { id: string; url: string; title: string; platform: string;
  thumbnail: string|null; mode: "download"|"notify"; quality: string /* preset id or "best" */;
  outDir: string; checkEveryHours: 1|3|6|12|24; enabled: boolean;
  lastCheckedAt: string|null; lastError: string|null; pendingNew: ChannelVideo[];
  createdAt: string };
```

### UI

Sidebar "Kênh" (between Tải xuống and Hàng đợi; badge = total pendingNew).
Add flow: URL → channel_fetch → picker (select all / N mới nhất / từ ngày,
quality, folder default `<default dir>\<channel title>`, "Theo dõi kênh này",
mode, interval) → "Tải N video" (enqueue + channel_seen_add) and/or "Lưu kênh"
(unselected videos are also marked seen so later checks only bring new ones).
Scheduler (UI, runs while the app runs, also hidden in tray): every minute pick
enabled channels whose interval elapsed; fetch latest 50; new = not in seen;
mode download → enqueue + seen_add + notify; notify → add to pendingNew + notify.
Settings: "Thu nhỏ xuống khay khi đóng" (on), "Khởi động cùng Windows" (off).
