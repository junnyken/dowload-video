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
# PO token provider, script mode.
#
# YouTube issues signed videoplayback URLs to one InnerTube client, and the
# android_vr URLs this app extracts also require a PO token — without one the
# CDN answers 403 on every byte fetch even when the requesting IP matches the
# ip= embedded in the link. Measured on production: extraction succeeded, every
# download failed, for 4K and 720p alike.
#
# The plugin's HTTP mode expects a separate provider service. This deployment
# runs backend and frontend as separate projects rather than from
# docker-compose.yml, so its default host `bgutil-pot` never resolved, and
# hosting it as its own project ran into the platform's health gate (the API
# serves /get_pot, not /, so the gate saw no 2xx and discarded the container).
#
# The same plugin also ships a script provider that runs the generator locally
# with no server at all, and it prefers Deno — which is already installed above
# for yt-dlp's JS challenge solving. So this needs no new service, no health
# gate, no cross-project networking, and nothing exposed publicly.
#
# Pinned to the plugin's own version (bgutil-ytdlp-pot-provider==1.3.1 below):
# the script reports its version to the plugin and a major mismatch is rejected,
# which is the very problem scripts/patch_bgutil.py exists to paper over for the
# 0.8.x HTTP image.
ARG BGUTIL_VERSION=1.3.1
RUN wget -q -O /tmp/bgutil.tar.gz \
      "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/${BGUTIL_VERSION}.tar.gz" \
    && mkdir -p /opt/bgutil-pot \
    && tar -xzf /tmp/bgutil.tar.gz -C /opt/bgutil-pot --strip-components=1 \
    && rm /tmp/bgutil.tar.gz \
    && test -f /opt/bgutil-pot/server/src/generate_once.ts \
    && DENO_DIR=/opt/deno-cache deno cache --allow-import \
         /opt/bgutil-pot/server/src/generate_once.ts \
    && chmod -R a+rX /opt/bgutil-pot /opt/deno-cache
# Deno writes to its cache at runtime; point it somewhere the app user can use.
ENV DENO_DIR=/opt/deno-cache

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
