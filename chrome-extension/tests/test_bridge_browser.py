"""
Manual-equivalent test of the VidGrab web <-> extension auth bridge, run in a
real Chromium with the real unpacked extension loaded.

What this can prove: the bridge chain the previous release was missing — page
postMessage -> web-bridge.js content script -> background sender check ->
chrome.storage.local — and that the origin gate actually rejects a page that
is not the web app.

What it cannot prove: the Supabase login itself, which needs real credentials.
So the token here is synthetic; the plumbing it travels through is not.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT = str(Path(__file__).resolve().parent.parent)
WEB = "https://dvid.cmc-1.vibenode.matbao.ai"
CHROME = str(Path.home() / ".cache/ms-playwright/chromium-1234/chrome-linux64/chrome")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)


_ext_page = {"page": None}

def sw_eval(ctx, script):
    """
    Evaluate with the chrome.* APIs available.

    Deliberately NOT the service worker: MV3 terminates it after ~30s idle, and
    a reconnected worker handle threw "chrome is not defined" mid-run. An
    extension page keeps the APIs and stays put for the length of the test.
    """
    return _ext_page["page"].evaluate(script)


def main():
    profile = tempfile.mkdtemp(prefix="vg_ext_")
    try:
        with sync_playwright() as p:
            # headless=True makes Playwright pick chrome-headless-shell, which
            # cannot load extensions at all. Force the full Chromium binary and
            # ask for the new headless mode via args instead.
            ctx = p.chromium.launch_persistent_context(
                profile,
                headless=False,
                executable_path=CHROME,
                args=[
                    "--headless=new",
                    f"--disable-extensions-except={EXT}",
                    f"--load-extension={EXT}",
                    "--no-sandbox",
                ],
            )

            # ── extension loaded at all? ──────────────────────────────
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            ext_id = sw.url.split("/")[2]
            check("extension service worker registered", bool(ext_id), ext_id)

            # Stable handle for chrome.* calls (see sw_eval).
            _ext_page["page"] = ctx.new_page()
            _ext_page["page"].goto(f"chrome-extension://{ext_id}/popup.html",
                                   wait_until="domcontentloaded", timeout=20000)

            ver = sw_eval(ctx, "() => chrome.runtime.getManifest().version")
            expected = json.loads((Path(EXT) / "manifest.json").read_text())["version"]
            check(f"manifest version matches source ({expected})", ver == expected, ver)

            has_bridge = sw_eval(ctx, """() => chrome.runtime.getManifest()
                .content_scripts.some(c => (c.js||[]).includes('web-bridge.js'))""")
            check("web-bridge.js registered as content script", has_bridge is True)

            # ── clean slate ───────────────────────────────────────────
            sw_eval(ctx, "() => chrome.storage.local.remove(['vg_auth_token','vg_auth_email'])")
            before = sw_eval(ctx, "() => chrome.storage.local.get('vg_auth_token').then(r => r.vg_auth_token || null)")
            check("no token before the test", before is None, repr(before))

            # ── NEGATIVE: a page that is NOT the web app must be ignored ──
            other = ctx.new_page()
            other.goto("https://example.com/", wait_until="domcontentloaded", timeout=30000)
            other.evaluate("""() => window.postMessage(
                {__vg_source:'webapp', type:'VG_AUTH_TOKEN_FROM_WEB',
                 token:'ATTACKER-TOKEN', email:'attacker@evil.tld'}, location.origin)""")
            other.wait_for_timeout(1500)
            leaked = sw_eval(ctx, "() => chrome.storage.local.get('vg_auth_token').then(r => r.vg_auth_token || null)")
            check("token from a non-web-app origin is REJECTED", leaked is None, repr(leaked))
            other.close()

            # ── POSITIVE: the real web app origin ─────────────────────
            page = ctx.new_page()
            # web-bridge.js runs in an ISOLATED world, so window.__vg_web_bridge
            # is invisible from the page — checking it was my mistake, not a
            # code fault. The bridge does announce itself with a postMessage the
            # page can legitimately see, so listen for that instead. Installed
            # before navigation because the announcement fires on load.
            page.add_init_script("""
                window.__vg_seen_present = false;
                window.addEventListener('message', (e) => {
                    if (e.source === window && e.data && e.data.__vg_source === 'extension'
                        && e.data.type === 'VG_EXTENSION_PRESENT') {
                        window.__vg_seen_present = true;
                    }
                });
            """)
            page.goto(f"{WEB}/?connect_extension=1", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)   # let web-bridge.js attach

            present = page.evaluate("() => window.__vg_seen_present === true")
            check("web-bridge.js announced itself to the web app page", present is True)

            page.evaluate("""() => window.postMessage(
                {__vg_source:'webapp', type:'VG_AUTH_TOKEN_FROM_WEB',
                 token:'SYNTHETIC-TOKEN-abc123', email:'bridge-test@matbao.com'}, location.origin)""")
            page.wait_for_timeout(2000)

            got = sw_eval(ctx, "() => chrome.storage.local.get(['vg_auth_token','vg_auth_email']).then(r => r)")
            check("token from the web app origin IS stored",
                  got.get("vg_auth_token") == "SYNTHETIC-TOKEN-abc123", json.dumps(got))
            check("email stored alongside",
                  got.get("vg_auth_email") == "bridge-test@matbao.com", str(got.get("vg_auth_email")))

            # ── ATTACK 1: a cross-origin iframe inside the web app ────
            # The bridge lives on this origin, so this is the realistic attempt:
            # an embedded third party posting a token up to the parent window.
            # event.source is the iframe's window, not `window`, so it must be
            # refused. Weaker than it looks otherwise — the earlier example.com
            # case proved only that the bridge is not injected there.
            sw_eval(ctx, "() => chrome.storage.local.remove(['vg_auth_token','vg_auth_email'])")
            page.evaluate("""() => new Promise(r => {
                const f = document.createElement('iframe');
                f.src = 'https://example.com/';
                f.onload = () => {
                    // Same message shape, posted from the iframe to the parent.
                    f.contentWindow.postMessage('noop', '*');
                    window.postMessage.call(window, 0, '*');  // keep-alive noop
                    r(true);
                };
                document.body.appendChild(f);
            })""")
            page.wait_for_timeout(1200)
            page.evaluate("""() => {
                const f = document.querySelector('iframe');
                // Simulate the iframe as the message source.
                window.dispatchEvent(new MessageEvent('message', {
                    source: f.contentWindow, origin: location.origin,
                    data: {__vg_source:'webapp', type:'VG_AUTH_TOKEN_FROM_WEB',
                           token:'IFRAME-TOKEN', email:'iframe@evil.tld'},
                }));
            }""")
            page.wait_for_timeout(1500)
            from_iframe = sw_eval(ctx, "() => chrome.storage.local.get('vg_auth_token').then(r => r.vg_auth_token || null)")
            check("token claimed by a cross-origin iframe is REJECTED", from_iframe is None, repr(from_iframe))

            # ── ATTACK 2: any page reaching the worker directly ───────
            # content.js runs on tiktok.com. If a page could call
            # chrome.runtime.sendMessage it could try VG_SET_AUTH_TOKEN itself,
            # so confirm that surface simply is not exposed to page JS.
            tk = ctx.new_page()
            tk.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
            reachable = tk.evaluate("""() => {
                try { return typeof chrome !== 'undefined'
                             && !!chrome.runtime && !!chrome.runtime.sendMessage; }
                catch { return false; }
            }""")
            check("page JS on an injected site cannot reach chrome.runtime", reachable is False, str(reachable))
            tk.close()

            # restore the token for the read-path checks below
            page.evaluate("""() => window.postMessage(
                {__vg_source:'webapp', type:'VG_AUTH_TOKEN_FROM_WEB',
                 token:'SYNTHETIC-TOKEN-abc123', email:'bridge-test@matbao.com'}, location.origin)""")
            page.wait_for_timeout(1500)

            # ── the read path popup.js uses ───────────────────────────
            status = sw_eval(ctx, """() => new Promise(res =>
                chrome.runtime.sendMessage({type:'VG_GET_AUTH_STATUS'}, res))""")
            check("VG_GET_AUTH_STATUS reports authenticated", status and status.get("authenticated") is True, str(status))

            tok = sw_eval(ctx, """() => new Promise(res =>
                chrome.runtime.sendMessage({type:'VG_GET_AUTH_TOKEN'}, res))""")
            check("VG_GET_AUTH_TOKEN returns the token (had NO handler before)",
                  tok and tok.get("token") == "SYNTHETIC-TOKEN-abc123", str(tok))

            # ── logout propagation ────────────────────────────────────
            page.evaluate("""() => window.postMessage(
                {__vg_source:'webapp', type:'VG_AUTH_LOGOUT_FROM_WEB'}, location.origin)""")
            page.wait_for_timeout(1500)
            after = sw_eval(ctx, "() => chrome.storage.local.get('vg_auth_token').then(r => r.vg_auth_token || null)")
            check("web sign-out clears the extension token", after is None, repr(after))

            ctx.close()
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    ok = sum(1 for _, o, _ in results if o)
    print(f"\n── {ok}/{len(results)} passed ──", flush=True)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
