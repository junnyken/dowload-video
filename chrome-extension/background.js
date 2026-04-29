/**
 * VidGrab Background Service Worker
 * Nhận dữ liệu từ content.js, lưu vào storage, broadcast live count lại tab.
 */

const STORAGE_KEY = (tabId) => `vg_videos_${tabId}`;

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

// ── Message handler ───────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id;

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

        // Broadcast live count về tab để panel cập nhật
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
      // Lấy active tab nếu không có tabId (popup không có sender.tab)
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

  // Popup hỏi số lượng (nhẹ hơn)
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

  // Clear store theo yêu cầu
  if (msg.type === 'VG_CLEAR_VIDEOS') {
    (async () => {
      const targetTabId = msg.tabId || tabId;
      if (targetTabId) await clearStore(targetTabId);
      sendResponse({ ok: true });
    })();
    return true;
  }
});

// ── Dọn storage khi tab đóng ─────────────────────────────────────
chrome.tabs.onRemoved.addListener((tabId) => {
  clearStore(tabId);
});
