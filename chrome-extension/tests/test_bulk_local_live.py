"""LIVE: 5.17.0 — choose reels in the popup list and download them all to this computer.

Real Facebook Reels page + the real extension. The server is faked with
Playwright routes: /api/v1/quota (remaining downloads) and /api/v1/fetch-link
(answers a direct MP4 on a local HTTP server, so chrome.downloads really saves
files). Checks:
  - "10 video đầu" picks 10, the button says so
  - quota 5 left: 5 go, 5 wait as "chưa tải" (the server is asked 5 times)
  - the queue goes on with the popup CLOSED; files land in Downloads/VidGrab
  - reopened popup shows "Hoàn tất" + per-row states
  - a 429 from the server (no downloads left) stops the queue, rest skipped
Run: python3 tests/test_bulk_local_live.py
"""
import http.server, json, os, shutil, socketserver, sys, tempfile, threading, time
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
    tmp = Path(tempfile.mkdtemp(prefix="vg_bulk_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    media = tmp / "media"
    media.mkdir()
    (media / "v.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(20000))

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(media), **k)

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mp4 = f"http://127.0.0.1:{srv.server_address[1]}/v.mp4"

    state = {"remaining": 5, "fail_after": None}
    fetch_links = []

    def on_quota(route):
        q = {"platform_quota": {"scope": "platform", "reset_time_vn": "00:00",
                                "platforms": [{"platform": "facebook", "remaining": state["remaining"]}]}} \
            if state["remaining"] is not None else {"platform_quota": {"unlimited": True}}
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True, "quota": q}))

    def on_fetch(route):
        body = json.loads(route.request.post_data or "{}")
        fetch_links.append(body.get("url"))
        time.sleep(0.4)
        if state["fail_after"] is not None and len(fetch_links) > state["fail_after"]:
            route.fulfill(status=429, content_type="application/json",
                          body=json.dumps({"detail": {"message": "Bạn đã hết lượt tải hôm nay."}}))
            return
        n = len(fetch_links)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"success": True, "title": f"reel test {n}", "direct_mp4_url": mp4, "thumbnail_url": ""}))

    def open_popup(ctx, ext_id, fb_id):
        pop = ctx.new_page()
        pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
        pop.evaluate("(a) => { window.getActiveTab = async () => { _activeTabId = a[0]; return { id: a[0], url: a[1] }; }; }", [fb_id, PAGE])
        pop.evaluate("() => initPopup()")
        pop.wait_for_timeout(1500)
        return pop

    def wait_bulk(sw, pred, secs=60):
        b = None
        for _ in range(secs * 2):
            b = sw.evaluate("() => new Promise(r => chrome.storage.local.get('vg_bulk', x => r(x.vg_bulk || null)))")
            if b and pred(b):
                return b
            time.sleep(0.5)
        return b

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                viewport={"width": 1280, "height": 900}, accept_downloads=True,
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--no-sandbox"])
            ctx.route("**/api/v1/fetch-link", on_fetch)
            ctx.route("**/api/v1/quota", on_quota)
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
            fb_id = pop.evaluate("""() => new Promise(r => chrome.tabs.query({}, ts =>
                r((ts.find(t => (t.url || '').includes('facebook.com/haidee.burrows')) || {}).id)))""")
            pop.close()
            pop = open_popup(ctx, ext_id, fb_id)
            pop.locator("#ch-scrape-btn").click()
            pop.wait_for_timeout(9000)
            if pop.locator("#ch-stop-local").is_visible():
                pop.locator("#ch-stop-local").click()
                pop.wait_for_timeout(2500)
            n = pop.locator("#ch-list .vg-ch-row").count()
            check("list has the reels, all chosen by default", n >= 10 and pop.locator("#ch-pick-count").inner_text() == f"Đã chọn {n}/{n}",
                  pop.locator("#ch-pick-count").inner_text())
            pop.locator("#ch-pick-10").click()
            check("'10 video đầu' -> 10 chosen, button says 10", pop.locator("#ch-pick-count").inner_text() == f"Đã chọn 10/{n}"
                  and "Tải 10 video về máy" in pop.locator("#ch-bulk-text").inner_text(), pop.locator("#ch-bulk-text").inner_text())
            first10 = [pop.locator("#ch-list .vg-ch-row").nth(k).get_attribute("data-url") for k in range(10)]

            # quota 5: 5 now, 5 later; close the popup right away
            pop.locator("#ch-bulk-btn").click()
            pop.wait_for_timeout(1200)
            msg = pop.locator("#ch-list-msg").inner_text()
            check("quota 5 of 10: says 5 wait for tomorrow", "Đang tải 5 video" in msg and "5 video chưa tải" in msg, msg[:120])
            pop.close()
            b = wait_bulk(sw, lambda b: b.get("finishedAt"))
            st = [x["status"] for x in (b or {}).get("items", [])]
            check("queue finished with the popup closed: 5 done, 5 skipped",
                  st.count("done") == 5 and st.count("skipped") == 5, str(st))
            check("server asked exactly for the first 5 chosen, in order", fetch_links == first10[:5], str(fetch_links)[:200])
            time.sleep(2)
            dls = sw.evaluate("() => new Promise(r => chrome.downloads.search({}, d => r(d.map(x => [x.filename, x.state]))))")
            # Playwright stores accepted downloads under GUID names, so count completed ones
            done = [d for d in dls if d[1] == "complete"]
            check("5 files downloaded to this computer", len(done) == 5, str(dls)[:240])
            pop = open_popup(ctx, ext_id, fb_id)
            info = pop.locator("#ch-bulk-info").inner_text()
            check("reopened popup: 'Hoàn tất: 5/10' and the reason for the rest", info.startswith("Hoàn tất: 5/10") and "hết lượt" in info, info)
            row_states = [pop.locator(f'#ch-list .vg-ch-row[data-url="{u}"] .vg-ch-state').inner_text() for u in first10[:6]] \
                if pop.locator("#ch-list .vg-ch-row").count() else []
            check("rows show their own state", row_states[:1] == ["✓ Đã tải về máy"] and row_states[5:6] == ["Chưa tải"], str(row_states))

            # a 429 from the server stops the queue (no client-side quota this time)
            pop.locator("#ch-bulk-clear").click()
            pop.wait_for_timeout(500)
            check("'Xoá kết quả' clears the old run", not pop.locator("#ch-bulk").is_visible())
            fetch_links.clear()
            state.update(remaining=None, fail_after=2)
            pop.locator("#ch-pick-all").click()
            pop.locator("#ch-bulk-btn").click()
            b = wait_bulk(sw, lambda b: b.get("finishedAt"))
            st = [x["status"] for x in (b or {}).get("items", [])]
            check("server 429 stops the queue: 2 done, the rest not tried", st.count("done") == 2 and st.count("running") == 0
                  and st.count("queued") == 0 and len(fetch_links) <= 4, f"{st.count('done')} done, {len(fetch_links)} asked, {st[:6]}")
            pop.wait_for_timeout(800)
            info = pop.locator("#ch-bulk-info").inner_text()
            check("popup says it stopped and why", info.startswith("Đã dừng") and "hết lượt" in info, info)
            ctx.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
