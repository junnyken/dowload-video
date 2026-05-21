# Sử dụng Python image chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt FFmpeg via static binary — tránh apt-get install hàng trăm packages (~15 phút)
# Static binary: không cần lib dependencies, build time ~2 phút
RUN apt-get update && apt-get install -y --no-install-recommends wget xz-utils nodejs \
    && wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    && tar xf ffmpeg-release-amd64-static.tar.xz \
    && mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ \
    && mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ \
    && rm -rf ffmpeg-*-amd64-static* ffmpeg-release-amd64-static.tar.xz \
    && apt-get purge -y wget xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy yêu cầu và cài đặt môi trường
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code backend
COPY . .

# Expose port (tuỳ chọn)
EXPOSE 8000

# Chạy ứng dụng bằng uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
