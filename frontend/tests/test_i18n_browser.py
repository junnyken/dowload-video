"""Vietnamese / English in a real Chromium against the production build (dist/).

Serves dist/ like nginx.conf (try_files $uri $uri/index.html /index.html) and
checks: the prerendered HTML per language (lang, title, canonical, hreflang),
the app in English under /en (nav, switch, links keep /en), the switch to
Vietnamese keeps the page, the platform page in both languages, and that the
head tags follow in-app navigation.
Run: npm run build && python3 tests/test_i18n_browser.py
"""
import http.server, os, socketserver, sys, tempfile, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

DIST = Path(__file__).resolve().parents[1] / "dist"
CHROME = next((str(p) for p in sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux64/chrome"), reverse=True)), None)
SITE = "https://dowloadvideo.io.vn"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DIST), **k)

    def log_message(self, *a):
        pass

    def send_head(self):
        path = self.path.split("?")[0].split("#")[0]
        f = DIST / path.lstrip("/")
        if path.startswith("/api/"):
            self.send_error(503); return None
        if not f.is_file() and not (f / "index.html").is_file():
            # SPA fallback; English paths get the English shell (nginx.conf location /en/)
            self.path = "/en/index.html" if path.startswith("/en/") else "/index.html"
        elif f.is_dir():
            self.path = path.rstrip("/") + "/index.html"
        return super().send_head()


def serve():
    """dist/ like nginx on a free port (+ a private fontconfig for Chromium). Returns (server, base URL)."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "fonts.conf").write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>/usr/share/fonts</dir><cachedir>{tmp}/fc</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main():
    srv, base = serve()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=["--headless=new", "--no-sandbox"])
            pg = b.new_page()
            # static HTML first (what crawlers read), no JavaScript
            raw = pg.request.get(f"{base}/en/pricing").text()
            check("static /en/pricing: lang en + English title", '<html lang="en"' in raw and "<title>VidGrab pricing" in raw)
            check("static /en/pricing: canonical + 3 hreflang", f'rel="canonical" href="{SITE}/en/pricing"' in raw
                  and f'hreflang="vi" href="{SITE}/pricing"' in raw and f'hreflang="x-default" href="{SITE}/pricing"' in raw)
            raw_vi = pg.request.get(f"{base}/tai-video-tiktok").text()
            check("static Vietnamese platform page links to its English twin", f'hreflang="en" href="{SITE}/en/tiktok-video-downloader"' in raw_vi
                  and '<html lang="vi"' in raw_vi)

            # the app in English
            pg.goto(f"{base}/en/pricing", wait_until="networkidle")
            pg.wait_for_timeout(1000)
            nav = pg.locator("nav").first.inner_text()
            check("app in English: html lang en", pg.evaluate("document.documentElement.lang") == "en")
            check("nav in English", "Platforms" in nav and "Extension" in nav and "Nền tảng" not in nav, nav.replace("\n", " | ")[:120])
            check("language switch offers VI", pg.get_by_role("button", name="Language: Tiếng Việt").inner_text().strip() == "VI")
            pg.get_by_role("button", name="Platforms").first.click()
            pg.wait_for_timeout(800)
            check("in-app link keeps /en", pg.url == f"{base}/en/platforms", pg.url)
            check("head follows navigation (title, canonical)", pg.title().startswith("Supported platforms")
                  and pg.evaluate("document.querySelector('link[rel=canonical]').href") == f"{SITE}/en/platforms", pg.title())
            pg.get_by_role("button", name="Language: Tiếng Việt").click()
            pg.wait_for_timeout(800)
            check("switch to Vietnamese keeps the page", pg.url == f"{base}/platforms", pg.url)
            nav = pg.locator("nav").first.inner_text()
            check("now Vietnamese: lang, nav, title", pg.evaluate("document.documentElement.lang") == "vi" and "Nền tảng" in nav
                  and pg.title().startswith("Nền tảng được hỗ trợ"), pg.title())
            check("the switch now offers EN", pg.get_by_role("button", name="Ngôn ngữ: English").inner_text().strip() == "EN")

            # platform landing page in both languages (rendered by React)
            pg.goto(f"{base}/en/tiktok-video-downloader", wait_until="networkidle")
            pg.wait_for_timeout(1000)
            h1 = pg.locator("h1").first.inner_text()
            check("English platform page (React)", h1 == "Download TikTok videos without watermark", h1)
            check("its section titles are English", pg.get_by_role("heading", name="How to download TikTok videos with VidGrab").count() == 1)
            check("its other-platform links are English URLs", pg.locator('a[href="/en/facebook-video-downloader"]').count() >= 1)
            pg.get_by_role("button", name="Language: Tiếng Việt").click()
            pg.wait_for_timeout(800)
            check("switch on a platform page -> Vietnamese slug", pg.url == f"{base}/tai-video-tiktok", pg.url)
            check("Vietnamese platform page", pg.locator("h1").first.inner_text() == "Tải video TikTok không logo")

            # every public English page: no Vietnamese words on screen, no raw
            # i18n keys ("ns.key" / "key_other"), no JavaScript errors
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)[:120]))
            for path in ["/en", "/en/pricing", "/en/faq", "/en/platforms", "/en/extension", "/en/download",
                         "/en/privacy", "/en/terms", "/en/takedown", "/en/douyin-video-downloader"]:
                errors.clear()
                pg.goto(f"{base}{path}", wait_until="networkidle")
                pg.wait_for_timeout(800)
                words = pg.evaluate(r"""() => {
                  const text = document.body.innerText.replace(/Tiếng Việt|Cốc Cốc/g, '');
                  const vi = text.match(/\S*[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]\S*/gi) || [];
                  const keys = text.match(/\b[a-z]+[A-Z]\w*\.[a-z]\w+\b|\b\w+_(one|other)\b/g) || [];
                  return { vi: [...new Set(vi)].slice(0, 8), keys: [...new Set(keys)].slice(0, 5) };
                }""")
                check(f"{path}: all English on screen", not words["vi"] and not words["keys"], f"{words}" if words["vi"] or words["keys"] else "")
                check(f"{path}: no JavaScript error", not errors, "; ".join(errors))
            b.close()
    finally:
        srv.shutdown()
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
