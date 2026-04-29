const API_BASE = 'https://dowload-video-trieunt.dev.matbao.ai';

// ── Tab switcher ───────────────────────────────────────────────────
function showTab(tab) {
  const isSingle = tab === 'single';
  document.getElementById('single-section').style.display = isSingle ? 'block' : 'none';
  document.getElementById('channel-section').classList.toggle('visible', !isSingle);

  const tS = document.getElementById('tab-single');
  const tC = document.getElementById('tab-channel');
  tS.classList.toggle('active', isSingle);
  tS.style.color = isSingle ? '#1a1a1a' : '#9ca3af';
  tC.classList.toggle('active', !isSingle);
  tC.style.color = !isSingle ? '#1a1a1a' : '#9ca3af';
}

document.getElementById('tab-single').addEventListener('click', () => showTab('single'));
document.getElementById('tab-channel').addEventListener('click', () => showTab('channel'));

// ── Active tab helper ──────────────────────────────────────────────
let _activeTabId = null;
async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  _activeTabId = tab?.id;
  return tab;
}

// ── Page detection + populate channel UI ──────────────────────────
const BADGE_MAP = {
  video:   { label: '▶ Video', cls: 'bg-green-900 text-green-400 border-green-800' },
  channel: { label: '📡 Kênh Douyin', cls: 'bg-orange-900 text-orange-400 border-orange-800' },
};

async function tryGetPageInfo(tabId) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, { type: 'VG_GET_PAGE_INFO' }, (resp) => {
      if (chrome.runtime.lastError || !resp) reject(chrome.runtime.lastError);
      else resolve(resp);
    });
  });
}

async function injectContentScript(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  await new Promise((r) => setTimeout(r, 600));
}

function showReloadBanner(tabId) {
  document.getElementById('reload-banner').classList.remove('hidden');
  document.getElementById('reload-btn').addEventListener('click', () => {
    chrome.tabs.reload(tabId);
    window.close();
  });
}

async function initPopup() {
  const pageBadge = document.getElementById('page-badge');

  try {
    const tab = await getActiveTab();
    if (!tab?.id) throw new Error('no tab');

    // Thử lấy page info từ content script
    let pageInfo;
    try {
      pageInfo = await tryGetPageInfo(tab.id);
    } catch {
      // Content script chưa inject (tab mở trước khi cài extension)
      // → inject động rồi thử lại
      try {
        await injectContentScript(tab.id);
        pageInfo = await tryGetPageInfo(tab.id);
      } catch {
        // Trang không hỗ trợ inject (chrome://, pdf, ...) → hiện reload banner
        showReloadBanner(tab.id);
        pageBadge.textContent = 'Cần reload trang';
        pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-yellow-900 text-yellow-400 border border-yellow-700';
        return;
      }
    }

    const bm = BADGE_MAP[pageInfo.pageType];
    if (bm) {
      pageBadge.textContent = bm.label;
      pageBadge.className = `text-xs px-2 py-0.5 rounded-full border ${bm.cls}`;
    }

    if (pageInfo.pageType === 'channel') {
      showTab('channel');
      document.getElementById('ch-url').textContent = pageInfo.url;

      // Lấy count trực tiếp từ content script (videoSet trong DOM)
      const countResp = await new Promise((r) =>
        chrome.tabs.sendMessage(tab.id, { type: 'VG_GET_COUNT' }, r)
      );
      const count = countResp?.count || 0;
      document.getElementById('ch-count').textContent = count;
      if (count > 0) {
        document.getElementById('ch-send-btn').classList.remove('hidden');
        document.getElementById('ch-send-text').textContent = `Gửi tải ${count} video`;
        document.getElementById('ch-status').textContent =
          `Đã thu thập ${count} video. Cuộn thêm hoặc gửi ngay.`;
        document.getElementById('ch-status').style.color = '#34d399';
      } else {
        document.getElementById('ch-status').textContent =
          'Panel live đang theo dõi DOM — cuộn trang để load video.';
      }
    } else {
      showTab('single');
    }
  } catch {
    pageBadge.textContent = 'Mở trang hỗ trợ';
    pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-500 border border-gray-700';
  }
}

initPopup();

// ── Live count polling khi đang ở tab Kênh ────────────────────────
let pollTimer = null;

document.getElementById('tab-channel').addEventListener('click', () => {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!_activeTabId) return;
    const res = await new Promise((r) =>
      chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_COUNT' }, r)
    );
    const count = res?.count || 0;
    document.getElementById('ch-count').textContent = count;
    if (count > 0) {
      document.getElementById('ch-send-btn').classList.remove('hidden');
      document.getElementById('ch-send-text').textContent = `Gửi tải ${count} video`;
    }
  }, 1500);
});

document.getElementById('tab-single').addEventListener('click', () => {
  clearInterval(pollTimer);
});

window.addEventListener('unload', () => clearInterval(pollTimer));

// ══════════════════════════════════════════════════════════════════
// SINGLE VIDEO — Download
// ══════════════════════════════════════════════════════════════════
document.getElementById('downloadBtn').addEventListener('click', async () => {
  const btnText   = document.getElementById('btnText');
  const spinner   = document.getElementById('spinner');
  const iconDl    = document.getElementById('icon-download');
  const statusMsg = document.getElementById('statusMsg');
  const btn       = document.getElementById('downloadBtn');
  const quality   = document.getElementById('qualitySelect').value;
  const noWm      = document.getElementById('removeWatermark').checked;

  btn.disabled = true;
  btn.classList.add('opacity-70');
  iconDl.style.display = 'none';
  spinner.style.display = 'block';
  btnText.textContent = 'Đang bóc tách...';
  statusMsg.textContent = 'Server đang xử lý...';
  statusMsg.className = 'mt-2 text-xs text-center text-orange-400 min-h-[18px]';

  try {
    const tab = await getActiveTab();
    if (!tab?.url || tab.url.startsWith('chrome://'))
      throw new Error('Không thể lấy link ở trang này.');

    const res = await fetch(`${API_BASE}/api/v1/fetch-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: tab.url, quality, remove_watermark: noWm }),
    });
    const data = await res.json();

    if (res.ok && data.success) {
      statusMsg.textContent = 'Thành công! Đang tải file...';
      statusMsg.className = 'mt-2 text-xs text-center text-green-400 min-h-[18px]';

      let dlUrl = data.direct_mp4_url || data.local_mp3_path || data.local_file_path;
      const ext = quality.includes('mp3') ? 'mp3' : 'mp4';
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      if (!dlUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (dlUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      }

      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.${ext}`, saveAs: true });
    } else {
      throw new Error(data.detail || 'Server báo lỗi.');
    }
  } catch (err) {
    statusMsg.textContent = `Lỗi: ${err.message}`;
    statusMsg.className = 'mt-2 text-xs text-center text-red-400 min-h-[18px]';
  } finally {
    btn.disabled = false;
    btn.classList.remove('opacity-70');
    iconDl.style.display = 'block';
    spinner.style.display = 'none';
    btnText.textContent = 'Tải Video Ngay';
  }
});

// ══════════════════════════════════════════════════════════════════
// CHANNEL — Trigger scroll + Send to backend
// ══════════════════════════════════════════════════════════════════
document.getElementById('ch-scrape-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;
  const btn = document.getElementById('ch-scrape-btn');
  const status = document.getElementById('ch-status');

  btn.disabled = true;
  document.getElementById('ch-btn-text').textContent = 'Đang cuộn...';
  document.getElementById('ch-spinner').style.display = 'block';
  status.textContent = 'Panel đang auto-scroll và intercept API Douyin...';

  try {
    await chrome.tabs.sendMessage(_activeTabId, { type: 'VG_TRIGGER_SCROLL' });
  } catch {
    status.textContent = 'Lỗi: Mở trang kênh Douyin trước rồi thử lại.';
    status.style.color = '#f87171';
  }

  // Sau 120s tự unlock nút (panel tự điều khiển scraping)
  setTimeout(() => {
    btn.disabled = false;
    document.getElementById('ch-btn-text').textContent = 'Cuộn thêm';
    document.getElementById('ch-spinner').style.display = 'none';
  }, 120000);
});

document.getElementById('ch-send-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;

  const sendBtn  = document.getElementById('ch-send-btn');
  const sendText = document.getElementById('ch-send-text');
  const status   = document.getElementById('ch-status');
  const batchDiv = document.getElementById('ch-batch-info');

  // Lấy URLs trực tiếp từ content script (videoSet - DOM based)
  const res = await new Promise((r) =>
    chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_URLS' }, r)
  );
  const urls = res?.urls || [];

  if (urls.length === 0) {
    status.textContent = 'Chưa có video. Cuộn trang kênh trước.';
    return;
  }

  sendBtn.disabled = true;
  sendText.textContent = `⏳ Đang gửi ${urls.length} video...`;
  status.textContent = `Đang gửi ${urls.length} URL lên server...`;

  try {
    const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, quality: 'video', remove_watermark: true }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.success) {
      status.textContent = `✅ Đã xếp hàng ${urls.length} video thành công!`;
      status.style.color = '#4ade80';
      sendText.textContent = '✅ Đã gửi';
      batchDiv.classList.remove('hidden');
      batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;

      chrome.tabs.create({ url: `${API_BASE}?batch=${data.batch_id}` });
    } else {
      throw new Error(data.detail || 'Lỗi không xác định');
    }
  } catch (err) {
    status.textContent = `❌ Lỗi: ${err.message}`;
    status.style.color = '#f87171';
    sendBtn.disabled = false;
    sendText.textContent = `📤 Thử lại (${videos.length} video)`;
  }
});
