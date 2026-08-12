FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests redis
COPY bot.py .
COPY extension/VidGrab-extension.zip /app/extension/VidGrab-extension.zip
CMD ["python", "-u", "bot.py"]
