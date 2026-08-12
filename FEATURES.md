# VidGrab — Tài liệu tính năng hiện tại (Context Brief)

> **Mục đích file này:** Mô tả trung thực và chi tiết toàn bộ tính năng đã xây, kiến trúc, giới hạn kỹ thuật, và khoảng trống cần nâng cấp — để AI / chatbot đọc hiểu sản phẩm và đề xuất cải tiến có giá trị thực sự. Không hype, không marketing. Trạng thái thực tế.

---

## 0. Trạng thái vận hành hiện tại (cập nhật 2026-06-30)

> ⚠️ **Đọc trước.** Đây là tình trạng THỰC TẾ đang chạy trên production.

### ✅ Đang hoạt động bình thường

| Nền tảng | Ghi chú |
|----------|---------|
| **TikTok** | Single + profile batch + xoá watermark (TikWM API) |
| **Douyin** | Single + user page batch; 3-layer fallback |
| **Facebook** | Video public; xem trước container |
| **Threads** | Public post + profile scraping |
| **Pinterest** | Single + board batch |
| **SoundCloud** | Tải nhạc trực tiếp MP3; cũng là nguồn fallback cho Spotify |
| **Spotify** | Tải được nhạc qua SoundCloud (auto-fallback, không cần YouTube) |
| **Bilibili** | Single + channel; AV1/H.264 @1080p–4K; subtitle extraction |
| **VK (VKontakte)** | Single + channel; MP3 + video |
| **Dailymotion** | Single + playlist batch |
| **Odysee** | Single; audio extraction |
| **SoundCloud** | Single + artist/playlist; MP3 direct |
| **Lemon8** | Single; batch cần cookie |
| **Rumble** | Single OK; batch experimental |
| **Podcast RSS** | Episode + feed batch; MP3 up to 20 eps/feed |
| **Container Preview (Phase 25)** | Xem trước playlist/profile/album trước khi tải (TikTok, Spotify, SoundCloud, Threads, Pinterest, Facebook, v.v.) |
| **Trim / GIF / Chapter extract** | FFmpeg-based media tools |

### 🟡 Hoạt động có điều kiện (cần cookie / proxy)

| Nền tảng | Điều kiện | Tình trạng hiện tại |
|----------|-----------|-------------------|
| **Instagram** | Cần cookie đăng nhập | Cookie pool hiện ít account (~2), có thể hết |
| **Twitter/X** | Cần cookie đăng nhập | Chưa cấu hình cookie, timeline batch offline |
| **Reddit** | Public subreddit OK, post private cần login | Partial; gated posts offline |
| **XiaoHongShu (小红书)** | Batch cần cookie | Single OK, profile batch phụ thuộc cookie |
| **Twitch** | Clips OK, live stream cần auth | Chỉ clip; live stream không hỗ trợ |
| **Snapchat** | Single OK, không có batch | Single post duy nhất |
| **YouTube channel/playlist** | Cần `YTDL_PROXY` residential proxy | Hiện chưa bật env var |

### ⛔ Tạm tắt

| Nền tảng | Lý do | Giải pháp |
|----------|-------|---------|
| **YouTube single video** | IP server Oracle (AS31898) bị YouTube chặn bot toàn dải ASN. Bytes download tắt. Metadata OK qua proxy. | Bật `YOUTUBE_ENABLED=true` + `YTDL_PROXY` → 1 commit, ~$0.02/video |

---

## 1. Tổng quan sản phẩm

**VidGrab** — Công cụ tải video đa nền tảng, hỗ trợ 20+ nền tảng, xử lý media nâng cao (trim, GIF, watermark removal), scrape channel/playlist hàng loạt, và hạ tầng enterprise-grade (circuit breaker, cookie pool, proxy rotation).

| Surface | Mô tả | URL / Ghi chú |
|---------|-------|--------------|
| **Web App** | React SPA + FastAPI, đầy đủ tính năng | `dowloadvideo.io.vn` |
| **Chrome Extension** | Manifest V3, v4.7, cài manual (chưa lên Chrome Store) | 4 tab, 380×500px |
| **Telegram Bot** | Chỉ phân phối extension ZIP | Chưa có tính năng tải video qua chat |

**Tech Stack:**
- **Frontend:** React 19 · Vite 8 · TailwindCSS 4 · Lucide React
- **Backend:** FastAPI 0.115 · Python 3.10 · Uvicorn 4 workers
- **Queue:** Celery 5.6 (8 concurrent workers) · Redis 7
- **DB:** Supabase (PostgreSQL 15) — auto-backup 30d
- **Video Engine:** yt-dlp ≥2025.5.1 · FFmpeg
- **YouTube Bypass:** bgutil-pot (2 instances) · Cobalt API v11
- **Proxy:** Proxying.io rotating residential (metadata-only) · ScraperAPI fallback
- **Infra:** Docker Compose 8 containers · Oracle Cloud VPS · Nginx

---

## 2. Platform Support Matrix — Đầy đủ

**Huyền thoại:** ✅ Full · 🟡 Cần cookie/proxy · ⛔ Tắt · 🔧 Experimental · ❌ Không có

| Nền tảng | Video đơn | Batch/Channel | MP3 | Xoá WM | Container Preview | Quality | Ghi chú quan trọng |
|----------|:---------:|:-------------:|:---:|:------:|:-----------------:|---------|-------------------|
| **TikTok** | ✅ | ✅ profile | ✅ | ✅ | ✅ | H.264 360p–1080p | TikWM fast-path no-WM. Max 100/batch; wave delay lớn batch. |
| **Douyin** | ✅ | ✅ user page | ✅ | ✅ | ✅ | H.264 720p | 3-layer fallback: iesdouyin → TikWM → ScraperAPI CN. Extension DOM intercept. |
| **Facebook** | ✅ public | ✅ video tab | ❌ | ❌ | ✅ partial | H.264 720p | Public video only. Private/reels login-gated. |
| **Threads** | ✅ public | ✅ profile | ❌ | N/A | ✅ | VP9/H.264 | Public only. Custom scraper, không dùng Graph API. |
| **Pinterest** | ✅ | ✅ board | ❌ | N/A | ✅ | H.264 720p+ | yt-dlp PinterestCollectionIE. Non-video pins tự skip. |
| **SoundCloud** | ✅ | ✅ artist/playlist | ✅ | N/A | ✅ | MP3 128–320kbps | Direct MP3. Spotify fallback source. |
| **Spotify** | ✅→SoundCloud | ✅ playlist/album/artist | ✅ | N/A | ✅ | MP3 320kbps (via SC) | No API key cần. YouTube source disabled → auto fallback SoundCloud. Match ~90% exact. |
| **Bilibili** | ✅ | ✅ channel/series | ✅ | ❌ | 🟡 | AV1/H.264 @1080p–4K, FLAC | yt-dlp + cookie support. Subtitle extraction available. Season/series support. |
| **VK (VKontakte)** | ✅ | ✅ channel | ✅ | ❌ | 🟡 | H.264 720p, MP3 | yt-dlp native. |
| **Dailymotion** | ✅ | ✅ playlist | ❌ | ❌ | 🟡 | H.264 720p | yt-dlp native. |
| **Odysee** | ✅ | — | ✅ | ❌ | — | H.264 1080p, audio | yt-dlp native. Single only. |
| **Rumble** | ✅ | 🟡 experimental | ❌ | ❌ | — | H.264 1080p | Single OK; channel batch thử nghiệm. |
| **Lemon8** | ✅ | 🟡 cần cookie | ✅ | ❌ | — | H.264 720p | Custom extractor. |
| **XiaoHongShu** | ✅ | 🟡 cần cookie | ❌ | ❌ | — | H.264 720p | Custom extractor. |
| **Snapchat** | ✅ | — | ❌ | ❌ | — | MP4 720p | Single post only. |
| **Twitch** | ✅ clip | — | ❌ | ❌ | — | H.264 1080p | Clips only; live stream cần auth. |
| **Podcast RSS** | ✅ episode | ✅ feed (20 eps) | ✅ | N/A | — | MP3 128–192kbps | Custom podcast_extractor. Feed parse + download. |
| **Instagram** | 🟡 public | 🟡 cần cookie | ❌ | ❌ | 🟡 | Reels/Stories/Carousel | Cookie pool hiện ít account. |
| **Twitter/X** | 🟡 | 🟡 cần cookie | ❌ | N/A | 🟡 | MP4 1080p | Chưa cấu hình cookie. |
| **Reddit** | ✅ public | 🟡 subreddit | ❌ | N/A | 🟡 | MP4 1080p | Public subreddit video OK. |
| **YouTube** | ⛔ | 🟡 cần YTDL_PROXY | ⛔ | N/A | 🟡 | H.264 720p–1080p | IP Oracle bị chặn. Bật lại = 1 commit + proxy config. |

---

## 3. Tính năng — Web App

### 3.1 Single Download (`/`)

- Dán URL → auto-detect platform → extract metadata → format list → tải
- **Quality tiers:** `video` (H.264+AAC, 360p–1080p) · `video_4k` · `video_fast` (no FFmpeg) · `video_720/1080` · `mp3_128/320`
- **Format selector đầy đủ:** Toàn bộ formats có sẵn sau fetch (codec, bitrate, resolution, dung lượng)
- **Progress real-time:** Polling 1.5s, elapsed timer, speed (kbps), ETA (giây)
- **Post-download actions:** Copy direct link · Tải thumbnail · Mở URL gốc

**Advanced Media Tools:**

| Tool | Mô tả | Giới hạn |
|------|-------|---------|
| Trim Video | Range slider + preset 15s/30s/60s · FFmpeg `-c copy` lossless | Max 10 phút |
| GIF Converter | 320–1080px · 10–30 FPS · 2-pass palette | Max 30s |
| Chapter Extractor | Tách YouTube chapters → file MP4 riêng | YouTube only |
| Preview Player | Stream xem trước (HTML5 video) | — |
| Cloud Save | Google Drive · Dropbox (direct API) | Cần user auth |
| Logo Inpaint | SHIFTMAP + temporal motion-comp | 🔧 Experimental; chưa expose UI |

**YouTube extraction pipeline — ⛔ hiện không tải được:**

```
1. Oracle VPS IP direct           → ❌ YouTube bot-block toàn ASN
2. bgutil-pot PO Token (2 inst.)  → Token OK, nhưng IP Oracle bị chặn
3. Android VR client fallback     → ❌ Cùng IP bị block
4. Proxying.io residential        → ✅ Metadata lấy được
5. Bytes qua proxy                → Tắt (cost: ~$0.02/video × bandwidth)
6. Cobalt API local (port 9000)   → ❌ `error.api.youtube.login` (same IP)
```

> **Giải thích:** YouTube CDN sign URL theo IP. Metadata proxy OK, bytes phải tải từ IP đã sign. Bật lại: `YOUTUBE_PROXY_DOWNLOAD=1` + `YTDL_PROXY` + `YOUTUBE_ENABLED=true` → 1 commit, cost ~$0.02/video.

---

### 3.2 Bulk Download (`/bulk`)

**Input:**
- Textarea nhiều URL (1 URL/dòng)
- Import file `.txt` / `.csv`
- Auto-clean tracking params

**Channel/Profile Scrape Mode:**
- Selector số lượng: 10 / 20 / 50 / 100 / 200 / 500 / custom
- Min views filter
- Discovery UI: radar animation + live backend message mỗi 5s

**Xử lý batch:**
- Celery worker: 8 concurrent, max 50 tasks/child
- Wave processing: 10 videos/đợt, delay ngẫu nhiên 3–8s
- Per-platform throttle: TikTok 10/min, Instagram 20/min (Redis sliding window)
- Cookie pool auto-rotate khi 429 / bot signal

**Job management table (polling 3s):**
- Status badges: Pending / Processing / Success / Failed
- Thumbnail + title + URL + link countdown (hết hạn 20 phút)
- Multi-select: Download staggered / Retry / Delete
- Tạo ZIP tất cả (Celery background task)
- Export CSV (title + URL + direct link)

---

### 3.3 Spotify Integration

- Single track: embed scrape → tìm trên nguồn ngoài → yt-dlp → MP3 + ID3 tags
- **Nguồn audio hiện tại:** SoundCloud (`scsearch1:`) — auto-fallback vì YouTube disabled. Match ~90% exact.
- Playlist/Album: fetch toàn bộ tracklist → tải từng track / ZIP
- Cache Redis 7 ngày (tránh search lặp)
- Không cần Spotify API key (scrape `open.spotify.com/embed/` → `__NEXT_DATA__`)

### 3.4 SoundCloud Integration

- Tải nhạc trực tiếp (yt-dlp native, MP3)
- Artist/playlist batch qua Container Preview
- Nguồn fallback khi Spotify + YouTube fail

### 3.5 Threads Integration

- Public post + profile (không dùng Graph API)
- OG tags + embedded JSON extraction
- Error codes rõ: `unsupported_threads_url` · `private_or_login_required` · `no_media_found`

### 3.6 Phase 25 — Container Preview & Discovery Framework

> **Mục đích:** Xem trước và chọn nội dung từ playlist/profile/album trước khi tải — không cần tải mù.

**Luồng:**
```
Dán URL container → POST /discover-container → job_id ngay
→ poll /discover-container/{job_id} (stage-by-stage, progress bar)
→ ContainerPreviewPanel: thumbnail grid + checkbox
→ POST /container/{id}/queue → batch_id → poll /jobs/{batch_id}
```

**Discovery job lifecycle:**
```
queued → resolving → discovering → assemble_sections → finalize → success
                                                              ↓
                                                         partial (timeout)
                                  ↓
                               failed
```

**Platform expanders (Phase 25):**

| Platform | Coverage | Source types | Ghi chú |
|----------|:--------:|-------------|---------|
| Spotify | ✅ full | artist, playlist, album | Lazy expand album tracks |
| SoundCloud | ✅ full | artist, playlist, media_tab | |
| Threads | ✅ full | profile | |
| Pinterest | ✅ full | board, profile | |
| TikTok / Douyin | ✅ full | profile | |
| Facebook | 🟡 partial | media_tab, channel | |
| Instagram | 🟡 cookie_required | profile, media_tab | Cookie pool cần đủ |
| Twitter/X | 🟡 cookie_required | profile, thread | Chưa có cookie config |
| Reddit | 🟡 partial | subreddit, profile | |
| YouTube | 🟡 proxy_required | channel, playlist | Cần `YTDL_PROXY` |

**9 Endpoints mới (Phase 25):**

| Endpoint | Mô tả |
|---------|-------|
| POST `/api/v1/resolve-input` | Normalize + classify URL (<50ms, no network) |
| GET `/api/v1/platforms/capabilities` | Ma trận đầy đủ platform × source_type × features |
| POST `/api/v1/discover-container` | Khởi tạo async discovery, trả job_id ngay |
| GET `/api/v1/discover-container/{job_id}` | Poll DiscoveryJobSnapshot (status/stage/progress/sections) |
| GET `/api/v1/container/{container_id}` | Full ContainerMeta (sections + items) |
| POST `/api/v1/container/{id}/expand` | Lazy expand section / child |
| POST `/api/v1/container/{id}/queue` | Queue items đã chọn → batch_id |
| POST `/api/v1/container/{id}/refresh` | Invalidate cache + re-discover |
| GET `/api/v1/container/{id}/manifest` | Export CSV toàn bộ items |

**Frontend Phase 25:** `ContainerPreviewPanel.jsx` · `useContainerDiscover.js` · `types/container.ts` · `api/container.ts`

---

### 3.7 Download History

- Tất cả jobs lưu Supabase PostgreSQL
- Hiển thị: thumbnail, title, platform, timestamp, dung lượng file
- Filter/search, pagination, xoá đơn / xoá tất cả

### 3.8 User Auth (Schema có, chưa enforce)

- Email/password signup + login (AuthModal)
- Tier system: Free / Pro (schema có, **chưa có tier difference thực sự**)
- Extension bridge: sign in extension → web auth đồng bộ

### 3.9 Admin Dashboard (`/vid-admin`)

**Auth:** `X-Admin-Token` header (hardcoded password `matbaosupport`)

| Panel | Nội dung |
|-------|---------|
| Stats | Downloads hôm nay · Active users · ScraperAPI credits · Recent failures |
| Analytics | 7/30-day charts: total/success/failed; per-platform breakdown |
| Active Jobs | Real-time danh sách jobs đang processing |
| Users | Toggle tier Free/Pro · Adjust quota |
| Cookies | Add/List/Revoke platform cookies trong pool |

---

## 4. Tính năng — Chrome Extension (v4.7, Manifest V3)

### Tab 1: Tải Video (Single)
- Auto-detect video trên tab hiện tại: YouTube, TikTok, Instagram, Facebook, Douyin, Spotify, Threads
- Quality selector: HD / 4K / MP3 320kbps
- Toggle: Xoá watermark (default ON) · Phụ đề (UI có, backend partial)
- Background download: service worker giữ ref → Chrome notification khi xong
- Right-click context menu: "Tải video" / "Tải MP3"

### Tab 2: Kênh / Batch
- **Douyin DOM intercept:** inject MAIN world tại `document_start`, capture `__INITIAL_STATE__` khi scroll — không cần copy URL
- **YouTube/TikTok/Generic:** selector số lượng → radar animation → live counter
- **Spotify playlist:** auto-detect → tracklist inline → MP3 / ZIP

### Tab 3: Lịch sử
- 100 lượt gần nhất (chrome.storage.local)
- Cross-tab sync

### Tab 4: Cài đặt
- Custom API server URL
- Server health indicator (ping `/api/v1/ping`)
- Dark/Light mode · Account bridge

> **Hạn chế:** Chưa lên Chrome Store (dùng `webRequest` deprecated API) → cài manual (Developer Mode). High friction.

---

## 5. Backend Architecture

### 5.1 Docker Compose — 8 containers

| Service | Image | Vai trò |
|---------|-------|---------|
| `redis:7` | Redis Alpine | Cache · Celery broker · Cookie pool · Rate limit |
| `bgutil-pot` × 2 | jim60105/bgutil-ytdlp-pot-provider | YouTube PO Token (Chrome headless, cache 3h) |
| `cobalt-api` | ghcr.io/imputnet/cobalt | YouTube fallback extractor (port 9000) |
| `backend` | FastAPI + Uvicorn | API server, 4 workers |
| `celery` | Celery worker | Background jobs, 8 concurrent, 50 tasks/child |
| `celery-beat` | Celery scheduler | 6 periodic tasks |
| `telegram-bot` | Python bot | Phân phối extension ZIP |
| `frontend` | Nginx | Serve React build |

### 5.2 Periodic Tasks (celery-beat)

| Task | Schedule | Mô tả |
|------|----------|-------|
| `cleanup-downloads` | Mỗi 5 phút | Xoá files >20 phút, enforce 10GB disk quota |
| `daily-summary` | 23:00 UTC | Telegram daily report |
| `check-api-credits` | Mỗi 6 giờ | Poll ScraperAPI balance, alert nếu <$5 |
| `ytdlp-auto-update` | 03:00 UTC | Auto-update yt-dlp binary |
| `refresh-po-token` | Mỗi 3 giờ @ :05 | Pre-warm YouTube PO Token |
| `check-cookie-expiry` | 09:30 UTC | Validate + rotate expired cookies |
| `scan-stale-jobs` | Mỗi 2 phút | Recover processing jobs stuck >5 phút |

### 5.3 Circuit Breaker (Per-Platform)

**File:** `backend/app/core/platform_circuit.py`

**States:** CLOSED → OPEN → HALF → CLOSED

**Trigger:** 5+ failures trong 300s (429, 403, timeout, login_required)

| Platform | Circuit | Ghi chú |
|----------|:-------:|---------|
| TikTok, Douyin, SoundCloud, Pinterest | Exempt | Proxy-backed, low-risk |
| Instagram, Twitter, YouTube | Active | Theo dõi failures |
| YouTube Oracle IP | PERMANENTLY OPEN | Không bypass được ASN block |

**Env vars:** `PLATFORM_CB_WINDOW=300` · `PLATFORM_CB_THRESHOLD=5` · `PLATFORM_CB_COOLDOWN=300`

### 5.4 Proxy Architecture

| Proxy | Dùng cho | Ghi chú |
|-------|---------|---------|
| **Proxying.io** (`IPROYAL_PROXY`) | Metadata phase (Phase A) | Rotating residential, $1.50/GB. **KHÔNG tải bytes** — code chặn cứng. |
| **ScraperAPI** (`SCRAPERAPI_KEY`) | Fallback pool | Không vượt được YouTube bot-check. Dùng cho TikTok/Instagram metadata. |
| **`YTDL_PROXY`** | YouTube channel discovery | Residential proxy riêng cho YouTube nếu bật. |
| **Oracle VPS IP trực tiếp** | Bytes download (Phase B) | Dùng cho mọi platform TRỪ YouTube (IP bị block). |

### 5.5 Cookie Management

- Multi-account pool lưu Redis
- Platforms: YouTube · TikTok · Facebook · Instagram · Twitter
- Rotation: LRU selection
- Soft block: 15 phút · Hard block: 6 giờ
- Auto-rotate khi detect 429 / bot signal
- Fallback: env vars (`YOUTUBE_COOKIES_B64`, v.v.)

### 5.6 Rate Limiting (Redis)

| Scope | Giới hạn |
|-------|---------|
| Global per-IP | 60 req/min |
| Heavy endpoints | 30 req/min (`/fetch-link`, `/bulk-download`, `/trim`) |
| Global concurrency | Max 10 concurrent `/fetch-link` |
| TikTok per-cookie | 10/min |
| Instagram per-cookie | 20/min |
| YouTube per-IP | 5/day (nếu bật) |
| IP daily quota | 50 downloads/day (configurable) |

### 5.7 Security

- CORS: allowed origins + `chrome-extension://.*`
- SSRF guard: reject private IPs / loopback
- Security headers: X-Content-Type-Options, X-Frame-Options, XSS
- Admin auth: `X-Admin-Token` header (hardcoded → TODO DB-backed)
- Error tracking: Sentry

---

## 6. Toàn bộ API Endpoints

### Core Download

| Method | Endpoint | Rate Limit | Mô tả |
|--------|----------|-----------|-------|
| POST | `/api/v1/fetch-link` | 30/min | Extract + download single video |
| POST | `/api/v1/fetch-spotify` | 30/min | Spotify playlist/track |
| POST | `/api/v1/fetch-threads` | 20/min | Threads public post/profile |
| POST | `/api/v1/bulk-download` | 30/min | Queue batch URLs |
| POST | `/api/v1/bulk-zip` | 30/min | Tạo ZIP batch hoàn thành |
| GET | `/api/v1/progress/{token}` | — | Poll real-time progress |
| GET | `/api/v1/jobs/{batch_id}` | — | Batch job status |
| GET | `/api/v1/quota` | — | Quota còn lại của IP |
| GET | `/api/v1/history` | — | Lịch sử tải |
| DELETE | `/api/v1/history/{job_id}` | — | Xoá 1 job |

### Media Processing

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/api/v1/proxy-download` | Stream qua backend (CORS bypass) |
| GET | `/api/v1/download-local` | File local (20-min expiry) |
| GET | `/api/v1/download-thumbnail` | Proxy thumbnail |
| POST | `/api/v1/trim` | Cắt video/audio (FFmpeg) |
| POST | `/api/v1/to-gif` | Convert clip → GIF |
| POST | `/api/v1/flow-cleanup/inpaint-logo` | Xoá logo video (experimental) |

### Container Discovery (Phase 25)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/api/v1/resolve-input` | Normalize + classify URL |
| GET | `/api/v1/platforms/capabilities` | Ma trận hỗ trợ |
| POST | `/api/v1/discover-container` | Khởi tạo discovery job |
| GET | `/api/v1/discover-container/{job_id}` | Poll progress |
| GET | `/api/v1/container/{container_id}` | Full ContainerMeta |
| POST | `/api/v1/container/{id}/expand` | Lazy expand section |
| POST | `/api/v1/container/{id}/queue` | Queue items đã chọn |
| POST | `/api/v1/container/{id}/refresh` | Re-discover |
| GET | `/api/v1/container/{id}/manifest` | Export CSV |

### Admin (`X-Admin-Token` required)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/api/v1/admin/stats` | Dashboard tổng quan |
| GET | `/api/v1/admin/analytics` | 7/30-day trends |
| GET | `/api/v1/admin/active-jobs` | Real-time jobs |
| POST | `/api/v1/admin/update-user` | Toggle user tier |
| POST | `/api/v1/admin/cookies/add` | Thêm platform cookie |
| GET | `/api/v1/admin/cookies/list` | List cookies + block status |
| DELETE | `/api/v1/admin/cookies/{cookie_id}` | Revoke cookie |

### User Auth & Extension

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/api/v1/user/login` | Login |
| GET | `/api/v1/user/profile` | Profile |
| POST | `/api/v1/user/upgrade` | Upgrade tier (webhook) |
| GET | `/api/v1/extension/download` | Download extension ZIP |
| GET | `/api/v1/ping` | Health check (extension) |
| GET | `/health` | Health detail (disk%, Redis, yt-dlp version, workers) |

---

## 7. Database Schema (Supabase PostgreSQL)

```sql
-- Jobs
download_jobs: id · batch_id · original_url · platform · title · direct_mp4_url
               status (pending/processing/success/failed) · error_message
               downloaded_height · file_size_mb · created_at · updated_at

-- Users
user_usage:    user_id · downloads_today · last_reset_at
user_credits:  user_id · credit_balance · tier (free/pro)
profiles:      id (FK → auth.users) · tier · created_at

-- System
provider_status: provider_name · remaining_credits · last_checked_at
```

---

## 8. Giới hạn hiện tại & Khoảng trống

### Giới hạn kỹ thuật đang gặp

| # | Vấn đề | Mức độ ảnh hưởng | Giải pháp |
|---|--------|:----------------:|---------|
| 1 | **YouTube tắt** | 🔴 Cao | Set `YOUTUBE_ENABLED=true` + `YTDL_PROXY` (1 commit) |
| 2 | **File expiry 20 phút** | 🟡 Trung bình | Tăng `FILE_EXPIRY_SINGLE_MIN=60`; CDN URL không re-sign được |
| 3 | **Celery restart mất jobs** | 🟡 Trung bình | Auto-resume code partial (job_lease.py), chưa integrate đầy đủ |
| 4 | **TikTok batch >100 chậm** | 🟡 Trung bình | Wave delay 3–8s/10 videos; điều chỉnh wave size |
| 5 | **Instagram cookie pool ít** | 🟡 Trung bình | Chỉ ~2 account; cần thêm qua admin panel |
| 6 | **Twitter/X không có cookie** | 🔴 Cao với feature | Chưa cấu hình → profile batch offline |
| 7 | **Chrome Store** | 🟡 Trung bình | Extension cài manual (Developer Mode), high friction |
| 8 | **Subtitle backend partial** | 🟡 Nhẹ | yt-dlp code có, chưa test end-to-end |
| 9 | **Admin auth hardcoded** | 🟡 Security | Password `matbaosupport` không có RBAC, không audit log |
| 10 | **10GB disk quota** | 🟡 Nhẹ | Cleanup 5 phút; không có user-side quota UI |

### Tính năng chưa có / chưa hoàn chỉnh

| Feature | Trạng thái hiện tại | Build status |
|---------|-------------------|:------------:|
| **User tier enforcement** | Schema có, không enforce thực | ❌ chưa build |
| **Payment / Stripe** | Webhook endpoint có, không có plan logic | ❌ chưa integrate |
| **Public API Key** | Credits endpoint có, không có self-service | ❌ chưa build |
| **Telegram Bot tải video** | Bot chỉ gửi extension ZIP | ❌ chưa build |
| **PWA install flow** | Responsive cơ bản | ❌ manifest chưa có |
| **Subtitle download** | Toggle UI có | 🔧 Backend partial |
| **Resume failed jobs** | Manual restart | 🔧 Partial (job_lease.py) |
| **Twitter/Reddit batch qua cookie** | Container discover OK | 🟡 Cần cookie config |
| **Browser push notification (user)** | Chỉ Telegram admin | ❌ chưa có |
| **Logo inpaint UI** | Endpoint có | 🔧 Chưa expose web UI |
| **Analytics chiều sâu** | Basic stats | ❌ Không có per-user/per-platform detail |
| **Mobile App** | Responsive web | ❌ Không có native app |
| **Multi-tenant (Enterprise)** | `docker-compose.enterprise.yml` | 🔧 Built, chưa deploy |

### Các Phase đã build nhưng chưa deploy (v5.x)

| Phase | Nội dung | Status |
|-------|---------|:------:|
| **Phase 16** | Multi-tenant (tenants + 7 tables) · Partner API (`vgp_` keys, 9 endpoints) · HMAC webhooks · White-label branding · Self-hosted Compose | 🔧 Built, chưa deploy |
| **Phase 17** | SRE: deploy pre/post checks · rollback.sh · backup.sh · smoke-test.sh · Structured JSON logging · Disk guardrail · Storage abstraction (local+S3) · DR runbook | 🔧 Built, chưa deploy |
| **Phase 18** | AI Media: Smart trim/clips/GIF/metadata/summary · FFmpeg heuristic · celery-analysis queue · Tier gating (free 5/day→pro 50/day→enterprise unlimited) · SmartActionsPanel.jsx | 🔧 Built, chưa wired |
| **Phase 19** | PWA: share_target · SW cache · NotificationCenter · usePWAInstall · useClipboardPaste · ExtensionInstallBanner · Telegram /pwa | 🔧 Built, chưa deploy |
| **Phase 20** | Billing: plans + usage_events + payment_events · entitlements.py · Stripe Customer Portal · PricingPage + BillingPage + PaywallGate + QuotaBar | 🔧 Built, chưa deploy |
| **Phase 21** | Convenience: QuickActionBar · SuccessCard · useSmartDefaults · usePresets · history rerun | 🔧 Built, chưa deploy |
| **Phase 23** | Mobile: MobileTabBar (5-tab IA) · ActiveJobsMobile · MobileShareIntake · MobileQuickTools · push subscription backend | 🔧 Built → LIVE |
| **Phase 24** | Search: SearchPage (/search) YouTube+TikTok · 4 preset chips · activeJobCount badge | 🔧 Built → LIVE |

---

## 9. Đề xuất nâng cấp ưu tiên

> **Dành cho AI context:** Đây là phân tích về những gì CÒN THIẾU và nên làm tiếp. Đọc phần này để đề xuất roadmap chính xác.

### Ưu tiên cao — Impact ngay lập tức

| # | Nâng cấp | Lý do | Effort |
|---|---------|-------|:------:|
| 1 | **Bật lại YouTube** | Nền tảng phổ biến nhất, đang tắt vì config. 1 commit + proxy env. | 🟢 Thấp |
| 2 | **Thêm cookie Twitter/X** | Profile batch container offline hoàn toàn; pool chỉ cần 2-3 account | 🟢 Thấp |
| 3 | **Thêm cookie Instagram** | Pool hiện ~2 account, dễ hết; thêm 3-5 account từ admin panel | 🟢 Thấp |
| 4 | **Subtitle download hoàn thiện** | UI đã có, chỉ cần test + fix backend | 🟡 Trung bình |
| 5 | **Job resume sau Celery restart** | `job_lease.py` có partial, cần integrate | 🟡 Trung bình |

### Ưu tiên trung bình — Cải thiện UX / revenue

| # | Nâng cấp | Lý do | Effort |
|---|---------|-------|:------:|
| 6 | **Deploy Phase 20 Billing + Stripe** | Tier enforced schema sẵn; cần Stripe webhook + hook vào entitlements.py | 🟡 Trung bình |
| 7 | **Deploy Phase 19 PWA** | Mobile UX cơ bản, install flow, push notification | 🟡 Trung bình |
| 8 | **Telegram Bot tải video** | Bot đã có, chỉ cần add handler `/dl {url}` → call `/api/v1/fetch-link` | 🟢 Thấp |
| 9 | **Chrome Extension → Chrome Store** | Migrate khỏi `webRequest` deprecated; MV3 declarativeNetRequest | 🔴 Cao |
| 10 | **Admin RBAC** | Hardcoded password rủi ro; DB-backed users + roles | 🟡 Trung bình |

### Ưu tiên thấp — Nice to have

| # | Nâng cấp | Ghi chú |
|---|---------|---------|
| 11 | Deploy Phase 16 Multi-tenant | Enterprise tier, partner API |
| 12 | Deploy Phase 17 SRE | Rollback/backup scripts, S3 storage |
| 13 | Deploy Phase 18 AI Media | Smart trim/clip heuristic AI |
| 14 | Logo Inpaint UI | Expose endpoint trong web UI |
| 15 | Analytics chiều sâu | Per-user, per-platform, per-hour |
| 16 | Platform mở rộng | Vimeo, Weibo, Naver TV, NicoNico... |

---

## 10. Cấu hình & Triển khai

### Environment Variables chính

```env
# Database
SUPABASE_URL / SUPABASE_KEY / SUPABASE_SERVICE_KEY

# Proxies
IPROYAL_PROXY=http://user:pass@host:port        # Proxying.io rotating residential (metadata-only)
SCRAPERAPI_API_KEY=...                          # Fallback pool
YTDL_PROXY=http://user:pass@res-host:port       # YouTube channel (bắt buộc nếu YOUTUBE_ENABLED=true)

# YouTube control
YOUTUBE_ENABLED=false
YOUTUBE_PROXY_DOWNLOAD=0
YT_MAX_HEIGHT_USER=1080
YT_QUOTA_USER=5

# Platform cookies (base64 Netscape format)
YOUTUBE_COOKIES_B64 / TIKTOK_COOKIES_B64 / FACEBOOK_COOKIES_B64
INSTAGRAM_COOKIES_B64 / TWITTER_COOKIES_B64

# YouTube bypass
BGUTIL_POT_URL=http://bgutil-pot1:4416,http://bgutil-pot2:4416
COBALT_API_URL=http://cobalt-api:9000

# Queue
REDIS_URL / CELERY_BROKER_URL / CELERY_RESULT_BACKEND

# Notifications
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_DIST_BOT_TOKEN

# Quotas
MAX_CONCURRENT_DOWNLOADS=10
DAILY_QUOTA_PER_IP=50
DOWNLOADS_MAX_GB=10
FILE_EXPIRY_SINGLE_MIN=20

# App
ENV=production
ADMIN_PASSWORD=matbaosupport   # ⚠️ TODO: DB-backed
FRONTEND_URL=https://dowloadvideo.io.vn
```

### Deployment

- **Production VPS:** Oracle Cloud (`dowloadvideo.io.vn`, IP: 161.118.208.230)
- **Deploy:** `bash ~/workspace/projects/Dowload-video/deploy-vps.sh`
  - git bundle → SCP → SSH → rsync → `docker compose up --build`
- **Preview:** `docker-compose.preview.yml` (bỏ bgutil-pot, dùng Cobalt only)
- **Database:** Supabase auto-backup daily, 30-day retention
- **Redis:** AOF persistence (`redis-data` volume)

---

## 11. Tóm tắt nhanh (Quick Reference cho AI)

| Câu hỏi | Trả lời |
|---------|---------|
| Tool tải được YouTube không? | ⛔ Không, IP server bị block. Bật lại = 1 commit + proxy config |
| Tool tải được Spotify không? | ✅ Có, qua SoundCloud. Chất lượng match ~90% |
| Nền tảng nào tải tốt nhất? | TikTok, Douyin, SoundCloud, Bilibili, Pinterest |
| Nền tảng nào cần cookie? | Instagram, Twitter/X, Reddit (private), YouTube (channel) |
| Có tải được nhạc không? | ✅ MP3: SoundCloud, Spotify (via SC), Bilibili, VK, Odysee, Podcast |
| Tải hàng loạt được không? | ✅ Bulk URL + Channel scrape (với wave throttle) |
| Có xem trước playlist trước khi tải không? | ✅ Container Preview (Phase 25), 10 platforms |
| Extension có tự cài được không? | ❌ Cần bật Developer Mode (chưa lên Chrome Store) |
| Billing / payment có không? | Schema ready, Stripe chưa integrate |
| Deploy ở đâu? | Oracle Cloud VPS, không dùng Coolify |

---

*Phiên bản: 5.1 | Cập nhật: 2026-06-30*

> **Thay đổi v5.1 (2026-06-30):** Bổ sung đầy đủ 20+ platform vào matrix (Bilibili, VK, Twitch, Rumble, Lemon8, XiaoHongShu, Snapchat, Odysee, Dailymotion, Podcast RSS); thêm bảng Phase 16–24 build status; thêm mục đề xuất nâng cấp ưu tiên; Quick Reference table cho AI; circuit breaker + proxy architecture chi tiết hơn.
