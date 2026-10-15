"""
VidGrab browser resolver (owner 15/10).

Douyin (and Xiaohongshu / Kuaishou) answer plain HTTP from a server with pages
that carry no video data: the data comes from XHRs signed by the page's own
JavaScript. Measured 15/10: a headless Chromium opening douyin.com/video/<id>
with no cookies, no proxy, gets a playable douyinvod.com link in ~8 s, and
that link downloads outside the browser with Referer: douyin.com.

So this service keeps ONE persistent Chromium profile (/data/profile —
cookies the sites set survive restarts, like a returning visitor) and opens a
tab per request:

  POST /resolve {"url": "..."}   header X-Browser-Key: <BROWSER_KEY>
    → {"ok": true, "platform", "video_url", "title", "thumbnail", "duration_sec",
       "uploader", "media_id", "images", "referer", "ms"}
    → {"ok": false, "platform", "code": captcha|not_found|timeout|unsupported|error, "ms"}
  GET /health → {"ok", "tabs_busy", "tabs_max"}

Not looking like a bot: up to MAX_TABS tabs at once (default 3), a random
wait before every navigation (JITTER_MIN_S..JITTER_MAX_S) and a random gap
between two navigations to the same platform, a normal desktop Chrome user
agent, no navigator.webdriver. Images, video and fonts are never downloaded
inside the browser (only the page + its scripts/XHRs), so a resolve costs a
few hundred KB, not the video.

Never logs a cookie or a media URL.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import random
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import resolvers as R

BROWSER_KEY = os.getenv("BROWSER_KEY", "")
PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/profile")
MAX_TABS = max(1, min(8, int(os.getenv("MAX_TABS", "3") or 3)))
JITTER_MIN_S = float(os.getenv("JITTER_MIN_S", "0.4"))
JITTER_MAX_S = float(os.getenv("JITTER_MAX_S", "2.5"))
GAP_MIN_S = float(os.getenv("GAP_MIN_S", "0.8"))
GAP_MAX_S = float(os.getenv("GAP_MAX_S", "3.0"))
PAGE_TIMEOUT_S = float(os.getenv("PAGE_TIMEOUT_S", "25"))
UA = os.getenv("BROWSER_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

app = FastAPI(title="VidGrab browser resolver")

_pw = None
_ctx = None
_ctx_lock = asyncio.Lock()
_sem = asyncio.Semaphore(MAX_TABS)
_busy = 0
_last_nav: Dict[str, float] = {}
_nav_lock: Dict[str, asyncio.Lock] = {}

_STEALTH = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
"""


async def _context():
    global _pw, _ctx
    async with _ctx_lock:
        if _ctx is not None:
            return _ctx
        from playwright.async_api import async_playwright
        _pw = await async_playwright().start()
        os.makedirs(PROFILE_DIR, exist_ok=True)
        _ctx = await _pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            user_agent=UA,
            locale="zh-CN",
            viewport={"width": 1366, "height": 860},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--mute-audio"],
        )
        await _ctx.add_init_script(_STEALTH)

        async def _block(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await _ctx.route("**/*", _block)
        return _ctx


async def _pace(platform: str) -> None:
    """Random wait before a navigation + a random minimum gap per platform."""
    await asyncio.sleep(random.uniform(JITTER_MIN_S, JITTER_MAX_S))
    lock = _nav_lock.setdefault(platform, asyncio.Lock())
    async with lock:
        gap = random.uniform(GAP_MIN_S, GAP_MAX_S)
        wait = _last_nav.get(platform, 0) + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_nav[platform] = time.monotonic()


async def _douyin(page, url: str) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    done = asyncio.Event()

    async def on_response(resp):
        if "/aweme/v1/web/aweme/detail" not in resp.url or found:
            return
        try:
            r = R.douyin_from_detail(await resp.json())
        except Exception:  # noqa: BLE001
            r = None
        if r:
            found.update(r)
            done.set()

    page.on("response", on_response)
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_S * 1000)
    try:
        await asyncio.wait_for(done.wait(), timeout=PAGE_TIMEOUT_S * 0.6)
    except asyncio.TimeoutError:
        pass
    if found:
        return found
    # Fallback: the player's <video>/<source> src (set by the page's JS).
    srcs = await page.evaluate("""() => [...document.querySelectorAll('video, video source')]
        .map(e => e.currentSrc || e.src || e.getAttribute('src') || '').filter(Boolean)""")
    for s in srcs:
        u = R.https(s)
        if u and R.is_media_url(u):
            return {"video_url": u, "title": (await page.title()).replace(" - 抖音", "")}
    return {}


async def _xiaohongshu(page, url: str) -> Dict[str, Any]:
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_S * 1000)
    for _ in range(int(PAGE_TIMEOUT_S * 2)):
        raw = await page.evaluate("""() => { try { return JSON.stringify(window.__INITIAL_STATE__,
            (k, v) => v === undefined ? null : v) } catch (e) { return null } }""")
        state = R.parse_json_maybe(raw or "")
        if state:
            note_id = (page.url.split("?")[0].rstrip("/").split("/") or [""])[-1]
            r = R.xhs_from_state(state, note_id)
            if r:
                return r
        await asyncio.sleep(0.5)
    return {}


async def _kuaishou(page, url: str) -> Dict[str, Any]:
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_S * 1000)
    for _ in range(int(PAGE_TIMEOUT_S * 2)):
        raw = await page.evaluate("""() => { try { return JSON.stringify(window.__APOLLO_STATE__) }
            catch (e) { return null } }""")
        r = R.kuaishou_from_apollo(R.parse_json_maybe(raw or "") or {})
        if r:
            return r
        await asyncio.sleep(0.5)
    srcs = await page.evaluate("""() => [...document.querySelectorAll('video, video source')]
        .map(e => e.currentSrc || e.src || '').filter(Boolean)""")
    for s in srcs:
        u = R.https(s)
        if u and R.is_media_url(u):
            return {"video_url": u, "title": await page.title()}
    return {}


_HANDLERS = {"douyin": _douyin, "xiaohongshu": _xiaohongshu, "kuaishou": _kuaishou}


class ResolveBody(BaseModel):
    url: str


@app.on_event("startup")
async def _warm():
    try:
        await _context()
        print(f"[browser] ready: {MAX_TABS} tabs, profile {PROFILE_DIR}")
    except Exception as exc:  # noqa: BLE001
        print(f"[browser] start failed: {type(exc).__name__}: {exc}")


@app.get("/health")
async def health():
    return {"ok": _ctx is not None, "tabs_busy": _busy, "tabs_max": MAX_TABS}


@app.post("/resolve")
async def resolve(body: ResolveBody, x_browser_key: Optional[str] = Header(default=None)):
    global _busy
    if not BROWSER_KEY or not x_browser_key or not hmac.compare_digest(x_browser_key, BROWSER_KEY):
        raise HTTPException(status_code=401, detail="bad key")
    url = (body.url or "").strip()
    platform = R.platform_of(url)
    if not platform or not url.startswith("https://"):
        return {"ok": False, "platform": platform, "code": "unsupported", "ms": 0}
    t0 = time.monotonic()
    async with _sem:
        _busy += 1
        page = None
        try:
            ctx = await _context()
            await _pace(platform)
            page = await ctx.new_page()
            try:
                result = await asyncio.wait_for(_HANDLERS[platform](page, url), timeout=PAGE_TIMEOUT_S + 5)
            except asyncio.TimeoutError:
                result = {}
                code = "timeout"
            else:
                code = None
            ms = int((time.monotonic() - t0) * 1000)
            if result.get("video_url") or result.get("images"):
                print(f"[browser] {platform} ok {ms}ms")
                return {"ok": True, "platform": platform, "referer": R.REFERERS[platform], "ms": ms, **result}
            try:
                title, text = await page.title(), await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:  # noqa: BLE001
                title, text = "", ""
            code = code or ("captcha" if R.looks_like_captcha(title, text) else "not_found")
            print(f"[browser] {platform} {code} {ms}ms")
            return {"ok": False, "platform": platform, "code": code, "ms": ms}
        except Exception as exc:  # noqa: BLE001
            print(f"[browser] {platform} error {type(exc).__name__}")
            return {"ok": False, "platform": platform, "code": "error", "ms": int((time.monotonic() - t0) * 1000)}
        finally:
            _busy -= 1
            if page is not None:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass
