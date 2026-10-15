# VidGrab douyin-mint (owner 15/10/2026): opens douyin.com ONCE in a real
# Chromium (patchright) and hands the anonymous visitor cookies to the backend,
# which signs Douyin web API calls with them (backend/app/services/douyin_sign).
# Not a per-video resolver: the backend asks for a new jar every few hours.
# Listens on 3000 (the port Vibe Host routes to). Required env: MINT_KEY.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && patchright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip
COPY app.py ./

ENV PORT=3000 PYTHONUNBUFFERED=1
EXPOSE 3000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
