"""LIVE: popup channel tab on a real Facebook Reels page (extension 5.16.0).

Start the scan from the popup -> "Dừng quét" shows; press it -> the page's
scroll stops (panel says so, popup button back to "Cuộn thêm"); the popup
lists the collected reels (title / thumbnail) and a row's "Tải" sends that
reel to /fetch-link (intercepted, nothing is downloaded).
Run: python3 tests/test_channel_list_live.py
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE = "https://www.facebook.com/haidee.burrows/reels/"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_chl_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    fetch_links = []

    def on_fetch(route):
        if route.request.method == "POST":
            fetch_links.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=503, content_type="application/json", body='{"detail":"test"}')

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                viewport={"width": 1280, "height": 900},
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            ctx.route("**/api/v1/fetch-link", on_fetch)
            # 5.18.0: these checks are about the server path — direct download off, quota API faked
            ctx.route("**/api/v1/client/quota/claim", lambda r: r.fulfill(status=200, content_type="application/json", body='{"allowed":true,"claimId":"testclaim0001"}'))
            ctx.route("**/api/v1/client/quota/settle", lambda r: r.fulfill(status=200, content_type="application/json", body='{}'))
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function'"):
                    break
                time.sleep(0.1)
            page = ctx.new_page()
            page.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true, vg_direct: false})")
            pop.reload(wait_until="domcontentloaded")
            pop.wait_for_timeout(500)
            fb_id = pop.evaluate("""() => new Promise(r => chrome.tabs.query({}, ts =>
                r((ts.find(t => (t.url || '').includes('facebook.com/haidee.burrows')) || {}).id)))""")
            # the popup acts on the Facebook tab, as when opened from the toolbar there
            pop.evaluate("(a) => { window.getActiveTab = async () => { _activeTabId = a[0]; return { id: a[0], url: a[1] }; }; }", [fb_id, PAGE])
            pop.evaluate("() => initPopup()")
            pop.wait_for_timeout(1500)
            check("popup opens on the Kênh tab", pop.locator("#channel-section").is_visible())
            pop.locator("#ch-scrape-btn").click()
            for _ in range(10):
                if pop.locator("#ch-stop-local").is_visible():
                    break
                pop.wait_for_timeout(500)
            check("'Dừng quét' shows while scrolling", pop.locator("#ch-stop-local").is_visible())
            pop.wait_for_timeout(5000)
            pop.locator("#ch-stop-local").click()
            for _ in range(20):
                if not pop.locator("#ch-stop-local").is_visible():
                    break
                pop.wait_for_timeout(500)
            panel = page.locator("#vg-ch-status").inner_text()
            check("scan stopped (page panel says so)", "Đã dừng quét" in panel, panel[:90])
            check("popup: stop hidden, button back to 'Cuộn thêm'", not pop.locator("#ch-stop-local").is_visible()
                  and pop.locator("#ch-btn-text").inner_text() == "Cuộn thêm", pop.locator("#ch-btn-text").inner_text())
            rows = pop.locator("#ch-list .vg-ch-row")
            n = rows.count()
            titles = [rows.nth(k).locator(".vg-ch-title").inner_text() for k in range(n)]
            thumbs = pop.locator("#ch-list img").count()
            print("     rows:", n, "with thumbnail:", thumbs, "sample titles:", titles[:2])
            check("list shows the collected reels", n >= 10 and pop.locator("#ch-list-count").inner_text() == f"({n})", str(n))
            check("rows have thumbnails", thumbs >= n // 2, f"{thumbs}/{n}")
            first_url = rows.nth(0).locator(".vg-ch-title").get_attribute("title")
            rows.nth(0).locator(".vg-ch-dl").click()
            for _ in range(20):
                if fetch_links:
                    break
                pop.wait_for_timeout(300)
            check("row 'Tải' sends that reel", fetch_links and fetch_links[0].get("url") == first_url
                  and first_url.startswith("https://www.facebook.com/reel/"), json.dumps(fetch_links[:1])[:160])
            check("row shows it is downloading", rows.nth(0).locator(".vg-ch-dl").inner_text() == "✓")
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
