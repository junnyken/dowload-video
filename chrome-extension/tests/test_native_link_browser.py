"""Extension <-> VidGrab app over native messaging (extension 5.22.0, app 0.20.0).

Builds the app's REAL link code (desktop/src-tauri/src/ext_bridge.rs,
ext_link.rs, native_host.rs, native_reg.rs) into one small binary in a temp
crate. Started plainly it is the app (bridge + link, commands on stdin);
started by Chromium with "chrome-extension://<id>/" it is the native
messaging host — the same branch as main.rs. Then, in real Chromium with the
extension loaded unpacked (pinned id from the manifest "key"):

  - the host manifest only names our id, and Chromium starts the host by itself
  - the app sees the browser (brand, extension version) with no request made
  - the app asks for a channel relay: the extension opens the tab itself
  - the relay page's progress reaches the app over the link (no server)
  - a download goes over the link
  - the app quits: the extension hears it; the app comes back: linked again

Run: python3 tests/test_native_link_browser.py   (needs cargo)
"""
import json, os, queue, re, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "desktop/src-tauri/src"
EXT_ID = "gkoepflfmpdmkhmccekmajlfdnfklmgi"
res = []

MAIN_RS = r'''
mod ext_bridge; mod ext_link; mod native_host; mod native_reg;
use std::io::{BufRead, Write};
fn main() {
    if let Some(origin) = native_host::launched_origin(std::env::args()) {
        std::process::exit(native_host::run(origin));
    }
    let token = native_reg::register("0.20.0").expect("register");
    ext_bridge::set_native_token(&token);
    ext_link::set_on_job(|j| { println!("JOB {}", serde_json::to_string(&j).unwrap()); std::io::stdout().flush().unwrap(); });
    let (l, p) = ext_bridge::bind().expect("bind");
    std::thread::spawn(move || ext_bridge::serve(l, p, "0.20.0", |_| {}, |s| { std::thread::spawn(move || ext_link::accept(s, "0.20.0")); }));
    println!("READY {p}"); std::io::stdout().flush().unwrap();
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let (cmd, arg) = line.split_once(' ').unwrap_or((line.as_str(), ""));
        let out = match cmd {
            "status" => serde_json::to_string(&ext_link::status()).unwrap(),
            "exe" => format!("{:?}", ext_link::browser_exe()),
            "relay" => serde_json::to_string(&ext_link::relay_start(arg)).unwrap(),
            "poll" => serde_json::to_string(&ext_link::relay_poll(arg.parse().unwrap())).unwrap(),
            _ => "?".into(),
        };
        println!("OUT {out}"); std::io::stdout().flush().unwrap();
    }
}
'''


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def build(tmp: Path) -> Path:
    crate = tmp / "link"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname="linkdemo"\nversion="0.1.0"\nedition="2021"\n'
        '[dependencies]\nserde={version="1",features=["derive"]}\nserde_json="1"\nregex="1"\ngetrandom="0.3"\n')
    for m in ("ext_bridge", "ext_link", "native_host", "native_reg"):
        shutil.copy(SRC / f"{m}.rs", crate / f"src/{m}.rs")
    (crate / "src/main.rs").write_text(MAIN_RS)
    target = Path(os.environ.get("CARGO_TARGET_DIR", tmp / "target"))
    subprocess.run(["cargo", "build", "-q", "--release"], cwd=crate, check=True, timeout=900,
                   env={**os.environ, "CARGO_TARGET_DIR": str(target)})
    return target / "release/linkdemo"


class App:
    def __init__(self, binary, env):
        self.p = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env)
        self.q, self.jobs = queue.Queue(), []
        threading.Thread(target=self._read, daemon=True).start()
        self.port = int(self._next("READY", 10))

    def _read(self):
        for line in self.p.stdout:
            kind, _, rest = line.rstrip("\n").partition(" ")
            (self.jobs.append(json.loads(rest)) if kind == "JOB" else self.q.put((kind, rest)))

    def _next(self, kind, timeout):
        k, v = self.q.get(timeout=timeout)
        assert k == kind, (k, v)
        return v

    def cmd(self, c, timeout=15):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()
        out = self._next("OUT", timeout)
        try:
            return json.loads(out)
        except ValueError:
            return out

    def stop(self):
        self.p.kill()
        self.p.wait()


def wait(fn, timeout=15, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def main():
    if not shutil.which("cargo"):
        print("SKIP: no cargo"); sys.exit(2)
    tmp = Path(tempfile.mkdtemp(prefix="vg_native_"))
    binary = build(tmp)
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    env = {**os.environ, "XDG_DATA_HOME": str(tmp / "data"), "FONTCONFIG_FILE": str(tmp / "fonts.conf")}
    os.environ.update(env)  # Chromium (and the host it starts) see the same folder

    app = App(binary, env)
    native = tmp / "data/xyz.tinhgon.vidgrab/native-host"
    manifest = json.loads((native / "xyz.tinhgon.vidgrab.json").read_text())
    check("host manifest names only our pinned id", manifest["allowed_origins"] == [f"chrome-extension://{EXT_ID}/"],
          json.dumps(manifest["allowed_origins"]))
    check("host is a per-version copy, not the app exe", manifest["path"].endswith("vidgrab-host-0.20.0.exe")
          and os.access(manifest["path"], os.X_OK), manifest["path"])
    profile = tmp / "profile"
    (profile / "NativeMessagingHosts").mkdir(parents=True)
    # Linux Chromium reads <user-data-dir>/NativeMessagingHosts (Windows: the HKCU keys native_reg.rs writes)
    shutil.copy(native / "xyz.tinhgon.vidgrab.json", profile / "NativeMessagingHosts/xyz.tinhgon.vidgrab.json")

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile), headless=False, executable_path=T.CHROME, env=env,
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            # Douyin itself stays out of it: the opened tab must not scroll a real page (the
            # test plays the relay page through vgRelayPut, the message content.js sends)
            ctx.route(re.compile(r"douyin\.com"), lambda r: r.abort())
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            check("the unpacked extension has the pinned id", sw.url.split("/")[2] == EXT_ID, sw.url)

            st = wait(lambda: (lambda s: s if s and s[0].get("extVersion") else None)(app.cmd("status")))
            info = (st or [{}])[0]
            check("the app sees the browser with no request made (Chromium started the host)",
                  info.get("extVersion") == "5.22.4" and bool(info.get("browser")), json.dumps(st))
            linked = wait(lambda: sw.evaluate("() => !!(vgNativePort && vgNativeApp.up && vgNativeApp.version === '0.20.0')"))
            check("the extension knows the app is on the link", linked)
            hosts = subprocess.run(["pgrep", "-fa", "vidgrab-host-0.20.0.exe"], capture_output=True, text=True).stdout
            check("the host runs from the copy with our origin", f"chrome-extension://{EXT_ID}/" in hosts, hosts.strip()[:200])

            # relay: the app asks, the extension opens the channel
            started = app.cmd("relay https://www.douyin.com/user/MS4wLjABAAAAabcdefghijklmn?from=x")
            rid = (started or {}).get("Ok", {}) or {}
            check("relay started by the extension", isinstance(rid, dict) and rid.get("id"), json.dumps(started))
            rid = rid.get("id")
            urls = lambda: sw.evaluate("async () => (await chrome.tabs.query({})).map(t => t.pendingUrl || t.url)")
            tab = wait(lambda: next((u for u in urls() if "#vg-relay=" in u), None), 20)
            check("the extension opened the channel tab itself (with a relay token)",
                  bool(tab) and tab.startswith("https://www.douyin.com/user/MS4wLjABAAAAabcdefghijklmn#vg-relay="), str(tab or urls()))
            doc = app.cmd(f"poll {rid}")
            check("relay document starts waiting / login (not signed in to Douyin here)",
                  doc and doc.get("status") in ("waiting", "login") and doc.get("channelUrl") == "https://www.douyin.com/user/MS4wLjABAAAAabcdefghijklmn",
                  json.dumps(doc)[:200])
            token = sw.evaluate("""async () => Object.keys((await chrome.storage.session.get('vg_local_relays')).vg_local_relays || {})[0]""")
            # the relay page's progress, through the same message content.js sends
            r = sw.evaluate("""(t) => vgRelayPut(t, {items: [{url: 'https://www.douyin.com/video/7412345678901234567', title: 'Một video'},
                                  {url: 'https://evil.example/video/1'}], channelTitle: 'Kênh thử', done: true, skipped: 1, loginWall: false})""", token)
            check("the page hears 'sent'", r.get("ok") is True, json.dumps(r))
            doc = wait(lambda: (lambda d: d if d and d.get("status") == "done" else None)(app.cmd(f"poll {rid}")))
            check("the app got the list over the link, cleaned like the server",
                  doc and [i["id"] for i in doc["items"]] == ["7412345678901234567"] and doc["channelTitle"] == "Kênh thử"
                  and doc["skipped"] == 1, json.dumps(doc)[:300])

            # download over the link
            ok = sw.evaluate("() => vgSendToApp({url: 'https://cdn.example/a.mpd', title: 'T', pageUrl: 'https://site.example/w'})")
            j = wait(lambda: app.jobs[-1] if app.jobs else None, 5)
            check("a download reached the app over the link", ok.get("ok") and j and j["url"] == "https://cdn.example/a.mpd"
                  and j["referer"] == "https://site.example/w", json.dumps(j))

            # the app quits and comes back
            app.stop()
            down = wait(lambda: sw.evaluate("() => vgNativeApp.up === false"), 10)
            check("app quit: the extension hears it (host stays)", down and vgport_open(sw))
            app = App(binary, env)
            back = wait(lambda: app.cmd("status"), 20)
            check("app back: the same host reconnects by itself", bool(back) and back[0].get("extVersion") == "5.22.4", json.dumps(back))
            ctx.close()
        gone = wait(lambda: not subprocess.run(["pgrep", "-f", "vidgrab-host-0.20.0.exe"], capture_output=True).stdout, 10)
        check("browser closed: the host ends", gone)
    finally:
        try:
            app.stop()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


def vgport_open(sw):
    return sw.evaluate("() => !!vgNativePort")


if __name__ == "__main__":
    main()
