# Hướng dẫn Khởi chạy và Yêu cầu Hệ thống

## 1. Yêu cầu Hệ thống (System Requirements)
Dự án Video Downloader tích hợp **Spotify to MP3 320kbps Engine** và **Automatic Cleanup**, do đó yêu cầu các thành phần phần mềm bổ sung:

- Node.js (phiên bản 18+ cho Frontend)
- Python 3.10+ (cho Backend)
- Redis (cho tính năng caching và Celery Worker)
- **FFmpeg (Bắt buộc)**: yt-dlp sử dụng Post-processor để chuyển đổi định dạng và chất lượng Audio. Bạn **bắt buộc** phải cài đặt hệ thống này trên máy chủ chạy Backend.

### Cài đặt FFmpeg:
**Trên Linux (Ubuntu/Debian/Render):**
```bash
sudo apt-get update && sudo apt-get install ffmpeg -y
```

**Trên Docker (Đã cấu hình trong thư mục backend/Dockerfile):**
```dockerfile
RUN apt-get update && apt-get install -y ffmpeg
```

**Trên Windows (Sử dụng choco hoặc tải thủ công):**
```powershell
choco install ffmpeg
```

## 2. Các biến môi trường (.env)
Bạn cần thiết lập các tệp `.env` ở Backend và Frontend:

### Backend `.env`:
```
# Cấu hình Database
SUPABASE_URL=...
SUPABASE_KEY=...

# Proxies
ZENROWS_API_KEY=7b73fbf26ec30aba13f7c67dd93d5766b9fcdcf9
SCRAPERAPI_KEY=ee4213d31acd11652b22b538c3f3efa2

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...

# Spotify
SPOTIPY_CLIENT_ID=...
SPOTIPY_CLIENT_SECRET=...

# Celery & Quotas
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
REDIS_URL=redis://localhost:6379/0
FREE_DAILY_LIMIT=5
```

## 3. Khởi chạy Hệ thống

### Bước 1: Khởi động Redis
- Chạy server Redis. (Trên Docker: `docker run -p 6379:6379 -d redis`)

### Bước 2: Chạy Backend (FastAPI + Celery)
Mở terminal và trỏ tới thư mục `backend/`:
```bash
# Cài thư viện
pip install -r requirements.txt

# 1. Chạy tiến trình Celery (Quản lý queue/tải xuống)
celery -A app.core.celery_app worker --loglevel=info

# 2. Khởi chạy FastAPI
uvicorn app.main:app --reload --port 8000
```

### Bước 3: Chạy Frontend (React/Vite)
Mở terminal khác và trỏ tới thư mục `frontend/`:
```bash
npm install
npm run dev
```
