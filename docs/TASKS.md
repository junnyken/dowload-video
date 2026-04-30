# Tasks: Video Downloader (VidGrab)

## Đang làm
- [ ] Verify webhook signature cho payments (`backend/app/api/payments.py:21`)
- [ ] Thiết lập Spotify credentials (`SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`) trong .env

## Đã xong
- [x] Initial commit — scaffold toàn bộ backend + frontend
- [x] FastAPI backend với rate limiting và CORS
- [x] Celery worker với Redis broker
- [x] yt-dlp integration đa nền tảng
- [x] Douyin extractor chuyên biệt (bypass watermark)
- [x] Cobalt API fallback cho YouTube
- [x] Spotify service (playlist/album → MP3)
- [x] Bulk download + channel scraping
- [x] ZIP batch download
- [x] Cache 24h cho URL đã fetch
- [x] Quota management (IP-based)
- [x] Admin dashboard
- [x] Chrome Extension
- [x] Auto cleanup file sau 15 phút
- [x] Proxy stream endpoint (bypass CORS)
- [x] Supabase schema (download_jobs, user_usage, user_credits)
- [x] Docker setup (Redis + Cobalt)
- [x] ZIP enhancement: hiển thị dung lượng ZIP + tổng files + ước tính size trước khi nén
- [x] Archive service: retry download 3 lần + exponential backoff + timeout 180s
- [x] Bulk job lưu file_size_mb vào DB cho từng video
- [x] Chrome Extension v4.0: download speed, copy link, retry, success animation, Spotify support
- [x] Content script v4.0: file size preview trên floating button, toast notification
- [x] Cache normalize URL mở rộng (YouTube, Douyin, Instagram, Facebook)
- [x] User-friendly Vietnamese error messages trong Celery tasks
- [x] Landing page: thêm Spotify platform, cập nhật mô tả đầy đủ

## Backlog
<!-- TODO: tính năng tương lai -->
- [ ] Xác thực người dùng (Supabase Auth)
- [ ] Dashboard analytics cho admin
- [ ] Hỗ trợ thêm nền tảng (Pinterest, Twitter/X)
- [ ] Webhook notification (Telegram) khi batch xong
- [ ] Resumable batch download (tải lại batch đã expire)
- [ ] ZIP compression level option (ZIP_STORED vs ZIP_DEFLATED)
- [ ] Proxy xoay vòng (rotating proxy) cho anti-bot bypass
