"""Final attempt - Fix TikWM via www.tikwm.com and test other endpoints."""
import sys, os, time, re, json
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

import httpx
import asyncio

def p(msg):
    print(msg, flush=True)

VIDEO_ID = "7629158822828838179"
SHORT_URL = "https://v.douyin.com/5dSZhI1Q4f8/"
CANONICAL_URL = f"https://www.douyin.com/video/{VIDEO_ID}"

async def test_tikwm_get():
    """TikWM - GET request via www.tikwm.com (not api.tikwm.com which is blocked)."""
    p("\n--- TikWM GET via www.tikwm.com ---")
    
    # Try different URL formats
    test_urls = [
        SHORT_URL,
        CANONICAL_URL,
        f"https://www.douyin.com/video/{VIDEO_ID}",
        f"https://www.iesdouyin.com/share/video/{VIDEO_ID}",
    ]
    
    for url in test_urls:
        p(f"\n  Testing URL: {url}")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # GET request like the original api.tikwm.com but to www.tikwm.com
                t0 = time.time()
                resp = await client.get(
                    "https://www.tikwm.com/api/",
                    params={"url": url, "hd": 1},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/json",
                    }
                )
                elapsed = time.time() - t0
                p(f"  HTTP: {resp.status_code} ({elapsed:.1f}s)")
                
                if resp.status_code == 200:
                    data = resp.json()
                    code = data.get("code")
                    msg = data.get("msg", "")
                    p(f"  code={code}, msg={msg}")
                    
                    if code == 0:
                        d = data.get("data", {})
                        title = d.get("title", "N/A")
                        hd = d.get("hdplay", "")
                        play = d.get("play", "")
                        size = d.get("size", 0)
                        cover = d.get("cover", "")
                        
                        direct = hd or play
                        if direct and direct.startswith("/"):
                            direct = f"https://www.tikwm.com{direct}"
                        
                        p(f"  Title: {title[:70]}")
                        p(f"  HD: {hd[:100] if hd else 'N/A'}")
                        p(f"  Play: {play[:100] if play else 'N/A'}")
                        p(f"  Size: {round(size/(1024*1024),2) if size else 0} MB")
                        p(f"  Cover: {cover[:80] if cover else 'N/A'}")
                        
                        if direct:
                            # Verify download
                            try:
                                head = await client.head(direct, follow_redirects=True, timeout=10)
                                ct = head.headers.get("content-type", "")
                                cl = head.headers.get("content-length", "")
                                p(f"  HEAD check: {head.status_code}, type={ct[:50]}, length={cl}")
                            except Exception as he:
                                p(f"  HEAD error: {he}")
                            
                            p(f"  ✅ TikWM SUCCESS with URL: {url[:60]}")
                            return {
                                "provider": "tikwm",
                                "title": title,
                                "direct_url": direct,
                                "cover": cover,
                                "size_mb": round(size/(1024*1024), 2) if size else 0,
                                "working_input_url": url,
                            }
                    elif code == -1:
                        p(f"  Parsing failed for this URL format")
                else:
                    p(f"  Body: {resp.text[:200]}")
        except Exception as e:
            p(f"  Error: {e}")
    
    # Try POST to www.tikwm.com
    p(f"\n  Trying POST method...")
    for url in [SHORT_URL, CANONICAL_URL]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                t0 = time.time()
                resp = await client.post(
                    "https://www.tikwm.com/api/",
                    data={"url": url, "hd": 1, "count": 12, "cursor": 0, "web": 1, "lang": "en"},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/json",
                        "Origin": "https://www.tikwm.com",
                        "Referer": "https://www.tikwm.com/",
                    }
                )
                elapsed = time.time() - t0
                p(f"  POST {url[:50]}: {resp.status_code} ({elapsed:.1f}s)")
                
                if resp.status_code == 200:
                    data = resp.json()
                    p(f"  code={data.get('code')}, msg={data.get('msg', '')}")
                    
                    if data.get("code") == 0:
                        d = data.get("data", {})
                        title = d.get("title", "N/A")
                        hd = d.get("hdplay", "")
                        play = d.get("play", "")
                        
                        direct = hd or play
                        if direct and direct.startswith("/"):
                            direct = f"https://www.tikwm.com{direct}"
                        
                        p(f"  Title: {title[:70]}")
                        p(f"  Direct: {direct[:120] if direct else 'N/A'}")
                        
                        if direct:
                            p(f"  ✅ TikWM POST SUCCESS")
                            return {
                                "provider": "tikwm_post",
                                "title": title,
                                "direct_url": direct,
                            }
        except Exception as e:
            p(f"  Error: {e}")
    
    return None


async def test_scraperapi_to_tikwm():
    """Route TikWM API call through ScraperAPI to bypass DNS block."""
    p("\n--- TikWM via ScraperAPI proxy (premium) ---")
    
    SCRAPER_KEY = os.getenv("SCRAPERAPI_API_KEY", "")
    if not SCRAPER_KEY:
        p("  No ScraperAPI key")
        return None
    
    from urllib.parse import urlencode, quote
    
    # Use ultra_premium for api.tikwm.com
    for url_input in [SHORT_URL, CANONICAL_URL]:
        tikwm_url = f"https://api.tikwm.com/api/?url={quote(url_input)}&hd=1"
        p(f"\n  Target: {tikwm_url[:100]}")
        
        params = urlencode({
            "api_key": SCRAPER_KEY,
            "url": tikwm_url,
            "ultra_premium": "true",
        })
        
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                t0 = time.time()
                resp = await client.get(f"http://api.scraperapi.com/?{params}")
                elapsed = time.time() - t0
                p(f"  HTTP: {resp.status_code} ({elapsed:.1f}s)")
                
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        p(f"  code={data.get('code')}, msg={data.get('msg', '')}")
                        
                        if data.get("code") == 0:
                            d = data.get("data", {})
                            title = d.get("title", "N/A")
                            hd = d.get("hdplay", "")
                            play = d.get("play", "")
                            direct = hd or play
                            if direct and direct.startswith("/"):
                                direct = f"https://api.tikwm.com{direct}"
                            
                            p(f"  Title: {title[:70]}")
                            p(f"  Direct: {direct[:120] if direct else 'N/A'}")
                            
                            if direct:
                                p(f"  ✅ TikWM via ScraperAPI SUCCESS")
                                return {
                                    "provider": "tikwm_via_scraper",
                                    "title": title,
                                    "direct_url": direct,
                                }
                    except json.JSONDecodeError:
                        p(f"  Not JSON: {resp.text[:300]}")
                else:
                    p(f"  Body: {resp.text[:300]}")
        except Exception as e:
            p(f"  Error: {e}")
    
    return None


async def test_download(result):
    """Test actual download from the best provider."""
    if not result:
        return
    
    p(f"\n--- Download Test ---")
    url = result.get("direct_url", "")
    p(f"  Provider: {result.get('provider')}")
    p(f"  URL: {url[:150]}")
    
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            t0 = time.time()
            async with client.stream("GET", url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
            }) as resp:
                ct = resp.headers.get("content-type", "?")
                cl = resp.headers.get("content-length", "?")
                p(f"  Status: {resp.status_code}, type={ct}, length={cl}")
                
                if resp.status_code == 200:
                    total = 0
                    async for chunk in resp.aiter_bytes(65536):
                        total += len(chunk)
                        if total >= 512 * 1024:
                            break
                    elapsed = time.time() - t0
                    p(f"  Downloaded: {total/1024:.1f} KB in {elapsed:.1f}s")
                    
                    if total > 5000:
                        p(f"  ✅ DOWNLOAD WORKS!")
                    else:
                        p(f"  ❌ Too small ({total} bytes)")
    except Exception as e:
        p(f"  ❌ Error: {e}")


async def main():
    p("=" * 60)
    p("FINAL ATTEMPT - Fix TikWM & Test All")
    p(f"Video ID: {VIDEO_ID}")
    p("=" * 60)
    
    result = await test_tikwm_get()
    
    if not result:
        result = await test_scraperapi_to_tikwm()
    
    if result:
        await test_download(result)
    
    p(f"\n{'='*60}")
    if result:
        p(f"✅ FOUND WORKING METHOD: {result.get('provider')}")
        p(f"   Title: {result.get('title', 'N/A')[:60]}")
        p(f"   URL: {result.get('direct_url', '')[:120]}")
    else:
        p(f"❌ ALL METHODS FAILED")

asyncio.run(main())
