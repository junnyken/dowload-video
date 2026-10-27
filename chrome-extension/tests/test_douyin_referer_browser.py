"""Douyin's CDN wants a douyin.com Referer; the extension adds it to its own downloads (5.22.1).

Owner 27/10: a direct Douyin download showed a red "Không thấy tệp trên trang" in Chrome, then the
same video came again ~15 s later through the server. Measured on the owner's link: the
v3-web.douyinvod.com file answers 403 without a Referer and 206 video/mp4 with
"https://www.douyin.com/" — chrome.downloads sends none.

Real Chromium; v3-web.douyinvod.com is mapped to a local "CDN" (--host-resolver-rules) that answers
403 unless Referer is https://www.douyin.com/ — the real host name, so the SHIPPED rule and the
manifest's own host permissions are what is tested (5.22.1 passed this test against localhost, which
the manifest happens to allow, yet had no permission for douyinvod.com: the rule did nothing for users).
  - the shipped rules are installed at start (douyinvod.com and co., extension requests only)
  - chrome.downloads alone never carries it (Chrome refuses the header, its requests pass no rule):
    vgSaveFile fails AND leaves no failed line in Chrome's downloads
  - vgSaveViaPage (fetch in the offscreen page, which gets the rule, then save the blob) gets the file
  - without the rule vgSaveViaPage fails too (control)
  - vgNeedsReferer: douyinvod.com hosts yes, others no
  - a web page's own request to that host gets no Referer from the extension

Negative control: --no-cdn-permission loads a copy without the Douyin CDN host permissions (= 5.22.1):
vgSaveViaPage must fail there.
Run: python3 tests/test_douyin_referer_browser.py [--no-cdn-permission]
"""
import http.server, json, os, shutil, sys, tempfile, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

REF = "https://www.douyin.com/"
seen = []
res = []


class CDN(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        ref = self.headers.get("Referer")
        seen.append((self.path, ref))
        if self.path.startswith("/page"):
            body, code, ctype = b"<!doctype html><title>p</title>", 200, "text/html"
        elif ref == REF:
            body, code, ctype = b"\0" * 64 * 1024, 200, "video/mp4"
        else:
            body, code, ctype = b"forbidden", 403, "video/mp4"  # what douyinvod.com does
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CDN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://v3-web.douyinvod.com:{srv.server_port}"
    weak = sys.argv[1:] == ["--no-cdn-permission"]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        tmp = Path(tmp)
        ext = T.EXT
        if weak:
            ext = tmp / "ext"
            shutil.copytree(T.EXT, ext, ignore=shutil.ignore_patterns("tests", "store-assets", "__pycache__"))
            m = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
            cdn = ("douyinvod", "douyincdn", "zjcdn", "bytecdn", "amemv", "snssdk")
            m["host_permissions"] = [h for h in m["host_permissions"] if not any(c in h for c in cdn)]
            (ext / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        (tmp / "fonts.conf").write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                                        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc</cachedir></fontconfig>')
        ctx = p.chromium.launch_persistent_context(
            str(tmp / "profile"), headless=False, executable_path=T.CHROME, accept_downloads=True,
            env={**os.environ, "FONTCONFIG_FILE": f"{tmp}/fonts.conf"},
            args=["--headless=new", f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-sandbox",
                  "--host-resolver-rules=MAP v3-web.douyinvod.com 127.0.0.1"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)

        rules = sw.evaluate("async () => { for (let i = 0; i < 20; i++) { const r = await chrome.declarativeNetRequest.getSessionRules(); if (r.length) return r; await new Promise(z => setTimeout(z, 250)); } return []; }")
        r = next((x for x in rules if x["id"] == 9101), None)
        check("shipped rule installed: douyinvod.com, Referer douyin.com, extension requests only",
              bool(r) and "douyinvod.com" in r["condition"]["requestDomains"] and r["condition"]["tabIds"] == [-1]
              and r["action"]["requestHeaders"][0]["value"] == REF, str(r)[:200])

        nr = sw.evaluate("() => [vgNeedsReferer('https://v3-web.douyinvod.com/x/?a=1'), vgNeedsReferer('https://v26.zjcdn.com/v'), vgNeedsReferer('https://cdn.example/a.mp4'), vgNeedsReferer('https://notdouyinvod.com/a')]")
        check("vgNeedsReferer: Douyin CDN hosts only", nr == [True, True, False, False], str(nr))

        ok = sw.evaluate(f"() => vgSaveFile('{base}/v1.mp4', 'plain')")
        left = sw.evaluate("() => new Promise(r => chrome.downloads.search({}, (l) => r(l.map(d => [d.url.split('/').pop(), d.state]))))")
        check("chrome.downloads alone gets no Referer even with the rule (why 5.22.0 failed)", ok is False and (f"/v1.mp4", None) in seen, str(seen[-1:]))
        check("the failed try leaves no line in Chrome's downloads", not any(u == "v1.mp4" for u, _ in left), str(left))

        ok = sw.evaluate(f"() => vgSaveViaPage('{base}/v2.mp4', 'via-page')")
        if weak:
            check("control: without the CDN host permission (5.22.1) vgSaveViaPage fails", ok is False, str(seen[-1:]))
            ctx.close()
            srv.shutdown()
            print(f"\n{sum(res)}/{len(res)} passed")
            sys.exit(0 if all(res) else 1)
        # (Playwright names every saved file by a GUID, so the check is on the blob: download, not the name)
        left = sw.evaluate("() => new Promise(r => chrome.downloads.search({}, (l) => r(l.map(d => [d.url.slice(0, 5), d.state, d.fileSize]))))")
        check("vgSaveViaPage: the file arrives (fetched with the Referer, saved from a blob)",
              ok is True and (f"/v2.mp4", REF) in seen and any(u == "blob:" and st == "complete" and sz == 64 * 1024 for u, st, sz in left), str(left))

        sw.evaluate("() => chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [9101] })")
        ok = sw.evaluate(f"() => vgSaveViaPage('{base}/v3.mp4', 'no-rule')")
        check("control: without the rule vgSaveViaPage fails (403)", ok is False and (f"/v3.mp4", None) in seen, str(seen[-1:]))

        sw.evaluate("() => vgRefererRules()")
        page = ctx.new_page()
        page.goto(f"{base}/page")
        page.evaluate(f"() => fetch('{base}/fromtab.mp4').then(r => r.status)")
        tab_ref = next((ref for path, ref in seen if path == "/fromtab.mp4"), "missing")
        check("a web page's own request gets no Referer from the extension", tab_ref != REF, str(tab_ref))
        ctx.close()
    srv.shutdown()
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
