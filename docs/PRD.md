# PRD: Video Downloader (VidGrab)

## Problem Statement

Người dùng cần tải video từ nhiều nền tảng (TikTok, YouTube, Instagram, Douyin, Spotify) nhưng các công cụ hiện tại thường bị chặn, có watermark, hoặc không hỗ trợ tải hàng loạt. Dự án cung cấp một nền tảng tải video tập trung, hỗ trợ nhiều nguồn, xóa watermark, và chuyển đổi sang MP3.

## Target Users

- Người dùng cá nhân muốn lưu trữ video ngoại tuyến
- Content creator cần tải nhanh nhiều video từ kênh/playlist
- Người dùng muốn chuyển Spotify playlist → MP3

## User Journeys

### 1. Tải video đơn
1. Paste URL video vào ô input
2. Chọn chất lượng (video/audio)
3. Nhận link tải trực tiếp → download

### 2. Tải hàng loạt (Bulk)
1. Paste nhiều URL (mỗi dòng 1 URL)
2. Hệ thống queue vào Celery → xử lý song song
3. Theo dõi tiến trình real-time
4. Download từng file hoặc ZIP toàn bộ batch

### 3. Spotify → MP3
1. Paste URL playlist/album Spotify
2. Hệ thống lấy danh sách track → tìm trên YouTube → convert MP3 320kbps
3. Download từng bài

### 4. Quản lý lịch sử
1. Xem lịch sử download gần nhất
2. Xóa job cũ

## Functional Requirements

### Đã có
- [x] Tải video đơn từ TikTok, YouTube, Instagram, Douyin, Facebook
- [x] Xóa watermark TikTok/Douyin
- [x] Tải hàng loạt URL (bulk download)
- [x] Scrape kênh/playlist (max 100 videos)
- [x] Spotify playlist/album → MP3
- [x] Celery task queue với Redis broker
- [x] Cache kết quả 24h (tránh re-fetch cùng URL)
- [x] Rate limiting (slowapi)
- [x] Quota per user (IP-based)
- [x] Proxy stream để bypass CORS
- [x] ZIP toàn bộ batch
- [x] Admin dashboard
- [x] Chrome Extension
- [x] Cobalt API integration (YouTube fallback)
- [x] Auto cleanup file sau 15 phút

### Out of Scope
<!-- TODO: liệt kê tính năng không làm -->

## Success Criteria
<!-- TODO: KPIs, metrics đo lường thành công -->

## Tech Stack

- **Frontend**: React 19 + Vite + TailwindCSS
- **Backend**: FastAPI + Python 3.12
- **Task Queue**: Celery + Redis
- **Database**: Supabase (PostgreSQL)
- **Video Engine**: yt-dlp + Cobalt API + FFmpeg
- **Proxy**: ScraperAPI, IPRoyal
