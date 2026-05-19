# Sử dụng Python image chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt FFmpeg (Bắt buộc cho tính năng convert MP3 320kbps) vả các dependencies
RUN apt-get update && apt-get install -y ffmpeg

# Copy yêu cầu và cài đặt môi trường
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PO Token provider plugin — yt-dlp needs this to bypass YouTube bot detection
# Works with bgutil-pot sidecar container (HTTP server on port 4416)
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider

# Copy toàn bộ code backend
COPY . .

# Expose port (tuỳ chọn)
EXPOSE 8000

# Chạy ứng dụng bằng uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
