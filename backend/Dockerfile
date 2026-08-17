# Sử dụng Python image chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt FFmpeg (kho Debian — nguồn static binary johnvansickle.com bị egress
# block từ container trên host này, apt ổn định hơn) + Deno (JS challenge
# solver cho yt-dlp 2026)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg wget unzip \
    && wget -q https://github.com/denoland/deno/releases/download/v2.4.1/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -q deno-x86_64-unknown-linux-gnu.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm deno-x86_64-unknown-linux-gnu.zip \
    && apt-get purge -y wget unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

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

# Single-container deploy: run celery worker (background) + uvicorn (foreground)
RUN chmod +x docker-entrypoint.sh
CMD ["./docker-entrypoint.sh"]
