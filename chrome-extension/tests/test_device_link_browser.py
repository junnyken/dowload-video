"""5.21.0: device linking + TikTok/YouTube handed to the VidGrab app.

Owner 24/10: one account on at most Free 2 / Pro 5 devices (website, extension
and app on one PC = one device), Free accounts on one device share one
allowance, and "Tự giao cho app với TikTok/YouTube".

A fake VidGrab app listens on 127.0.0.1 (a real HTTP server, the same
/vidgrab/ping and /vidgrab/download the app's ext_bridge.rs answers); the API
is faked with Playwright routes; the fake site runs the real web-bridge.js.
Checks:
  [1] guest calls carry no X-VG-Device; signed-in calls do (64 hex, stable)
  [2] app running -> the extension adopts the app's machine id
  [3] the site in this browser receives the same id (VG_DEVICE_ID)
  [4] TikTok and YouTube go to the app (no claim, no fetch-link); the person's
      page URL is what the app gets
  [5] Facebook, MP3 and app-off stay in the browser / server path
  [6] 401 device_signed_out -> session dropped, popup message stored
Run: python3 tests/test_device_link_browser.py
"""
import json, os, shutil, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SITE = "https://dvid.vibe1.tinhgon.xyz/"
APP_DEVICE = "ab12" * 16
res = []
app_jobs = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


class FakeApp(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        o = self.headers.get("Origin")
        if o and o.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.startswith("/vidgrab/ping"):
            self._send(200, {"app": "VidGrab", "version": "0.19.4", "device": APP_DEVICE})
        else:
            self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        app_jobs.append(json.loads(self.rfile.read(n) or b"{}"))
        self._send(200, {"ok": True})


def start_app():
    for port in (17325, 17326, 17327):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), FakeApp)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return srv
        except OSError:
            continue
    raise SystemExit("no free app port")


PAGE = """<!doctype html><title>VidGrab</title><body>site<script>
window.vgIds = [];
window.addEventListener('message', (e) => { if (e.data && e.data.type === 'VG_DEVICE_ID') window.vgIds.push(e.data.id); });
</script></body>"""


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_devlink_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    calls = []           # (path, X-VG-Device or None)
    mode = {"fetch_link": 503}

    def api(route):
        req = route.request
        path = req.url.split("/api/v1/", 1)[1].split("?")[0]
        calls.append((path, req.headers.get("x-vg-device")))
        if path == "fetch-link" and mode["fetch_link"] == 401:
            route.fulfill(status=401, content_type="application/json", body=json.dumps(
                {"detail": {"error_code": "device_signed_out", "user_message": "signed out (server text)", "max_devices": 2}}))
            return
        if path == "client/quota/claim":
            route.fulfill(status=429, content_type="application/json", body='{"allowed":false,"detail":"stop"}')
            return
        route.fulfill(status=503, content_type="application/json", body='{"detail":"t"}')

    srv = None
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            ctx.route(SITE, lambda r: r.fulfill(status=200, content_type="text/html", body=PAGE))
            ctx.route("**/api/v1/**", api)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            for _ in range(40):
                if sw.evaluate("() => typeof vgSaveVideo === 'function' && typeof vgDeviceId === 'function'"):
                    break
                time.sleep(0.25)
            ev = sw.evaluate

            # [1] guest: no header; signed in: header, stable
            h_guest = ev("() => authHeaders({})")
            ev("() => chrome.storage.local.set({ vg_auth_token: 'vgx_test.sig' })")
            h1 = ev("() => authHeaders({})")
            h2 = ev("() => authHeaders({})")
            dev1 = h1.get("X-VG-Device", "")
            check("[1] guest call has no device id; signed-in call has a 64-hex one, the same each time",
                  "X-VG-Device" not in h_guest and len(dev1) == 64 and h2.get("X-VG-Device") == dev1, f"{h_guest} {dev1[:12]}")

            # [2] the app is on this computer: its machine id is adopted
            srv = start_app()
            app = ev("() => vgFindApp(true)")
            time.sleep(0.3)
            dev2 = ev("() => vgDeviceId()")
            check("[2] app running -> extension uses the app's machine id", app.get("port") and dev2 == APP_DEVICE, f"{app} {dev2[:12]}")

            # [3] the site in this browser gets the same id
            page = ctx.new_page()
            page.goto(SITE, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.evaluate("() => window.postMessage({ __vg_source: 'webapp', type: 'VG_REQUEST_DEVICE_ID' }, location.origin)")
            page.wait_for_timeout(800)
            ids = page.evaluate("() => window.vgIds")
            check("[3] VidGrab site receives the same device id from web-bridge.js", ids and set(ids) == {APP_DEVICE}, str(ids)[:80])

            # [4] TikTok / YouTube -> the app
            calls.clear(); app_jobs.clear()
            tt = "https://www.tiktok.com/@someone/video/7400000000000000001"
            yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            r1 = ev(f"() => vgSaveVideo({{ url: '{tt}', quality: 'video' }}, null, null)")
            r2 = ev(f"() => vgSaveVideo({{ url: '{yt}', quality: 'video' }}, null, null)")
            urls = [j.get("url") for j in app_jobs]
            check("[4] TikTok and YouTube handed to the app, nothing asked of the server",
                  r1.get("via") == "app" and r2.get("via") == "app" and urls == [tt, yt] and not calls,
                  f"via={r1.get('via')},{r2.get('via')} app={urls} api={calls}")
            check("[4b] the result says 'sent to the app' (popup/notification text)", r1["data"].get("sent_to_app") is True)

            # [5] not handed off: Facebook, MP3, switch off
            app_jobs.clear()
            for payload in ("{ url: 'https://www.facebook.com/reel/910445711561956', quality: 'video' }",
                            f"{{ url: '{tt}', quality: 'mp3_320' }}"):
                ev(f"() => vgSaveVideo({payload}, null, null).catch(e => e.message)")
            ev("() => chrome.storage.local.set({ vg_app_handoff: false })")
            ev(f"() => vgSaveVideo({{ url: '{tt}', quality: 'video' }}, null, null).catch(e => e.message)")
            ev("() => chrome.storage.local.remove('vg_app_handoff')")
            check("[5] Facebook, MP3 and the switch off do not go to the app", not app_jobs, str(app_jobs)[:120])
            check("[5b] those calls carried the device id to the server",
                  calls and all(d == APP_DEVICE for _, d in calls), str(calls)[:160])

            # [6] the account signed this device out
            mode["fetch_link"] = 401
            ev("() => chrome.storage.local.set({ vg_auth_token: 'vgx_test.sig', vg_direct: false })")
            ev("() => vgFetchAndSave({ url: 'https://www.instagram.com/reel/abc/', quality: 'video' }, null).catch(e => e.message)")
            st = {}
            for _ in range(20):
                st = ev("() => new Promise(r => chrome.storage.local.get(['vg_auth_token', 'vg_signed_out_msg'], r))")
                if not st.get("vg_auth_token"):
                    break
                time.sleep(0.25)
            check("[6] 401 device_signed_out -> session dropped, message kept for the popup",
                  not st.get("vg_auth_token") and st.get("vg_signed_out_msg") == "signed out (server text)", str(st))
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{sw.url.split('/')[2]}/popup.html", wait_until="domcontentloaded")
            pop.wait_for_timeout(1200)
            hint = pop.evaluate("() => (document.getElementById('vg-url-hint') || {}).textContent || ''")
            left = ev("() => new Promise(r => chrome.storage.local.get('vg_signed_out_msg', r))")
            check("[6b] popup shows the signed-out notice once", "thiết bị" in hint and not left.get("vg_signed_out_msg"), hint[:90])
            ctx.close()
    finally:
        if srv:
            srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
