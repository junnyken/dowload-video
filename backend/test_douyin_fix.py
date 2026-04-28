"""Test Douyin extraction approaches."""
import httpx
import re
import json
import sys
import urllib.parse

sys.stdout.reconfigure(encoding='utf-8')

URL = "https://v.douyin.com/U7FRoXWosnY/"

print("=== Step 1: Resolve short URL ===")
r = httpx.get(URL, follow_redirects=True, headers={
    'User-Agent': 'com.ss.android.ugc.aweme/230904 (Linux; Android 12; SM-G998B)'
})
final_url = str(r.url)
print(f"Final URL: {final_url[:150]}")

vid_match = re.search(r'/video/(\d+)', final_url)
if vid_match:
    vid = vid_match.group(1)
    print(f"Video ID: {vid}")
else:
    print("No video ID found!")
    sys.exit(1)

print("\n=== Step 2: Try Douyin share page scraping ===")
share_url = f"https://www.douyin.com/video/{vid}"
r2 = httpx.get(share_url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://www.douyin.com/',
    'Cookie': '',
}, follow_redirects=True)
print(f"Status: {r2.status_code}, Length: {len(r2.text)}")

# Find RENDER_DATA
m = re.search(r'id="RENDER_DATA"[^>]*>(.*?)</script>', r2.text, re.DOTALL)
if m:
    encoded_data = m.group(1)
    decoded = urllib.parse.unquote(encoded_data)
    print(f"Found RENDER_DATA ({len(decoded)} chars)")
    # Try to parse JSON
    try:
        data = json.loads(decoded)
        # Find video URLs in the data
        data_str = json.dumps(data)
        mp4_urls = re.findall(r'https?://[^"]+\.mp4[^"]*', data_str)
        print(f"MP4 URLs found: {len(mp4_urls)}")
        for u in mp4_urls[:3]:
            print(f"  {u[:150]}")
        # Find title
        titles = re.findall(r'"desc"\s*:\s*"([^"]+)"', data_str)
        if titles:
            print(f"Title: {titles[0][:80]}")
    except Exception as e:
        print(f"JSON parse error: {e}")
else:
    print("No RENDER_DATA found")
    # Try _ROUTER_DATA
    m2 = re.search(r'self\.__next_f\.push.*?"data":\s*({.*?})\s*\]', r2.text, re.DOTALL)
    if m2:
        print("Found Next.js data")
    
    # Check for other patterns
    patterns = ['RENDER_DATA', 'SSR_RENDER_DATA', 'INITIAL_STATE', '_ROUTER_DATA', 'routerData', '__next']
    for p in patterns:
        if p in r2.text:
            print(f"Found pattern: {p}")

    # Find any video URLs
    mp4_urls = re.findall(r'https?://[^"\']+\.mp4[^"\']*', r2.text)
    print(f"MP4 URLs in page: {len(mp4_urls)}")
    for u in mp4_urls[:3]:
        print(f"  {u[:150]}")

print("\n=== Step 3: Try TikTok API for Douyin ===")
# Some TikTok APIs work for Douyin too
try:
    r3 = httpx.post("https://www.tikwm.com/api/", data={"url": f"https://www.douyin.com/video/{vid}"}, timeout=15)
    data = r3.json()
    print(f"tikwm code: {data.get('code')}, msg: {data.get('msg', '')[:80]}")
    if data.get('code') == 0:
        d = data.get('data', {})
        print(f"Title: {d.get('title', '')[:80]}")
        print(f"Play URL: {d.get('play', '')[:150]}")
except Exception as e:
    print(f"tikwm error: {e}")

print("\n=== Step 4: Try cobalt.tools ===")
try:
    r4 = httpx.post("https://co.cobalt.tools/", 
        json={"url": f"https://www.douyin.com/video/{vid}"},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=15)
    print(f"cobalt status: {r4.status_code}")
    if r4.status_code == 200:
        data = r4.json()
        print(f"cobalt response: {json.dumps(data)[:200]}")
except Exception as e:
    print(f"cobalt error: {e}")
