const DEFAULT_API_BASE = 'https://dvid-api.cmc-1.vibenode.matbao.ai';
const WEB_BASE = 'https://dvid.cmc-1.vibenode.matbao.ai';
let API_BASE = DEFAULT_API_BASE;

// Video titles / creator names / platform strings come from the source
// platform (anyone can name a public video anything) and get interpolated
// into innerHTML for history/archive rows — escape them so a malicious
// title can't run script in the popup (which holds the user's auth token).
function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// The footer's real version only used to appear after opening Settings
// (showVersionInfo() also fires a live backend health check, so it isn't
// called on every popup open) — leaving the hardcoded "v5.0.0" placeholder
// from popup.html visible by default, which reads as a stale/old install.
{
  const footerVer = document.getElementById('footer-version');
  if (footerVer) footerVer.textContent = `v${chrome.runtime.getManifest().version}`;
}
let _lastDownloadData = null;
let _lastNoWm = false;

// ── Channel: qty selector state ───────────────────────────────────
let _chMaxVideos = 10;

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.ch-qty-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _chMaxVideos = parseInt(btn.dataset.qty);
      document.getElementById('ch-qty-custom').value = '';
      document.querySelectorAll('.ch-qty-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
  document.getElementById('ch-qty-custom')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value);
    if (v > 0) {
      _chMaxVideos = v;
      document.querySelectorAll('.ch-qty-btn').forEach(b => b.classList.remove('active'));
    }
  });
});

// ── Channel: discovery & job polling ─────────────────────────────
let _chDiscoveryTimer = null, _chDiscoverySec = 0, _chJobPollTimer = null, _chBatchId = null;

function startChannelDiscovery() {
  _chDiscoverySec = 0;
  document.getElementById('ch-discovery').classList.remove('hidden');
  document.getElementById('ch-results').classList.add('hidden');
  document.getElementById('ch-discovery-msg').textContent = 'Đang kết nối tới server...';
  clearInterval(_chDiscoveryTimer);
  _chDiscoveryTimer = setInterval(() => {
    _chDiscoverySec++;
    const m = String(Math.floor(_chDiscoverySec / 60)).padStart(2, '0');
    const s = String(_chDiscoverySec % 60).padStart(2, '0');
    document.getElementById('ch-discovery-timer').textContent = `${m}:${s}`;
  }, 1000);
}

function stopChannelDiscovery() {
  clearInterval(_chDiscoveryTimer);
  _chDiscoveryTimer = null;
}

function startJobPolling(batchId, maxVideos) {
  _chBatchId = batchId;
  clearTimeout(_chJobPollTimer);
  const webLink = document.getElementById('ch-open-web');
  // This is the ONLY way a user gets from "channel scrape complete" to actual
  // files — the batch is processed server-side, nothing here calls
  // chrome.downloads.download() for channel/bulk mode. Two bugs made it dead:
  // (1) it pointed at API_BASE (backend, returns {"status":"ok"}) instead of
  // WEB_BASE (the frontend, whose BulkContent.jsx reads ?batch= and
  // auto-downloads each finished video); (2) it carried a redundant inline
  // display:none alongside class="hidden" so classList.remove() never
  // actually revealed it — same bug as the earlier Spotify header fix.
  webLink.href = `${WEB_BASE}/?batch=${batchId}`;
  webLink.style.display = 'block';
  webLink.classList.remove('hidden');

  // Adaptive polling: fast (2s) while a batch is fresh so short jobs feel
  // snappy, backing off to 5s after 30s and 10s after 2min — a channel
  // pulling 100+ videos can run for many minutes, and hammering the server
  // every 2s for the whole duration wastes requests for no visible benefit
  // once progress has been slow for a while.
  const pollStartedAt = Date.now();
  const nextDelay = () => {
    const elapsed = Date.now() - pollStartedAt;
    if (elapsed > 120_000) return 10_000;
    if (elapsed > 30_000) return 5_000;
    return 2_000;
  };

  const poll = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/jobs/${batchId}`);
      if (!resp.ok) { _chJobPollTimer = setTimeout(poll, nextDelay()); return; }
      const data = await resp.json();
      const jobs = (data.jobs || []).filter(j => j.original_url !== 'batch_zip');

      // Discovery phase: only 1 processing job (the channel placeholder)
      const isDiscovering = jobs.length <= 1 && jobs.some(j => j.status === 'processing');
      if (isDiscovering) {
        const msg = jobs[0]?.error_message;
        if (msg) document.getElementById('ch-discovery-msg').textContent = msg;
        _chJobPollTimer = setTimeout(poll, nextDelay());
        return;
      }

      // Jobs found — switch to results view
      stopChannelDiscovery();
      document.getElementById('ch-discovery').classList.add('hidden');
      document.getElementById('ch-results').classList.remove('hidden');

      const videoJobs = jobs.filter(j => j.status !== 'processing' || jobs.length > 1);
      const success = videoJobs.filter(j => j.status === 'success').length;
      const total = Math.max(videoJobs.length, 1);
      const pct = Math.round((success / total) * 100);

      document.getElementById('ch-job-stats').textContent = `${success} / ${total}`;
      document.getElementById('ch-job-bar').style.width = pct + '%';
      document.getElementById('ch-result-msg').textContent =
        success === total && total > 0
          ? `✅ Hoàn tất ${total} video!`
          : `⏳ Đang xử lý... (${total - success} còn lại)`;

      document.getElementById('ch-count').textContent = total;

      if (success !== total || total === 0) {
        _chJobPollTimer = setTimeout(poll, nextDelay());
      }
    } catch { _chJobPollTimer = setTimeout(poll, nextDelay()); }
  };

  _chJobPollTimer = setTimeout(poll, 2_000);
}

// Khởi tạo API_BASE từ storage ngay khi popup mở
chrome.storage.sync.get('vg_api_base', (r) => {
  if (r.vg_api_base?.trim()) API_BASE = r.vg_api_base.trim();
});

// ── Tab switcher ──────────────────────────────────────────────────
let _lastActiveTab = 'single';
function showTab(tab) {
  _lastActiveTab = tab;
  document.getElementById('single-section').style.display = tab === 'single' ? 'block' : 'none';
  document.getElementById('channel-section').style.display = tab === 'channel' ? 'block' : 'none';
  document.getElementById('history-section').style.display = tab === 'history' ? 'block' : 'none';
  const sp = document.getElementById('settings-panel');
  if (sp) sp.style.display = tab === 'settings' ? 'block' : 'none';

  ['tab-single','tab-channel','tab-history'].forEach(id => {
    const el = document.getElementById(id);
    const isActive = (id === `tab-${tab}`);
    el.classList.toggle('active', isActive);
    el.style.color = '';
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

// ── Server health check ───────────────────────────────────────────
async function checkServerHealth() {
  const dot = document.getElementById('server-health-dot');
  if (!dot) return;
  dot.style.background = '#6b7280'; // grey = checking
  dot.title = 'Đang kiểm tra server...';

  const result = await new Promise((resolve) =>
    chrome.runtime.sendMessage({ type: 'VG_HEALTH_CHECK' }, resolve)
  );

  if (result?.ok) {
    dot.style.background = '#22c55e';
    dot.title = 'Server hoạt động bình thường';
  } else {
    dot.style.background = '#ef4444';
    dot.title = 'Không kết nối được server — kiểm tra lại URL hoặc mạng';
    const statusEl = document.getElementById('statusMsg');
    if (statusEl) {
      statusEl.textContent = '⚠ Không kết nối được server';
      statusEl.className = 'mt-2 text-xs text-center text-yellow-400 min-h-[18px]';
    }
  }
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
  // Chạy health check song song với page detection
  checkServerHealth();

  const pageBadge = document.getElementById('page-badge');
  try {
    const tab = await getActiveTab();
    if (!tab?.id) throw new Error('no tab');

    let pageInfo;
    try { pageInfo = await tryGetPageInfo(tab.id); }
    catch {
      try { await injectContentScript(tab.id); pageInfo = await tryGetPageInfo(tab.id); }
      catch {
        showReloadBanner(tab.id);
        pageBadge.textContent = 'Cần reload trang';
        pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-yellow-900 text-yellow-400 border border-yellow-700';
        return;
      }
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
        document.getElementById('ch-btn-text').textContent = 'Bắt Đầu Tải Kênh';
        document.getElementById('ch-status').textContent = 'Chọn số lượng video rồi nhấn bắt đầu.';
        document.getElementById('ch-count').textContent = '—';
        document.getElementById('ch-count-selector').classList.remove('hidden');
        document.getElementById('ch-send-btn').classList.add('hidden');
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
      updateWatermarkUI(tab.url || '');
    }
  } catch {
    pageBadge.textContent = 'Không hỗ trợ';
    pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-500 border border-gray-700';
    showUnsupportedPage();
  }
}
initPopup();

// ── Unsupported page state ────────────────────────────────────────
function showUnsupportedPage() {
  const panel = document.getElementById('unsupported-page-state');
  if (!panel) return;
  // Hide main tab content, show friendly message
  document.getElementById('single-section')?.style.setProperty('display', 'none');
  document.getElementById('channel-section')?.style.setProperty('display', 'none');
  panel.style.display = 'block';
}

// ── Onboarding ───────────────────────────────────────────────────
let _obCurrentStep = 1;

function showOnboarding() {
  const overlay = document.getElementById('onboarding-overlay');
  if (overlay) overlay.style.display = 'flex';
}

function hideOnboarding() {
  const overlay = document.getElementById('onboarding-overlay');
  if (overlay) overlay.style.display = 'none';
  chrome.storage.local.set({ vg_onboarding_done: true });
}

function obNext() {
  const current = document.getElementById(`ob-step-${_obCurrentStep}`);
  if (current) current.style.display = 'none';
  _obCurrentStep++;
  const next = document.getElementById(`ob-step-${_obCurrentStep}`);
  if (next) next.style.display = 'block';
}

function obFinish() {
  hideOnboarding();
}

// MV3 extension pages enforce script-src 'self' — inline onclick="" attributes
// are silently dropped (no console error), so these must be wired here instead.
document.getElementById('ob-next-1')?.addEventListener('click', obNext);
document.getElementById('ob-next-2')?.addEventListener('click', obNext);
document.getElementById('ob-finish')?.addEventListener('click', obFinish);

function checkOnboarding() {
  chrome.storage.local.get('vg_onboarding_done', (r) => {
    if (r.vg_onboarding_done === false) {
      showOnboarding();
    }
  });
}
checkOnboarding();

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

// ── Elapsed timer (hiển thị số giây đang xử lý) ──────────────────
let _elapsedTimer = null;
let _elapsedSec = 0;

function startElapsedTimer(statusEl) {
  _elapsedSec = 0;
  clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    _elapsedSec++;
    statusEl.textContent = `⏳ Server đang xử lý... ${_elapsedSec}s`;
  }, 1000);
}

function stopElapsedTimer() {
  clearInterval(_elapsedTimer);
  _elapsedTimer = null;
}

// Sync elapsed từ background (backup nếu timer popup lệch)
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'VG_FETCH_PROGRESS') {
    _elapsedSec = msg.elapsed;
    const statusEl = document.getElementById('statusMsg');
    if (statusEl && _elapsedTimer) {
      statusEl.textContent = `⏳ Server đang xử lý... ${msg.elapsed}s`;
    }
  }
  if (msg.type === 'VG_DOWNLOAD_PROGRESS') handleDownloadProgress(msg);
});

// ── Helper: gọi fetch-link qua background ─────────────────────────
async function apiFetchLink(payload) {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage({ type: 'VG_FETCH_LINK', payload }, resolve)
  );
}

// ── Helper: resolve download URL ──────────────────────────────────
function resolveDownloadUrl(data, ext, base) {
  const apiBase = base || API_BASE;
  let dlUrl = ext === 'mp3'
    ? (data.local_mp3_path || data.local_file_path || data.direct_mp4_url)
    : (data.direct_mp4_url || data.local_file_path || data.local_mp3_path);
  if (!dlUrl) return null;

  if (data.is_audio_only || (dlUrl.endsWith('.mp3') || dlUrl.endsWith('.m4a'))) ext = 'mp3';

  const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

  if (dlUrl.startsWith('/app/downloads/') || dlUrl.startsWith('downloads/')) {
    dlUrl = `${apiBase}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
  } else if (!dlUrl.includes('matbao.ai')) {
    dlUrl = `${apiBase}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
  }
  return { dlUrl, safeName, ext };
}

// ══════════════════════════════════════════════════════════════════
// SINGLE VIDEO — Download + Preview + Progress
// ══════════════════════════════════════════════════════════════════
document.getElementById('downloadBtn').addEventListener('click', async () => {
  const btnText = document.getElementById('btnText'), spinner = document.getElementById('spinner'),
    iconDl = document.getElementById('icon-download'), statusMsg = document.getElementById('statusMsg'),
    btn = document.getElementById('downloadBtn'), quality = document.getElementById('qualitySelect').value,
    noWm = document.getElementById('removeWatermark').checked;
  const subtitleMode = document.getElementById('subtitleModeSelect')?.value || 'off';
  const dlSubs = subtitleMode !== 'off';
  _lastNoWm = noWm;

  btn.disabled = true;
  iconDl.style.display = 'none'; spinner.style.display = 'block';
  btnText.textContent = 'Đang xử lý...';
  statusMsg.textContent = '⏳ Server đang xử lý... 0s';

  startElapsedTimer(statusMsg);

  try {
    const tab = await getActiveTab();
    if (!tab?.url || tab.url.startsWith('chrome://')) throw new Error('Không thể lấy link ở trang này.');

    // Send to background worker — download continues even if popup is closed
    chrome.runtime.sendMessage({
      type: 'VG_DOWNLOAD_NOW',
      payload: { url: tab.url, quality, remove_watermark: noWm, download_subs: dlSubs, subtitle_mode: subtitleMode, subtitle_language: 'auto' },
    }, (ack) => {
      if (ack?.queued) {
        stopElapsedTimer();
        statusMsg.textContent = '⚡ Đang tải nền — có thể đóng popup!';
        btn.disabled = false;
        iconDl.style.display = 'block'; spinner.style.display = 'none';
        btnText.textContent = 'Tải Video Ngay';
      }
    });

    // Listen for completion from background
    // (fires if popup is still open when download finishes)
    const _dlDoneHandler = (msg) => {
      if (msg.type !== 'VG_DOWNLOAD_DONE') return;
      chrome.runtime.onMessage.removeListener(_dlDoneHandler);
      stopElapsedTimer();
      if (msg.error) {
        statusMsg.textContent = `❌ ${msg.error}`;
        const errCard = document.getElementById('error-card');
        const errText = document.getElementById('error-text');
        if (errCard && errText) { errText.textContent = msg.error; errCard.classList.remove('hidden'); }
        btn.disabled = false;
        iconDl.style.display = 'block'; spinner.style.display = 'none';
        btnText.textContent = quality && quality.startsWith('mp3') ? 'Tải Nhạc MP3 Ngay' : 'Tải Video Ngay';
        return;
      }
      const data = msg.data;
      _lastDownloadData = data;
      if (msg.dlUrl) _lastDownloadData._resolvedUrl = msg.dlUrl;
      showPreviewCard(data, tab.url);
      showMultiFormat(data);
      document.getElementById('error-card')?.classList.add('hidden');
      const isMp3Done = quality && quality.startsWith('mp3');
      const isCleanTikTok = isWatermarkPlatform(tab.url) && noWm;
      statusMsg.textContent = isMp3Done
        ? '✅ Tải nhạc hoàn tất! File MP3 đã lưu vào thư mục VidGrab.'
        : isCleanTikTok
          ? '✅ Đã tải bản không watermark! File đã lưu vào thư mục VidGrab.'
          : '✅ Tải hoàn tất! File đã lưu vào thư mục VidGrab.';
      document.getElementById('dl-progress')?.classList.remove('hidden');

      const successCheck = document.getElementById('success-check');
      if (successCheck) { successCheck.classList.add('show'); setTimeout(() => successCheck.classList.remove('show'), 3000); }

      btn.disabled = false;
      iconDl.style.display = 'block'; spinner.style.display = 'none';
      btnText.textContent = isMp3Done ? 'Tải Nhạc MP3 Ngay' : 'Tải Video Ngay';
    };
    chrome.runtime.onMessage.addListener(_dlDoneHandler);

  } catch (err) {
    stopElapsedTimer();
    statusMsg.textContent = `❌ ${err.message}`;
    const errCard = document.getElementById('error-card');
    const errText = document.getElementById('error-text');
    if (errCard && errText) { errText.textContent = err.message; errCard.classList.remove('hidden'); }
    btn.disabled = false;
    iconDl.style.display = 'block'; spinner.style.display = 'none';
    btnText.textContent = quality && quality.startsWith('mp3') ? 'Tải Nhạc MP3 Ngay' : 'Tải Video Ngay';
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

function isWatermarkPlatform(url) {
  if (!url) return false;
  const u = url.toLowerCase();
  return u.includes('tiktok.com') || u.includes('vm.tiktok.com') ||
    u.includes('douyin.com') || u.includes('v.douyin.com') || u.includes('iesdouyin.com');
}

function updateWatermarkUI(url) {
  const cb = document.getElementById('removeWatermark');
  const hint = document.getElementById('wm-platform-hint');
  if (!cb || !hint) return;
  const wmLabel = cb.closest('.toggle-item');
  if (isWatermarkPlatform(url)) {
    cb.disabled = false;
    hint.textContent = 'TikTok/Douyin ✓';
    hint.style.display = 'inline';
    hint.style.background = 'rgba(52,211,153,0.15)';
    hint.style.color = '#34d399';
    hint.style.border = '1px solid rgba(52,211,153,0.3)';
    if (wmLabel) wmLabel.style.opacity = '1';
  } else if (url && url.length > 0) {
    cb.checked = false;
    cb.disabled = true;
    hint.textContent = 'Chỉ TikTok';
    hint.style.display = 'inline';
    hint.style.background = 'rgba(148,163,184,0.1)';
    hint.style.color = '#64748b';
    hint.style.border = '1px solid rgba(148,163,184,0.2)';
    if (wmLabel) wmLabel.style.opacity = '0.5';
  } else {
    cb.disabled = false;
    hint.style.display = 'none';
    if (wmLabel) wmLabel.style.opacity = '1';
  }
}

function showPreviewCard(data, pageUrl) {
  const card = document.getElementById('preview-card');
  if (!card) return;
  const title = document.getElementById('preview-title');
  const dur = document.getElementById('preview-duration');
  const size = document.getElementById('preview-size');
  const platform = document.getElementById('preview-platform');

  if (title) title.textContent = data.title || 'Video';
  if (dur && data.duration > 0) {
    const m = Math.floor(data.duration / 60), s = data.duration % 60;
    dur.textContent = `${m}:${String(s).padStart(2,'0')}`;
  }
  if (size && data.file_size_mb) size.textContent = `${data.file_size_mb} MB`;
  if (platform) platform.textContent = detectPlatform(pageUrl || data.original_url);
  const wmBadge = document.getElementById('preview-wm-badge');
  if (wmBadge) wmBadge.style.display = (isWatermarkPlatform(pageUrl || '') && _lastNoWm) ? 'inline' : 'none';
  card.classList.remove('hidden');

  // AI Gợi ý chỉ khả dụng khi server có file local để ffprobe (không phải stream trực tiếp)
  const aiBtn = document.getElementById('action-ai-analyze');
  const aiResults = document.getElementById('ai-analyze-results');
  if (aiBtn) {
    const hasLocalFile = !!(data.local_file_path && (data.local_file_path.startsWith('/app/downloads/') || data.local_file_path.startsWith('downloads/')));
    aiBtn.classList.toggle('hidden', !hasLocalFile);
    aiBtn.disabled = false;
    aiBtn.textContent = '🤖 AI Gợi ý';
  }
  if (aiResults) aiResults.classList.add('hidden');
}

// ── Action buttons ────────────────────────────────────────────────
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

document.getElementById('action-translate-subs')?.addEventListener('click', () => {
  chrome.tabs.create({ url: `${WEB_BASE}/transcript-translate` });
});

// ── AI Smart Actions (trim/highlight/GIF suggestions) ─────────────
const AI_POLL_INTERVAL_MS = 2000;
const AI_POLL_MAX_ATTEMPTS = 30; // ~60s timeout, matches web app's SmartActionsPanel

function fmtAiSeconds(s) {
  if (s == null) return '—';
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m > 0 ? `${m}:${String(sec).padStart(2, '0')}` : `${sec}s`;
}

function renderAiAnalyzeResults(result) {
  const box = document.getElementById('ai-analyze-results');
  if (!box) return;
  const trim = result.trim_suggestions || [];
  const clips = result.clip_suggestions || [];
  const gifs = result.gif_suggestions || [];
  const parts = [];
  if (trim.length) {
    const t = trim[0];
    parts.push(`✂️ <b>Cắt gợi ý:</b> ${fmtAiSeconds(t.start)} → ${fmtAiSeconds(t.end)}${t.reason ? ` — ${t.reason}` : ''}`);
  }
  if (clips.length) {
    parts.push(`🎬 <b>${clips.length} đoạn nổi bật</b> — hay nhất: ${fmtAiSeconds(clips[0].start)} → ${fmtAiSeconds(clips[0].end)}${clips[0].label ? ` (${clips[0].label})` : ''}`);
  }
  if (gifs.length) {
    parts.push(`🖼️ <b>${gifs.length} đoạn GIF gợi ý</b> — bắt đầu ${fmtAiSeconds(gifs[0].start)}`);
  }
  if (!parts.length) {
    parts.push(result.fallback_used
      ? 'Không đủ dữ liệu để phân tích sâu (fallback mode).'
      : 'Không tìm thấy gợi ý nào đáng chú ý cho video này.');
  }
  parts.push('<div style="margin-top:6px;"><a href="#" id="ai-open-web-link" style="color:#818cf8;font-weight:600;">Xem chi tiết & áp dụng trên Web →</a></div>');
  box.innerHTML = parts.join('<br>');
  box.classList.remove('hidden');
  document.getElementById('ai-open-web-link')?.addEventListener('click', (e) => {
    e.preventDefault();
    getActiveTab().then((tab) => {
      chrome.tabs.create({ url: `${WEB_BASE}?url=${encodeURIComponent(tab?.url || '')}` });
    });
  });
}

async function pollAiAnalysis(jobId, attempt = 0) {
  const box = document.getElementById('ai-analyze-results');
  try {
    const res = await fetch(`${API_BASE}/api/v1/analyze/${jobId}`);
    const data = await res.json();
    if (data.status === 'done' || data.status === 'cached') {
      renderAiAnalyzeResults(data);
      const aiBtn = document.getElementById('action-ai-analyze');
      if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = '🤖 AI Gợi ý'; }
      return;
    }
    if (attempt >= AI_POLL_MAX_ATTEMPTS) {
      if (box) { box.innerHTML = '⏱️ Phân tích quá lâu, thử lại sau.'; box.classList.remove('hidden'); }
      const aiBtn = document.getElementById('action-ai-analyze');
      if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = '🤖 AI Gợi ý'; }
      return;
    }
    setTimeout(() => pollAiAnalysis(jobId, attempt + 1), AI_POLL_INTERVAL_MS);
  } catch {
    if (box) { box.innerHTML = '❌ Lỗi khi lấy kết quả phân tích.'; box.classList.remove('hidden'); }
    const aiBtn = document.getElementById('action-ai-analyze');
    if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = '🤖 AI Gợi ý'; }
  }
}

document.getElementById('action-ai-analyze')?.addEventListener('click', async () => {
  const btn = document.getElementById('action-ai-analyze');
  const box = document.getElementById('ai-analyze-results');
  if (!_lastDownloadData?.local_file_path) return;
  btn.disabled = true; btn.textContent = '⏳ Đang phân tích...';
  if (box) { box.innerHTML = '⏳ Đang phân tích video (có thể mất vài chục giây)...'; box.classList.remove('hidden'); }
  try {
    const res = await fetch(`${API_BASE}/api/v1/analyze-media`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: _lastDownloadData.local_file_path,
        media_path: _lastDownloadData.local_file_path,
        analyses: ['trim', 'gif', 'metadata', 'clips'],
        duration_hint_s: _lastDownloadData.duration || null,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pollAiAnalysis(data.job_id);
  } catch {
    if (box) { box.innerHTML = '❌ Không thể gửi yêu cầu phân tích.'; box.classList.remove('hidden'); }
    btn.disabled = false; btn.textContent = '🤖 AI Gợi ý';
  }
});

// ── Download Progress ─────────────────────────────────────────────
let _dlStartTime = 0, _dlLastReceived = 0, _dlLastTime = 0;

function handleDownloadProgress(msg) {
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
    if (speedEl && msg.received > 0 && now - _dlLastTime > 400) {
      const deltaBytes = msg.received - _dlLastReceived;
      const deltaSec = (now - _dlLastTime) / 1000;
      if (deltaSec > 0) speedEl.textContent = `${(deltaBytes / 1048576 / deltaSec).toFixed(1)} MB/s`;
      _dlLastReceived = msg.received; _dlLastTime = now;
    }
  } else if (msg.state === 'error') {
    pctEl.textContent = '❌ Lỗi'; fill.style.background = '#ef4444'; _dlStartTime = 0;
  }
}

// ══════════════════════════════════════════════════════════════════
// HISTORY / ARCHIVE TAB
// ══════════════════════════════════════════════════════════════════

let _historySubTab = 'local';

function showHistoryTab(tab) {
  _historySubTab = tab;
  const isLocal = tab === 'local';
  document.getElementById('hpanel-local').style.display    = isLocal ? 'block' : 'none';
  document.getElementById('hpanel-archive').style.display  = isLocal ? 'none'  : 'block';
  const localBtn   = document.getElementById('htab-local');
  const archiveBtn = document.getElementById('htab-archive');
  if (localBtn)   { localBtn.style.background   = isLocal ? '#FBBF24' : 'none'; localBtn.style.color   = isLocal ? '#012622' : '#64748b'; }
  if (archiveBtn) { archiveBtn.style.background = isLocal ? 'none' : '#FBBF24'; archiveBtn.style.color = isLocal ? '#64748b' : '#012622'; }
  if (!isLocal) loadArchive();
}

document.getElementById('htab-local')?.addEventListener('click', () => showHistoryTab('local'));
document.getElementById('htab-archive')?.addEventListener('click', () => showHistoryTab('archive'));

function loadHistory() {
  chrome.runtime.sendMessage({ type: 'VG_GET_HISTORY' }, (resp) => {
    const list  = document.getElementById('history-list');
    const empty = document.getElementById('history-empty');
    const history = (resp?.history || []).slice(0, 20);
    if (history.length === 0) { list.innerHTML = ''; list.appendChild(empty); empty.style.display = 'block'; return; }

    list.innerHTML = '';
    history.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'history-item';
      const thumbSrc = item.thumbnail || "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='30' fill='%23374151'><rect width='40' height='30' rx='4'/></svg>";
      const timeAgo = getTimeAgo(item.timestamp);
      row.innerHTML = `
        <img src="${escapeHtml(thumbSrc)}" style="width:40px;height:28px;border-radius:4px;object-fit:cover;flex-shrink:0;" loading="lazy">
        <div style="flex:1;overflow:hidden;min-width:0;">
          <div style="font-size:11px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(item.title || 'Video')}</div>
          <div style="font-size:9px;color:#6b7280;">${item.noWatermark ? '<span style="color:#34d399;font-weight:700;">✓WM </span>' : ''}${item.hasSubtitle ? '<span style="color:#60a5fa;font-weight:700;">✓SUB </span>' : ''}${escapeHtml(item.quality || '')} ${item.fileSize ? '• ' + escapeHtml(item.fileSize) : ''} • ${escapeHtml(timeAgo)}</div>
        </div>
        <button class="save-archive-btn" data-url="${encodeURIComponent(item.url || '')}" data-title="${encodeURIComponent(item.title || 'Video')}" data-thumb="${encodeURIComponent(item.thumbnail || '')}"
          style="flex-shrink:0;padding:3px 6px;font-size:9px;font-weight:700;color:#FBBF24;background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:5px;cursor:pointer;white-space:nowrap;"
          title="Lưu vào Archive">🗄</button>
      `;
      list.appendChild(row);
    });

    // Bind "Save to Archive" buttons
    list.querySelectorAll('.save-archive-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const url   = decodeURIComponent(btn.dataset.url || '');
        const title = decodeURIComponent(btn.dataset.title || 'Video');
        const thumb = decodeURIComponent(btn.dataset.thumb || '');
        if (!url) return;
        btn.textContent = '⏳';
        btn.disabled = true;
        try {
          const saved = await saveToArchive({ original_url: url, title, thumbnail_url: thumb });
          btn.textContent = saved ? '✅' : '❌';
        } catch { btn.textContent = '❌'; }
      });
    });
  });
}

async function saveToArchive(item) {
  const token = await new Promise(r => chrome.storage.local.get('vg_auth_token', d => r(d.vg_auth_token)));
  if (!token) { alert('Đăng nhập để lưu vào Archive.'); return false; }
  const r = await fetch(`${API_BASE}/api/v1/archive`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ ...item, platform: detectPlatform(item.original_url || '') }),
  });
  return r.ok;
}

function detectPlatform(url) {
  if (/youtube\.com|youtu\.be/.test(url)) return 'youtube';
  if (/tiktok\.com/.test(url)) return 'tiktok';
  if (/facebook\.com|fb\.watch/.test(url)) return 'facebook';
  if (/instagram\.com/.test(url)) return 'instagram';
  if (/twitter\.com|x\.com/.test(url)) return 'twitter';
  if (/reddit\.com/.test(url)) return 'reddit';
  return 'other';
}

async function loadArchive() {
  const loginPrompt = document.getElementById('archive-login-prompt');
  const archiveList = document.getElementById('archive-list');
  const loading     = document.getElementById('archive-loading');
  const empty       = document.getElementById('archive-empty');

  const token = await new Promise(r => chrome.storage.local.get('vg_auth_token', d => r(d.vg_auth_token)));
  if (!token) {
    loginPrompt.style.display = 'block';
    archiveList.style.display = 'none';
    loading.style.display     = 'none';
    empty.style.display       = 'none';
    return;
  }

  loginPrompt.style.display = 'none';
  loading.style.display     = 'block';
  archiveList.innerHTML     = '';
  empty.style.display       = 'none';

  try {
    const r = await fetch(`${API_BASE}/api/v1/archive?limit=20&sort=archived_at`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) throw new Error('Unauthorized');
    const d = await r.json();
    const items = d.items || [];

    loading.style.display = 'none';
    if (items.length === 0) { empty.style.display = 'block'; return; }

    archiveList.style.display = 'flex';
    archiveList.style.flexDirection = 'column';
    archiveList.style.gap = '5px';

    items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'history-item';
      const thumbSrc = item.thumbnail_url || "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='30' fill='%23374151'><rect width='40' height='30' rx='4'/></svg>";
      const dateStr  = item.archived_at ? new Date(item.archived_at).toLocaleDateString('vi-VN') : '';
      row.innerHTML = `
        <img src="${escapeHtml(thumbSrc)}" style="width:40px;height:28px;border-radius:4px;object-fit:cover;flex-shrink:0;" loading="lazy">
        <div style="flex:1;overflow:hidden;min-width:0;">
          <div style="font-size:11px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(item.title || 'Video')}</div>
          <div style="font-size:9px;color:#6b7280;">${escapeHtml(item.platform || '')} • ${escapeHtml(item.creator_name || '')} ${dateStr ? '• ' + escapeHtml(dateStr) : ''}</div>
        </div>
        <button class="star-btn" data-id="${escapeHtml(item.id)}" data-starred="${item.is_starred ? '1':'0'}"
          style="flex-shrink:0;font-size:13px;background:none;border:none;cursor:pointer;color:${item.is_starred ? '#FBBF24' : '#374151'};"
          title="${item.is_starred ? 'Bỏ gắn sao' : 'Gắn sao'}">★</button>
      `;
      archiveList.appendChild(row);
    });

    // Star toggle
    archiveList.querySelectorAll('.star-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const isStarred = btn.dataset.starred === '1';
        btn.style.color = !isStarred ? '#FBBF24' : '#374151';
        btn.dataset.starred = isStarred ? '0' : '1';
        await fetch(`${API_BASE}/api/v1/archive/${btn.dataset.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ is_starred: !isStarred }),
        });
      });
    });
  } catch {
    loading.style.display = 'none';
    loginPrompt.style.display = 'block';
  }
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
// SETTINGS PANEL
// ══════════════════════════════════════════════════════════════════
const settingsBtn = document.getElementById('settings-btn');
const settingsPanel = document.getElementById('settings-panel');
const settingsApiUrl = document.getElementById('settings-api-url');
const settingsSave = document.getElementById('settings-save');
const settingsReset = document.getElementById('settings-reset');

// Load current value vào input khi mở
function closeSettingsPanel() {
  settingsPanel.classList.add('hidden');
  settingsPanel.style.display = 'none';
  showTab(_lastActiveTab);
}
function openSettingsPanel() {
  document.getElementById('single-section').style.display = 'none';
  document.getElementById('channel-section').style.display = 'none';
  document.getElementById('history-section').style.display = 'none';
  settingsPanel.classList.remove('hidden');
  settingsPanel.style.display = 'block';
  chrome.storage.sync.get('vg_api_base', (r) => {
    settingsApiUrl.value = r.vg_api_base || DEFAULT_API_BASE;
  });
}
settingsBtn?.addEventListener('click', () => {
  const isOpen = !settingsPanel.classList.contains('hidden');
  isOpen ? closeSettingsPanel() : openSettingsPanel();
});

settingsSave?.addEventListener('click', () => {
  const val = settingsApiUrl.value.trim().replace(/\/$/, '');
  if (!val.startsWith('http')) {
    settingsSave.textContent = '❌ URL không hợp lệ';
    setTimeout(() => { settingsSave.textContent = 'Lưu'; }, 2000);
    return;
  }
  chrome.storage.sync.set({ vg_api_base: val }, () => {
    API_BASE = val;
    settingsSave.textContent = '✅ Đã lưu';
    setTimeout(() => { settingsSave.textContent = 'Lưu'; closeSettingsPanel(); }, 1500);
    checkServerHealth(); // re-check với URL mới
  });
});

settingsReset?.addEventListener('click', () => {
  chrome.storage.sync.remove('vg_api_base', () => {
    API_BASE = DEFAULT_API_BASE;
    settingsApiUrl.value = DEFAULT_API_BASE;
    settingsReset.textContent = '✅ Đã reset';
    setTimeout(() => { settingsReset.textContent = 'Đặt lại mặc định'; }, 1500);
    checkServerHealth();
  });
});

// ── Account auth bridge ────────────────────────────────────────────
const authStatusRow    = document.getElementById('auth-status-row');
const authConnectBtn   = document.getElementById('auth-connect-btn');
const authDisconnectBtn = document.getElementById('auth-disconnect-btn');

function updateAuthUI(authenticated, email) {
  if (!authStatusRow) return;
  if (authenticated) {
    authStatusRow.innerHTML = `<span style="color:#4ade80;">✓ Đã đăng nhập${email ? ` — ${email}` : ''}</span>`;
    authConnectBtn.style.display = 'none';
    authDisconnectBtn.style.display = 'block';
  } else {
    authStatusRow.textContent = 'Chưa đăng nhập — lịch sử sẽ không được lưu';
    authConnectBtn.style.display = 'block';
    authDisconnectBtn.style.display = 'none';
  }
}

// Check auth status on settings panel open
function checkAuthStatus() {
  chrome.runtime.sendMessage({ type: 'VG_GET_AUTH_STATUS' }, (res) => {
    if (chrome.runtime.lastError) return;
    updateAuthUI(res?.authenticated || false);
  });
  // Also try to get email from storage
  chrome.storage.local.get('vg_auth_email', (r) => {
    if (r.vg_auth_email) {
      chrome.runtime.sendMessage({ type: 'VG_GET_AUTH_STATUS' }, (res) => {
        if (res?.authenticated) updateAuthUI(true, r.vg_auth_email);
      });
    }
  });
}

authConnectBtn?.addEventListener('click', () => {
  // Open web app login page in new tab
  chrome.tabs.create({ url: `${API_BASE}/?connect_extension=1` });
});

authDisconnectBtn?.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'VG_CLEAR_AUTH_TOKEN' }, () => {
    chrome.storage.local.remove('vg_auth_email');
    updateAuthUI(false);
  });
});

// Listen for token passed from web page (via content script / postMessage bridge)
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'VG_AUTH_TOKEN_FROM_WEB' && msg.token) {
    chrome.runtime.sendMessage({ type: 'VG_SET_AUTH_TOKEN', token: msg.token });
    if (msg.email) chrome.storage.local.set({ vg_auth_email: msg.email });
    updateAuthUI(true, msg.email);
  }
});

// ── Tier + Quota display in settings panel ─────────────────────────
async function loadTierQuota() {
  const tierRow = document.getElementById('vg-tier-quota-row');
  if (!tierRow) return;
  tierRow.innerHTML = '<span style="color:#64748b;font-size:10px;">Đang kiểm tra quota…</span>';

  const base = await getApiBaseLocal();
  // Get auth token from background
  chrome.runtime.sendMessage({ type: 'VG_GET_AUTH_TOKEN' }, async (res) => {
    const token = res?.token;
    if (!token) {
      tierRow.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#64748b;font-size:10px;">Tier</span>
          <span style="color:#94a3b8;font-size:10px;">Khách · 5 lượt/ngày</span>
        </div>`;
      return;
    }
    try {
      const r = await fetch(`${base}/api/v1/user/usage`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) throw new Error();
      const d = await r.json();
      const tier = (d.tier || 'free').toLowerCase();
      const used = d.used ?? d.downloads_today ?? 0;
      const limit = d.limit ?? d.daily_limit ?? (tier === 'pro' ? 100 : 10);
      const pct = Math.min(100, Math.round((used / limit) * 100));
      const tierLabel = tier === 'pro'
        ? '<span style="color:#a78bfa;font-weight:700;background:rgba(139,92,246,0.15);padding:1px 5px;border-radius:4px;font-size:9px;">PRO</span>'
        : '<span style="color:#94a3b8;font-size:9px;background:rgba(148,163,184,0.1);padding:1px 5px;border-radius:4px;">FREE</span>';
      const barColor = tier === 'pro' ? '#8b5cf6' : pct >= 90 ? '#ef4444' : '#3b82f6';
      tierRow.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <span style="color:#94a3b8;font-size:10px;">Gói</span>
          ${tierLabel}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#64748b;margin-bottom:3px;">
          <span>Quota hôm nay</span>
          <span style="color:#e2e8f0;font-family:monospace;">${used}/${limit}</span>
        </div>
        <div style="height:3px;background:rgba(148,163,184,0.15);border-radius:2px;overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:${barColor};border-radius:2px;transition:width 0.3s;"></div>
        </div>
        ${tier !== 'pro' ? `<div style="margin-top:5px;font-size:9px;color:#6366f1;cursor:pointer;" id="vg-upgrade-link">▸ Nâng cấp Pro — tải video YouTube, batch 100, ZIP…</div>` : ''}
      `;
      document.getElementById('vg-upgrade-link')?.addEventListener('click', () => {
        chrome.tabs.create({ url: `${base}/upgrade` });
      });
    } catch {
      tierRow.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#64748b;font-size:10px;">Tier</span>
          <span style="color:#64748b;font-size:10px;">Không tải được</span>
        </div>`;
    }
  });
}

// Inject tier/quota row into settings panel (idempotent, runs before versionRow)
function ensureTierQuotaRow() {
  if (document.getElementById('vg-tier-quota-row')) return;
  const row = document.createElement('div');
  row.id = 'vg-tier-quota-row';
  row.style.cssText = 'margin-top:8px;padding-top:8px;border-top:1px solid rgba(148,163,184,0.1);';
  settingsPanel?.appendChild(row);
}

// Run auth check whenever settings panel opens
settingsBtn?.addEventListener('click', () => {
  checkAuthStatus();
  showVersionInfo();
  ensureTierQuotaRow();
  loadTierQuota();
}, true); // capture phase to run before the existing hide/show handler

// ── Version + backend compatibility display ────────────────────────
function showVersionInfo() {
  const manifest = chrome.runtime.getManifest();
  const extVer = manifest.version;

  // Update footer version
  const footerVer = document.getElementById('footer-version');
  if (footerVer) footerVer.textContent = `v${extVer}`;

  // Inject version row into settings panel (idempotent)
  let versionRow = document.getElementById('vg-version-row');
  if (!versionRow) {
    versionRow = document.createElement('div');
    versionRow.id = 'vg-version-row';
    versionRow.style.cssText = 'margin-top:10px;padding-top:8px;border-top:1px solid rgba(148,163,184,0.1);';
    settingsPanel?.appendChild(versionRow);
  }
  versionRow.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#475569;">
      <span>Extension</span>
      <span style="color:#818cf8;font-weight:700;font-family:monospace;">v${extVer}</span>
    </div>
    <div id="vg-backend-ver" style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#475569;margin-top:3px;">
      <span>Backend</span>
      <span style="color:#374151;font-family:monospace;">kiểm tra...</span>
    </div>
  `;

  // Async: check backend version/health
  (async () => {
    const base = await getApiBaseLocal();
    try {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 5000);
      const r = await fetch(`${base}/health`, { signal: controller.signal, cache: 'no-store' });
      const d = await r.json();
      const ytdlpVer = d.ytdlp_version || '—';
      const disk = d.disk?.used_pct != null ? `${d.disk.used_pct}%` : '';
      const el = document.getElementById('vg-backend-ver');
      if (el) el.innerHTML = `
        <span>Backend yt-dlp</span>
        <span style="color:#34d399;font-weight:700;font-family:monospace;">${ytdlpVer}${disk ? ` · disk ${disk}` : ''}</span>
      `;
    } catch {
      const el = document.getElementById('vg-backend-ver');
      if (el) el.innerHTML = `<span>Backend</span><span style="color:#ef4444;font-family:monospace;">offline</span>`;
    }
  })();
}

// Local helper to get API base without background message round-trip
async function getApiBaseLocal() {
  return new Promise((resolve) => {
    chrome.storage.sync.get('vg_api_base', (r) => {
      resolve((r.vg_api_base?.trim()) || 'https://dvid-api.cmc-1.vibenode.matbao.ai');
    });
  });
}

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
    // Directly trigger the bulk submit for generic channels (YouTube, etc.)
    btn.disabled = false; document.getElementById('ch-spinner').style.display = 'none';
    const urls = [tab.url];
    const status = document.getElementById('ch-status');
    const batchDiv = document.getElementById('ch-batch-info');
    try {
      const payload = { urls, quality: 'video', remove_watermark: true, channel_mode: true, max_videos: _chMaxVideos };
      const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.success) {
        batchDiv.classList.remove('hidden');
        batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;
        status.textContent = `🔍 Đang quét ${_chMaxVideos} video từ kênh...`;
        status.style.color = '#fb923c';
        document.getElementById('ch-btn-text').textContent = '⏳ Đang quét...';
        btn.disabled = true;
        startChannelDiscovery();
        startJobPolling(data.batch_id, _chMaxVideos);
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: tab.url }).catch(() => {});
      } else throw new Error(data.detail || 'Lỗi không xác định');
    } catch (err) {
      status.textContent = `❌ Lỗi: ${err.message}`; status.style.color = '#f87171';
      document.getElementById('ch-btn-text').textContent = 'Bắt Đầu Tải Kênh';
    }
    return;
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
    const payload = {
      urls,
      quality: isSpotify ? 'mp3_320' : 'video',
      remove_watermark: true,
      channel_mode: isGeneric,
      max_videos: isGeneric ? _chMaxVideos : (urls.length || 20),
    };
    const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.success) {
      batchDiv.classList.remove('hidden');
      batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;
      if (isGeneric) {
        // Show discovery progress in popup instead of opening new tab
        status.textContent = `🔍 Đang quét ${_chMaxVideos} video từ kênh...`;
        status.style.color = '#fb923c';
        sendText.textContent = '⏳ Đang quét...';
        startChannelDiscovery();
        startJobPolling(data.batch_id, _chMaxVideos);
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: tab.url }).catch(() => {});
      } else {
        status.textContent = `✅ Đã xếp hàng ${urls.length} video thành công!`; status.style.color = '#4ade80';
        sendText.textContent = '✅ Đã gửi';
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: tab.url }).catch(() => {});
        chrome.tabs.create({ url: `${API_BASE}?batch=${data.batch_id}` });
      }
    } else throw new Error(data.detail || 'Lỗi không xác định');
  } catch (err) {
    status.textContent = `❌ Lỗi: ${err.message}`; status.style.color = '#f87171';
    sendBtn.disabled = false; sendText.textContent = isGeneric ? `📤 Thử lại` : `📤 Thử lại (${urls.length} video)`;
  }
});

// ── SPOTIFY UI HELPERS ─────────────────────────────────────────────
function formatTime(sec) { if (!sec) return '0:00'; return `${Math.floor(sec/60)}:${(sec%60).toString().padStart(2,'0')}`; }

function renderSpotifyPlaylist(data) {
  // One .parentElement too many here used to hide the whole .content wrapper
  // (which sp-header / sp-tracklist-container also live in) instead of just
  // the "collected count" card — the entire Spotify UI silently rendered
  // with zero height no matter what its own hidden/display toggles did.
  document.getElementById('ch-count').parentElement.style.display = 'none';
  document.getElementById('ch-url').parentElement.style.display = 'none';
  document.getElementById('ch-status').style.display = 'none';
  document.getElementById('ch-scrape-btn').style.display = 'none';
  const header = document.getElementById('sp-header'), tracklist = document.getElementById('sp-tracklist-container');
  // sp-header needs display:flex (not the browser's block default) once un-hidden —
  // it used to carry a redundant inline display:none that permanently overrode
  // classList.remove('hidden'), so the whole Spotify header never rendered at all.
  header.style.display = 'flex';
  document.getElementById('sp-thumb').src = data.thumbnail || '';
  document.getElementById('sp-title').textContent = data.playlist_name || data.album_name || 'Spotify Music';
  document.getElementById('sp-count').textContent = `${data.tracks.length} bài nhạc`;
  document.getElementById('sp-type-label').textContent = data.type === 'album' ? 'ALBUM • SPOTIFY' : 'PLAYLIST • SPOTIFY';
  header.classList.remove('hidden'); tracklist.classList.remove('hidden');
  const sendBtn = document.getElementById('ch-send-btn');
  sendBtn.classList.remove('hidden');
  // Spotify's bulk-download CTA is meant to stand out from the plain green
  // .btn-green send button — the from-orange-500/to-yellow-400 Tailwind
  // classes this reached for don't exist in the bundled tailwind.css, so it
  // silently fell back to .btn-green's own gradient (looked fine, just not
  // the intended distinct color). Set it directly instead.
  sendBtn.style.background = 'linear-gradient(135deg, #f97316, #eab308)';
  sendBtn.style.color = '#1a1a1a';
  document.getElementById('ch-send-text').textContent = 'Tải tất cả (.ZIP)';
  tracklist.innerHTML = '';
  data.tracks.forEach((track, idx) => {
    const row = document.createElement('div');
    // .sp-track (popup.html) only styles the row container — the Tailwind
    // utility classes this template used to reach for (bg-gray-800,
    // hover:bg-gray-700, text-green-400, rounded, px-2 py-1, ...) don't
    // exist in the bundled tailwind.css (it was never built against this
    // dynamically-generated markup), so every track rendered as unstyled
    // black-on-transparent text with a plain "MP3" text button.
    row.className = 'sp-track';
    row.innerHTML = `<img src="${escapeHtml(track.thumbnail || data.thumbnail || '')}" style="width:32px;height:32px;border-radius:6px;object-fit:cover;flex-shrink:0;">
      <div style="flex:1;overflow:hidden;min-width:0;">
        <div style="font-size:11px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(track.name)}</div>
        <div style="font-size:10px;color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(track.artist_str || 'Unknown')}</div>
      </div>
      <div style="font-size:10px;color:#475569;font-family:monospace;flex-shrink:0;">${formatTime(track.duration)}</div>
      <button style="flex-shrink:0;padding:4px 9px;font-size:9px;font-weight:700;color:#fff;background:#16a34a;border:none;border-radius:7px;cursor:pointer;transition:background 0.15s;" id="sp-dl-${idx}">MP3</button>`;
    tracklist.appendChild(row);
    document.getElementById(`sp-dl-${idx}`).addEventListener('click', (e) => downloadSingleTrack(track, e.currentTarget));
  });
}

async function downloadSingleTrack(track, btn) {
  btn.innerHTML = '⏳'; btn.disabled = true;
  try {
    // Route qua background để có timeout 120s
    const result = await apiFetchLink({ url: track.search_query, quality: 'mp3_320', remove_watermark: true });
    if (!result?.ok || !result.data?.success) throw new Error(result?.data?.detail || result?.error || 'Server error');

    const data = result.data;
    let dlUrl = data.local_mp3_path || data.local_file_path || data.direct_mp4_url;
    if (!dlUrl) throw new Error('No download URL');
    const safeName = (data.title || track.name).replace(/[/\\?%*:|"<>]/g, '-');

    if (dlUrl.startsWith('downloads/') || dlUrl.startsWith('/app/downloads/')) {
      dlUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.mp3`;
    } else if (dlUrl.startsWith('http') && !dlUrl.includes('matbao.ai')) {
      dlUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=mp3`;
    }

    chrome.downloads.download({ url: dlUrl, filename: `VidGrab/${safeName}.mp3`, saveAs: false });
    btn.innerHTML = '✅'; btn.classList.replace('bg-green-600','bg-gray-600');
    chrome.runtime.sendMessage({ type: 'VG_SAVE_HISTORY', record: {
      title: data.title || track.name, thumbnail: track.thumbnail || '', url: track.search_query,
      quality: 'MP3', fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '',
    }});
  } catch(e) { btn.innerHTML = '❌'; btn.title = e.message; btn.classList.replace('bg-green-600','bg-red-600'); }
}

// ══════════════════════════════════════════════════════════════════
// MULTI-FORMAT POPUP
// ══════════════════════════════════════════════════════════════════
function showMultiFormat(data) {
  const panel = document.getElementById('formats-panel');
  const list = document.getElementById('formats-list');
  if (!panel || !list) return;

  const formats = data.available_formats || [];
  if (formats.length === 0) return;

  const videoUrl = data.original_url || '';
  list.innerHTML = '';

  formats.forEach((fmt) => {
    const row = document.createElement('div');
    row.className = 'fmt-item';

    const isVideo = fmt.type === 'video';
    const icon = isVideo ? '🎬' : '🎵';
    const label = fmt.label || (isVideo ? `${fmt.height}p` : 'Audio');
    const needsMerge = fmt.requires_merge || !fmt.url;
    const mergeBadge = needsMerge ? `<span class="fmt-badge fmt-badge-merge">GHÉP TỆP</span>` : '';
    const sizeMB = fmt.filesize_mb || fmt.file_size_mb || 0;
    const sizeBadge = sizeMB ? `<span style="font-size:10px;color:#f97316;font-weight:600;">${sizeMB} MB</span>` : '';
    const codecBadge = '';

    row.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:14px;">${icon}</span>
        <div>
          <div class="fmt-label">${label}</div>
          <div class="fmt-meta">${mergeBadge}${sizeBadge}</div>
        </div>
      </div>
      <span class="fmt-dl-btn">⬇ Tải</span>
    `;

    row.addEventListener('click', async () => {
      const dlBtn = row.querySelector('span:last-child');
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');
      const ext = isVideo ? 'mp4' : 'mp3';

      if (needsMerge) {
        dlBtn.textContent = '⏳';
        try {
          const quality = isVideo ? `video_${fmt.height}` : 'mp3_320';
          const result = await apiFetchLink({ url: videoUrl, quality, remove_watermark: _lastNoWm });
          if (!result?.ok || !result.data?.success) { dlBtn.textContent = '❌'; return; }

          const r = result.data;
          let finalUrl;
          const audioPath = ext === 'mp3' ? (r.local_mp3_path || r.local_file_path) : r.local_file_path;
          if (audioPath) {
            finalUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(audioPath)}&filename=${encodeURIComponent(safeName)}.${ext}`;
          } else if (r.direct_mp4_url) {
            finalUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(r.direct_mp4_url)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
          }
          if (finalUrl) { chrome.downloads.download({ url: finalUrl, filename: `VidGrab/${safeName}_${label}.${ext}`, saveAs: true }); dlBtn.textContent = '✅'; }
          else dlBtn.textContent = '❌';
        } catch { dlBtn.textContent = '❌'; }
        return;
      }

      const dlUrl = fmt.url;
      let finalUrl;
      if (dlUrl?.startsWith('/app/downloads/') || dlUrl?.startsWith('downloads/')) {
        finalUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}.${ext}`;
      } else if (dlUrl && !dlUrl.includes('matbao.ai')) {
        finalUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else {
        finalUrl = dlUrl;
      }
      if (finalUrl) { chrome.downloads.download({ url: finalUrl, filename: `VidGrab/${safeName}_${label}.${ext}`, saveAs: true }); dlBtn.textContent = '✅'; }
    });

    list.appendChild(row);
  });

  panel.classList.remove('hidden');
}

document.getElementById('formats-close')?.addEventListener('click', () => {
  document.getElementById('formats-panel')?.classList.add('hidden');
});

// ── Quality pill selector → hidden <select> (was an inline <script> in
// popup.html, which MV3's script-src 'self' CSP silently drops entirely —
// every quality pill click was a no-op and downloads always used whatever
// the <select>'s hardcoded default option was, regardless of what the user
// visually selected). ──
function updateDownloadBtn(val) {
  const btnText = document.getElementById('btnText');
  if (!btnText) return;
  btnText.textContent = val && val.startsWith('mp3') ? 'Tải Nhạc MP3 Ngay' : 'Tải Video Ngay';
}

document.querySelectorAll('.q-pill').forEach(pill => {
  pill.addEventListener('click', (e) => {
    // Prevent double-firing: radio click bubbles up to label → skip
    if (e.target.tagName === 'INPUT') return;
    document.querySelectorAll('.q-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    const val = pill.querySelector('input[type=radio]').value;
    document.getElementById('qualitySelect').value = val;
    pill.querySelector('input[type=radio]').checked = true;
    updateDownloadBtn(val);
  });
});
// Init select from active pill
const _activeQPill = document.querySelector('.q-pill.active input');
if (_activeQPill) {
  document.getElementById('qualitySelect').value = _activeQPill.value;
  updateDownloadBtn(_activeQPill.value);
}
