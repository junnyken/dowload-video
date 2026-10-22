"""5.18.1: on a feed (TikTok For You…) the popup takes the video ON SCREEN.

Owner 22/10 (screenshot): on tiktok.com/foryou the popup listed 12 bare "MP4"
files (the feed preloads the next videos) and said "Trang này không phải video".
A TikTok-like feed is served at https://www.tiktok.com/foryou (Playwright
route, so the real content script runs there): three posts, each with a
player wrapper "xgwrapper-0-<id>", an author link and a caption; the page is
scrolled so the SECOND post is on screen and playing. Checks:
  - content.js VG_CURRENT_VIDEO -> the second post's link + caption
  - the popup fills the link box with it and says "Video đang xem: …"
  - no raw found-files list on TikTok (5.18.2)
Run: python3 tests/test_current_video.py
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

IDS = ["7400000000000000001", "7400000000000000002", "7400000000000000003"]
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


FEED = """<!doctype html><html><head><title>TikTok - Make Your Day</title>
<style>body{margin:0} article{height:100vh;display:flex;align-items:center;justify-content:center}
video{width:360px;height:640px;background:#000}</style></head><body>
%s
<script>
  window.scrollTo(0, innerHeight);  // the second post fills the screen
  const vs = document.querySelectorAll('video');
  vs[1].play().catch(() => {});
</script></body></html>"""
POST = """<article data-e2e="recommend-list-item-container">
  <div id="xgwrapper-0-{id}" class="xgplayer-container"><video src="https://v16-webapp-prime.tiktok.com/video/tos/{id}/?mime_type=video_mp4" muted loop playsinline poster=""></video></div>
  <div><a href="/@creator{n}">creator{n}</a><div data-e2e="video-desc">Caption of post {n} #test</div></div>
</article>"""


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_cur_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    html = FEED % "\n".join(POST.format(id=i, n=k + 1) for k, i in enumerate(IDS))
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(tmp / "profile"), headless=False, executable_path=T.CHROME, locale="vi-VN",
                viewport={"width": 1280, "height": 900},
                args=["--headless=new", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--no-sandbox",
                      "--autoplay-policy=no-user-gesture-required"])
            ctx.route("https://www.tiktok.com/foryou", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
            # each preloaded "video" is a 400 KB MP4-looking file, so the sniffer lists it (>300 KB)
            blob = b"\x00\x00\x00\x18ftypmp42" + os.urandom(400_000)
            ctx.route("https://v16-webapp-prime.tiktok.com/**", lambda r: r.fulfill(status=200, content_type="video/mp4", body=blob))
            ctx.route("**/api/v1/**", lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"test"}'))
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            page = ctx.new_page()
            page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            pop = ctx.new_page()
            pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop.evaluate("() => chrome.storage.local.set({vg_onboarding_done: true})")
            tab_id = pop.evaluate("() => new Promise(r => chrome.tabs.query({}, ts => r((ts.find(t => (t.url || '').includes('tiktok.com/foryou')) || {}).id)))")
            cur = pop.evaluate("(id) => new Promise(r => chrome.tabs.sendMessage(id, { type: 'VG_CURRENT_VIDEO' }, x => r(x && x.video)))", tab_id)
            check("content: the video on screen is the SECOND post",
                  cur and cur.get("url") == f"https://www.tiktok.com/@creator2/video/{IDS[1]}", json.dumps(cur)[:200])
            check("content: its caption", cur and cur.get("title") == "Caption of post 2 #test", cur and cur.get("title"))

            # the popup as opened on that tab (its own getActiveTab points there)
            pop2 = ctx.new_page()
            pop2.add_init_script(f"""
              const _q = chrome.tabs.query.bind(chrome.tabs);
              chrome.tabs.query = (info, cb) => {{
                const p = new Promise((res) => _q({{}}, (ts) => res(ts.filter(x => x.id === {tab_id}))));
                if (cb) {{ p.then(cb); return undefined; }}
                return p;
              }};
            """)
            pop2.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            pop2.wait_for_timeout(2500)
            val = pop2.locator("#vg-url-input").input_value()
            hint = pop2.locator("#vg-url-hint").inner_text()
            check("popup: link box = the video on screen", val == f"https://www.tiktok.com/@creator2/video/{IDS[1]}", val)
            check("popup: 'Video đang xem: …' with the caption, not 'không phải video'",
                  hint.startswith("Video đang xem:") and "Caption of post 2" in hint and "không phải video" not in hint, hint[:120])
            check("popup: the badge says '▶ Video đang xem' (was stuck on 'Đang nhận diện...')",
                  pop2.locator("#page-badge").inner_text().strip() == "▶ Video đang xem", pop2.locator("#page-badge").inner_text())
            # 5.18.2: no raw file list on TikTok (preloaded files, blob: player, CDN refuses them)
            check("popup: no 'Video trên trang này' file list on TikTok", not pop2.locator("#vg-sniff").is_visible())
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
