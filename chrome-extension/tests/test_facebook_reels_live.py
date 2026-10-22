"""LIVE (needs the internet): the extension on a real public Facebook Reels tab.

Opens https://www.facebook.com/haidee.burrows/reels/ (public profile, signed
out) with "#vg-relay=<token>" as the app / website does, intercepts the
extension's PUT /channel-relay/<token> and checks that it scrolled past the
sign-in dialog and sent the whole tab (reel links, channel title, done).
Signed out Facebook shows ~10 reels before the dialog; the tab has ~29.

Run: python3 tests/test_facebook_reels_live.py
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE = "https://www.facebook.com/haidee.burrows/reels/"
TOKEN = "T" * 32
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_fb_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    puts = []

    def on_relay(route):
        req = route.request
        if req.method == "PUT":
            puts.append(json.loads(req.post_data or "{}"))
        n = len((puts[-1] if puts else {}).get("items", []))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "count": n}))

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                viewport={"width": 1280, "height": 900},
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--no-sandbox"])
            ctx.route(f"**/api/v1/channel-relay/{TOKEN}", on_relay)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function'"):
                    break
                time.sleep(0.1)
            page = ctx.new_page()
            page.goto(f"{PAGE}#vg-relay={TOKEN}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            check("Channel Scraper panel on the Reels tab", page.locator("#vg-channel-panel").count() == 1)
            done = None
            for _ in range(60):                 # up to ~2 min of scrolling
                done = next((x for x in puts if x.get("done")), None)
                if done:
                    break
                page.wait_for_timeout(2000)
            items = (done or {}).get("items", [])
            urls = [i["url"] for i in items]
            print("     progress puts:", [len(x.get("items", [])) for x in puts])
            check("relay finished (done=true)", bool(done), f"{len(puts)} puts")
            check("more than the 10 shown before the sign-in dialog", len(items) > 10, str(len(items)))
            check("every item is a Facebook reel link", items and all(u.startswith("https://www.facebook.com/reel/") for u in urls), str(urls[:2]))
            check("no duplicates", len(set(urls)) == len(urls))
            check("channel title sent", bool((done or {}).get("channelTitle")), str((done or {}).get("channelTitle")))
            panel = page.locator("#vg-ch-status").inner_text() if page.locator("#vg-ch-status").count() else ""
            check("panel says it was sent", "Đã gửi" in panel, panel[:80])
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
