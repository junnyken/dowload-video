"""Manual-URL input, driven in a real Chromium with the real extension."""
import shutil, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

EXT    = str(Path(__file__).resolve().parent.parent)
CHROME = str(Path.home() / ".cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
res = []
def check(n, ok, d=""):
    res.append(ok); print(("  PASS  " if ok else "  FAIL  ") + n + (f"   [{d}]" if d else ""), flush=True)

def main():
    prof = tempfile.mkdtemp(prefix="vg_url_")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                prof, headless=False, executable_path=CHROME,
                args=["--headless=new", f"--disable-extensions-except={EXT}",
                      f"--load-extension={EXT}", "--no-sandbox"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]

            # Reproduce the reported scenario: a feed page is the active tab.
            feed = ctx.new_page()
            feed.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
            feed.bring_to_front()

            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded", timeout=20000)
            pop.wait_for_timeout(2000)

            check("URL input exists in the popup", pop.locator("#vg-url-input").count() == 1)
            check("paste button exists", pop.locator("#vg-url-paste").count() == 1)

            # clipboardRead must be OPTIONAL: a fresh install shows no
            # "Read data you copy and paste" warning, and nothing prompts on open.
            manifest = pop.evaluate("() => chrome.runtime.getManifest()")
            check("clipboardRead is NOT an install-time permission",
                  "clipboardRead" not in (manifest.get("permissions") or []),
                  str(manifest.get("permissions")))
            check("clipboardRead is declared optional",
                  "clipboardRead" in (manifest.get("optional_permissions") or []),
                  str(manifest.get("optional_permissions")))
            granted = pop.evaluate("""() => new Promise(r =>
                chrome.permissions.contains({permissions:['clipboardRead']}, g => r(!!g)))""")
            check("permission is not granted just by opening the popup", granted is False, str(granted))

            hint = pop.locator("#vg-url-hint").inner_text()
            check("feed page is called out instead of failing silently", bool(hint.strip()), hint.strip()[:60])

            # Typing a link must be what the download uses.
            target = "https://x.com/i/status/1234567890123456789"
            pop.fill("#vg-url-input", target)
            pop.wait_for_timeout(400)
            check("typed link is accepted", pop.locator("#vg-url-hint").inner_text().strip().startswith("Sẽ tải"),
                  pop.locator("#vg-url-hint").inner_text().strip()[:40])
            got = pop.evaluate("() => getTargetUrl()")
            check("getTargetUrl() returns the typed link, not the tab", got == target, str(got))

            # Invalid input must be rejected, not silently sent.
            pop.fill("#vg-url-input", "not a url")
            pop.wait_for_timeout(300)
            rejected = pop.evaluate("""async () => {
                try { await getTargetUrl(); return 'accepted'; }
                catch (e) { return e.message; }
            }""")
            check("invalid link is rejected", "hợp lệ" in str(rejected), str(rejected)[:50])

            # Empty box must fall back to the active tab. Opening the popup as a
            # tab makes OUR page the active tab, so stub the lookup to model the
            # real action-popup case where the feed tab is active.
            pop.fill("#vg-url-input", "")
            pop.wait_for_timeout(300)
            pop.evaluate("() => { window.getActiveTab = async () => ({ url: 'https://x.com/home' }) }")
            fb = pop.evaluate("() => getTargetUrl().catch(e => 'ERR:' + e.message)")
            check("empty box falls back to the active tab", str(fb) == "https://x.com/home", str(fb)[:60])

            # And our own pages must never be treated as a download target.
            pop.evaluate("() => { window.getActiveTab = async () => ({ url: 'chrome-extension://abc/popup.html' }) }")
            own = pop.evaluate("() => getTargetUrl().then(v => 'accepted:' + v).catch(e => e.message)")
            check("extension's own page is not a download target", "Không lấy được link" in str(own), str(own)[:45])

            # The feed detector must actually recognise a feed path.
            feeds = pop.evaluate("""() => ['https://x.com/home','https://www.tiktok.com/foryou','https://x.com/explore']
                                            .map(u => looksLikeFeed(u))""")
            check("feed paths are recognised", all(feeds), str(feeds))
            posts = pop.evaluate("""() => ['https://x.com/i/status/123','https://www.tiktok.com/@a/video/7']
                                            .map(u => looksLikeFeed(u))""")
            check("real post URLs are NOT treated as feeds", not any(posts), str(posts))
            ctx.close()
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    ok = sum(1 for r in res if r)
    print(f"\n── {ok}/{len(res)} passed ──", flush=True)
    return 0 if ok == len(res) else 1

if __name__ == "__main__":
    sys.exit(main())
