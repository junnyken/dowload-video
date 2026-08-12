/**
 * VidGrab Background Service Worker v4.9
 * - API proxy (bypass CORS)
 * - VG_FETCH_LINK: fetch-link với 120s timeout + không bị abort khi popup đóng
 * - Badge count on extension icon
 * - Download progress tracking
 * - Notifications on download complete
 * - Download history storage
 * - API_BASE configurable via chrome.storage.sync
 * - MV3: alarms-based heartbeat to prevent SW suspension during downloads
 */

const DEFAULT_API_BASE = 'https://dowload-video.mk.dev.matbao.ai';
const STORAGE_KEY = (tabId) => `vg_videos_${tabId}`;
const HISTORY_KEY = 'vg_download_history';
const AUTH_TOKEN_KEY = 'vg_auth_token';   // Supabase access_token
const MAX_HISTORY = 100;

// ── Auth token helpers ────────────────────────────────────────────
async function getAuthToken() {
  try {
    const r = await chrome.storage.local.get(AUTH_TOKEN_KEY);
    return r[AUTH_TOKEN_KEY] || null;
  } catch { return null; }
}

async function authHeaders(extra = {}) {
  const token = await getAuthToken();
  if (!token) return extra;
  return { ...extra, Authorization: `Bearer ${token}` };
}

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

// ── BYOC: ephemeral browser-session cookies for login-gated platforms ─────
// The backend already supports a per-request `user_cookies_b64` field
// (see /fetch-link in routes.py) — it writes a temp file, uses it for this
// ONE extraction, then deletes it in a `finally` block. Nothing is ever
// written to the shared cookie pool (DB/Redis). This reads the user's own
// live session straight from the browser via chrome.cookies, so most people
// never have to manually export/paste a cookies.txt file at all: if they're
// already logged into Instagram/Twitter/etc. in this browser, it just works.
// Only read for platforms that actually gate content behind login — skipped
// entirely for TikTok/YouTube/etc., both for privacy (don't touch cookies
// we don't need) and to avoid wasted chrome.cookies calls.
const COOKIE_GATED_DOMAINS = [
  'instagram.com', 'twitter.com', 'x.com', 'facebook.com',
  'reddit.com', 'bilibili.com', 'xiaohongshu.com', 'lemon8-app.com',
];

function _cookieGatedDomainFor(pageUrl) {
  try {
    const host = new URL(pageUrl).hostname.replace(/^www\./, '');
    return COOKIE_GATED_DOMAINS.find((d) => host === d || host.endsWith('.' + d)) || null;
  } catch {
    return null;
  }
}

function _toNetscapeCookieFile(cookies) {
  const lines = ['# Netscape HTTP Cookie File'];
  for (const c of cookies) {
    const domain = c.domain.startsWith('.') ? c.domain : (c.hostOnly ? c.domain : '.' + c.domain);
    const includeSubdomains = domain.startsWith('.') ? 'TRUE' : 'FALSE';
    const expiry = c.session ? '0' : String(Math.round(c.expirationDate || 0));
    lines.push([domain, includeSubdomains, c.path || '/', c.secure ? 'TRUE' : 'FALSE', expiry, c.name, c.value].join('\t'));
  }
  return lines.join('\n');
}

// Returns base64 cookies.txt for `pageUrl`'s platform, or undefined if the
// platform isn't login-gated / the user has no session there (falls back
// to the shared admin cookie pool server-side, same as today).
async function getEphemeralCookiesB64(pageUrl) {
  const domain = _cookieGatedDomainFor(pageUrl);
  if (!domain) return undefined;
  try {
    const cookies = await chrome.cookies.getAll({ domain });
    if (!cookies || cookies.length === 0) return undefined;
    const text = _toNetscapeCookieFile(cookies);
    return btoa(unescape(encodeURIComponent(text)));
  } catch (e) {
    console.warn('[VidGrab] Could not read browser cookies for', domain, e);
    return undefined;
  }
}

// ── E1: extension-direct download (user IP) ──────────────────────────
// A raw CDN URL is downloadable straight from the browser → bytes use the
// USER's IP + session (no server bandwidth, spreads identity, ban-proof).
// NOT eligible: server-local files, our own server, YouTube (IP-locked CDN),
// HLS manifests (.m3u8 need server transcode). Those go through the server.
function _isBrowserDirectUrl(u) {
  if (!u || !u.startsWith('http')) return false;
  const low = u.toLowerCase();
  if (low.includes('matbao.ai')) return false;       // already our server
  if (low.includes('googlevideo.com')) return false; // YouTube — IP-locked
  if (low.includes('.m3u8')) return false;           // HLS — needs server merge
  return true;
}

function _serverWrap(rawUrl, base, safeName, finalExt) {
  if (rawUrl.startsWith('/app/downloads/') || rawUrl.startsWith('downloads/')) {
    return `${base}/api/v1/download-local?filepath=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(safeName)}.${finalExt}`;
  }
  if (rawUrl.includes('matbao.ai')) return rawUrl;
  return `${base}/api/v1/proxy-download?url=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(safeName)}&ext=${finalExt}`;
}

// Download preferring the user's own IP; fall back to the server proxy if the
// direct download is interrupted (403 / CORS / referer-gated CDN).
function smartDownload({ rawUrl, base, safeName, finalExt, saveAs }) {
  const filename = `VidGrab/${safeName}.${finalExt}`;
  const proxyUrl = _serverWrap(rawUrl, base, safeName, finalExt);

  if (!_isBrowserDirectUrl(rawUrl)) {
    chrome.downloads.download({ url: proxyUrl, filename, saveAs });
    return;
  }
  chrome.downloads.download({ url: rawUrl, filename, saveAs }, (downloadId) => {
    if (chrome.runtime.lastError || !downloadId) {
      console.log('[VidGrab] direct download rejected → server fallback');
      chrome.downloads.download({ url: proxyUrl, filename, saveAs });
      return;
    }
    const onChanged = (delta) => {
      if (delta.id !== downloadId || !delta.state) return;
      if (delta.state.current === 'interrupted') {
        chrome.downloads.onChanged.removeListener(onChanged);
        console.log('[VidGrab] direct download interrupted → server fallback');
        chrome.downloads.download({ url: proxyUrl, filename, saveAs });
      } else if (delta.state.current === 'complete') {
        chrome.downloads.onChanged.removeListener(onChanged);
      }
    };
    chrome.downloads.onChanged.addListener(onChanged);
  });
}

// Keep cached value updated
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.vg_api_base) {
    _apiBase = changes.vg_api_base.newValue || DEFAULT_API_BASE;
  }
});

// Warm cache on startup
getApiBase().then((v) => { _apiBase = v; });

// ── MV3 Service Worker keepalive ──────────────────────────────────
// Alarms fire every 1 min to extend SW lifetime during background downloads.
// Without this, Chrome can suspend the SW after ~30s idle, losing the
// activeDownloads Map and stopping download progress tracking.
chrome.alarms.create('vg-sw-heartbeat', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'vg-sw-heartbeat') {
    // Re-warm API cache so it's ready immediately on wake
    getApiBase().then((v) => { _apiBase = v; }).catch(() => {});
  }
});

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
      if (item.state === 'complete') { activeDownloads.delete(id); continue; }
      const pct = (item.totalBytes > 0 && item.bytesReceived >= 0)
        ? Math.min(99, Math.round((item.bytesReceived / item.totalBytes) * 100))
        : -1;
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

  // ── Auth token management ─────────────────────────────────────────────────────────────────
  if (msg.type === 'VG_SET_AUTH_TOKEN') {
    chrome.storage.local.set({ [AUTH_TOKEN_KEY]: msg.token }, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'VG_CLEAR_AUTH_TOKEN') {
    chrome.storage.local.remove(AUTH_TOKEN_KEY, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'VG_GET_AUTH_STATUS') {
    getAuthToken().then(token => sendResponse({ authenticated: !!token }));
    return true;
  }

  // ── VG_DOWNLOAD_NOW: full download pipeline in background — survives popup close ──────────
  // popup sends this instead of VG_FETCH_LINK so the download completes even if popup closes.
  // Flow: fetch-link API → resolve URL → chrome.downloads.download (saveAs:false) → notify.
  if (msg.type === 'VG_DOWNLOAD_NOW') {
    (async () => {
      const base = await getApiBase();
      const { url, quality, remove_watermark, download_subs } = msg.payload;

      // Ack immediately so popup can show "running in background" state
      sendResponse({ ok: true, queued: true });

      // Store pending state so popup can read it when reopened
      const pendingKey = `vg_dl_pending_${Date.now()}`;
      await chrome.storage.local.set({ [pendingKey]: { url, quality, status: 'processing', startedAt: Date.now() } });

      let notifTitle = 'VidGrab — Đang xử lý...';
      try {
        const controller = new AbortController();
        const abortTimer = setTimeout(() => controller.abort(), 150_000);

        // Ping elapsed to popup every 3s (popup may be closed — sendMessage fails silently)
        const startTime = Date.now();
        const pingInterval = setInterval(() => {
          const elapsed = Math.round((Date.now() - startTime) / 1000);
          chrome.runtime.sendMessage({ type: 'VG_FETCH_PROGRESS', elapsed }).catch(() => {});
        }, 3000);

        const user_cookies_b64 = await getEphemeralCookiesB64(url);
        const resp = await fetch(`${base}/api/v1/fetch-link`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ url, quality, remove_watermark, download_subs, user_cookies_b64 }),
          signal: controller.signal,
        });
        clearTimeout(abortTimer);
        clearInterval(pingInterval);

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (!data?.success) throw new Error(data?.detail || 'Server lỗi');

        // Resolve download URL (same logic as popup resolveDownloadUrl)
        const ext = (quality || '').includes('mp3') || data.is_audio_only ? 'mp3' : 'mp4';
        let dlUrl = ext === 'mp3'
          ? (data.local_mp3_path || data.local_file_path || data.direct_mp4_url)
          : (data.direct_mp4_url || data.local_file_path || data.local_mp3_path);

        if (!dlUrl) throw new Error('Không lấy được link tải');

        const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');
        const finalExt = (data.is_audio_only || dlUrl.endsWith('.mp3') || dlUrl.endsWith('.m4a')) ? 'mp3' : ext;

        // E1: prefer direct (user IP), fall back to server proxy on failure
        smartDownload({ rawUrl: dlUrl, base, safeName, finalExt, saveAs: false });
        // Subtitle: prefer local backend file, fall back to CDN URL
        if (data.subtitle_file_url) {
          const subUrl = data.subtitle_file_url.startsWith('http') ? data.subtitle_file_url : `${base}${data.subtitle_file_url}`;
          chrome.downloads.download({ url: subUrl, filename: `VidGrab/${safeName}.srt`, saveAs: false });
        } else if (data.subtitle_url) {
          chrome.downloads.download({ url: data.subtitle_url, filename: `VidGrab/${safeName}.srt`, saveAs: false });
        }

        notifTitle = `✅ ${data.title || 'Video'} — đang tải`;
        const _hadSubtitle = !!(data.subtitle_file_url || data.subtitle_url);
        addToHistory({
          title: data.title || 'Video', thumbnail: data.thumbnail_url || '', url,
          quality: finalExt === 'mp3' ? 'MP3' : (quality || 'HD'),
          fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '',
          noWatermark: remove_watermark && finalExt !== 'mp3',
          hasSubtitle: _hadSubtitle,
          subtitleLanguages: data.available_subtitle_languages || [],
          subtitleSource: data.subtitle_source || 'none',
        });

        // Notify popup (may be closed — fails silently)
        chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_DONE', data, dlUrl, ext: finalExt, safeName }).catch(() => {});

        // Update pending store with success
        await chrome.storage.local.set({ [pendingKey]: { url, quality, status: 'success', title: data.title, thumbnail: data.thumbnail_url, dlUrl, ext: finalExt, safeName, doneAt: Date.now() } });
      } catch (err) {
        notifTitle = `❌ Lỗi tải video`;
        chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_DONE', error: err.message }).catch(() => {});
        await chrome.storage.local.set({ [pendingKey]: { url, quality, status: 'error', error: err.message, doneAt: Date.now() } });
      }

      // Show Chrome notification
      chrome.notifications.create(`vg-bg-${Date.now()}`, {
        type: 'basic', iconUrl: 'assets/icon.png',
        title: notifTitle,
        message: `VidGrab đã hoàn tất • ${new URL(url).hostname}`,
        priority: 1,
      });

      // Auto-clean pending store after 10 min
      setTimeout(() => chrome.storage.local.remove(pendingKey), 10 * 60 * 1000);
    })();
    return true;
  }

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
        const body = { ...msg.payload };
        if (!body.user_cookies_b64 && body.url) {
          body.user_cookies_b64 = await getEphemeralCookiesB64(body.url);
        }
        const resp = await fetch(`${base}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
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
        const user_cookies_b64 = await getEphemeralCookiesB64(msg.url);
        const resp = await fetch(`${base}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: msg.url, quality: 'video', remove_watermark: true, user_cookies_b64 }),
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
chrome.runtime.onInstalled.addListener((details) => {
  const patterns = [
    '*://*.tiktok.com/*', '*://*.youtube.com/*', '*://*.facebook.com/*',
    '*://*.douyin.com/*', '*://*.instagram.com/*', '*://open.spotify.com/*',
  ];
  chrome.contextMenus.create({ id: 'vg-download-video', title: '⬇ Tải với VidGrab', contexts: ['page', 'link', 'video', 'audio'], documentUrlPatterns: patterns });
  chrome.contextMenus.create({ id: 'vg-download-mp3', title: '🎵 Tải MP3 với VidGrab', contexts: ['page', 'link', 'video', 'audio'], documentUrlPatterns: patterns });
  // E2: download media found on the current page using the user's own session/IP
  chrome.contextMenus.create({ id: 'vg-download-page-media', title: '⚡ Tải media từ trang (phiên của bạn)', contexts: ['page', 'video', 'audio'] });

  // Track first install vs update for onboarding
  if (details.reason === 'install') {
    chrome.storage.local.set({ vg_onboarding_done: false, vg_install_time: Date.now() });
  }
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.url) return;
  const base = await getApiBase();

  // ── E2: download media captured on the page via the user's own session/IP ──
  if (info.menuItemId === 'vg-download-page-media') {
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '⚡ VidGrab: Đang quét media trên trang...' }).catch(() => {});
    let resp = null;
    try {
      resp = await chrome.tabs.sendMessage(tab.id, { type: 'VG_SCAN_PAGE_MEDIA' });
    } catch {
      resp = null;
    }
    const media = (resp && resp.media) || [];
    if (media.length) {
      const safeName = ((resp.pageTitle || 'video').replace(/[/\\?%*:|"<>]/g, '-')).slice(0, 80) || 'video';
      smartDownload({ rawUrl: media[0], base, safeName, finalExt: media[0].toLowerCase().includes('.mp3') || media[0].toLowerCase().includes('.m4a') ? 'mp3' : 'mp4', saveAs: true });
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '✅ Đang tải media từ trang (phiên của bạn)!' }).catch(() => {});
      return;
    }
    // Auto-fallback: no direct media on page → download via the server (VidGrab),
    // same as "Tải với VidGrab". One action that always works.
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '↪ Không thấy media trực tiếp — đang tải qua VidGrab...' }).catch(() => {});
    // fall through to the server download flow below (isMP3 stays false → video)
  }

  const targetUrl = info.linkUrl || tab.url;
  const isMP3 = info.menuItemId === 'vg-download-mp3';
  chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '⚡ VidGrab: Đang xử lý...' }).catch(() => {});

  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 120_000);
    const user_cookies_b64 = await getEphemeralCookiesB64(targetUrl);
    const resp = await fetch(`${base}/api/v1/fetch-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: targetUrl, quality: isMP3 ? 'mp3_320' : 'video', remove_watermark: true, user_cookies_b64 }),
      signal: controller.signal,
    });
    const data = await resp.json();

    if (resp.ok && data.success) {
      let ext = isMP3 ? 'mp3' : 'mp4';
      if (data.is_audio_only) ext = 'mp3';
      // For MP3: prioritize local converted file, never the video stream URL
      const targetDlUrl = isMP3
        ? (data.local_mp3_path || data.local_file_path || data.direct_mp4_url)
        : (data.direct_mp4_url || data.local_file_path);
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      if (!targetDlUrl) {
        chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '❌ Không lấy được link tải' }).catch(() => {});
        return;
      }
      // E1: prefer direct (user IP), fall back to server proxy on failure
      smartDownload({ rawUrl: targetDlUrl, base, safeName, finalExt: ext, saveAs: true });
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
