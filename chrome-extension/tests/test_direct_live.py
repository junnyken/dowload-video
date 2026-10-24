"""LIVE: 5.18.0 — videos downloaded by the browser itself, the server only counts.

Real Facebook pages + the real extension; the VidGrab server is faked with
Playwright routes (nothing real is counted): /client/quota/claim and /settle
(recorded), /fetch-link (recorded — it must NOT be called when the browser
can do it). Checks:
  [single] "Tải" from the reel page the person is on: no extra window, the
           file comes from Facebook's CDN, one claim, settle "completed",
           and the MP4 has both a video and a sound track
  [bulk]   3 reels from the popup list through the minimised VidGrab window:
           3 files, 3 claims, 0 fetch-link, the helper window closes after
  [quota]  a refused claim stops the queue before anything is downloaded
  [fallback] a page with no file (YouTube) goes to the server as before
Run: python3 tests/test_direct_live.py
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CHANNEL = "https://www.facebook.com/haidee.burrows/reels/"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def has_tracks(path):
    """'vide' and 'soun' handler types in the MP4 boxes = picture and sound."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False, False
    return b"hdlrvide" in data.replace(b"\x00", b"") or b"vide" in data[:4_000_000], b"soun" in data[:4_000_000]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_direct_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    claims, settles, fetch_links = [], [], []
    state = {"refuse": False}

    def on_claim(route):
        claims.append(json.loads(route.request.post_data or "{}"))
        if state["refuse"]:
            route.fulfill(status=429, content_type="application/json", body=json.dumps(
                {"allowed": False, "detail": "Bạn đã hết lượt tải hôm nay.", "resetTimeVn": "07:00"}))
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"allowed": True, "claimId": f"c{len(claims):06d}abc", "remaining": 9}))

    def on_settle(route):
        settles.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body='{"refunded":false}')

    def on_fetch(route):
        fetch_links.append(json.loads(route.request.post_data or "{}").get("url"))
        route.fulfill(status=503, content_type="application/json", body='{"detail":"test server"}')

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                viewport={"width": 1280, "height": 900}, accept_downloads=True,
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            ctx.route("**/api/v1/client/quota/claim", on_claim)
            ctx.route("**/api/v1/client/quota/settle", on_settle)
            ctx.route("**/api/v1/fetch-link", on_fetch)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function' && !!self.vgDirect"):
                    break
                time.sleep(0.1)

            # collect a few reel links from the channel (the content script's list)
            page = ctx.new_page()
            page.goto(CHANNEL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            reels = page.evaluate("""() => [...new Set([...document.querySelectorAll('a[href*="/reel/"]')]
                .map(a => (a.href.match(/\\/reel\\/(\\d+)/) || [])[1]).filter(Boolean))].slice(0, 4)
                .map(id => 'https://www.facebook.com/reel/' + id)""")
            check("found reels on the channel", len(reels) >= 4, str(len(reels)))

            def downloads():
                return sw.evaluate("() => new Promise(r => chrome.downloads.search({}, d => r(d.map(x => [x.url, x.filename, x.state, x.fileSize]))))")

            def wait_downloads(n, secs=90):
                for _ in range(secs):
                    d = [x for x in downloads() if x[2] == "complete"]
                    if len(d) >= n:
                        return d
                    time.sleep(1)
                return [x for x in downloads() if x[2] == "complete"]

            # [single] from the reel page the person is on
            reel_tab = ctx.new_page()
            reel_tab.goto(reels[0], wait_until="domcontentloaded", timeout=60000)
            reel_tab.wait_for_timeout(4000)
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            tab_id = pop.evaluate("(u) => new Promise(r => chrome.tabs.query({}, ts => r((ts.find(t => (t.url || '').startsWith(u)) || {}).id)))", reels[0])
            windows_before = sw.evaluate("() => new Promise(r => chrome.windows.getAll({}, w => r(w.length)))")
            ack = pop.evaluate("(a) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_NOW', payload: { url: a[0], quality: 'video', remove_watermark: true, download_subs: false, pageTabId: a[1] } }, r))", [reels[0], tab_id])
            d = wait_downloads(1)
            check("[single] queued, then one file complete", bool(ack and ack.get("queued")) and len(d) == 1, str(d)[:200])
            check("[single] file came from Facebook's CDN (fbcdn.net), not the VidGrab server", d and ".fbcdn.net/" in d[0][0], d[0][0][:90] if d else "")
            check("[single] one claim for that reel, settled 'completed', no fetch-link",
                  len(claims) == 1 and claims[0].get("url") == reels[0] and claims[0].get("route") == "local"
                  and any(s.get("outcome") == "completed" for s in settles) and not fetch_links,
                  f"claims={len(claims)} settles={settles} fetch_links={fetch_links}")
            check("[single] no extra window opened (read in the person's tab)",
                  sw.evaluate("() => new Promise(r => chrome.windows.getAll({}, w => r(w.length)))") == windows_before)
            vide, soun = has_tracks(d[0][1]) if d else (False, False)
            check("[single] the MP4 has picture AND sound", vide and soun, f"video={vide} sound={soun} size={d[0][3] if d else 0}")

            # [bulk] the next 3 reels through the popup's queue (minimised VidGrab window)
            claims.clear(); settles.clear()
            items = [{"url": u, "title": ""} for u in reels[1:4]]
            ack = pop.evaluate("(items) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_BULK_START', items, later: [], quality: 'video' }, r))", items)
            b = None
            for _ in range(120):
                b = sw.evaluate("() => new Promise(r => chrome.storage.local.get('vg_bulk', x => r(x.vg_bulk || null)))")
                if b and b.get("finishedAt"):
                    break
                time.sleep(1)
            st = [(x["status"], x.get("via")) for x in (b or {}).get("items", [])]
            check("[bulk] 3 done, all direct", ack and ack.get("ok") and len(st) == 3 and all(s == "done" and (v or "").startswith("direct:") for s, v in st), str(st))
            d = wait_downloads(4)
            check("[bulk] 3 more files complete from fbcdn", len([x for x in d if ".fbcdn.net/" in x[0]]) == 4, str(len(d)))
            check("[bulk] 3 claims, 3 settled 'completed', still 0 fetch-link",
                  len(claims) == 3 and sum(s.get("outcome") == "completed" for s in settles) == 3 and not fetch_links,
                  f"claims={len(claims)} settles={len(settles)} fetch_links={len(fetch_links)}")
            time.sleep(5)
            wins = sw.evaluate("() => new Promise(r => chrome.windows.getAll({}, w => r(w.length)))")
            check("[bulk] the minimised VidGrab windows closed afterwards (one per video)", wins == windows_before, f"{wins} vs {windows_before}")

            # [quota] a refused claim stops the queue before any download
            claims.clear(); settles.clear()
            n_before = len(downloads())
            state["refuse"] = True
            pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_BULK_CLEAR' }, r))")
            pop.evaluate("(items) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_BULK_START', items, later: [], quality: 'video' }, r))", items)
            for _ in range(40):
                b = sw.evaluate("() => new Promise(r => chrome.storage.local.get('vg_bulk', x => r(x.vg_bulk || null)))")
                if b and b.get("finishedAt"):
                    break
                time.sleep(0.5)
            st = [x["status"] for x in (b or {}).get("items", [])]
            check("[quota] refused claim: queue stopped, nothing downloaded",
                  b and b.get("stopped") and "done" not in st and len(downloads()) == n_before and "hết lượt" in (b.get("stopReason") or ""),
                  f"{st} reason={b and b.get('stopReason')}")
            state["refuse"] = False

            # [not a video] a CDN that refuses answers with a web page (TikTok gave an .htm,
            # owner 22/10): vgSaveFile must call that a failure and remove the file
            import http.server, socketserver, threading
            media = tmp / "media"
            media.mkdir(exist_ok=True)
            (media / "page.mp4").write_text("<!doctype html><title>TikTok - Make Your Day</title>" + "x" * 50000)
            (media / "real.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(60000))
            class H(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **k):
                    super().__init__(*a, directory=str(media), **k)
                def log_message(self, *a):
                    pass
                def guess_type(self, path):
                    return "text/html" if path.endswith("page.mp4") else "video/mp4"
            class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
                daemon_threads = True
            srv = S(("127.0.0.1", 0), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            bad = sw.evaluate("(u) => vgSaveFile(u, 'refused')", base + "/page.mp4")
            good = sw.evaluate("(u) => vgSaveFile(u, 'real')", base + "/real.mp4")
            time.sleep(1)
            left = sw.evaluate("() => new Promise(r => chrome.downloads.search({}, d => r(d.filter(x => x.url.includes('/page.mp4')).length)))")
            check("[not a video] an HTML answer is a failure and is removed; a real file is fine", bad is False and good is True and left == 0,
                  f"bad={bad} good={good} html entries left={left}")
            srv.shutdown()

            # [fallback] YouTube is never tried in the browser. Called in the service worker
            # itself: a routed request sent from a VG_DOWNLOAD_NOW message never reached the
            # Playwright route here (same on 5.17.0, before this change) — a harness quirk.
            claims.clear()
            out = sw.evaluate("() => vgSaveVideo({ url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', quality: 'video', remove_watermark: false, download_subs: false }, null, null).then(r => 'ok ' + r.via, e => 'ERR ' + e.status)")
            check("[fallback] YouTube goes to the server (fetch-link), no claim",
                  fetch_links == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] and not claims and out == "ERR 503",
                  f"fetch_links={fetch_links} claims={len(claims)} out={out}")
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
