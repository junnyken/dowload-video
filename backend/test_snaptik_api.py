"""
Test Douyin extraction using the SSR/share approach similar to SnapTikth.
They use server-side rendered pages + multiple server fallbacks.
"""
import httpx
import re
import json
from urllib.parse import unquote

douyin_url = "https://v.douyin.com/U7FRoXWosnY/"

def extract_douyin_video(short_url: str) -> dict:
    """Extract Douyin video info using web scraping approach."""
    
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        # Step 1: Resolve short URL → get video ID
        r = client.get(short_url, headers={"User-Agent": UA})
        final_url = str(r.url)
        print(f"Resolved: {final_url}")
        
        vid_match = re.search(r'/video/(\d+)', final_url)
        if not vid_match:
            raise ValueError(f"Cannot extract video ID from {final_url}")
        video_id = vid_match.group(1)
        print(f"Video ID: {video_id}")
        
        # Step 2: Fetch the video page (SSR)
        video_url = f"https://www.douyin.com/video/{video_id}"
        page = client.get(video_url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.douyin.com/",
        })
        
        print(f"Page status: {page.status_code}, length: {len(page.text)}")
        
        # Step 3: Extract RENDER_DATA (SSR embedded JSON)
        render_match = re.search(
            r'<script\s+id="RENDER_DATA"\s+type="application/json">(.*?)</script>',
            page.text, re.DOTALL
        )
        
        if render_match:
            raw = unquote(render_match.group(1))
            print(f"RENDER_DATA found, length: {len(raw)}")
            
            try:
                data = json.loads(raw)
                # Navigate the nested structure
                for key, val in data.items():
                    if isinstance(val, dict):
                        # Look for awemeDetail or similar
                        detail = None
                        if "awemeDetail" in str(val)[:500]:
                            # Find it recursively
                            detail_str = re.search(r'"awemeDetail"\s*:\s*({.*?"desc".*?})', json.dumps(val, ensure_ascii=False))
                        
                        # Try to find video play URL
                        play_urls = re.findall(r'"playApi"\s*:\s*"([^"]+)"', json.dumps(val, ensure_ascii=False))
                        if play_urls:
                            play_url = play_urls[0].replace("\\u002F", "/")
                            print(f"✅ playApi: {play_url[:150]}")
                        
                        # Find no-watermark URL (bit_rate list)
                        bitrate_urls = re.findall(r'"url_list"\s*:\s*\["([^"]+)"', json.dumps(val, ensure_ascii=False))
                        if bitrate_urls:
                            for bu in bitrate_urls[:3]:
                                clean = bu.replace("\\u002F", "/")
                                print(f"  url_list: {clean[:150]}")
                        
                        # Find desc/title  
                        desc_match = re.findall(r'"desc"\s*:\s*"([^"]{5,200})"', json.dumps(val, ensure_ascii=False))
                        if desc_match:
                            print(f"  Title: {desc_match[0][:100]}")
                        
                        # Find cover/thumbnail
                        cover_match = re.findall(r'"cover"\s*:\s*\{[^}]*"url_list"\s*:\s*\["([^"]+)"', json.dumps(val, ensure_ascii=False))
                        if cover_match:
                            print(f"  Cover: {cover_match[0][:100]}")
                            
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
        else:
            print("No RENDER_DATA found")
            
            # Fallback: look for __NEXT_DATA__ or similar
            next_data = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', page.text, re.DOTALL)
            if next_data:
                print("Found __NEXT_DATA__")
                raw = next_data.group(1)[:2000]
                print(raw[:500])
            
            # Look for any video CDN URLs
            cdn_urls = re.findall(r'(https?://v[0-9]*[\w.-]+douyin[\w.-]+/[^\s"\'<>]+)', page.text)
            if cdn_urls:
                print(f"CDN URLs found: {len(cdn_urls)}")
                for u in cdn_urls[:3]:
                    print(f"  {u[:150]}")
            else:
                # Check if page requires JS (cookie wall)
                if "验证" in page.text or "captcha" in page.text.lower():
                    print("⚠️ Page requires CAPTCHA/verification")
                elif len(page.text) < 5000:
                    print(f"⚠️ Page too small, likely blocked. Content: {page.text[:500]}")

extract_douyin_video(douyin_url)
