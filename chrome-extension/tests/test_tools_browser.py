"""5.20.0 — resume a dropped download, file name template, preview, QR, recording.

Real extension in Chromium; local test servers stand in for the CDN / site.
  [resume]  a server drops the connection half-way on the first try (with ETag
            + Range support): the download goes on (Chrome's retry, then
            vgSaveFile's resume) and the saved file is complete
  [name]    template "{platform} - {date} - {title}" -> the saved name
  [preview] VG_PROBE on the tab the person is on: title + size from the CDN
  [qr]      the popup's QR encodes WEB_BASE/share-target?url=<the video>
  [record]  VG_REC_START / STOP on a tab -> a WebM is saved (or the browser's
            refusal is reported cleanly)
Run: python3 tests/test_tools_browser.py
"""
import http.server, json, os, re, shutil, socketserver, sys, tempfile, threading, time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

res = []
SIZE = 600_000
BODY = b"\x00\x00\x00\x18ftypmp42" + os.urandom(SIZE - 12)


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


class Flaky(http.server.BaseHTTPRequestHandler):
    """First full GET: headers for the whole file, half of it, then the socket is closed."""
    hits = {"full": 0, "range": 0}

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            Flaky.hits["range"] += 1
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", f"bytes {start}-{SIZE - 1}/{SIZE}")
            self.send_header("Content-Length", str(SIZE - start))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(BODY[start:])
            return
        Flaky.hits["full"] += 1
        if Flaky.hits["full"] > 1:   # the connection is back: the whole file
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(SIZE))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(BODY)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(SIZE))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"v1"')
        self.send_header("Last-Modified", "Wed, 21 Oct 2026 07:28:00 GMT")
        self.end_headers()
        self.wfile.write(BODY[: SIZE // 2])
        self.wfile.flush()
        self.connection.shutdown(2)   # dropped mid-file


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_tools_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    srv = S(("127.0.0.1", 0), Flaky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    flaky = f"http://127.0.0.1:{srv.server_address[1]}/clip.mp4"
    page_url = "https://www.dailymotion.com/video/xvgtest1"
    cdn = "https://cdn.vgtest.example/clip.mp4"
    page = f"""<!doctype html><title>Clip for preview</title>
      <meta property="og:title" content="Clip for preview"><meta property="og:video:secure_url" content="{cdn}"><body>page</body>"""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN", accept_downloads=True,
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox",
                      "--auto-accept-this-tab-capture", "--use-fake-ui-for-media-stream"])
            ctx.route(page_url, lambda r: r.fulfill(status=200, content_type="text/html", body=page))
            ctx.route(cdn, lambda r: r.fulfill(status=200, headers={"Content-Type": "video/mp4", "Content-Length": "7340032"}, body=b"")
                      if r.request.method == "HEAD" else r.fulfill(status=206, headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-0/7340032"}, body=b"\x00"))
            ctx.route("**/api/v1/**", lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"t"}'))
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            for _ in range(40):
                if sw.evaluate("() => typeof vgProbe === 'function'"):
                    break
                time.sleep(0.25)

            # [resume]
            ok = sw.evaluate("(u) => vgSaveFile(u, 'resume-test')", flaky)
            d = sw.evaluate("() => new Promise(r => chrome.downloads.search({ orderBy: ['-startTime'], limit: 1 }, x => r(x[0])))")
            got = Path(d["filename"]).read_bytes() if d and os.path.exists(d["filename"]) else b""
            check("[resume] dropped half-way on the first try: the saved file is complete and identical",
                  ok is True and got == BODY and Flaky.hits["full"] + Flaky.hits["range"] >= 2, f"ok={ok} size={len(got)}/{SIZE} hits={Flaky.hits}")

            # [name]
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({ vg_onboarding_done: true })")
            pop.evaluate("() => chrome.storage.sync.set({ vg_name_template: '{platform} - {date} - {title}' })")
            name = sw.evaluate("() => vgFileName('Một clip hay', 'https://www.facebook.com/reel/910445711561956')")
            want = f"facebook - {date.today().isoformat()} - Một clip hay"
            check("[name] template applied", name == want, name)
            pop.evaluate("() => chrome.storage.sync.set({ vg_name_template: '{title}' })")
            check("[name] default '{title}' unchanged", sw.evaluate("() => vgFileName('Một clip hay', 'https://x.com/a')") == "Một clip hay")

            # [preview]
            site = ctx.new_page()
            site.goto(page_url, wait_until="domcontentloaded")
            site.wait_for_timeout(800)
            tab_id = pop.evaluate("(u) => new Promise(r => chrome.tabs.query({}, ts => r((ts.find(t => t.url === u) || {}).id)))", page_url)
            pr = pop.evaluate("(a) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_PROBE', url: a[0], pageTabId: a[1] }, r))", [page_url, tab_id])
            m = (pr or {}).get("media") or {}
            check("[preview] the file on the page, its title and size (7 MB)", pr and pr.get("direct") and m.get("url") == cdn
                  and m.get("title") == "Clip for preview" and m.get("size") == 7340032, json.dumps(pr)[:200])
            pr2 = pop.evaluate("(u) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_PROBE', url: u, pageTabId: null }, r))",
                               "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            check("[preview] YouTube: says it goes through the server", pr2 == {"direct": False}, str(pr2))

            # [qr]
            pop2 = ctx.new_page()
            pop2.add_init_script(f"""
              const _q = chrome.tabs.query.bind(chrome.tabs);
              chrome.tabs.query = (info, cb) => {{ const p = new Promise((res) => _q({{}}, (ts) => res(ts.filter(x => x.id === {tab_id})))); if (cb) {{ p.then(cb); return undefined; }} return p; }};
            """)
            pop2.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop2.wait_for_timeout(1500)
            pop2.locator("#vg-qr-btn").click()
            pop2.wait_for_timeout(500)
            src = pop2.locator("#vg-qr-code img").get_attribute("src") or ""
            want_link = f"https://dvid.vibe1.tinhgon.xyz/share-target?url={pop2.evaluate('(u) => encodeURIComponent(u)', page_url)}"
            # the same encoder, same input -> same image: proves what the QR holds
            same = pop2.evaluate("(l) => { const q = qrcode(0, 'M'); q.addData(l); q.make(); return q.createDataURL(4, 8); }", want_link)
            check("[qr] QR = VidGrab share-target link of this video", src.startswith("data:image/") and src == same, want_link)

            pop2.locator("#vg-preview-btn").click()
            pop2.wait_for_timeout(2500)
            info = pop2.locator("#vg-preview-info").inner_text()
            check("[preview] popup card: title + 7.0 MB + 'tải thẳng'", "Clip for preview" in info and "7.0 MB" in info and "tải thẳng" in info, info)

            # [record]
            site.bring_to_front()
            r = pop.evaluate("(id) => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_REC_START', tabId: id }, r))", tab_id)
            if r and r.get("ok"):
                time.sleep(3)
                st = pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_REC_STOP' }, r))")
                time.sleep(1.5)
                d = sw.evaluate("() => new Promise(r => chrome.downloads.search({ orderBy: ['-startTime'], limit: 1 }, x => r(x[0])))")
                check("[record] 3 s of the tab saved as WebM", st and st.get("ok") and d and d.get("filename", "").endswith(".webm") and st.get("bytes", 0) > 1000,
                      f"{st} {d and d.get('filename')}")
            else:
                print(f"     [record] not startable in this automated browser: {r} (needs the toolbar click = 'invoked')")
            ctx.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
