"""
The four ported admin pages, driven in a real browser against the built bundle.

Serves frontend/dist locally so this exercises the production build, not the dev
server. Without an admin session the pages must still mount and show their own
error state rather than crashing the shell — that is the difference between a
port that works and one that merely compiles.
"""
import http.server, socketserver, threading, sys, functools
from pathlib import Path
from playwright.sync_api import sync_playwright

DIST   = Path(__file__).resolve().parent.parent / "dist"
CHROME = str(Path.home() / ".cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
PAGES = [
    ("/vid-admin/queue-health",       "Queue Health"),
    ("/vid-admin/playbooks",          "Playbooks"),
    ("/vid-admin/automation-history", "Automation History"),
    ("/vid-admin/youtube-gate",       "YouTube Gate"),
    ("/vid-admin/anomalies",          "Anomalies"),
    ("/vid-admin/ops-signals",        "Ops Signals"),
]
res = []
def check(n, ok, d=""):
    res.append(ok); print(("  PASS  " if ok else "  FAIL  ") + n + (f"   [{d}]" if d else ""), flush=True)

class SPA(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        p = DIST / self.path.lstrip("/")
        if not p.is_file():
            self.path = "/index.html"     # SPA fallback
        return super().do_GET()

def main():
    handler = functools.partial(SPA, directory=str(DIST))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            pg = b.new_page()
            calls = []
            pg.on("request", lambda r: calls.append(r.url) if "/api/v1/" in r.url else None)
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))

            # A session token so the pages take their authenticated path.
            pg.goto(base + "/vid-admin/login", wait_until="domcontentloaded", timeout=30000)
            pg.evaluate("""() => localStorage.setItem('vg_admin_session', JSON.stringify({
                email:'t@t', role:'superadmin', sessionToken:'probe-token',
                expiresAt: new Date(Date.now()+3600e3).toISOString()}))""")

            for path, title in PAGES:
                calls.clear(); errors.clear()
                pg.goto(base + path, wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(1800)
                body = pg.inner_text("body")
                check(f"{path} renders its heading", title in body, title if title in body else body[:60])
                check(f"{path} raises no page error", not errors, "; ".join(errors)[:70])

            # Each page must actually talk to its endpoint.
            wanted = {
                "/vid-admin/queue-health":       "/intelligence/queue-health",
                "/vid-admin/playbooks":          "/intelligence/playbooks",
                "/vid-admin/automation-history": "/intelligence/automation-history",
                "/vid-admin/youtube-gate":       "/admin/youtube/status",
                "/vid-admin/anomalies":          "/intelligence/anomalies",
                "/vid-admin/ops-signals":        "/admin/ops-signals",
            }
            for path, frag in wanted.items():
                calls.clear()
                pg.goto(base + path, wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(1800)
                check(f"{path} calls {frag}", any(frag in c for c in calls),
                      next((c for c in calls if "/api/v1/" in c), "no api call"))
            b.close()
        httpd.shutdown()

    ok = sum(1 for r in res if r)
    print(f"\n── {ok}/{len(res)} passed ──", flush=True)
    return 0 if ok == len(res) else 1

if __name__ == "__main__":
    sys.exit(main())
