/**
 * VidGrab web ⇄ extension auth bridge
 *
 * Runs ONLY on the VidGrab web app origin (see manifest content_scripts).
 *
 * Why this file exists: popup.js has always had a handler for
 * `VG_AUTH_TOKEN_FROM_WEB`, and background.js has always had
 * `VG_SET_AUTH_TOKEN` — but nothing anywhere ever sent either message. No
 * content script ran on the web app, and the web app had no bridge code. So
 * "Kết nối tài khoản" could never succeed: the extension stayed anonymous
 * forever, which is why history/archive/Pro quota never worked from it.
 *
 * Relaying via the popup would not work either — the popup is closed while the
 * user is logging in on the web tab. This talks straight to the service worker.
 *
 * Trust: the page and this content script share a window but not a JS context.
 * We accept a token only when it came from THIS page's own origin, and
 * background.js independently re-checks our sender origin before storing it —
 * neither side takes the other's word for it.
 */
(function () {
  if (window.__vg_web_bridge) return;
  window.__vg_web_bridge = true;

  window.addEventListener('message', (event) => {
    // Same-window only: rejects messages posted by cross-origin iframes.
    if (event.source !== window) return;
    // Same-origin only: rejects a same-window message forged with a spoofed
    // origin by an embedded document.
    if (event.origin !== window.location.origin) return;

    const data = event.data;
    if (!data || data.__vg_source !== 'webapp') return;

    if (data.type === 'VG_AUTH_TOKEN_FROM_WEB' && typeof data.token === 'string' && data.token) {
      chrome.runtime.sendMessage(
        {
          type: 'VG_SET_AUTH_TOKEN',
          token: data.token,
          email: typeof data.email === 'string' ? data.email : undefined,
        },
        () => { if (chrome.runtime.lastError) { /* SW asleep — retried on next page load */ } }
      );
      return;
    }

    if (data.type === 'VG_AUTH_LOGOUT_FROM_WEB') {
      chrome.runtime.sendMessage({ type: 'VG_CLEAR_AUTH_TOKEN' }, () => {
        if (chrome.runtime.lastError) { /* ignore */ }
      });
    }
  });

  // Tell the page an extension is present, so it can decide whether to show
  // the "connect" affordance at all.
  window.postMessage({ __vg_source: 'extension', type: 'VG_EXTENSION_PRESENT' }, window.location.origin);
})();
