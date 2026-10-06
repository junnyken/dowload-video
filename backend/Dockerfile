# Sử dụng Python image chính thức
#
# 3.12, not 3.13: this is the version the test suite actually runs on
# (backend/venv is 3.12), so the 1457 passing tests are evidence for this
# image rather than for a version nobody has exercised.
#
# Why move at all — 3.10 still works today. yt-dlp already prints "Support for
# Python version 3.10 has been deprecated" on every run, and yt-dlp is the one
# dependency this project cannot afford to stop upgrading: a stale copy of it
# is the most likely root cause of the Facebook reel outage on 03-09, fixed by
# an image rebuild that pulled a newer release. Being unable to take the next
# yt-dlp because the base image aged out is the failure worth avoiding early.
FROM python:3.12-slim

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
    && cd /opt/bgutil-pot/server \
    && DENO_DIR=/opt/deno-cache DENO_NO_PROMPT=1 deno install --allow-scripts \
    && test -d /opt/bgutil-pot/server/node_modules \
    && cd /app \
    && mkdir -p /opt/bgutil-cache \
    && chmod -R a+rX /opt/bgutil-pot /opt/deno-cache \
    && chmod 777 /opt/bgutil-cache \
    && apt-get purge -y wget unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Deno reads its module cache from here at runtime.
ENV DENO_DIR=/opt/deno-cache
# The generator writes a token cache under XDG_CACHE_HOME (falling back to
# $HOME/.cache); point it somewhere writable rather than relying on the
# container user having a usable home directory.
ENV XDG_CACHE_HOME=/opt/bgutil-cache

# PhantomJS for yt-dlp's iq.com (iQIYI) extractor. yt-dlp 2026.08.19 runs the
# iQIYI signature code ONLY through PhantomJSwrapper, which needs an executable
# named `phantomjs` on PATH ("PhantomJS not found" on 2026-10-05). Deno and
# --js-runtimes do not apply to it (yt-dlp issue #17570). PhantomJS is
# discontinued; 2.1.1 is its last release and still what yt-dlp calls.
# Official build, pinned by SHA-256 (same file on bitbucket.org/ariya and the
# github.com/Medium mirror, checked 2026-10-06). Downloaded and unpacked with
# Python so no wget/bzip2 is added to the image; only libfontconfig1 (the
# binary's one missing shared library on slim) is installed.
# The wrapper sets OPENSSL_CONF=/dev/null for this process only: the binary
# links OpenSSL 1.0 statically, which cannot parse Debian's OpenSSL 3 config
# and exits with "Auto configuration failed" otherwise.
ARG PHANTOMJS_VERSION=2.1.1
ARG PHANTOMJS_SHA256=86dd9a4bf4aee45f1a84c9f61cf1947c1d6dce9b9e8d2a907105da7852460d2f
# Optional: a failed download or checksum mismatch must not break the whole
# backend build for one extra extractor — iq.com then stays unavailable (as
# before) and /health reports js_runtimes.phantomjs = not found.
RUN apt-get update && apt-get install -y --no-install-recommends libfontconfig1 \
    && rm -rf /var/lib/apt/lists/* \
    && ( python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/Medium/phantomjs/releases/download/v${PHANTOMJS_VERSION}/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64.tar.bz2', '/tmp/phantomjs.tar.bz2')" \
    && echo "${PHANTOMJS_SHA256}  /tmp/phantomjs.tar.bz2" | sha256sum -c - \
    && python3 -c "import tarfile; tarfile.open('/tmp/phantomjs.tar.bz2').extract('phantomjs-${PHANTOMJS_VERSION}-linux-x86_64/bin/phantomjs', '/opt', filter='data')" \
    && mv /opt/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64 /opt/phantomjs \
    && chmod 0755 /opt/phantomjs/bin/phantomjs \
    && rm /tmp/phantomjs.tar.bz2 \
    && printf '#!/bin/sh\nOPENSSL_CONF=/dev/null exec /opt/phantomjs/bin/phantomjs "$@"\n' > /usr/local/bin/phantomjs \
    && chmod 0755 /usr/local/bin/phantomjs \
    && phantomjs -v ) \
    || ( echo "WARNING: PhantomJS not installed - iq.com (iQIYI) stays unavailable" \
         && rm -rf /tmp/phantomjs.tar.bz2 /opt/phantomjs /opt/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64 /usr/local/bin/phantomjs )

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

# Run the app as a normal user instead of root.
#
# Celery said so itself on every boot: "SecurityWarning: You're running the
# worker with superuser privileges". It matters more here than in a typical
# web app — this container hands attacker-supplied URLs to yt-dlp and pipes
# whatever comes back through ffmpeg, which is the largest remote-code-
# execution surface in the project. Root inside a container is not root on the
# host, but it is the difference between an exploit that owns the process and
# one that also rewrites the application it lives in.
#
# The container still STARTS as root, deliberately: the platform mounts a
# volume over /app/downloads, and a mount arrives owned by root no matter what
# this image does. The entrypoint fixes ownership of the mount and then drops
# to this user, which is the only ordering that survives both a mounted and an
# unmounted deploy. Dropping here with USER would work today and break the
# first time a volume appears.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/downloads /app/ytdlp_cache /opt/bgutil-cache \
    && chown -R appuser:appuser /app /opt/bgutil-cache

# Single-container deploy: run celery worker (background) + uvicorn (foreground)
RUN chmod +x docker-entrypoint.sh
CMD ["./docker-entrypoint.sh"]
