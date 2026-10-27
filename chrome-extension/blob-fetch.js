// 5.22.1: a file whose CDN wants a Referer (Douyin). chrome.downloads cannot send one (Chrome refuses
// the header) and its requests pass no extension rule, but this page's fetch does get the
// declarativeNetRequest Referer from background.js — so fetch here, hand a blob: URL back to save.
(() => {
  const urls = new Set();
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.target !== 'offscreen') return false;
    if (msg.type === 'BLOB_FETCH') {
      (async () => {
        const r = await fetch(msg.url, { credentials: 'omit' });
        if (!r.ok) return { ok: false, status: r.status };
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        urls.add(url);
        return { ok: true, url, bytes: blob.size };
      })().then(sendResponse, (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    if (msg.type === 'BLOB_RELEASE') {
      if (urls.delete(msg.url)) URL.revokeObjectURL(msg.url);
      sendResponse({ ok: true, left: urls.size });
    }
    return false;
  });
})();
