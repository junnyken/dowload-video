"""
/reset-password, driven against the production bundle.

The route did not exist: resetPassword() sent users to <origin>/reset-password,
which was absent from PATH_MAP, so the recovery link fell through to the landing
page. The token in the fragment signs the user in silently, so it looked like the
reset had worked while the old password was still the only one.
"""
import http.server, socketserver, threading, sys, functools
from pathlib import Path
from playwright.sync_api import sync_playwright

DIST   = Path(__file__).resolve().parent.parent / "dist"
CHROME = str(Path.home() / ".cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
res = []
def check(n, ok, d=""):
    res.append(ok); print(("  PASS  " if ok else "  FAIL  ") + n + (f"   [{d}]" if d else ""), flush=True)

class SPA(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (DIST / self.path.lstrip("/").split("?")[0]).is_file():
            self.path = "/index.html"
        return super().do_GET()
    def log_message(self, *a): pass

def main():
    handler = functools.partial(SPA, directory=str(DIST))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            pg = b.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))

            # 1. The route resolves to its own page, not the landing page.
            pg.goto(base + "/reset-password", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(1500)
            body = pg.inner_text("body")
            check("route renders the reset page", "Đặt mật khẩu mới" in body, body[:70])
            check("no page error", not errs, "; ".join(errs)[:70])
            check("both password fields present",
                  pg.locator("input[type=password]").count() == 2,
                  str(pg.locator("input[type=password]").count()))

            # 2. Without a recovery session the user is told, not left guessing.
            check("missing recovery session is called out",
                  "Không tìm thấy phiên đặt lại mật khẩu" in body)

            # 3. Validation before anything is sent.
            btn = pg.locator("button[type=submit]")
            check("submit disabled while empty", btn.is_disabled())
            pg.fill("input[type=password] >> nth=0", "short")
            pg.wait_for_timeout(250)
            check("too-short password rejected", "Cần ít nhất 8 ký tự" in pg.inner_text("body"))
            pg.fill("input[type=password] >> nth=0", "longenough123")
            pg.fill("input[type=password] >> nth=1", "different123")
            pg.wait_for_timeout(250)
            check("mismatch rejected", "Hai mật khẩu chưa khớp" in pg.inner_text("body"))
            check("submit still disabled on mismatch", btn.is_disabled())
            pg.fill("input[type=password] >> nth=1", "longenough123")
            pg.wait_for_timeout(250)
            check("submit enabled when valid and matching", btn.is_enabled())

            # 4. An expired link reports itself instead of showing a dead form.
            # Opened in a FRESH page: changing only the fragment on the current
            # page is a same-document navigation, which never reloads and so is
            # not how a link from an email actually arrives.
            pg2 = b.new_page()
            pg2.goto(base + "/reset-password#error=access_denied&error_description=Email+link+is+invalid+or+has+expired",
                     wait_until="domcontentloaded", timeout=30000)
            pg2.wait_for_timeout(1500)
            t = pg2.inner_text("body")
            check("expired link is reported", "Liên kết không dùng được" in t)
            check("expired reason is shown", "expired" in t.lower(), t[:80])
            b.close()
        httpd.shutdown()
    ok = sum(1 for r in res if r)
    print(f"\n── {ok}/{len(res)} passed ──", flush=True)
    return 0 if ok == len(res) else 1

if __name__ == "__main__":
    sys.exit(main())
