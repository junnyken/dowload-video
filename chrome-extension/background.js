/**
 * VidGrab Background Service Worker v4.1
 * - API proxy (bypass CORS)
 * - VG_FETCH_LINK: fetch-link với 120s timeout + không bị abort khi popup đóng
 * - Badge count on extension icon
 * - Download progress tracking
 * - Notifications on download complete
 * - Download history storage
 * - API_BASE configurable via chrome.storage.sync
 */

const DEFAULT_API_BASE = 'https://dowload-video.mk.dev.matbao.ai';
const STORAGE_KEY = (tabId) => `vg_videos_${tabId}`;
const HISTORY_KEY = 'vg_download_history';
const MAX_HISTORY = 100;

// ── API Base (configurable) ───────────────────────────────────────
let _apiBase = DEFAULT_API_BASE;

async function getApiBase() {
  try {
    const r = await chrome.storage.sync.get('vg_api_base');
    return (r.vg_api_base && r.vg_api_base.trim()) ? r.vg_api_base.trim() : DEFAULT_API_BASE;
  } catch {
    return _apiBase;
  }
}

// Keep cached value updated
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.vg_api_base) {
    _apiBase = changes.vg_api_base.newValue || DEFAULT_API_BASE;
  }
});

// Warm cache on startup
getApiBase().then((v) => { _apiBase = v; });

// ── Helpers ───────────────────────────────────────────────────────
async function loadStore(tabId) {
  const key = STORAGE_KEY(tabId);
  const res = await chrome.storage.local.get(key);
  return res[key] || {};
}

async function saveStore(tabId, store) {
  await chrome.storage.local.set({ [STORAGE_KEY(tabId)]: store });
}

async function clearStore(tabId) {
  await chrome.storage.local.remove(STORAGE_KEY(tabId));
}

// ── Download History ──────────────────────────────────────────────
async function addToHistory(record) {
  const res = await chrome.storage.local.get(HISTORY_KEY);
  const history = res[HISTORY_KEY] || [];
  history.unshift({
    ...record,
    timestamp: Date.now(),
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
  });
  if (history.length > MAX_HISTORY) history.length = MAX_HISTORY;
  await chrome.storage.local.set({ [HISTORY_KEY]: history });
}

// ── Badge Count ───────────────────────────────────────────────────
async function updateBadgeForTab(tabId) {
  try {
    const tab = tabId
      ? await chrome.tabs.get(tabId).catch(() => null)
      : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    if (!tab?.url) { chrome.action.setBadgeText({ text: '' }); return; }

    const url = tab.url;
    const isSupported = /tiktok\.com|youtube\.com|facebook\.com|douyin\.com|instagram\.com|spotify\.com/i.test(url);

    if (isSupported) {
      const isVideo = /\/(video|watch|shorts|reel|reels|stories|p)\//i.test(url)
        || /\/watch\?/.test(url) || /spotify\.com\/track\//.test(url);
      const isChannel = /\/@|\/user\/|\/channel\/|\/c\/|\/playlist\?|spotify\.com\/(playlist|album)\//.test(url);

      if (isVideo) {
        chrome.action.setBadgeText({ tabId: tab.id, text: '1' });
        chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: '#f97316' });
      } else if (isChannel) {
        chrome.action.setBadgeText({ tabId: tab.id, text: '∞' });
        chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: '#22c55e' });
      } else {
        chrome.action.setBadgeText({ tabId: tab.id, text: '' });
      }
    } else {
      chrome.action.setBadgeText({ tabId: tab.id, text: '' });
    }
  } catch { /* ignore */ }
}

chrome.tabs.onActivated.addListener(({ tabId }) => updateBadgeForTab(tabId));
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === 'complete') updateBadgeForTab(tabId);
});

// ── Download Progress & Notification ──────────────────────────────
const activeDownloads = new Map();

chrome.downloads.onCreated.addListener((item) => {
  if (item.filename?.includes('VidGrab') || item.url?.includes('matbao.ai')) {
    activeDownloads.set(item.id, {
      filename: item.filename,
      startTime: Date.now(),
      totalBytes: item.totalBytes || 0,
    });
  }
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!activeDownloads.has(delta.id)) return;

  if (delta.state?.current === 'complete') {
    const info = activeDownloads.get(delta.id);
    activeDownloads.delete(delta.id);
    const filename = info.filename?.split('/').pop() || 'Video';
    chrome.notifications.create(`vg-dl-${delta.id}`, {
      type: 'basic', iconUrl: 'assets/icon.png',
      title: '✅ VidGrab — Tải thành công!', message: filename, priority: 1,
    });
    chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_PROGRESS', downloadId: delta.id, state: 'complete', progress: 100 }).catch(() => {});
  }

  if (delta.state?.current === 'interrupted') {
    activeDownloads.delete(delta.id);
    chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_PROGRESS', downloadId: delta.id, state: 'error' }).catch(() => {});
  }
});

setInterval(async () => {
  if (activeDownloads.size === 0) return;
  for (const [id] of activeDownloads) {
    try {
      const [item] = await chrome.downloads.search({ id });
      if (!item) continue;
      const pct = item.totalBytes > 0 ? Math.round((item.bytesReceived / item.totalBytes) * 100) : -1;
      chrome.runtime.sendMessage({
        type: 'VG_DOWNLOAD_PROGRESS', downloadId: id, state: 'downloading',
        progress: pct, received: item.bytesReceived, total: item.totalBytes,
      }).catch(() => {});
    } catch { /* ignore */ }
  }
}, 500);

// ── Message handler ───────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  // ── VG_FETCH_LINK: fetch-link qua background (không abort khi popup đóng, có 120s timeout)
  if (msg.type === 'VG_FETCH_LINK') {
    (async () => {
      const base = await getApiBase();
      const controller = new AbortController();
      const abortTimer = setTimeout(() => controller.abort(), 120_000);

      // Ping elapsed seconds back to popup every 2 seconds
      const startTime = Date.now();
      const pingInterval = setInterval(() => {
        const elapsed = Math.round((Date.now() - startTime) / 1000);
        chrome.runtime.sendMessage({ type: 'VG_FETCH_PROGRESS', elapsed }).catch(() => {});
      }, 2000);

      try {
        const resp = await fetch(`${base}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(msg.payload),
          signal: controller.signal,
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, status: resp.status, data });
      } catch (err) {
        sendResponse({
          ok: false,
          timeout: err.name === 'AbortError',
          error: err.name === 'AbortError'
            ? 'Server xử lý quá 120s. Thử chất lượng thấp hơn hoặc mở web.'
            : err.message,
        });
      } finally {
        clearTimeout(abortTimer);
        clearInterval(pingInterval);
      }
    })();
    return true; // giữ channel mở để sendResponse async
  }

  // ── VG_HEALTH_CHECK: ping server để kiểm tra kết nối
  if (msg.type === 'VG_HEALTH_CHECK') {
    (async () => {
      const base = await getApiBase();
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 5000);
        const r = await fetch(`${base}/api/v1/ping`, { signal: controller.signal, cache: 'no-store' });
        sendResponse({ ok: r.ok, status: r.status });
      } catch (err) {
        sendResponse({ ok: false, error: err.message });
      }
    })();
    return true;
  }

  // ── VG_API_FETCH: generic proxy (content script & popup)
  if (msg.type === 'VG_API_FETCH') {
    (async () => {
      try {
        const resp = await fetch(msg.url, {
          method: msg.method || 'POST',
          headers: msg.headers || { 'Content-Type': 'application/json' },
          body: msg.body ? JSON.stringify(msg.body) : undefined,
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, status: resp.status, data });
      } catch (err) {
        sendResponse({ ok: false, error: err.message });
      }
    })();
    return true;
  }

  // Content script gửi videos vừa intercept được
  if (msg.type === 'VG_STORE_VIDEOS' && tabId) {
    (async () => {
      const store = await loadStore(tabId);
      let added = 0;
      for (const v of msg.videos || []) {
        if (!store[v.aweme_id]) { store[v.aweme_id] = v; added++; }
      }
      if (added > 0) {
        await saveStore(tabId, store);
        const count = Object.keys(store).length;
        chrome.tabs.sendMessage(tabId, { type: 'VG_LIVE_COUNT', count, latest_added: added }).catch(() => {});
        sendResponse({ ok: true, count, added });
      } else {
        sendResponse({ ok: true, count: Object.keys(store).length, added: 0 });
      }
    })();
    return true;
  }

  if (msg.type === 'VG_GET_VIDEOS') {
    (async () => {
      let targetTabId = msg.tabId || tabId;
      if (!targetTabId) {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        targetTabId = tab?.id;
      }
      if (!targetTabId) { sendResponse({ videos: [], count: 0 }); return; }
      const store = await loadStore(targetTabId);
      sendResponse({ videos: Object.values(store), count: Object.keys(store).length });
    })();
    return true;
  }

  if (msg.type === 'VG_GET_COUNT') {
    (async () => {
      let targetTabId = msg.tabId || tabId;
      if (!targetTabId) {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        targetTabId = tab?.id;
      }
      if (!targetTabId) { sendResponse({ count: 0 }); return; }
      const store = await loadStore(targetTabId);
      sendResponse({ count: Object.keys(store).length });
    })();
    return true;
  }

  if (msg.type === 'VG_CLEAR_VIDEOS') {
    (async () => {
      const targetTabId = msg.tabId || tabId;
      if (targetTabId) await clearStore(targetTabId);
      sendResponse({ ok: true });
    })();
    return true;
  }

  if (msg.type === 'VG_SAVE_HISTORY') {
    addToHistory(msg.record).then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === 'VG_GET_HISTORY') {
    chrome.storage.local.get(HISTORY_KEY).then((res) => {
      sendResponse({ history: res[HISTORY_KEY] || [] });
    });
    return true;
  }

  if (msg.type === 'VG_CLEAR_HISTORY') {
    chrome.storage.local.remove(HISTORY_KEY).then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === 'VG_FETCH_FORMATS') {
    (async () => {
      try {
        const base = await getApiBase();
        const resp = await fetch(`${base}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: msg.url, quality: 'video', remove_watermark: true }),
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, data });
      } catch (err) {
        sendResponse({ ok: false, error: err.message });
      }
    })();
    return true;
  }
});

// ── Dọn storage khi tab đóng ─────────────────────────────────────
chrome.tabs.onRemoved.addListener((tabId) => { clearStore(tabId); });

// ── Right-click Context Menu ─────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  const patterns = [
    '*://*.tiktok.com/*', '*://*.youtube.com/*', '*://*.facebook.com/*',
    '*://*.douyin.com/*', '*://*.instagram.com/*', '*://open.spotify.com/*',
  ];
  chrome.contextMenus.create({ id: 'vg-download-video', title: '⬇ Tải với VidGrab', contexts: ['page', 'link', 'video', 'audio'], documentUrlPatterns: patterns });
  chrome.contextMenus.create({ id: 'vg-download-mp3', title: '🎵 Tải MP3 với VidGrab', contexts: ['page', 'link', 'video', 'audio'], documentUrlPatterns: patterns });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.url) return;
  const base = await getApiBase();
  const targetUrl = info.linkUrl || tab.url;
  const isMP3 = info.menuItemId === 'vg-download-mp3';
  chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '⚡ VidGrab: Đang xử lý...' }).catch(() => {});

  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 120_000);
    const resp = await fetch(`${base}/api/v1/fetch-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: targetUrl, quality: isMP3 ? 'mp3_320' : 'video', remove_watermark: true }),
      signal: controller.signal,
    });
    const data = await resp.json();

    if (resp.ok && data.success) {
      const targetDlUrl = data.direct_mp4_url || data.local_file_path;
      let ext = isMP3 ? 'mp3' : 'mp4';
      if (data.is_audio_only) ext = 'mp3';
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      let dlUrl;
      if (targetDlUrl && !targetDlUrl.includes('matbao.ai')) {
        dlUrl = `${base}/api/v1/proxy-download?url=${encodeURIComponent(targetDlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (targetDlUrl?.startsWith('/app/downloads/')) {
        dlUrl = `${base}/api/v1/download-local?filepath=${encodeURIComponent(targetDlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      } else {
        dlUrl = targetDlUrl;
      }

      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.${ext}`, saveAs: true });
      addToHistory({ title: data.title || 'Video', thumbnail: data.thumbnail_url || '', url: targetUrl, quality: isMP3 ? 'MP3' : 'HD', fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '' });
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '✅ Đang tải xuống!' }).catch(() => {});
    } else {
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '❌ ' + (data.detail || 'Lỗi') }).catch(() => {});
    }
  } catch (err) {
    const msg = err.name === 'AbortError' ? 'Xử lý quá lâu' : 'Lỗi kết nối';
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: `❌ ${msg}` }).catch(() => {});
  }
});
