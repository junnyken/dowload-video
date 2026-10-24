"""HLS downloaded in the browser (5.13.0), checked with ffprobe.

Real HLS made by ffmpeg (docker image `vg-ffmpeg`: python:3.12-slim + apt
ffmpeg): a TS master with 360p/180p (H.264 + AAC, 6 s), an fMP4 stream, a
master whose audio is a separate playlist. The extension downloads them from
a local page exactly as a user would (popup "Tải về máy"), and ffprobe reads
the saved MP4: codecs, size, duration. Also: refusal of separate audio,
cancel mid-way, and that nothing goes to the VidGrab server.

Run: python3 tests/test_hls_browser.py   (needs docker + the vg-ffmpeg image)
"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402  (server, Chromium path, check())
from playwright.sync_api import sync_playwright  # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def ff(root: Path, args: str):
    cmd = f"docker run --rm -u {os.getuid()}:{os.getgid()} -v {root}:/w -w /w vg-ffmpeg sh -c {json.dumps(args)}"
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:])
    return p.stdout


def make_fixtures(root: Path):
    src = ("-f lavfi -i testsrc2=size={w}x{h}:rate=25 -f lavfi -i sine=frequency=440:sample_rate=44100 "
           "-t 6 -c:v libx264 -pix_fmt yuv420p -g 50 -keyint_min 50 -sc_threshold 0 -c:a aac -b:a 64k")
    ff(root, "mkdir -p ts360 ts180 fmp4 sep/video sep/audio && "
       f"ffmpeg -loglevel error -y {src.format(w=640, h=360)} -f hls -hls_time 2 -hls_playlist_type vod ts360/index.m3u8 && "
       f"ffmpeg -loglevel error -y {src.format(w=320, h=180)} -f hls -hls_time 2 -hls_playlist_type vod ts180/index.m3u8 && "
       f"ffmpeg -loglevel error -y {src.format(w=640, h=360)} -f hls -hls_time 2 -hls_playlist_type vod "
       "-hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 fmp4/index.m3u8 && "
       "ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=25 -t 6 -c:v libx264 -pix_fmt yuv420p -an "
       "-f hls -hls_time 2 -hls_playlist_type vod sep/video/index.m3u8 && "
       "ffmpeg -loglevel error -y -f lavfi -i sine=frequency=440:sample_rate=44100 -t 6 -c:a aac "
       "-f hls -hls_time 2 -hls_playlist_type vod sep/audio/index.m3u8 && "
       "mkdir -p long && ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=320x180:rate=25 -t 30 -c:v libx264 "
       "-pix_fmt yuv420p -g 25 -keyint_min 25 -sc_threshold 0 -an -f hls -hls_time 1 -hls_playlist_type vod long/index.m3u8")
    (root / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360\nts360/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=320x180\nts180/index.m3u8\n")
    (root / "sep/master.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="vi",DEFAULT=YES,URI="audio/index.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360,AUDIO="a"\nvideo/index.m3u8\n')
    (root / "page.html").write_text("""<!doctype html><meta charset="utf-8"><title>HLS Fixture</title>
<script>
 (async () => {
  for (const u of ['master.m3u8', 'fmp4/index.m3u8', 'sep/master.m3u8', 'long/index.m3u8'])
    await fetch(u).then(r => r.text()).catch(() => null);
 })();
</script>""")


def serve_slow(root: Path):
    """T.serve, but segments under /long/ answer after 0.3 s (cancel case)."""
    import http.server, socketserver, threading
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)
        def guess_type(self, path):
            return T.TYPES.get(Path(path).suffix, "application/octet-stream")
        def log_message(self, *a):
            pass
        def send_head(self):
            if self.path.startswith("/long/") and self.path.endswith(".ts"):
                time.sleep(0.3)
            return super().send_head()
    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def probe(path: Path):
    out = ff(path.parent, f"ffprobe -v error -show_entries stream=codec_name,codec_type,width,height:format=duration "
                          f"-of json {json.dumps(path.name)}")
    return json.loads(out)


def main():
    if subprocess.run("docker image inspect vg-ffmpeg", shell=True, capture_output=True).returncode != 0:
        print("SKIP: docker image vg-ffmpeg missing"); sys.exit(2)
    tmp = Path(tempfile.mkdtemp(prefix="vg_hls_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    root = tmp / "www"
    root.mkdir()
    make_fixtures(root)
    srv, port = serve_slow(root)
    base = f"http://127.0.0.1:{port}"
    dl_dir = tmp / "downloads"
    dl_dir.mkdir()
    server_calls = []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, accept_downloads=True,
                downloads_path=str(dl_dir),
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--lang=vi", "--no-sandbox"])
            ctx.route("**/dvid-api.vibe1.tinhgon.xyz/**", lambda r: (server_calls.append(r.request.url), r.abort()))
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            for _ in range(50):
                if sw.evaluate("() => typeof self.vgSniffList === 'function'"):
                    break
                time.sleep(0.1)

            page = ctx.new_page()
            page.goto(f"{base}/page.html", wait_until="load")
            page.wait_for_timeout(2500)
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true})")
            pop.reload(wait_until="domcontentloaded")
            pop.wait_for_timeout(600)
            tab_id = pop.evaluate(f"""() => new Promise(r => chrome.tabs.query({{}}, ts =>
                r((ts.find(t => (t.url || '').startsWith('{base}/page.html')) || {{}}).id)))""")
            items = []
            for _ in range(20):
                items = pop.evaluate("(id) => new Promise(r => chrome.runtime.sendMessage({type:'VG_SNIFF_LIST', tabId:id}, x => r(x.items)))", tab_id)
                if len(items) >= 4 and not any(i.get("pending") for i in items):
                    break
                pop.wait_for_timeout(300)
            by = {i["url"].replace(base, ""): i for i in items}
            check("4 HLS streams listed", sorted(by) == ["/fmp4/index.m3u8", "/long/index.m3u8", "/master.m3u8", "/sep/master.m3u8"], str(sorted(by)))

            pop.evaluate("(a) => renderSniff({id:a[0], url:a[1]})", [tab_id, f"{base}/page.html"])
            pop.wait_for_timeout(400)
            rows = pop.locator("#vg-sniff .vg-sniff-row")

            def row_for(path):
                for k in range(rows.count()):
                    if rows.nth(k).locator(".vg-sniff-meta").get_attribute("title") == base + path:
                        return rows.nth(k)
                return None

            def jobs():
                return pop.evaluate("() => new Promise(r => chrome.runtime.sendMessage({type:'VG_HLS_JOBS'}, x => r(x.jobs)))")

            def wait_job(pred, secs=60):
                for _ in range(int(secs / 0.5)):
                    js = jobs()
                    hit = [j for j in js if pred(j)]
                    if hit and hit[0]["state"] in ("done", "failed", "cancelled"):
                        return hit[0]
                    pop.wait_for_timeout(500)
                return (hit or [None])[0]

            def saved(job):
                d = pop.evaluate("(id) => new Promise(r => chrome.downloads.search({id}, x => r(x[0] || null)))", job.get("downloadId"))
                return Path(d["filename"]) if d else None

            # ── TS master, 360p chosen ──
            r = row_for("/master.m3u8")
            check("TS master row: quality picker 360p/180p + 'Tải về máy' + 'Qua VidGrab'",
                  r and r.locator(".vg-sniff-quality option").all_inner_texts() == ["360p", "180p"]
                  and r.locator(".vg-sniff-dl").inner_text() == "Tải về máy" and r.locator(".vg-sniff-server").count() == 1)
            r.locator(".vg-sniff-quality").select_option("360")
            r.locator(".vg-sniff-dl").click()
            j = wait_job(lambda j: j["url"] == base + "/master.m3u8")
            check("TS download done in the browser", j and j["state"] == "done", json.dumps(j)[:200])
            f = saved(j) if j else None
            if f and f.exists():
                pr = probe(f)
                v = [s for s in pr["streams"] if s["codec_type"] == "video"]
                a = [s for s in pr["streams"] if s["codec_type"] == "audio"]
                dur = float(pr["format"]["duration"])
                check("TS → MP4: H.264 640x360 + AAC", v and v[0]["codec_name"] == "h264" and v[0]["width"] == 640
                      and v[0]["height"] == 360 and a and a[0]["codec_name"] == "aac", json.dumps(pr["streams"]))
                check("TS → MP4: whole 6 s", 5.5 <= dur <= 6.6, str(dur))
                check("file is a real MP4 (ftyp box)", f.read_bytes()[4:8] == b"ftyp")
            else:
                check("TS output saved", False, str(f))
            check("job remembers 360p", j and j.get("height") == 360, str(j and j.get("height")))

            # ── fMP4 stream ──
            row_for("/fmp4/index.m3u8").locator(".vg-sniff-dl").click()
            j2 = wait_job(lambda j: j["url"] == base + "/fmp4/index.m3u8")
            check("fMP4 download done", j2 and j2["state"] == "done", json.dumps(j2)[:200])
            f2 = saved(j2) if j2 else None
            if f2 and f2.exists():
                pr = probe(f2)
                dur = float(pr["format"]["duration"])
                check("fMP4 → MP4: H.264 + AAC, 6 s", {s["codec_name"] for s in pr["streams"]} >= {"h264", "aac"}
                      and 5.5 <= dur <= 6.6, f"{json.dumps(pr['streams'])} {dur}")

            # ── separate audio: refused, pointed to VidGrab ──
            row_for("/sep/master.m3u8").locator(".vg-sniff-dl").click()
            j3 = wait_job(lambda j: j["url"] == base + "/sep/master.m3u8")
            check("separate-audio stream refused with a clear message", j3 and j3["state"] == "failed"
                  and "Qua VidGrab" in j3.get("message", ""), json.dumps(j3)[:200])

            # ── cancel mid-way: 30 one-second segments, each answered after 0.3 s ──
            jobs_before = len(jobs())
            row_for("/long/index.m3u8").locator(".vg-sniff-dl").click()
            pop.wait_for_timeout(1200)
            newest = jobs()[0]
            check("long job is running with progress", newest["state"] == "running" and 0 < newest["done"] < newest["total"] == 30,
                  f'{newest["state"]} {newest["done"]}/{newest["total"]}')
            pop.evaluate("(id) => chrome.runtime.sendMessage({type:'VG_HLS_CANCEL', id})", newest["id"])
            j4 = wait_job(lambda j: j["id"] == newest["id"], 20)
            check("cancel stops the job", j4 and j4["state"] == "cancelled", json.dumps(j4)[:160])
            check("a new job was added for the cancel case", len(jobs()) == jobs_before + 1)

            # popup shows the jobs
            pop.evaluate("() => renderHlsJobs()")
            pop.wait_for_timeout(300)
            states = pop.locator(".vg-hls-job").evaluate_all("els => els.map(e => e.dataset.state)")
            check("popup lists the jobs (done, done, failed, cancelled)", sorted(states) == sorted(["cancelled", "failed", "done", "done"]), str(states))
            dl_calls = [u for u in server_calls if any(k in u for k in ("fetch-link", "proxy-download", "bulk-download", "download-local"))]
            check("no download went through the VidGrab server", not dl_calls, str(dl_calls[:3]))
            leftovers = pop.evaluate("""async () => { const d = await navigator.storage.getDirectory(); const n = [];
                for await (const [k] of d.entries()) n.push(k); return n; }""")
            check("no temporary files left on the extension's disk", leftovers == [], str(leftovers))
            ctx.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
