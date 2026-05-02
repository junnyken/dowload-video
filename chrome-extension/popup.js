const API_BASE = 'https://dowload-video.mk.dev.matbao.ai';
let _lastDownloadData = null; // Store last successful fetch for copy/retry

// ── Tab switcher (3 tabs) ──────────────────────────────────────────
function showTab(tab) {
  document.getElementById('single-section').style.display = tab === 'single' ? 'block' : 'none';
  document.getElementById('channel-section').classList.toggle('visible', tab === 'channel');
  document.getElementById('channel-section').style.display = tab === 'channel' ? 'block' : 'none';
  document.getElementById('history-section').style.display = tab === 'history' ? 'block' : 'none';

  ['tab-single','tab-channel','tab-history'].forEach(id => {
    const el = document.getElementById(id);
    const isActive = (id === `tab-${tab}`);
    el.classList.toggle('active', isActive);
    el.style.color = isActive ? '#1a1a1a' : '#9ca3af';
  });

  if (tab === 'history') loadHistory();
}

document.getElementById('tab-single').addEventListener('click', () => showTab('single'));
document.getElementById('tab-channel').addEventListener('click', () => showTab('channel'));
document.getElementById('tab-history').addEventListener('click', () => showTab('history'));

// ── Active tab helper ──────────────────────────────────────────────
let _activeTabId = null;
async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  _activeTabId = tab?.id;
  return tab;
}

// ── Page detection ─────────────────────────────────────────────────
const BADGE_MAP = {
  video:   { label: '▶ Video / Nhạc', cls: 'bg-green-900 text-green-400 border-green-800' },
  channel: { label: '📡 Kênh Douyin', cls: 'bg-orange-900 text-orange-400 border-orange-800' },
  generic_channel: { label: '📺 Kênh / Playlist', cls: 'bg-orange-900 text-orange-400 border-orange-800' },
  spotify_playlist: { label: '🎵 Spotify Playlist', cls: 'bg-green-900 text-green-400 border-green-800' },
  instagram: { label: '📸 Instagram', cls: 'bg-pink-900 text-pink-400 border-pink-800' },
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

    let pageInfo;
    try { pageInfo = await tryGetPageInfo(tab.id); }
    catch {
      try { await injectContentScript(tab.id); pageInfo = await tryGetPageInfo(tab.id); }
      catch { showReloadBanner(tab.id); pageBadge.textContent = 'Cần reload trang';
        pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-yellow-900 text-yellow-400 border border-yellow-700'; return; }
    }

    const bm = BADGE_MAP[pageInfo.pageType];
    if (bm) { pageBadge.textContent = bm.label; pageBadge.className = `text-xs px-2 py-0.5 rounded-full border ${bm.cls}`; }

    if (pageInfo.pageType === 'channel' || pageInfo.pageType === 'spotify_playlist' || pageInfo.pageType === 'generic_channel') {
      showTab('channel');
      document.getElementById('ch-url').textContent = pageInfo.url;
      if (pageInfo.pageType === 'spotify_playlist') {
        document.getElementById('ch-btn-text').textContent = 'Lấy danh sách Spotify';
        document.getElementById('ch-status').textContent = 'Nhấn nút để lấy danh sách bài hát từ Spotify.';
      } else if (pageInfo.pageType === 'generic_channel') {
        document.getElementById('ch-btn-text').textContent = 'Quét toàn bộ Kênh/Playlist này';
        document.getElementById('ch-status').textContent = 'Hệ thống sẽ tự động tìm tất cả video trong kênh này.';
        document.getElementById('ch-count').textContent = '∞';
        document.getElementById('ch-send-btn').classList.remove('hidden');
        document.getElementById('ch-send-text').textContent = 'Tải tất cả Video của Kênh';
      } else if (pageInfo.pageType === 'channel') {
        const countResp = await new Promise((r) => chrome.tabs.sendMessage(tab.id, { type: 'VG_GET_COUNT' }, r));
        const count = countResp?.count || 0;
        document.getElementById('ch-count').textContent = count;
        if (count > 0) {
          document.getElementById('ch-send-btn').classList.remove('hidden');
          document.getElementById('ch-send-text').textContent = `Gửi tải ${count} video`;
          document.getElementById('ch-status').textContent = `Đã thu thập ${count} video. Cuộn thêm hoặc gửi ngay.`;
          document.getElementById('ch-status').style.color = '#34d399';
        } else {
          document.getElementById('ch-status').textContent = 'Panel live đang theo dõi DOM — cuộn trang để load video.';
        }
      }
    } else {
      showTab('single');
      if (pageInfo?.url?.includes('spotify.com/track/')) document.getElementById('qualitySelect').value = 'mp3_320';
    }
  } catch {
    pageBadge.textContent = 'Mở trang hỗ trợ';
    pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-500 border border-gray-700';
  }
}
initPopup();

// ── Live count polling ─────────────────────────────────────────────
let pollTimer = null;
document.getElementById('tab-channel').addEventListener('click', async () => {
  clearInterval(pollTimer);
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  if (pageInfo?.pageType === 'spotify_playlist' || pageInfo?.pageType === 'generic_channel') return;
  pollTimer = setInterval(async () => {
    if (!_activeTabId) return;
    const res = await new Promise((r) => chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_COUNT' }, r)).catch(() => null);
    const count = res?.count || 0;
    document.getElementById('ch-count').textContent = count;
    if (count > 0) { document.getElementById('ch-send-btn').classList.remove('hidden'); document.getElementById('ch-send-text').textContent = `Gửi tải ${count} video`; }
  }, 1500);
});
document.getElementById('tab-single').addEventListener('click', () => clearInterval(pollTimer));
window.addEventListener('unload', () => clearInterval(pollTimer));

// ══════════════════════════════════════════════════════════════════
// SINGLE VIDEO — Download + Preview + Progress
// ══════════════════════════════════════════════════════════════════
document.getElementById('downloadBtn').addEventListener('click', async () => {
  const btnText = document.getElementById('btnText'), spinner = document.getElementById('spinner'),
    iconDl = document.getElementById('icon-download'), statusMsg = document.getElementById('statusMsg'),
    btn = document.getElementById('downloadBtn'), quality = document.getElementById('qualitySelect').value,
    noWm = document.getElementById('removeWatermark').checked, dlSubs = document.getElementById('downloadSubs').checked;

  btn.disabled = true; btn.classList.add('opacity-70'); iconDl.style.display = 'none'; spinner.style.display = 'block';
  btnText.textContent = 'Đang bóc tách...'; statusMsg.textContent = 'Server đang xử lý...';
  statusMsg.className = 'mt-2 text-xs text-center text-orange-400 min-h-[18px]';

  try {
    const tab = await getActiveTab();
    if (!tab?.url || tab.url.startsWith('chrome://')) throw new Error('Không thể lấy link ở trang này.');

    const res = await fetch(`${API_BASE}/api/v1/fetch-link`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: tab.url, quality, remove_watermark: noWm, download_subs: dlSubs }),
    });
    const data = await res.json();

    if (res.ok && data.success) {
      _lastDownloadData = data;
      // Show preview card
      showPreviewCard(data, tab.url);
      showMultiFormat(data);

      // Hide error card if visible
      const errCard = document.getElementById('error-card');
      if (errCard) errCard.classList.add('hidden');

      statusMsg.textContent = 'Thành công! Đang tải file...';
      statusMsg.className = 'mt-2 text-xs text-center text-green-400 min-h-[18px]';

      let dlUrl, ext = quality.includes('mp3') ? 'mp3' : 'mp4';
      if (ext === 'mp3') { dlUrl = data.local_mp3_path || data.direct_mp3_url || data.direct_mp4_url || data.local_file_path; }
      else { dlUrl = data.direct_mp4_url || data.local_file_path || data.local_mp3_path; }
      if (data.is_audio_only || (dlUrl && (dlUrl.endsWith('.mp3') || dlUrl.endsWith('.m4a')))) ext = 'mp3';
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      if (!dlUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (dlUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      }

      _lastDownloadData._resolvedUrl = dlUrl;
      _lastDownloadData._ext = ext;
      _lastDownloadData._safeName = safeName;

      // Show progress bar
      document.getElementById('dl-progress').classList.remove('hidden');
      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.${ext}`, saveAs: true });

      if (data.subtitle_url) chrome.downloads.download({ url: data.subtitle_url, filename: `VidGrab/${safeName}.srt`, saveAs: false });

      // Save to history
      chrome.runtime.sendMessage({ type: 'VG_SAVE_HISTORY', record: {
        title: data.title || 'Video', thumbnail: data.thumbnail_url || '', url: tab.url,
        quality: ext === 'mp3' ? 'MP3 320k' : quality.replace('video_','').toUpperCase() || 'HD',
        fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '',
      }});
    } else { throw new Error(data.detail || 'Server báo lỗi.'); }
  } catch (err) {
    statusMsg.textContent = `Lỗi: ${err.message}`;
    statusMsg.className = 'mt-2 text-xs text-center text-red-400 min-h-[18px]';
    // Show error card with retry
    const errCard = document.getElementById('error-card');
    const errText = document.getElementById('error-text');
    if (errCard && errText) {
      errText.textContent = err.message;
      errCard.classList.remove('hidden');
    }
  } finally {
    btn.disabled = false; btn.classList.remove('opacity-70'); iconDl.style.display = 'block';
    spinner.style.display = 'none'; btnText.textContent = 'Tải Video Ngay';
  }
});

// ── Retry button ───────────────────────────────────────────────────
document.getElementById('retry-btn')?.addEventListener('click', () => {
  document.getElementById('error-card')?.classList.add('hidden');
  document.getElementById('downloadBtn')?.click();
});

// ── Preview Card ───────────────────────────────────────────────────
function detectPlatform(url) {
  if (!url) return '';
  if (url.includes('tiktok.com')) return 'TikTok';
  if (url.includes('youtube.com') || url.includes('youtu.be')) return 'YouTube';
  if (url.includes('facebook.com')) return 'Facebook';
  if (url.includes('instagram.com')) return 'Instagram';
  if (url.includes('douyin.com')) return 'Douyin';
  if (url.includes('spotify.com')) return 'Spotify';
  return '';
}

function showPreviewCard(data, pageUrl) {
  const card = document.getElementById('preview-card');
  if (!card) return;
  const thumb = document.getElementById('preview-thumb');
  const title = document.getElementById('preview-title');
  const dur = document.getElementById('preview-duration');
  const size = document.getElementById('preview-size');
  const platform = document.getElementById('preview-platform');

  if (data.thumbnail_url) thumb.src = data.thumbnail_url;
  title.textContent = data.title || 'Video';
  if (data.duration > 0) {
    const m = Math.floor(data.duration / 60), s = data.duration % 60;
    dur.textContent = `${m}:${String(s).padStart(2,'0')}`;
  }
  if (data.file_size_mb) size.textContent = `${data.file_size_mb} MB`;
  if (platform) platform.textContent = detectPlatform(pageUrl || data.original_url);
  card.classList.remove('hidden');
}

// ── Action buttons (Copy link, thumbnail, open web) ────────────────
document.getElementById('action-copy-link')?.addEventListener('click', async () => {
  const btn = document.getElementById('action-copy-link');
  if (!_lastDownloadData?._resolvedUrl) return;
  try {
    await navigator.clipboard.writeText(_lastDownloadData._resolvedUrl);
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = '📋 Copy link'; }, 2000);
  } catch { btn.textContent = '❌ Lỗi'; setTimeout(() => { btn.textContent = '📋 Copy link'; }, 2000); }
});

document.getElementById('action-open-web')?.addEventListener('click', async () => {
  const tab = await getActiveTab();
  if (tab?.url) chrome.tabs.create({ url: `${API_BASE}?url=${encodeURIComponent(tab.url)}` });
});

document.getElementById('action-dl-thumb')?.addEventListener('click', () => {
  if (!_lastDownloadData?.thumbnail_url) return;
  const safeName = (_lastDownloadData.title || 'thumbnail').replace(/[/\\?%*:|"<>]/g, '-');
  chrome.downloads.download({ url: _lastDownloadData.thumbnail_url, filename: `VidGrab/${safeName}_thumb.jpg`, saveAs: true });
  const btn = document.getElementById('action-dl-thumb');
  btn.textContent = '✅ Đang tải'; setTimeout(() => { btn.textContent = '🖼️ Ảnh bìa'; }, 2000);
});

// ── Download Progress Listener (with speed) ───────────────────────
let _dlStartTime = 0;
let _dlLastReceived = 0;
let _dlLastTime = 0;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== 'VG_DOWNLOAD_PROGRESS') return;
  const wrap = document.getElementById('dl-progress');
  const fill = document.getElementById('dl-progress-fill');
  const pctEl = document.getElementById('dl-progress-pct');
  const info = document.getElementById('dl-progress-info');
  const speedEl = document.getElementById('dl-progress-speed');
  if (!wrap) return;

  if (msg.state === 'complete') {
    fill.style.width = '100%'; pctEl.textContent = '✅ Xong!';
    info.textContent = ''; if (speedEl) speedEl.textContent = '';
    wrap.style.borderColor = '#22c55e';
    // Show success animation
    const successCheck = document.getElementById('success-check');
    if (successCheck) { successCheck.classList.add('show'); setTimeout(() => successCheck.classList.remove('show'), 3000); }
    setTimeout(() => { wrap.classList.add('hidden'); wrap.style.borderColor = ''; }, 4000);
  } else if (msg.state === 'downloading' && msg.progress >= 0) {
    fill.style.width = msg.progress + '%'; pctEl.textContent = msg.progress + '%';
    const now = Date.now();
    if (!_dlStartTime) { _dlStartTime = now; _dlLastReceived = 0; _dlLastTime = now; }
    if (msg.total > 0) {
      const mb = (msg.received / 1048576).toFixed(1);
      const totalMb = (msg.total / 1048576).toFixed(1);
      info.textContent = `${mb} / ${totalMb} MB`;
    }
    // Calculate speed (MB/s)
    if (speedEl && msg.received > 0 && now - _dlLastTime > 400) {
      const deltaBytes = msg.received - _dlLastReceived;
      const deltaSec = (now - _dlLastTime) / 1000;
      if (deltaSec > 0) {
        const speedMBs = (deltaBytes / 1048576 / deltaSec).toFixed(1);
        speedEl.textContent = `${speedMBs} MB/s`;
      }
      _dlLastReceived = msg.received;
      _dlLastTime = now;
    }
  } else if (msg.state === 'error') {
    pctEl.textContent = '❌ Lỗi'; fill.style.background = '#ef4444';
    _dlStartTime = 0;
  }
});

// ══════════════════════════════════════════════════════════════════
// HISTORY TAB
// ══════════════════════════════════════════════════════════════════
function loadHistory() {
  chrome.runtime.sendMessage({ type: 'VG_GET_HISTORY' }, (resp) => {
    const list = document.getElementById('history-list');
    const empty = document.getElementById('history-empty');
    const history = resp?.history || [];
    if (history.length === 0) { list.innerHTML = ''; list.appendChild(empty); empty.style.display = 'block'; return; }

    list.innerHTML = '';
    history.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'history-item';
      const thumbSrc = item.thumbnail || 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" fill="%23374151"><rect width="40" height="30" rx="4"/></svg>';
      const timeAgo = getTimeAgo(item.timestamp);
      row.innerHTML = `
        <img src="${thumbSrc}" class="w-10 h-8 rounded object-cover bg-gray-800 flex-shrink-0">
        <div class="flex-1 overflow-hidden">
          <div class="text-[11px] font-bold text-white truncate">${item.title || 'Video'}</div>
          <div class="text-[9px] text-gray-500">${item.quality || ''} ${item.fileSize ? '• ' + item.fileSize : ''} • ${timeAgo}</div>
        </div>
      `;
      list.appendChild(row);
    });
  });
}

function getTimeAgo(ts) {
  const diff = Date.now() - ts;
  if (diff < 60000) return 'Vừa xong';
  if (diff < 3600000) return Math.floor(diff / 60000) + ' phút trước';
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' giờ trước';
  return Math.floor(diff / 86400000) + ' ngày trước';
}

document.getElementById('clear-history-btn').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'VG_CLEAR_HISTORY' }, () => loadHistory());
});

// ══════════════════════════════════════════════════════════════════
// CHANNEL — Trigger scroll / Fetch Spotify + Send to backend
// ══════════════════════════════════════════════════════════════════
let _spotifyTracksCache = [];

document.getElementById('ch-scrape-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;
  const btn = document.getElementById('ch-scrape-btn');
  const status = document.getElementById('ch-status');
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  btn.disabled = true; document.getElementById('ch-spinner').style.display = 'block';

  if (pageInfo?.pageType === 'spotify_playlist') {
    document.getElementById('ch-btn-text').textContent = 'Đang lấy dữ liệu Spotify...';
    status.textContent = 'Đang gọi API Spotify...';
    try {
      const res = await fetch(`${API_BASE}/api/v1/fetch-spotify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: tab.url }) });
      const data = await res.json();
      if (data.success && data.tracks) { _spotifyTracksCache = data.tracks.map(t => t.search_query); renderSpotifyPlaylist(data); }
      else throw new Error(data.detail || 'Lỗi server');
    } catch (e) { status.textContent = `Lỗi: ${e.message}`; status.style.color = '#f87171'; }
    finally { btn.disabled = false; document.getElementById('ch-btn-text').textContent = 'Lấy danh sách Spotify'; document.getElementById('ch-spinner').style.display = 'none'; }
    return;
  }

  if (pageInfo?.pageType === 'generic_channel') {
    document.getElementById('ch-send-btn').click(); btn.disabled = false; document.getElementById('ch-spinner').style.display = 'none'; return;
  }

  document.getElementById('ch-btn-text').textContent = 'Đang cuộn...';
  status.textContent = 'Panel đang auto-scroll và intercept API Douyin...';
  try { await chrome.tabs.sendMessage(_activeTabId, { type: 'VG_TRIGGER_SCROLL' }); }
  catch { status.textContent = 'Lỗi: Mở trang kênh Douyin trước rồi thử lại.'; status.style.color = '#f87171'; }
  setTimeout(() => { btn.disabled = false; document.getElementById('ch-btn-text').textContent = 'Cuộn thêm'; document.getElementById('ch-spinner').style.display = 'none'; }, 120000);
});

document.getElementById('ch-send-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;
  const sendBtn = document.getElementById('ch-send-btn'), sendText = document.getElementById('ch-send-text'),
    status = document.getElementById('ch-status'), batchDiv = document.getElementById('ch-batch-info');
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  let urls = [], isSpotify = pageInfo?.pageType === 'spotify_playlist', isGeneric = pageInfo?.pageType === 'generic_channel';

  if (isSpotify) urls = _spotifyTracksCache;
  else if (isGeneric) urls = [tab.url];
  else { const res = await new Promise((r) => chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_URLS' }, r)).catch(() => null); urls = res?.urls || []; }

  if (urls.length === 0) { status.textContent = isSpotify ? 'Chưa lấy danh sách Spotify.' : 'Chưa có video. Cuộn trang kênh trước.'; return; }
  sendBtn.disabled = true; sendText.textContent = `⏳ Đang gửi ${urls.length} video...`; status.textContent = `Đang gửi ${urls.length} URL lên server...`;

  try {
    const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, quality: isSpotify ? 'mp3_320' : 'video', remove_watermark: true, channel_mode: isGeneric }) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.success) {
      status.textContent = `✅ Đã xếp hàng ${urls.length} video thành công!`; status.style.color = '#4ade80';
      sendText.textContent = '✅ Đã gửi'; batchDiv.classList.remove('hidden');
      batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;
      chrome.tabs.create({ url: `${API_BASE}?batch=${data.batch_id}` });
    } else throw new Error(data.detail || 'Lỗi không xác định');
  } catch (err) {
    status.textContent = `❌ Lỗi: ${err.message}`; status.style.color = '#f87171';
    sendBtn.disabled = false; sendText.textContent = `📤 Thử lại (${urls.length} video)`;
  }
});

// ── SPOTIFY UI HELPERS ─────────────────────────────────────────────
function formatTime(sec) { if (!sec) return '0:00'; return `${Math.floor(sec/60)}:${(sec%60).toString().padStart(2,'0')}`; }

function renderSpotifyPlaylist(data) {
  document.getElementById('ch-count').parentElement.parentElement.style.display = 'none';
  document.getElementById('ch-url').parentElement.style.display = 'none';
  document.getElementById('ch-status').style.display = 'none';
  document.getElementById('ch-scrape-btn').style.display = 'none';
  const header = document.getElementById('sp-header'), tracklist = document.getElementById('sp-tracklist-container');
  document.getElementById('sp-thumb').src = data.thumbnail || '';
  document.getElementById('sp-title').textContent = data.playlist_name || data.album_name || 'Spotify Music';
  document.getElementById('sp-count').textContent = `${data.tracks.length} bài nhạc`;
  document.getElementById('sp-type-label').textContent = data.type === 'album' ? 'ALBUM • SPOTIFY' : 'PLAYLIST • SPOTIFY';
  header.classList.remove('hidden'); tracklist.classList.remove('hidden');
  const sendBtn = document.getElementById('ch-send-btn');
  sendBtn.classList.remove('hidden','bg-green-600','hover:bg-green-500','text-white');
  sendBtn.classList.add('bg-gradient-to-r','from-orange-500','to-yellow-400','hover:from-orange-600','hover:to-yellow-500','text-gray-900');
  document.getElementById('ch-send-text').textContent = 'Tải tất cả (.ZIP)';
  tracklist.innerHTML = '';
  data.tracks.forEach((track, idx) => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2 bg-gray-800 p-1.5 rounded-lg hover:bg-gray-700 transition-colors group border border-transparent hover:border-gray-600';
    row.innerHTML = `<img src="${track.thumbnail || data.thumbnail || ''}" class="w-8 h-8 rounded object-cover shadow-sm flex-shrink-0">
      <div class="flex-1 overflow-hidden min-w-0"><div class="text-[11px] font-bold text-white truncate group-hover:text-green-400 transition-colors">${track.name}</div>
      <div class="text-[10px] text-gray-400 truncate">${track.artist_str || 'Unknown'}</div></div>
      <div class="text-[10px] text-gray-500 font-mono flex-shrink-0">${formatTime(track.duration)}</div>
      <button class="bg-green-600 hover:bg-green-500 text-white text-[9px] font-bold px-2 py-1 rounded flex-shrink-0 transition-all" id="sp-dl-${idx}">MP3</button>`;
    tracklist.appendChild(row);
    document.getElementById(`sp-dl-${idx}`).addEventListener('click', (e) => downloadSingleTrack(track, e.currentTarget));
  });
}

async function downloadSingleTrack(track, btn) {
  const originalHtml = btn.innerHTML; btn.innerHTML = '⏳'; btn.disabled = true;
  try {
    const resp = await fetch(`${API_BASE}/api/v1/fetch-link`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: track.search_query, quality: 'mp3_320', remove_watermark: true }) });
    const data = await resp.json();
    if (resp.ok && data.success) {
      let dlUrl = data.local_file_path || data.local_mp3_path || data.direct_mp3_url || data.direct_mp4_url;
      if (!dlUrl) throw new Error('No download URL');
      const safeName = (data.title || track.name).replace(/[/\\?%*:|"<>]/g, '-');
      
      // Local file on server (starts with downloads/ or /app/downloads/)
      if (dlUrl.startsWith('downloads/') || dlUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.mp3`;
      }
      // Remote URL — proxy it
      else if (dlUrl.startsWith('http') && !dlUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=mp3`;
      }
      
      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.mp3`, saveAs: false });
      btn.innerHTML = '✅'; btn.classList.replace('bg-green-600','bg-gray-600');
      
      // Save to history
      chrome.runtime.sendMessage({ type: 'VG_SAVE_HISTORY', record: {
        title: data.title || track.name, thumbnail: track.thumbnail || '', url: track.search_query,
        quality: 'MP3', fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '',
      }});
    } else throw new Error(data.detail || 'Server error');
  } catch(e) { btn.innerHTML = '❌'; btn.title = e.message; btn.classList.replace('bg-green-600','bg-red-600'); }
}

// ══════════════════════════════════════════════════════════════════
// DARK / LIGHT MODE TOGGLE
// ══════════════════════════════════════════════════════════════════
const themeBtn = document.getElementById('theme-toggle');
chrome.storage.local.get('vg_theme', (res) => {
  if (res.vg_theme === 'light') {
    document.body.classList.add('light-mode');
    themeBtn.textContent = '☀️';
  }
});
themeBtn.addEventListener('click', () => {
  const isLight = document.body.classList.toggle('light-mode');
  themeBtn.textContent = isLight ? '☀️' : '🌙';
  chrome.storage.local.set({ vg_theme: isLight ? 'light' : 'dark' });
});

// ══════════════════════════════════════════════════════════════════
// MULTI-FORMAT POPUP — Show all available qualities
// ══════════════════════════════════════════════════════════════════
function showMultiFormat(data) {
  const panel = document.getElementById('formats-panel');
  const list = document.getElementById('formats-list');
  if (!panel || !list) return;

  const formats = data.available_formats || [];
  if (formats.length === 0) return;

  // original_url is the YouTube/platform URL used to re-fetch for server-side merge
  const videoUrl = data.original_url || '';

  list.innerHTML = '';
  formats.forEach((fmt) => {
    const row = document.createElement('div');
    row.className = 'fmt-item';

    const isVideo = fmt.type === 'video';
    const icon = isVideo ? '🎬' : '🎵';
    const label = fmt.label || (isVideo ? `${fmt.height}p` : 'Audio');
    // requires_merge: true means no direct URL — server must download+merge
    const needsMerge = fmt.requires_merge || !fmt.url;
    const mergeBadge = needsMerge ? `<span class="text-[9px] px-1.5 py-0.5 rounded bg-blue-800 text-blue-200">GHÉP TỆP</span>` : '';
    const codecBadge = fmt.codec ? `<span class="text-[9px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">${fmt.codec}</span>` : '';
    // Backend returns filesize_mb (not file_size_mb)
    const sizeMB = fmt.filesize_mb || fmt.file_size_mb || 0;
    const sizeBadge = sizeMB ? `<span class="text-[10px] text-orange-400 font-bold">${sizeMB} MB</span>` : '';

    row.innerHTML = `
      <div class="flex items-center gap-2">
        <span class="text-sm">${icon}</span>
        <div>
          <div class="text-[11px] font-bold text-white">${label}</div>
          <div class="flex gap-1 mt-0.5">${mergeBadge}${codecBadge} ${sizeBadge}</div>
        </div>
      </div>
      <span class="text-[10px] text-orange-400 font-bold hover:text-orange-300">⬇</span>
    `;

    row.addEventListener('click', async () => {
      const dlBtn = row.querySelector('span:last-child');
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');
      const ext = isVideo ? 'mp4' : 'mp3';

      if (needsMerge) {
        // Trigger server-side download: backend downloads+merges the HD stream
        dlBtn.textContent = '⏳';
        try {
          const quality = isVideo ? `video_${fmt.height}` : 'mp3_320';
          const resp = await fetch(`${API_BASE}/api/v1/fetch-link`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: videoUrl, quality, remove_watermark: true }),
          });
          const result = await resp.json();
          if (result.success) {
            let finalUrl;
            if (result.local_file_path) {
              finalUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(result.local_file_path)}&filename=${encodeURIComponent(safeName)}.${ext}`;
            } else if (result.direct_mp4_url) {
              finalUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(result.direct_mp4_url)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
            }
            if (finalUrl) {
              chrome.downloads.download({ url: finalUrl, filename: `VidGrab/${safeName}_${label}.${ext}`, saveAs: true });
              dlBtn.textContent = '✅';
            } else {
              dlBtn.textContent = '❌';
            }
          } else {
            dlBtn.textContent = '❌';
          }
        } catch {
          dlBtn.textContent = '❌';
        }
        return;
      }

      // Direct URL download (non-merge formats)
      const dlUrl = fmt.url;
      let finalUrl;
      if (dlUrl && !dlUrl.includes('matbao.ai')) {
        finalUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (dlUrl?.startsWith('/app/downloads/')) {
        finalUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      } else {
        finalUrl = dlUrl;
      }
      if (finalUrl) {
        chrome.downloads.download({ url: finalUrl, filename: `VidGrab/${safeName}_${label}.${ext}`, saveAs: true });
        dlBtn.textContent = '✅';
      }
    });

    list.appendChild(row);
  });

  panel.classList.remove('hidden');
}

// Close formats panel
document.getElementById('formats-close')?.addEventListener('click', () => {
  document.getElementById('formats-panel')?.classList.add('hidden');
});
