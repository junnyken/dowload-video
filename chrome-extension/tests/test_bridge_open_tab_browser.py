"""The VidGrab site tab open BEFORE the extension is installed/updated still signs it in (5.22.1).

Chrome runs content scripts only in pages opened after an install/update, so the site tab the person
already had open never handed its session over and the extension said "Chưa đăng nhập" (owner 27/10).
background.js now puts web-bridge.js into those tabs from onInstalled (vgBridgeOpenSiteTabs).

Real Chromium. A tab "opened before the install" is made with a copy of the extension whose
web-bridge content script excludes the site page, so Chrome does not put the bridge there by itself
(chrome.runtime.reload() is no use: under Playwright it disables the unpacked extension).

  - control: the site tab has no bridge -> the extension gets no session
  - vgBridgeOpenSiteTabs() (what onInstalled runs) -> the page's session reaches the extension

Negative control: --old runs the same with background.js from git HEAD (no vgBridgeOpenSiteTabs).
Run: python3 tests/test_bridge_open_tab_browser.py [--site=https://dowloadvideo.io.vn/] [--old]
"""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 5.22.2: also the site's own domain (owner 27/10: signed in on dowloadvideo.io.vn, nothing reached the extension)
SITE = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--site=")), "https://dvid.vibe1.tinhgon.xyz/")
# what frontend/src/context/AuthContext.jsx does: send on load and again when the extension says it is there
PAGE = """<!doctype html><title>site</title><script>
const send = () => window.postMessage({__vg_source:'webapp', type:'VG_AUTH_TOKEN_FROM_WEB', token:'tok-from-site', email:'a@b.c'}, location.origin);
send();
addEventListener('message', (e) => { if (e.data && e.data.__vg_source === 'extension' && e.data.type === 'VG_EXTENSION_PRESENT') send(); });
</script>"""
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def token(sw):
    return sw.evaluate("async () => (await chrome.storage.local.get('vg_auth_token')).vg_auth_token || null")


def wait_token(sw, timeout=10):
    end, got = time.time() + timeout, None
    while time.time() < end and got != "tok-from-site":
        time.sleep(0.5)
        got = token(sw)
    return got


def extension_copy(tmp: Path, old: bool) -> Path:
    ext = tmp / "ext"
    shutil.copytree(T.EXT, ext, ignore=shutil.ignore_patterns("tests", "store-assets", "__pycache__"))
    m = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    for cs in m["content_scripts"]:
        if "web-bridge.js" in cs["js"]:
            cs["exclude_matches"] = [SITE + "*"]  # the tab gets no bridge from Chrome, as if opened before the install
    (ext / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    if old:
        head = subprocess.run(["git", "show", "HEAD:chrome-extension/background.js"], cwd=T.EXT, capture_output=True, text=True, check=True).stdout
        (ext / "background.js").write_text(head, encoding="utf-8")
    return ext


def main():
    old = "--old" in sys.argv[1:]
    src = (Path(T.EXT) / "background.js").read_text(encoding="utf-8")
    hook = re.search(r"chrome\.runtime\.onInstalled\.addListener\(\(details\) => \{(.*?)\n\}\);", src, re.S)
    if not old:
        check("onInstalled calls vgBridgeOpenSiteTabs", bool(hook) and "vgBridgeOpenSiteTabs()" in hook.group(1))
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        tmp = Path(tmp)
        ext = extension_copy(tmp, old)
        # Chromium crashes drawing text with the workspace fontconfig: a private one
        (tmp / "fonts.conf").write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                                        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc</cachedir></fontconfig>')
        ctx = p.chromium.launch_persistent_context(
            str(tmp / "profile"), headless=False, executable_path=T.CHROME, env={**os.environ, "FONTCONFIG_FILE": f"{tmp}/fonts.conf"},
            args=["--headless=new", f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-sandbox"])
        ctx.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=PAGE) if r.request.url.startswith(SITE)
                  else r.abort() if r.request.url.startswith("http") else r.continue_())
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        time.sleep(3)  # onInstalled has run (no site tab yet), so only the call below can put the bridge in
        page = ctx.new_page()
        page.goto(SITE)
        got = wait_token(sw, 4)
        check("control: the site tab without the bridge hands over nothing", got is None, str(got))

        sw.evaluate("() => typeof vgBridgeOpenSiteTabs === 'function' ? vgBridgeOpenSiteTabs() : null")  # what onInstalled runs
        got = wait_token(sw)
        check("the site tab open before the install signs the extension in", got == "tok-from-site", str(got))
        ctx.close()
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
