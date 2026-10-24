"""5.20.1: the Chrome Web Store build is clean of YouTube downloading and asks
before the sensitive things; the website build is unchanged.

Builds both zips with scripts/package-extension.py, unpacks each and loads it.
Store build:
  - YouTube refused on every path: vgSaveVideo throws (no fetch-link call),
    the popup's YouTube note has no "open on the website" button
  - sign-in cookies OFF by default (getEphemeralCookiesB64 -> nothing),
    and ON once the person ticks the switch
  - tabCapture is optional: not granted at install
Website build:
  - cookies ON by default; YouTube not refused
Run: python3 tests/test_store_build.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def load(p, tmp, ext_dir, tag):
    ctx = p.chromium.launch_persistent_context(
        str(tmp / f"profile-{tag}"), headless=False, executable_path=T.CHROME, locale="en-US",
        args=["--headless=new", "--lang=en", f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}", "--no-sandbox"])
    fetch_links = []
    ctx.route("**/api/v1/fetch-link", lambda r: (fetch_links.append(r.request.url), r.fulfill(status=503, content_type="application/json", body='{"detail":"t"}')))
    ctx.route("**/api/v1/client/quota/claim", lambda r: r.fulfill(status=200, content_type="application/json", body='{"allowed":true,"claimId":"x1234567"}'))
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
    for _ in range(40):
        if sw.evaluate("() => typeof vgSaveVideo === 'function'"):
            break
        time.sleep(0.25)
    return ctx, sw, fetch_links


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_store_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts/package-extension.py"), "--store"], check=True, capture_output=True)
        version = json.loads((ROOT / "chrome-extension/manifest.json").read_text())["version"]
        store_zip = ROOT / f"chrome-extension/store-assets/VidGrab-store-v{version}.zip"
        store_dir = tmp / "store"
        zipfile.ZipFile(store_zip).extractall(store_dir)
        m = json.loads((store_dir / "manifest.json").read_text())
        check("store manifest: no YouTube host, tabCapture only optional",
              not any("youtube" in h for h in m.get("host_permissions", [])) and "tabCapture" not in m["permissions"]
              and "tabCapture" in m.get("optional_permissions", []), str(m.get("optional_permissions")))
        with sync_playwright() as p:
            ctx, sw, fl = load(p, tmp, store_dir, "store")
            out = sw.evaluate("() => vgSaveVideo({ url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', quality: 'video' }, null, null).then(() => 'downloaded', e => 'refused: ' + e.message)")
            time.sleep(1)
            check("store: YouTube refused, the server is not asked", out.startswith("refused") and not fl, f"{out} fetch_links={fl}")
            check("store: tabCapture not granted at install", sw.evaluate("() => chrome.permissions.contains({ permissions: ['tabCapture'] })") is False)
            ck = sw.evaluate("() => getEphemeralCookiesB64('https://www.facebook.com/reel/1')")
            check("store: sign-in cookies off by default", ck is None, str(ck)[:40])
            sw.evaluate("() => chrome.cookies.set({ url: 'https://www.facebook.com', name: 'c_user', value: 'test', domain: '.facebook.com' })")
            sw.evaluate("() => chrome.storage.sync.set({ vg_send_cookies: true })")
            ck = sw.evaluate("() => getEphemeralCookiesB64('https://www.facebook.com/reel/1')")
            check("store: ticking the switch turns them on", isinstance(ck, str) and len(ck) > 10, str(ck)[:20])
            ext = sw.url.split("/")[2]
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext}/popup.html", wait_until="domcontentloaded")
            pop.wait_for_timeout(800)
            note = pop.evaluate("() => document.getElementById('vg-yt-note').textContent.trim()")
            check("store popup: YouTube note has no 'open on the website' button",
                  pop.locator("#vg-yt-open").count() == 0 and "does not download YouTube" in note, note)
            ctx.close()

            ctx, sw, fl = load(p, tmp, ROOT / "chrome-extension", "web")
            sw.evaluate("() => chrome.cookies.set({ url: 'https://www.facebook.com', name: 'c_user', value: 'test', domain: '.facebook.com' })")
            ck = sw.evaluate("() => getEphemeralCookiesB64('https://www.facebook.com/reel/1')")
            check("website build: cookies on by default (unchanged)", isinstance(ck, str) and len(ck) > 10, str(ck)[:20])
            check("website build: YouTube not refused", sw.evaluate("() => vgYouTubeRefused('https://www.youtube.com/watch?v=dQw4w9WgXcQ')") is False)
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
