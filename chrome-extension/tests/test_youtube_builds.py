"""YouTube in the two builds (5.14.1), each loaded in a real Chromium.

website build (chrome-extension/ as is, the zip the VidGrab site serves):
    YouTube links are downloaded like any other.
Chrome Web Store build (scripts/package-extension.py --store, unzipped):
    no YouTube host permission; a YouTube link is refused with a "not
    supported" note and NO way to the website (5.20.1, Web Store policy),
    other links still work.
Run: python3 tests/test_youtube_builds.py
"""
import os, shutil, subprocess, sys, tempfile, time, zipfile, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
res = []
YT = "https://www.youtube.com/shorts/CxIhGIlrV1Q"


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def run(p, ext_dir: Path, profile: Path):
    ctx = p.chromium.launch_persistent_context(
        str(profile), headless=False, executable_path=T.CHROME,
        args=["--headless=new", f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}", "--lang=vi", "--no-sandbox"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
    ext_id = sw.url.split("/")[2]
    pop = ctx.new_page()
    pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
    pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true})")
    pop.reload(wait_until="domcontentloaded")
    pop.wait_for_timeout(500)
    pop.fill("#vg-url-input", YT)
    yt = pop.evaluate("() => getTargetUrl().then(v => 'accepted:' + v).catch(e => e.message)")
    note = pop.locator("#vg-yt-note").is_visible()
    no_web_btn = pop.locator("#vg-yt-open").count() == 0
    pop.fill("#vg-url-input", "https://www.tiktok.com/@a/video/1")
    other = pop.evaluate("() => getTargetUrl().then(v => 'accepted:' + v).catch(e => e.message)")
    hosts = pop.evaluate("() => chrome.runtime.getManifest().host_permissions")
    ctx.close()
    return yt, note, other, hosts, no_web_btn


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_ytb_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts/package-extension.py"), "--store"], check=True,
                       capture_output=True, text=True)
        version = json.loads((ROOT / "chrome-extension/manifest.json").read_text())["version"]
        store_zip = ROOT / f"chrome-extension/store-assets/VidGrab-store-v{version}.zip"
        store_dir = tmp / "store"
        zipfile.ZipFile(store_zip).extractall(store_dir)
        with sync_playwright() as p:
            yt, note, other, hosts, no_web_btn = run(p, Path(T.EXT), tmp / "p1")
            check("website build: YouTube link accepted", yt == f"accepted:{YT}", yt)
            check("website build: no 'use the web' note", note is False)
            check("website build: has the YouTube host permission", "*://*.youtube.com/*" in hosts)
            yt, note, other, hosts, no_web_btn = run(p, store_dir, tmp / "p2")
            check("store build: YouTube link refused", "không hỗ trợ tải video YouTube" in yt, yt)
            check("store build: the note shows, with no button to the website", note is True and no_web_btn, f"note={note} no_web_btn={no_web_btn}")
            check("store build: other links still accepted", other == "accepted:https://www.tiktok.com/@a/video/1", other)
            check("store build: no YouTube host permission", not any("yout" in h for h in hosts), str([h for h in hosts if 'yout' in h]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
