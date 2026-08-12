"""Quick test for the updated Douyin extraction pipeline."""
import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from app.services.douyin_extractor import extract_douyin_video

async def main():
    test_url = "https://v.douyin.com/5dSZhI1Q4f8/"
    print(f"Testing: {test_url}\n", flush=True)
    
    try:
        result = await extract_douyin_video(test_url)
        print(f"\n{'='*60}", flush=True)
        print(f"Provider:  {result.get('provider')}", flush=True)
        print(f"Title:     {result.get('title', '')[:80]}", flush=True)
        print(f"Video URL: {result.get('direct_mp4_url', '')[:150]}", flush=True)
        print(f"Thumbnail: {result.get('thumbnail_url', '')[:100]}", flush=True)
        print(f"Audio URL: {result.get('audio_url', '')[:100]}", flush=True)
        print(f"Size:      {result.get('file_size_mb')} MB", flush=True)
        print(f"Is Audio:  {result.get('is_audio')}", flush=True)
        print(f"{'='*60}", flush=True)
        print("✅ SUCCESS!", flush=True)
    except Exception as e:
        print(f"\n❌ FAILED: {e}", flush=True)

asyncio.run(main())
