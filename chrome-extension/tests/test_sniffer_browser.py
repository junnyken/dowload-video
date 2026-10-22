"""Media sniffer (5.12.0) in a real Chromium with the real extension.

A local page plays what real players fetch — a progressive MP4, an HLS master
with two qualities (and then its 720p playlist and a segment, as hls.js does),
an AES-128 HLS, a clear DASH, a DRM DASH — plus things that must NOT be listed
(a tiny MP4, .ts segments). Every listed item is checked one by one; then the
popup rows, the badge, a real download, the reset on navigation, a site the
user has not allowed, and the YouTube refusal.

Run: python3 tests/test_sniffer_browser.py
(Chromium from Playwright's cache; FONTCONFIG_FILE is set below because the
workspace's fontconfig cache crashes Chromium text rendering.)
"""
import http.server, json, os, shutil, socketserver, sys, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright

EXT = str(Path(__file__).resolve().parent.parent)
CHROME = next((str(p) for p in sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux64/chrome"), reverse=True)), None)
BIG = 1_200_000
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def fixtures(root: Path):
    (root / "hls/360").mkdir(parents=True)
    (root / "hls/720").mkdir(parents=True)
    for d in ("enc", "dash", "drm"):
        (root / d).mkdir()
    (root / "big.mp4").write_bytes(bytes(range(256)) * (BIG // 256) + b"\0" * (BIG % 256))
    (root / "tiny.mp4").write_bytes(b"\1" * 50_000)
    (root / "hls/master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720\n720/index.m3u8\n")
    media = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\ns1.ts\n#EXTINF:6.0,\ns2.ts\n#EXT-X-ENDLIST\n"
    (root / "hls/360/index.m3u8").write_text(media)
    (root / "hls/720/index.m3u8").write_text(media)
    (root / "hls/720/s1.ts").write_bytes(b"\x47" * 600_000)
    (root / "enc/index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k.key\"\n#EXTINF:5,\na.ts\n#EXT-X-ENDLIST\n")
    mpd = ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
           'mediaPresentationDuration="PT30S"><Period>{cp}<AdaptationSet mimeType="video/mp4">'
           '<Representation id="1" bandwidth="900000" height="480"/>'
           '<Representation id="2" bandwidth="4000000" height="1080"/></AdaptationSet>'
           '<AdaptationSet mimeType="audio/mp4"><Representation id="a" bandwidth="128000"/>'
           '</AdaptationSet></Period></MPD>')
    (root / "dash/stream.mpd").write_text(mpd.format(cp=""))
    (root / "drm/stream.mpd").write_text(mpd.format(
        cp='<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>'))
    (root / "page.html").write_text("""<!doctype html><html><head><meta charset="utf-8">
<title>Fixture Video Page</title></head><body>
<video id="v" src="big.mp4" muted autoplay preload="auto"></video>
<script>
 (async () => {
  const get = (u) => fetch(u).then(r => r.arrayBuffer()).catch(() => null);
  await get('tiny.mp4');
  await get('hls/master.m3u8');          // what hls.js fetches, in this order
  await get('hls/720/index.m3u8');
  await get('hls/720/s1.ts');
  await get('enc/index.m3u8');
  await get('dash/stream.mpd');
  await get('drm/stream.mpd');
  document.title = 'Fixture Video Page';
 })();
</script></body></html>""")
    (root / "page2.html").write_text("<!doctype html><title>No media</title><p>nothing here</p>")


TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".mpd": "application/dash+xml", ".mp4": "video/mp4",
         ".ts": "video/mp2t", ".html": "text/html; charset=utf-8"}


def serve(root: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def guess_type(self, path):
            return TYPES.get(Path(path).suffix, "application/octet-stream")

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main():
    if not CHROME:
        print("no Chromium in ~/.cache/ms-playwright"); sys.exit(2)
    tmp = Path(tempfile.mkdtemp(prefix="vg_sniff_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    root = tmp / "www"
    root.mkdir()
    fixtures(root)
    srv, port = serve(root)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=CHROME, accept_downloads=True,
                args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                      "--no-sandbox", "--autoplay-policy=no-user-gesture-required",
                      "--host-resolver-rules=MAP vgfixture.test 127.0.0.1"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            # Wait for the worker's first run: requests made while a freshly
            # installed extension is still starting reach no listener (a real
            # user installs it long before opening the page).
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function'"):
                    break
                time.sleep(0.1)

            page = ctx.new_page()
            page.goto(f"{base}/page.html", wait_until="load")
            page.wait_for_timeout(3500)

            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true})")   # fresh profile
            pop.reload(wait_until="domcontentloaded")
            pop.wait_for_timeout(800)
            tab_id = pop.evaluate(f"""() => new Promise(r => chrome.tabs.query({{}}, ts =>
                r((ts.find(t => (t.url || '').startsWith('{base}/page.html')) || {{}}).id)))""")
            check("fixture tab found", bool(tab_id), str(tab_id))

            def listed():
                return pop.evaluate("(id) => new Promise(r => chrome.runtime.sendMessage({type:'VG_SNIFF_LIST', tabId:id}, x => r(x.items)))", tab_id)

            items = []
            for _ in range(20):                 # manifests are read asynchronously
                items = listed()
                if items and not any(i.get("pending") for i in items):
                    break
                pop.wait_for_timeout(300)
            for i in items:
                print("     item:", i["kind"], i["url"].replace(base, ""), "size", i.get("size"),
                      "h", i.get("height"), "enc", i.get("encryption"), "dur", i.get("duration"),
                      "variants", [v.get("height") for v in i.get("variants") or []])
            by = {i["url"].replace(base, ""): i for i in items}
            check("exactly 5 media listed", len(items) == 5, str(sorted(by)))
            mp4 = by.get("/big.mp4") or {}
            check("progressive MP4: kind file, full size", mp4.get("kind") == "file" and mp4.get("size") == BIG, str(mp4.get("size")))
            check("MP4 carries the page title", mp4.get("title") == "Fixture Video Page", mp4.get("title"))
            hls = by.get("/hls/master.m3u8") or {}
            check("HLS master: 720p best, variants 720·360", hls.get("kind") == "hls" and hls.get("height") == 720
                  and [v["height"] for v in hls.get("variants") or []] == [720, 360], json.dumps(hls.get("variants")))
            check("HLS duration read from the best variant", hls.get("duration") == 12, str(hls.get("duration")))
            check("HLS clear", hls.get("encryption") == "none" and hls.get("live") is False)
            check("720p child playlist NOT listed (the master stands for it)", "/hls/720/index.m3u8" not in by)
            check("tiny MP4 and .ts segment NOT listed", "/tiny.mp4" not in by and "/hls/720/s1.ts" not in by)
            check("AES-128 HLS flagged", (by.get("/enc/index.m3u8") or {}).get("encryption") == "aes128")
            dash = by.get("/dash/stream.mpd") or {}
            check("DASH: 1080p best, 30 s, 1 audio, clear", dash.get("height") == 1080 and dash.get("duration") == 30
                  and dash.get("audios") == 1 and dash.get("encryption") == "none", json.dumps(dash)[:160])
            check("DRM DASH flagged", (by.get("/drm/stream.mpd") or {}).get("encryption") == "drm")

            badge = pop.evaluate("(id) => chrome.action.getBadgeText({tabId:id})", tab_id)
            check("badge shows 5", badge == "5", badge)

            # popup rows, rendered for that tab by the popup's own code
            pop.evaluate("(a) => renderSniff({id:a[0], url:a[1]})", [tab_id, f"{base}/page.html"])
            pop.wait_for_timeout(400)
            rows = pop.locator("#vg-sniff .vg-sniff-row")
            kinds = [rows.nth(k).locator(".vg-sniff-kind").inner_text() for k in range(rows.count())]
            check("5 rows in the popup", rows.count() == 5, str(kinds))
            check("panel visible with count", pop.locator("#vg-sniff").is_visible()
                  and pop.locator("#vg-sniff-count").inner_text() == "(5)")
            blocked = sorted(pop.locator(".vg-sniff-blocked").all_inner_texts())
            check("encrypted + DRM rows have no download button", len(blocked) == 2
                  and any("DRM" in b for b in blocked) and any("mã hoá" in b for b in blocked), str(blocked))
            dl = pop.locator(".vg-sniff-dl").all_inner_texts()
            check("download buttons: MP4 direct + 2 streams via VidGrab", sorted(dl) == sorted(["Tải", "Tải qua VidGrab", "Tải qua VidGrab"]), str(dl))
            hls_row = [k for k in range(rows.count()) if kinds[k] == "HLS" and "720p · 360p" in rows.nth(k).inner_text()]
            check("HLS row shows 720p · 360p and 0:12", bool(hls_row) and "0:12" in rows.nth(hls_row[0]).inner_text(),
                  rows.nth(hls_row[0]).inner_text().replace("\n", " | ") if hls_row else "")

            # a real download of the MP4 from the user's browser
            mp4_row = [k for k in range(rows.count()) if kinds[k] == "MP4"][0]
            rows.nth(mp4_row).locator(".vg-sniff-dl").click()
            done = None
            for _ in range(40):
                done = pop.evaluate("""() => new Promise(r => chrome.downloads.search({orderBy:['-startTime'], limit:1}, d => r(d[0] || null)))""")
                if done and done.get("state") in ("complete", "interrupted"):
                    break
                pop.wait_for_timeout(250)
            check("MP4 downloaded straight from the page's server", done and done.get("state") == "complete"
                  and done.get("fileSize") == BIG and done.get("url", "").endswith("/big.mp4"),
                  f"{(done or {}).get('state')} {(done or {}).get('fileSize')} {(done or {}).get('filename', '')[-40:]}")
            # (Playwright saves downloads under GUID names, so the path is not checkable here)
            check("download made by the extension itself", (done or {}).get("byExtensionId") == ext_id,
                  str((done or {}).get("byExtensionId")))

            # a real navigation starts a new list
            page.goto(f"{base}/page2.html", wait_until="load")
            page.wait_for_timeout(800)
            check("navigation clears the list", listed() == [])
            badge2 = pop.evaluate("(id) => chrome.action.getBadgeText({tabId:id})", tab_id)
            check("badge cleared", badge2 == "", badge2)

            # a site the user has not allowed: nothing is seen, and the popup asks
            other = ctx.new_page()
            other.goto(f"http://vgfixture.test:{port}/page.html", wait_until="load")
            other.wait_for_timeout(2500)
            other_id = pop.evaluate(f"""() => new Promise(r => chrome.tabs.query({{}}, ts =>
                r((ts.find(t => (t.url || '').startsWith('http://vgfixture.test')) || {{}}).id)))""")
            seen = pop.evaluate("(id) => new Promise(r => chrome.runtime.sendMessage({type:'VG_SNIFF_LIST', tabId:id}, x => r(x.items)))", other_id)
            check("not-allowed site: nothing captured", seen == [], str(len(seen)))
            pop.evaluate("(a) => initSniff({id:a[0], url:a[1]})", [other_id, f"http://vgfixture.test:{port}/page.html"])
            pop.wait_for_timeout(300)
            check("popup offers 'Bắt video trên trang này'", pop.locator("#vg-sniff-allow").is_visible()
                  and pop.locator("#vg-sniff-allow").inner_text() == "Bắt video trên trang này")
            granted = pop.evaluate("""() => new Promise(r => chrome.permissions.contains({origins:['http://vgfixture.test/*']}, r))""")
            check("nothing granted without the user's click", granted is False)

            # YouTube: refused, sent to the web
            pop.fill("#vg-url-input", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            err = pop.evaluate("() => getTargetUrl().then(v => 'accepted:' + v).catch(e => e.message)")
            check("YouTube link refused by the extension", "không tải video YouTube" in err, err)
            check("YouTube note offers the web", pop.locator("#vg-yt-note").is_visible())
            pop.fill("#vg-url-input", "https://www.tiktok.com/@a/video/123")
            ok = pop.evaluate("() => getTargetUrl().then(v => 'accepted:' + v).catch(e => e.message)")
            check("other links still accepted", ok == "accepted:https://www.tiktok.com/@a/video/123", ok)

            man = pop.evaluate("() => chrome.runtime.getManifest()")
            check("no YouTube host permission", not any("youtube" in h for h in man.get("host_permissions", [])))
            check("footer shows the real version", pop.locator("#footer-version").inner_text() == f"v{man['version']}",
                  pop.locator("#footer-version").inner_text())
            ctx.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
