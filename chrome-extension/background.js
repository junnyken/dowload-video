/**
 * VidGrab Background Service Worker v3.0
 * - API proxy (bypass CORS)
 * - Badge count on extension icon
 * - Keyboard shortcut handler (Ctrl+Shift+D)
 * - Download progress tracking
 * - Notifications on download complete
 * - Download history storage
 */

const API_BASE = 'https://dowload-video.mk.dev.matbao.ai';
const STORAGE_KEY = (tabId) => `vg_videos_${tabId}`;
const HISTORY_KEY = 'vg_download_history';
const MAX_HISTORY = 100;

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
  // Keep only last MAX_HISTORY
  if (history.length > MAX_HISTORY) history.length = MAX_HISTORY;
  await chrome.storage.local.set({ [HISTORY_KEY]: history });
}

// ── Badge Count ───────────────────────────────────────────────────
async function updateBadgeForTab(tabId) {
  try {
    const tab = tabId
      ? await chrome.tabs.get(tabId).catch(() => null)
      : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    if (!tab?.url) {
      chrome.action.setBadgeText({ text: '' });
      return;
    }

    const url = tab.url;
    const isSupported = /tiktok\.com|youtube\.com|facebook\.com|douyin\.com|instagram\.com|spotify\.com/i.test(url);

    if (isSupported) {
      // Check if it's a video page
      const isVideo = /\/(video|watch|shorts|reel|reels|stories|p)\//i.test(url)
        || /\/watch\?/.test(url)
        || /spotify\.com\/track\//.test(url);
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
  } catch (e) { /* ignore */ }
}

// Update badge when tab changes
chrome.tabs.onActivated.addListener(({ tabId }) => updateBadgeForTab(tabId));
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === 'complete') updateBadgeForTab(tabId);
});

// ── Keyboard Shortcut Handler ─────────────────────────────────────
chrome.commands.onCommand.addListener(async (command) => {
  if (command !== 'quick-download') return;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || tab.url.startsWith('chrome://')) return;

  try {
    // Send toast notification to page
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '⚡ VidGrab: Đang tải...' }).catch(() => {});

    const resp = await fetch(`${API_BASE}/api/v1/fetch-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: tab.url,
        quality: tab.url.includes('spotify.com') ? 'mp3_320' : 'video',
        remove_watermark: true,
      }),
    });
    const data = await resp.json();

    if (resp.ok && data.success) {
      const targetUrl = data.direct_mp4_url || data.local_file_path;
      let ext = 'mp4';
      if (data.is_audio_only || (targetUrl && (targetUrl.endsWith('.mp3') || targetUrl.endsWith('.m4a')))) {
        ext = 'mp3';
      }
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      let dlUrl;
      if (targetUrl && !targetUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(targetUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (targetUrl && targetUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(targetUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      } else {
        dlUrl = targetUrl;
      }

      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.${ext}`, saveAs: false });

      // Save to history
      addToHistory({
        title: data.title || 'Video',
        thumbnail: data.thumbnail_url || '',
        url: tab.url,
        quality: ext === 'mp3' ? 'MP3' : 'Video HD',
        fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '',
      });

      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '✅ Đang tải xuống!' }).catch(() => {});
    } else {
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '❌ Lỗi: ' + (data.detail || 'Server lỗi') }).catch(() => {});
    }
  } catch (err) {
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '❌ Lỗi kết nối server' }).catch(() => {});
  }
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

  // Track progress
  if (delta.state?.current === 'complete') {
    const info = activeDownloads.get(delta.id);
    activeDownloads.delete(delta.id);

    // Send notification
    const filename = info.filename?.split('/').pop() || 'Video';
    chrome.notifications.create(`vg-dl-${delta.id}`, {
      type: 'basic',
      iconUrl: 'assets/icon.png',
      title: '✅ VidGrab — Tải thành công!',
      message: `${filename}`,
      priority: 1,
    });

    // Broadcast progress complete to popup
    chrome.runtime.sendMessage({
      type: 'VG_DOWNLOAD_PROGRESS',
      downloadId: delta.id,
      state: 'complete',
      progress: 100,
    }).catch(() => {});
  }

  if (delta.state?.current === 'interrupted') {
    activeDownloads.delete(delta.id);
    chrome.runtime.sendMessage({
      type: 'VG_DOWNLOAD_PROGRESS',
      downloadId: delta.id,
      state: 'error',
    }).catch(() => {});
  }
});

// Periodic progress polling for active downloads
setInterval(async () => {
  if (activeDownloads.size === 0) return;
  for (const [id] of activeDownloads) {
    try {
      const [item] = await chrome.downloads.search({ id });
      if (!item) continue;
      const pct = item.totalBytes > 0
        ? Math.round((item.bytesReceived / item.totalBytes) * 100)
        : -1;
      chrome.runtime.sendMessage({
        type: 'VG_DOWNLOAD_PROGRESS',
        downloadId: id,
        state: 'downloading',
        progress: pct,
        received: item.bytesReceived,
        total: item.totalBytes,
      }).catch(() => {});
    } catch { /* ignore */ }
  }
}, 500);

// ── Message handler ───────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  // API PROXY — content script & popup gửi qua đây để bypass CORS
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
        if (!store[v.aweme_id]) {
          store[v.aweme_id] = v;
          added++;
        }
      }

      if (added > 0) {
        await saveStore(tabId, store);
        const count = Object.keys(store).length;
        chrome.tabs.sendMessage(tabId, {
          type: 'VG_LIVE_COUNT',
          count,
          latest_added: added,
        }).catch(() => {});
        sendResponse({ ok: true, count, added });
      } else {
        sendResponse({ ok: true, count: Object.keys(store).length, added: 0 });
      }
    })();
    return true;
  }

  // Popup hỏi danh sách video
  if (msg.type === 'VG_GET_VIDEOS') {
    (async () => {
      let targetTabId = msg.tabId || tabId;
      if (!targetTabId) {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        targetTabId = tab?.id;
      }
      if (!targetTabId) { sendResponse({ videos: [], count: 0 }); return; }
      const store = await loadStore(targetTabId);
      const videos = Object.values(store);
      sendResponse({ videos, count: videos.length });
    })();
    return true;
  }

  // Popup hỏi số lượng
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

  // Clear store
  if (msg.type === 'VG_CLEAR_VIDEOS') {
    (async () => {
      const targetTabId = msg.tabId || tabId;
      if (targetTabId) await clearStore(targetTabId);
      sendResponse({ ok: true });
    })();
    return true;
  }

  // Save to history (from popup)
  if (msg.type === 'VG_SAVE_HISTORY') {
    addToHistory(msg.record).then(() => sendResponse({ ok: true }));
    return true;
  }

  // Get history
  if (msg.type === 'VG_GET_HISTORY') {
    chrome.storage.local.get(HISTORY_KEY).then((res) => {
      sendResponse({ history: res[HISTORY_KEY] || [] });
    });
    return true;
  }

  // Clear history
  if (msg.type === 'VG_CLEAR_HISTORY') {
    chrome.storage.local.remove(HISTORY_KEY).then(() => sendResponse({ ok: true }));
    return true;
  }
});

// ── Dọn storage khi tab đóng ─────────────────────────────────────
chrome.tabs.onRemoved.addListener((tabId) => {
  clearStore(tabId);
});
