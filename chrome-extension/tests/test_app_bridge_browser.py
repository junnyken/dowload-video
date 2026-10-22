"""Extension -> VidGrab app on this computer (extension 5.14.0, app 0.19.0).

Builds the app's REAL bridge code (desktop/src-tauri/src/ext_bridge.rs) into
a tiny binary in a temp crate, runs it next to Chromium with the extension,
and checks: no app -> DASH goes to VidGrab; app running -> "Tải bằng app"
sends the stream (url, page title, page as referer) and the bridge receives
it; a web page posting to the bridge is refused and nothing is queued.

Run: python3 tests/test_app_bridge_browser.py   (needs cargo)
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BRIDGE_SRC = Path(__file__).resolve().parents[2] / "desktop/src-tauri/src/ext_bridge.rs"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def build_bridge(tmp: Path) -> Path:
    crate = tmp / "bridge"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname="bridgedemo"\nversion="0.1.0"\nedition="2021"\n'
                                      '[dependencies]\nserde={version="1",features=["derive"]}\nserde_json="1"\n')
    shutil.copy(BRIDGE_SRC, crate / "src/ext_bridge.rs")
    (crate / "src/main.rs").write_text(
        "mod ext_bridge;\nfn main(){let (l,p)=ext_bridge::bind().expect(\"bind\");eprintln!(\"bridge on {p}\");"
        "ext_bridge::serve(l,p,\"0.19.0\",|j|{println!(\"{}\",serde_json::to_string(&j).unwrap());"
        "use std::io::Write;std::io::stdout().flush().unwrap();});}\n")
    target = Path(os.environ.get("CARGO_TARGET_DIR", tmp / "target"))
    subprocess.run(["cargo", "build", "-q", "--release"], cwd=crate, check=True, timeout=600,
                   env={**os.environ, "CARGO_TARGET_DIR": str(target)})
    return target / "release/bridgedemo"


def main():
    if not shutil.which("cargo"):
        print("SKIP: no cargo"); sys.exit(2)
    tmp = Path(tempfile.mkdtemp(prefix="vg_bridge_"))
    binary = build_bridge(tmp)
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    root = tmp / "www"
    root.mkdir()
    T.fixtures(root)
    srv, port = T.serve(root)
    base = f"http://127.0.0.1:{port}"
    bridge = None
    jobs = []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME,
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--no-sandbox"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function'"):
                    break
                time.sleep(0.1)
            page = ctx.new_page()
            page.goto(f"{base}/page.html", wait_until="load")
            page.wait_for_timeout(3000)
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true})")
            pop.reload(wait_until="domcontentloaded")
            pop.wait_for_timeout(500)
            tab_id = pop.evaluate(f"""() => new Promise(r => chrome.tabs.query({{}}, ts =>
                r((ts.find(t => (t.url || '').startsWith('{base}/page.html')) || {{}}).id)))""")

            def render():
                pop.evaluate("(a) => initSniff({id:a[0], url:a[1]})", [tab_id, f"{base}/page.html"])
                pop.wait_for_timeout(700)
                rows = pop.locator("#vg-sniff .vg-sniff-row")
                return {rows.nth(k).locator(".vg-sniff-meta").get_attribute("title").replace(base, ""): rows.nth(k)
                        for k in range(rows.count())}

            # ── no app ──
            st = pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({type:'VG_APP_STATUS', force:true}, r))")
            check("no app found while it is not running", st == {"found": False, "version": None}, str(st))
            rows = render()
            dash = rows["/dash/stream.mpd"]
            check("without the app, DASH offers 'Tải qua VidGrab' (with the app hint)",
                  dash.locator(".vg-sniff-dl").inner_text() == "Tải qua VidGrab" and dash.locator(".vg-sniff-app").count() == 0
                  and "app VidGrab" in (dash.locator(".vg-sniff-dl").get_attribute("title") or ""))

            # ── app running (its real bridge code) ──
            bridge = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            threading.Thread(target=lambda: [jobs.append(json.loads(l)) for l in bridge.stdout], daemon=True).start()
            time.sleep(0.5)
            st = pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({type:'VG_APP_STATUS', force:true}, r))")
            check("app found on 127.0.0.1 with its version", st == {"found": True, "version": "0.19.0"}, str(st))
            rows = render()
            dash = rows["/dash/stream.mpd"]
            check("DASH row: 'Tải bằng app' + 'Qua VidGrab'", dash.locator(".vg-sniff-app").inner_text() == "Tải bằng app"
                  and dash.locator(".vg-sniff-server").count() == 1)
            hls = rows["/hls/master.m3u8"]
            check("HLS row keeps 'Tải về máy' and adds 'App'", hls.locator(".vg-sniff-dl").inner_text() == "Tải về máy"
                  and hls.locator(".vg-sniff-app").inner_text() == "App")
            drm = rows["/drm/stream.mpd"]
            check("DRM row: no app button", drm.locator(".vg-sniff-app").count() == 0)

            dash.locator(".vg-sniff-app").click()
            for _ in range(20):
                if jobs:
                    break
                time.sleep(0.2)
            j = jobs[0] if jobs else {}
            check("the app received the DASH stream", j.get("url") == f"{base}/dash/stream.mpd", json.dumps(j))
            check("with the page title and the page as referer", j.get("title") == "Fixture Video Page"
                  and j.get("referer") == f"{base}/page.html", json.dumps(j))
            pop.wait_for_timeout(300)
            check("popup confirms", "app VidGrab" in pop.locator("#vg-sniff-msg").inner_text(), pop.locator("#vg-sniff-msg").inner_text())

            # ── a web page cannot use the bridge ──
            n = len(jobs)
            attempt = page.evaluate("""async () => {
                const out = [];
                for (const port of [17325, 17326, 17327]) {
                  try {
                    const r = await fetch(`http://127.0.0.1:${port}/vidgrab/download`, {method:'POST',
                      headers:{'Content-Type':'application/json'}, body: JSON.stringify({url:'https://evil.example/x.mp4'})});
                    out.push(port + ':' + r.status);
                  } catch (e) { out.push(port + ':blocked'); }
                  try {
                    await fetch(`http://127.0.0.1:${port}/vidgrab/download`, {method:'POST', mode:'no-cors',
                      headers:{'Content-Type':'text/plain'}, body: JSON.stringify({url:'https://evil.example/y.mp4'})});
                    out.push(port + ':no-cors-sent');
                  } catch (e) { out.push(port + ':no-cors-blocked'); }
                }
                return out;
            }""")
            time.sleep(0.5)
            check("a web page's requests queue nothing", len(jobs) == n, f"{attempt} jobs={jobs[n:]}")
            ctx.close()
    finally:
        if bridge:
            bridge.kill()
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
