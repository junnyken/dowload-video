"""Quick test for TikWM API integration."""
import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

from app.services.douyin_extractor import extract_douyin_video

async def main():
    test_url = "https://v.douyin.com/U7FRoXWosnY/"
    print(f"Testing: {test_url}\n")
    
    try:
        result = await extract_douyin_video(test_url)
        print(f"\n{'='*60}")
        print(f"Provider:  {result.get('provider')}")
        print(f"Title:     {result.get('title', '')[:80]}")
        print(f"Thumbnail: {result.get('thumbnail_url', '')[:100]}")
        print(f"Video URL: {result.get('direct_mp4_url', '')[:120]}")
        print(f"Size:      {result.get('file_size_mb')} MB")
        print(f"SUCCESS!")
    except Exception as e:
        print(f"FAILED: {e}")

asyncio.run(main())
