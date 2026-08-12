import httpx
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Simulate frontend call
print("=== Test: Fetch link for TikTok ===")
try:
    r = httpx.post(
        "http://127.0.0.1:8000/api/v1/fetch-link",
        json={
            "url": "https://www.tiktok.com/@rollwgrzxkh/video/7629952502376500487",
            "quality": "video",
            "remove_watermark": True,
        },
        timeout=60,
    )
    data = r.json()
    print("HTTP Status:", r.status_code)
    print("Data:", data)
except Exception as e:
    print(f"ERROR: {e}")
