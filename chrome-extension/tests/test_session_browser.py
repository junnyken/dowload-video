"""5.18.3: signed in on the site = signed in in the extension, for 30 days.

Owner 22/10: Pro on the site, "Khách … 5 lượt/ngày" in the extension — it only
got the web's 1-hour Supabase token after its own "Kết nối tài khoản", and
never renewed it. A fake VidGrab site is served at
https://dvid.vibe1.tinhgon.xyz/ (the real web-bridge.js runs there); the
server is faked with Playwright routes. Checks:
  [1] the site posts its session -> the extension stores its own vgx_ token
      (POST /extension/session with the web token), with expiry and email
  [2] the site posts again (every visit) -> kept, no second POST
  [3] a download's claim is sent with the vgx_ token (= the Pro account)
  [4] a session with < 7 days left is renewed with the vgx_ token itself
  [5] sign-out in the extension -> DELETE /extension/session, token removed
  [6] sign-out on the site -> the extension is signed out too
Run: python3 tests/test_session_browser.py
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SITE = "https://dvid.vibe1.tinhgon.xyz/"
WEB_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.web-session"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


PAGE = """<!doctype html><title>VidGrab</title><body>site
<script>
  window.vgPostSession = () => window.postMessage({ __vg_source: 'webapp', type: 'VG_AUTH_TOKEN_FROM_WEB', token: '%s', email: 'pro@example.com' }, location.origin);
  window.vgPostLogout = () => window.postMessage({ __vg_source: 'webapp', type: 'VG_AUTH_LOGOUT_FROM_WEB' }, location.origin);
</script></body>""" % WEB_JWT


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_sess_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    posts, deletes, claims = [], [], []
    n = {"i": 0}

    def on_session(route):
        auth = route.request.headers.get("authorization", "")
        if route.request.method == "DELETE":
            deletes.append(auth)
            route.fulfill(status=200, content_type="application/json", body='{"revoked":true}')
            return
        posts.append(auth)
        n["i"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"token": f"vgx_test{n['i']}.sig", "expiresAt": int(time.time()) + 30 * 86400, "email": "pro@example.com"}))

    def on_claim(route):
        claims.append(route.request.headers.get("authorization", ""))
        route.fulfill(status=429, content_type="application/json", body='{"allowed":false,"detail":"stop here"}')

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            ctx.route(SITE, lambda r: r.fulfill(status=200, content_type="text/html", body=PAGE))
            ctx.route("**/api/v1/extension/session", on_session)
            ctx.route("**/api/v1/client/quota/claim", on_claim)
            ctx.route("**/api/v1/fetch-link", lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"t"}'))
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            store = lambda: sw.evaluate("() => new Promise(r => chrome.storage.local.get(['vg_auth_token','vg_auth_exp','vg_auth_email'], r))")

            def wait(pred, secs=10):
                for _ in range(secs * 4):
                    s = store()
                    if pred(s):
                        return s
                    time.sleep(0.25)
                return store()

            site = ctx.new_page()
            site.goto(SITE, wait_until="domcontentloaded")
            site.wait_for_timeout(800)
            site.evaluate("() => vgPostSession()")
            s = wait(lambda s: (s.get("vg_auth_token") or "").startswith("vgx_"))
            check("[1] web session swapped for the extension's own vgx_ token",
                  s.get("vg_auth_token") == "vgx_test1.sig" and posts == [f"Bearer {WEB_JWT}"]
                  and s.get("vg_auth_email") == "pro@example.com" and s.get("vg_auth_exp", 0) > time.time() + 29 * 86400,
                  f"{s} posts={posts}")
            site.evaluate("() => vgPostSession()")
            time.sleep(1.5)
            check("[2] the site posts again: kept, no new session asked", len(posts) == 1 and store().get("vg_auth_token") == "vgx_test1.sig", str(posts))

            out = sw.evaluate("() => vgClaim('https://www.facebook.com/reel/910445711561956').then(x => x)")
            check("[3] a download's claim carries the vgx_ token", claims == ["Bearer vgx_test1.sig"], f"{claims} {out}")

            sw.evaluate("() => chrome.storage.local.set({ vg_auth_exp: Math.floor(Date.now() / 1000) + 3 * 86400 })")
            sw.evaluate("() => vgRenewSession()")
            s = wait(lambda s: s.get("vg_auth_token") == "vgx_test2.sig")
            check("[4] < 7 days left: renewed with the vgx_ token itself", s.get("vg_auth_token") == "vgx_test2.sig"
                  and posts[-1] == "Bearer vgx_test1.sig", f"{s.get('vg_auth_token')} posts={posts}")

            ext_id = sw.url.split("/")[2]
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({ type: 'VG_CLEAR_AUTH_TOKEN' }, r))")
            s = wait(lambda s: not s.get("vg_auth_token"))
            check("[5] sign-out in the extension: DELETE /extension/session, token removed",
                  deletes == ["Bearer vgx_test2.sig"] and not s.get("vg_auth_token") and not s.get("vg_auth_exp"), f"{deletes} {s}")

            site.evaluate("() => vgPostSession()")
            wait(lambda s: (s.get("vg_auth_token") or "").startswith("vgx_"))
            site.evaluate("() => vgPostLogout()")
            s = wait(lambda s: not s.get("vg_auth_token"))
            check("[6] sign-out on the site signs the extension out", not s.get("vg_auth_token"), str(s))
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
