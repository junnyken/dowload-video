# Sử dụng Python image chính thức
FROM python:3.10-slim

# Không đệm stdout/stderr. Codebase này dùng print() rất nhiều cho chẩn đoán
# ([Startup], [Heartbeat], [Alerts], [Watchdog]...); khi stdout là pipe thì
# Python đệm theo block, nên các dòng đó ra log trễ và mất hẳn nếu tiến trình
# chết trước lúc flush — đúng lúc cần đọc nhất.
ENV PYTHONUNBUFFERED=1

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt FFmpeg (kho Debian — nguồn static binary johnvansickle.com bị egress
# block từ container trên host này, apt ổn định hơn) + Deno (JS challenge
# solver cho yt-dlp 2026)
# The same RUN also fetches the PO token provider's script, because the last
# line purges wget — a separate later layer had nothing to download with and
# failed at exit 127.
#
# Why it is needed: android_vr URLs require a PO token, and without one the CDN
# answers 403 on every byte fetch even when the requesting IP matches the ip=
# signed into the link. Measured on production: extraction succeeded, every
# download failed. The plugin's HTTP mode wants a separate service — the compose
# hostname `bgutil-pot` does not resolve on this split-project host, and hosting
# it as its own project failed the platform's health gate (it serves /get_pot,
# not /). The script provider runs the generator locally instead and prefers
# Deno, installed just below for yt-dlp's JS challenge solving.
#
# Pinned to the plugin's own version: the script reports its version and a major
# mismatch is rejected — the drift scripts/patch_bgutil.py works around for the
# 0.8.x HTTP image.
ARG BGUTIL_VERSION=1.3.1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg wget unzip \
    && wget -q https://github.com/denoland/deno/releases/download/v2.4.1/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -q deno-x86_64-unknown-linux-gnu.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm deno-x86_64-unknown-linux-gnu.zip \
    && wget -q -O /tmp/bgutil.tar.gz \
         "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/${BGUTIL_VERSION}.tar.gz" \
    && mkdir -p /opt/bgutil-pot \
    && tar -xzf /tmp/bgutil.tar.gz -C /opt/bgutil-pot --strip-components=1 \
    && rm /tmp/bgutil.tar.gz \
    && test -f /opt/bgutil-pot/server/src/generate_once.ts \
    && DENO_DIR=/opt/deno-cache deno cache --allow-import \
         /opt/bgutil-pot/server/src/generate_once.ts \
    && chmod -R a+rX /opt/bgutil-pot /opt/deno-cache \
    && apt-get purge -y wget unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Deno writes to its cache at runtime; point it where the app user can read it.
ENV DENO_DIR=/opt/deno-cache

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
