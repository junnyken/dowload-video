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
  video:   { label: '▶ Video / Nhạc', cls: 'bg-green-900 text-green-400 border-green-800' },
  channel: { label: '📡 Kênh Douyin', cls: 'bg-orange-900 text-orange-400 border-orange-800' },
  generic_channel: { label: '📺 Kênh / Playlist', cls: 'bg-orange-900 text-orange-400 border-orange-800' },
  spotify_playlist: { label: '🎵 Spotify Playlist', cls: 'bg-green-900 text-green-400 border-green-800' },
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

    if (pageInfo.pageType === 'channel' || pageInfo.pageType === 'spotify_playlist' || pageInfo.pageType === 'generic_channel') {
      showTab('channel');
      document.getElementById('ch-url').textContent = pageInfo.url;
      
      if (pageInfo.pageType === 'spotify_playlist') {
         document.getElementById('ch-btn-text').textContent = 'Lấy danh sách Spotify';
         document.getElementById('ch-status').textContent = 'Nhấn nút để lấy danh sách bài hát từ Spotify.';
         if (document.getElementById('vg-source-badge')) {
            document.getElementById('vg-source-badge').textContent = 'Spotify API';
         }
      } else if (pageInfo.pageType === 'generic_channel') {
         document.getElementById('ch-btn-text').textContent = 'Quét toàn bộ Kênh/Playlist này';
         document.getElementById('ch-status').textContent = 'Hệ thống sẽ tự động tìm tất cả video trong kênh này.';
         document.getElementById('ch-count').textContent = '∞';
         document.getElementById('ch-send-btn').classList.remove('hidden');
         document.getElementById('ch-send-text').textContent = `Tải tất cả Video của Kênh`;
         if (document.getElementById('vg-source-badge')) {
            document.getElementById('vg-source-badge').textContent = 'Server Scraper';
         }
      } else if (pageInfo.pageType === 'channel') {
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
      }
    } else {
      showTab('single');
      if (pageInfo?.url?.includes('spotify.com/track/')) {
         document.getElementById('qualitySelect').value = 'mp3_320';
      }
    }
  } catch {
    pageBadge.textContent = 'Mở trang hỗ trợ';
    pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-500 border border-gray-700';
  }
}

initPopup();

// ── Live count polling khi đang ở tab Kênh ────────────────────────
let pollTimer = null;

document.getElementById('tab-channel').addEventListener('click', async () => {
  clearInterval(pollTimer);
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  if (pageInfo?.pageType === 'spotify_playlist' || pageInfo?.pageType === 'generic_channel') return;

  pollTimer = setInterval(async () => {
    if (!_activeTabId) return;
    const res = await new Promise((r) =>
      chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_COUNT' }, r)
    ).catch(() => null);
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
  const dlSubs    = document.getElementById('downloadSubs').checked;

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
      body: JSON.stringify({ url: tab.url, quality, remove_watermark: noWm, download_subs: dlSubs }),
    });
    const data = await res.json();

    if (res.ok && data.success) {
      statusMsg.textContent = 'Thành công! Đang tải file...';
      statusMsg.className = 'mt-2 text-xs text-center text-green-400 min-h-[18px]';

      let dlUrl;
      let ext = quality.includes('mp3') ? 'mp3' : 'mp4';
      if (ext === 'mp3') {
        dlUrl = data.local_mp3_path || data.direct_mp3_url || data.direct_mp4_url || data.local_file_path;
      } else {
        dlUrl = data.direct_mp4_url || data.local_file_path || data.local_mp3_path;
      }
      if (data.is_audio_only || (dlUrl && (dlUrl.endsWith('.mp3') || dlUrl.endsWith('.m4a')))) {
          ext = 'mp3';
      }
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

      if (!dlUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else if (dlUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      }

      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.${ext}`, saveAs: true });
      
      if (data.subtitle_url) {
        chrome.downloads.download({ url: data.subtitle_url, filename: `VidGrab/${safeName}.srt`, saveAs: false });
      }
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
// CHANNEL — Trigger scroll / Fetch Spotify + Send to backend
// ══════════════════════════════════════════════════════════════════
let _spotifyTracksCache = [];

document.getElementById('ch-scrape-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;
  const btn = document.getElementById('ch-scrape-btn');
  const status = document.getElementById('ch-status');
  
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);

  btn.disabled = true;
  document.getElementById('ch-spinner').style.display = 'block';
  
  if (pageInfo?.pageType === 'spotify_playlist') {
     document.getElementById('ch-btn-text').textContent = 'Đang lấy dữ liệu Spotify...';
     status.textContent = 'Đang gọi API Spotify...';
     try {
       const res = await fetch(`${API_BASE}/api/v1/fetch-spotify`, {
         method: 'POST',
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({ url: tab.url }),
       });
       const data = await res.json();
       if (data.success && data.tracks) {
         _spotifyTracksCache = data.tracks.map(t => t.search_query);
         renderSpotifyPlaylist(data);
       } else {
         throw new Error(data.detail || 'Lỗi server');
       }
     } catch (e) {
       status.textContent = `Lỗi: ${e.message}`;
       status.style.color = '#f87171';
     } finally {
       btn.disabled = false;
       document.getElementById('ch-btn-text').textContent = 'Lấy danh sách Spotify';
       document.getElementById('ch-spinner').style.display = 'none';
     }
     return;
  }
  
  if (pageInfo?.pageType === 'generic_channel') {
     // Trigger the bulk download directly for the channel URL
     document.getElementById('ch-send-btn').click();
     btn.disabled = false;
     document.getElementById('ch-spinner').style.display = 'none';
     return;
  }

  document.getElementById('ch-btn-text').textContent = 'Đang cuộn...';
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

  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  let urls = [];
  let isSpotify = pageInfo?.pageType === 'spotify_playlist';
  let isGeneric = pageInfo?.pageType === 'generic_channel';
  
  if (isSpotify) {
     urls = _spotifyTracksCache;
  } else if (isGeneric) {
     urls = [tab.url];
  } else {
     // Lấy URLs trực tiếp từ content script (videoSet - DOM based)
     const res = await new Promise((r) =>
       chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_URLS' }, r)
     ).catch(() => null);
     urls = res?.urls || [];
  }

  if (urls.length === 0) {
    status.textContent = isSpotify ? 'Chưa lấy danh sách Spotify.' : 'Chưa có video. Cuộn trang kênh trước.';
    return;
  }

  sendBtn.disabled = true;
  sendText.textContent = `⏳ Đang gửi ${urls.length} video...`;
  status.textContent = `Đang gửi ${urls.length} URL lên server...`;

  try {
    const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
         urls, 
         quality: isSpotify ? 'mp3_320' : 'video', 
         remove_watermark: true,
         channel_mode: isGeneric 
      }),
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
    sendText.textContent = `📤 Thử lại (${urls.length} video)`;
  }
});

// ── SPOTIFY INLINE UI HELPERS ──
function formatTime(sec) {
  if (!sec) return '0:00';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function renderSpotifyPlaylist(data) {
  // Hide generic elements
  document.getElementById('ch-count').parentElement.parentElement.style.display = 'none';
  document.getElementById('ch-url').parentElement.style.display = 'none';
  document.getElementById('ch-status').style.display = 'none';
  document.getElementById('ch-scrape-btn').style.display = 'none';
  
  // Update header
  const header = document.getElementById('sp-header');
  const tracklist = document.getElementById('sp-tracklist-container');
  
  document.getElementById('sp-thumb').src = data.thumbnail || 'https://via.placeholder.com/150?text=Spotify';
  document.getElementById('sp-title').textContent = data.playlist_name || data.album_name || 'Spotify Music';
  document.getElementById('sp-count').textContent = `${data.tracks.length} bài nhạc`;
  document.getElementById('sp-type-label').textContent = data.type === 'album' ? 'ALBUM • SPOTIFY' : 'PLAYLIST • SPOTIFY';
  
  header.classList.remove('hidden');
  tracklist.classList.remove('hidden');
  
  // Show Zip download button
  const sendBtn = document.getElementById('ch-send-btn');
  sendBtn.classList.remove('hidden', 'bg-green-600', 'hover:bg-green-500', 'text-white');
  sendBtn.classList.add('bg-gradient-to-r', 'from-orange-500', 'to-yellow-400', 'hover:from-orange-600', 'hover:to-yellow-500', 'text-gray-900');
  document.getElementById('ch-send-text').textContent = 'Tải tất cả (.ZIP)';
  
  tracklist.innerHTML = '';
  data.tracks.forEach((track, idx) => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-3 bg-gray-800 p-2 rounded-lg hover:bg-gray-700 transition-colors group border border-transparent hover:border-gray-600';
    
    const thumbUrl = track.thumbnail || data.thumbnail || '';
    
    row.innerHTML = `
      <img src="${thumbUrl}" class="w-10 h-10 rounded object-cover shadow-sm">
      <div class="flex-1 overflow-hidden">
         <div class="text-[13px] font-bold text-white truncate group-hover:text-green-400 transition-colors">${track.name}</div>
         <div class="text-[11px] text-gray-400 truncate">${track.artist_str || 'Unknown Artist'}</div>
      </div>
      <div class="text-[11px] text-gray-500 font-mono">${formatTime(track.duration)}</div>
      <button class="bg-green-600 hover:bg-green-500 text-white text-[10px] font-bold px-2 py-1.5 rounded flex items-center gap-1 transition-all" id="sp-dl-${idx}">
         <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
         MP3
      </button>
    `;
    tracklist.appendChild(row);
    
    document.getElementById(`sp-dl-${idx}`).addEventListener('click', (e) => {
       downloadSingleTrack(track, e.currentTarget);
    });
  });
}

async function downloadSingleTrack(track, btn) {
  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<div class="spinner !inline-block !w-3 !h-3 !border-2" style="display:inline-block"></div>';
  btn.disabled = true;
  
  try {
    const resp = await fetch(`${API_BASE}/api/v1/fetch-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: track.search_query, quality: 'mp3_320', remove_watermark: true }),
    });
    const data = await resp.json();
    if (resp.ok && data.success) {
      let dlUrl = data.local_mp3_path || data.direct_mp3_url || data.direct_mp4_url || data.local_file_path;
      const safeName = (data.title || track.name).replace(/[/\\?%*:|"<>]/g, '-');
      
      if (dlUrl && !dlUrl.includes('matbao.ai')) {
        dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=mp3`;
      } else if (dlUrl && dlUrl.startsWith('/app/downloads/')) {
        dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.mp3`;
      }
      
      chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.mp3`, saveAs: false });
      btn.innerHTML = '✅';
      btn.classList.replace('bg-green-600', 'bg-gray-600');
      btn.classList.replace('hover:bg-green-500', 'hover:bg-gray-500');
    } else {
      throw new Error('Lỗi');
    }
  } catch (err) {
    btn.innerHTML = '❌';
    btn.classList.replace('bg-green-600', 'bg-red-600');
    btn.classList.replace('hover:bg-green-500', 'hover:bg-red-500');
  }
}

