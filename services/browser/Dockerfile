# VidGrab browser resolver (owner 15/10): a real Chromium with a persistent
# profile resolves Douyin / Xiaohongshu / Kuaishou pages that block plain HTTP
# (their pages need a JS signature). Free step before the paid Apify actor.
# Listens on 3000 (the port Vibe Host routes to).
# Required env: BROWSER_KEY (same value as BROWSER_RESOLVER_KEY on the backend)
# Optional env: MAX_TABS (default 3), JITTER_MIN_S / JITTER_MAX_S, PROFILE_DIR
# Data path: /data (persistent profile → cookies survive restarts).
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py resolvers.py ./

ENV PORT=3000 PROFILE_DIR=/data/profile MAX_TABS=3 PYTHONUNBUFFERED=1
EXPOSE 3000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
