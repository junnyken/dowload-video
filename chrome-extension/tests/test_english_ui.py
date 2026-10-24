"""5.20.0: the extension in English for every browser that is not Vietnamese.

Chromium is started with --lang=en (Chrome's UI language decides chrome.i18n).
Checks:
  - popup main view: key labels in English, no Vietnamese words on screen
    (the language name "Tiếng Việt" is allowed)
  - Kênh/Channel tab, settings panel, tools row (preview / phone / record)
  - the in-page channel panel on a Facebook Reels page (served by a route)
  - and with --lang=vi the same popup is Vietnamese (the two do not mix)
Run: python3 tests/test_english_ui.py
"""
import os, re, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sniffer_browser as T  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

VI = re.compile(r"\S*[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]\S*", re.I)
FB = "https://www.facebook.com/haidee.burrows/reels/"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


def vi_words(text):
    return sorted(set(VI.findall(text.replace("Tiếng Việt", ""))))[:10]


def run(p, tmp, lang):
    ctx = p.chromium.launch_persistent_context(
        str(tmp / f"profile-{lang}"), headless=False, executable_path=T.CHROME,
        locale="en-US" if lang == "en" else "vi-VN", viewport={"width": 1280, "height": 900},
        args=["--headless=new", f"--lang={lang}", f"--disable-extensions-except={T.EXT}", f"--load-extension={T.EXT}", "--no-sandbox"])
    ctx.route(FB, lambda r: r.fulfill(status=200, content_type="text/html",
                                      body='<!doctype html><title>Reels</title><body><a href="https://www.facebook.com/reel/910445711561956">r</a></body>'))
    ctx.route("**/api/v1/**", lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"t"}'))
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
    ext_id = sw.url.split("/")[2]
    pop = ctx.new_page()
    pop.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
    pop.evaluate("() => chrome.storage.local.set({ vg_onboarding_done: true })")
    pop.reload(wait_until="domcontentloaded")
    pop.wait_for_timeout(1500)
    return ctx, sw, ext_id, pop


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vg_en_"))
    (tmp / "fonts.conf").write_text(
        f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>/usr/share/fonts</dir><cachedir>{tmp}/fc-cache</cachedir></fontconfig>')
    os.environ["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")
    try:
        with sync_playwright() as p:
            ctx, sw, ext_id, pop = run(p, tmp, "en")
            check("Chrome UI language is English", sw.evaluate("() => chrome.i18n.getUILanguage()").lower().startswith("en"),
                  sw.evaluate("() => chrome.i18n.getUILanguage()"))
            check("manifest name/description resolved", sw.evaluate("() => chrome.runtime.getManifest().name") == "VidGrab - Video Downloader")
            # 5.20.0: the font is bundled — the popup asks Google for nothing
            google = []
            pop.on("request", lambda r: google.append(r.url) if "fonts.g" in r.url else None)
            pop.reload(wait_until="networkidle")
            pop.wait_for_timeout(800)
            font_ok = pop.evaluate("() => document.fonts.ready.then(() => document.fonts.check('600 12px Inter'))")
            check("bundled Inter font loaded, no request to Google Fonts", font_ok and not google, f"inter={font_ok} google={google[:2]}")
            main_text = pop.evaluate("() => document.body.innerText")
            check("popup main view: no Vietnamese words", not vi_words(main_text), str(vi_words(main_text)))
            btn = pop.locator("#btnText").inner_text().strip()
            check("download button in English", re.search(r"download", btn, re.I) is not None, btn)
            tools = [pop.locator(s).inner_text().strip() for s in ("#vg-preview-btn", "#vg-qr-btn", "#vg-rec-btn")]
            check("preview / phone / record buttons in English", all(not vi_words(x) for x in tools) and all(tools), str(tools))
            tabs = [pop.locator(s).inner_text().strip() for s in ("#tab-single", "#tab-channel")]
            check("tabs in English", all(not vi_words(x) for x in tabs), str(tabs))
            pop.locator("#tab-channel").click()
            pop.wait_for_timeout(500)
            ch = pop.evaluate("() => document.getElementById('channel-section') ? document.getElementById('channel-section').innerText : ''")
            pick = [pop.locator(s).inner_text().strip() for s in ("#ch-pick-all", "#ch-pick-none", "#ch-pick-10")]
            check("channel tab + pick buttons in English", not vi_words(ch) and all(not vi_words(x) for x in pick), f"{pick} {vi_words(ch)}")
            settings = pop.evaluate("() => { const s = document.getElementById('settings-panel'); s.classList.remove('hidden'); return s.innerText; }")
            check("settings panel in English (incl. file name template)", not vi_words(settings) and "{title}" in settings, str(vi_words(settings)))

            page = ctx.new_page()
            page.goto(FB, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            panel = page.locator("#vg-channel-panel")
            ptext = panel.inner_text() if panel.count() else ""
            check("in-page channel panel in English", panel.count() == 1 and not vi_words(ptext), f"{vi_words(ptext)} {ptext[:80]!r}")
            ctx.close()

            ctx, sw, ext_id, pop = run(p, tmp, "vi")
            btn = pop.locator("#btnText").inner_text().strip()
            check("--lang=vi: the same popup is Vietnamese", sw.evaluate("() => chrome.i18n.getUILanguage()").lower().startswith("vi")
                  and btn == "Tải Video Ngay", f"{sw.evaluate('() => chrome.i18n.getUILanguage()')} {btn}")
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
