"""
VidGrab douyin-mint (owner 15/10/2026).

  POST /mint/douyin   header X-Mint-Key: <MINT_KEY>
    → 200 {"ua", "cookies": {...douyin.com cookies...}, "ms"}
    → 502 {"code": "no_uifid" | "error", "ms"}
  GET /health         → {"ok": true, "busy": bool}

Each mint launches a fresh headless Chromium, opens one public Douyin video
page, waits until Douyin's own JavaScript has set UIFID_TEMP and s_v_web_id,
copies the cookies and closes the browser. Nothing is kept between mints and
no browser runs between requests (the box has 1 CPU). One mint at a time.
Cookie values are never logged.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import random
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

MINT_KEY = os.getenv("MINT_KEY", "")
# Public videos to open; any one works. Override with MINT_VIDEO_IDS=id1,id2.
VIDEO_IDS = [v for v in os.getenv("MINT_VIDEO_IDS", "7696039395691101618,7673062705862806822").split(",") if v.strip()]
WAIT_S = float(os.getenv("MINT_WAIT_S", "25"))

app = FastAPI(title="VidGrab douyin-mint")
_lock = asyncio.Lock()


def _has_visitor(ck: dict) -> bool:
    return any(k.lower().startswith("uifid") and v for k, v in ck.items()) and bool(ck.get("s_v_web_id"))


async def _mint() -> dict:
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"])
        try:
            ctx = await browser.new_context(locale="zh-CN", viewport={"width": 1366, "height": 860})

            async def _block(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await ctx.route("**/*", _block)
            page = await ctx.new_page()
            vid = random.choice(VIDEO_IDS).strip()
            await page.goto(f"https://www.douyin.com/video/{vid}", wait_until="domcontentloaded", timeout=40000)
            ck: dict = {}
            deadline = time.monotonic() + WAIT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                ck = {c["name"]: c["value"] for c in await ctx.cookies() if "douyin.com" in c.get("domain", "")}
                if _has_visitor(ck):
                    break
            return {"ua": await page.evaluate("navigator.userAgent"), "cookies": ck}
        finally:
            await browser.close()


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True, "busy": _lock.locked()}


@app.post("/mint/douyin")
async def mint(x_mint_key: str | None = Header(default=None)):
    if not MINT_KEY or not x_mint_key or not hmac.compare_digest(x_mint_key, MINT_KEY):
        raise HTTPException(status_code=401, detail="bad key")
    t0 = time.monotonic()
    async with _lock:
        try:
            out = await asyncio.wait_for(_mint(), timeout=WAIT_S + 45)
        except Exception as exc:  # noqa: BLE001
            print(f"[mint] error {type(exc).__name__}")
            return JSONResponse({"code": "error", "ms": int((time.monotonic() - t0) * 1000)}, status_code=502)
    ms = int((time.monotonic() - t0) * 1000)
    if not _has_visitor(out["cookies"]):
        print(f"[mint] no_uifid {ms}ms")
        return JSONResponse({"code": "no_uifid", "ms": ms}, status_code=502)
    print(f"[mint] ok {ms}ms ({len(out['cookies'])} cookies)")
    return {**out, "ms": ms}
