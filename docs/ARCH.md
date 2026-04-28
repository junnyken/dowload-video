# Architecture: Video Downloader (VidGrab)

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite 8, TailwindCSS 4 |
| Backend | FastAPI 0.115, Python 3.12, Uvicorn |
| Task Queue | Celery 5.6, Redis 7 |
| Database | Supabase (PostgreSQL) |
| Video Engine | yt-dlp, FFmpeg, Cobalt API v11 |
| Proxy | ScraperAPI, IPRoyal |
| Container | Docker (Redis + Cobalt via docker-compose) |

## Services

```
┌─────────────┐     ┌─────────────────┐     ┌───────────────┐
│  Frontend   │────▶│  FastAPI :8000  │────▶│  Supabase DB  │
│  Vite :5173 │     │                 │     └───────────────┘
└─────────────┘     │  - /api/v1/*    │
                    │  - /api/v1/admin│     ┌───────────────┐
                    │  - /api/v1/pay  │────▶│  Redis :6379  │
                    └────────┬────────┘     └───────┬───────┘
                             │                      │
                    ┌────────▼────────┐    ┌────────▼───────┐
                    │  Celery Worker  │    │  Cobalt API    │
                    │  (4 concurrent) │    │  :9000         │
                    └─────────────────┘    └────────────────┘
```

## Folder Structure

```
Dowload-video/
├── backend/
│   ├── app/
│   │   ├── api/          # Routes: routes.py, admin.py, payments.py
│   │   ├── core/         # DB, cache, celery, quotas, proxy
│   │   ├── services/     # downloader, douyin, cobalt, spotify, apify
│   │   ├── tasks/        # video_tasks.py (Celery tasks)
│   │   └── utils/        # helpers, link_resolver
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/   # Sidebar, BulkContent, Dashboard, History...
│       └── pages/Admin/
├── chrome_extension/
├── database/             # schema.sql
└── docker-compose.cobalt.yml
```

## API Endpoints

| Method | Path | Mô tả |
|---|---|---|
| GET | `/` | Health check |
| GET | `/health` | Detailed health |
| POST | `/api/v1/fetch-link` | Tải video đơn |
| POST | `/api/v1/fetch-spotify` | Spotify playlist/album |
| POST | `/api/v1/bulk-download` | Tải hàng loạt |
| POST | `/api/v1/bulk-zip` | Tạo ZIP cho batch |
| GET | `/api/v1/jobs/{batch_id}` | Poll tiến trình batch |
| GET | `/api/v1/quota` | Quota người dùng |
| GET | `/api/v1/history` | Lịch sử download |
| DELETE | `/api/v1/history/{job_id}` | Xóa job |
| GET | `/api/v1/proxy-download` | Stream video qua backend |
| GET | `/api/v1/download-local` | Tải file local (MP3) |
| GET | `/api/v1/admin/stats` | Admin statistics |
| POST | `/api/v1/admin/update-user` | Admin update user |
| POST | `/api/v1/payments/webhook` | Payment webhook |

## Data Models (Supabase)

### `download_jobs`
| Column | Type | Mô tả |
|---|---|---|
| id | UUID PK | Job ID |
| batch_id | VARCHAR | Nhóm các URL cùng batch |
| original_url | TEXT | URL gốc |
| title | TEXT | Tiêu đề video |
| direct_mp4_url | TEXT | Link tải trực tiếp |
| status | ENUM | pending/processing/success/failed |
| error_message | TEXT | Lỗi nếu có |
| created_at | TIMESTAMPTZ | Thời điểm tạo |

### `user_usage` · `user_credits` · `provider_status`
<!-- TODO: bổ sung schema chi tiết -->

## Video Extraction Flow

```
URL input
  ├── Douyin/TikTok → douyin_extractor (TikWM / douyin.wtf / ScraperAPI)
  ├── YouTube       → Cobalt API → yt-dlp fallback
  ├── Spotify       → spotipy (get tracks) → yt-dlp ytsearch → FFmpeg MP3
  └── Others        → yt-dlp + proxy (IPRoyal for meta, direct CDN for download)
```

## Key Decisions

- **Celery + Redis** thay vì async FastAPI thuần: cần persistent queue, retry, và worker isolation cho FFmpeg
- **Cache 24h**: tránh re-fetch cùng URL trong ngày, tiết kiệm proxy cost
- **Cobalt API**: YouTube ngày càng chặn yt-dlp bot, Cobalt là fallback ổn định
- **IP-based quota**: không yêu cầu đăng ký tài khoản, giảm friction người dùng
- **Auto cleanup 15 phút**: tránh disk đầy từ file MP3/MP4 tạm
