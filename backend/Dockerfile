# Sử dụng Python image chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt FFmpeg via static binary + Deno (JS challenge solver cho yt-dlp 2026)
RUN apt-get update && apt-get install -y --no-install-recommends wget xz-utils unzip \
    && wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    && tar xf ffmpeg-release-amd64-static.tar.xz \
    && mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ \
    && mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ \
    && rm -rf ffmpeg-*-amd64-static* ffmpeg-release-amd64-static.tar.xz \
    && wget -q https://github.com/denoland/deno/releases/download/v2.4.1/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -q deno-x86_64-unknown-linux-gnu.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm deno-x86_64-unknown-linux-gnu.zip \
    && apt-get purge -y wget xz-utils unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy yêu cầu và cài đặt môi trường
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Patch bgutil-ytdlp-pot-provider: server v0.8.x vs plugin v1.3.x — API compatible,
# only the major version string differs. Remove the hard raise so the plugin proceeds.
COPY scripts/patch_bgutil.py /tmp/patch_bgutil.py
RUN python3 /tmp/patch_bgutil.py && rm /tmp/patch_bgutil.py

# Copy toàn bộ code backend
COPY . .

# Pre-warm yt-dlp EJS solver cache (deno + yt_dlp_ejs, caches player JS)
RUN mkdir -p /app/ytdlp_cache && \
    python3 -m yt_dlp \
        --no-download \
        --skip-download \
        --cache-dir /app/ytdlp_cache \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
    2>/dev/null || true

# Expose port (tuỳ chọn)
EXPOSE 8000

# Chạy ứng dụng bằng uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
