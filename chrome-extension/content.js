/**
 * VidGrab Content Script v2.2
 *
 * CHANNEL SCRAPER: dùng MutationObserver theo dõi DOM real-time (primary)
 * + interceptor.js network data (bonus nếu hoạt động)
 *
 * SINGLE VIDEO: nút floating download
 */
(function () {
  if (window.__vidgrab_injected) return;
  window.__vidgrab_injected = true;

  const API_BASE = 'https://dowload-video-trieunt.dev.matbao.ai';

  // ── Page type ─────────────────────────────────────────────────────
  function getPageType() {
    const url = window.location.href;
    if (
      /tiktok\.com\/@[^/]+\/video\//.test(url) ||
      /youtube\.com\/watch/.test(url) ||
      /facebook\.com\/.+\/videos\//.test(url) ||
      /facebook\.com\/watch/.test(url) ||
      /douyin\.com\/(video|note)\//.test(url)
    ) return 'video';
    if (/douyin\.com\/(user\/|@)/.test(url)) return 'channel';
    return null;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function buildProxyUrl(rawUrl, title, ext) {
    if (!rawUrl) return null;
    if (rawUrl.includes('matbao.ai')) return rawUrl;
    if (rawUrl.startsWith('/app/downloads/'))
      return `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(title || 'video')}.${ext}`;
    return `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(title || 'video')}&ext=${ext}`;
  }

  // ── Shared video store (DOM + interceptor) ────────────────────────
  const videoSet = new Set(); // canonical douyin.com/video/xxx URLs

  function scanDOM() {
    document.querySelectorAll('a[href]').forEach((a) => {
      // href có thể là relative (/video/xxx) hoặc absolute
      const href = a.href || '';
      const m = href.match(/douyin\.com\/(video|note)\/(\d{15,25})/);
      if (m) videoSet.add(`https://www.douyin.com/${m[1]}/${m[2]}`);
    });
    return videoSet.size;
  }

  // ── Relay từ interceptor.js (MAIN world) ──────────────────────────
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (event.data?.__vg_source !== 'interceptor') return;
    if (event.data?.type !== 'VG_DOUYIN_API') return;

    const videos = event.data.videos || [];
    videos.forEach((v) => {
      if (v.aweme_id) videoSet.add(`https://www.douyin.com/video/${v.aweme_id}`);
    });

    // Cũng lưu lên background để popup lấy được
    chrome.runtime.sendMessage({ type: 'VG_STORE_VIDEOS', videos }, () => {
      if (chrome.runtime.lastError) {}
    });

    updatePanelCount();
    console.log(`[VidGrab Interceptor] Captured ${videos.length} videos from API. Total: ${videoSet.size}`);
  });

  // Hàm update counter panel (gọi từ nhiều nơi)
  function updatePanelCount() {
    const n = videoSet.size;
    const countEl = document.getElementById('vg-ch-count');
    const sendBtn = document.getElementById('vg-send-btn');
    const sendText = document.getElementById('vg-send-text');
    const statusEl = document.getElementById('vg-ch-status');
    if (!countEl) return;

    countEl.textContent = n;
    if (n > 0) {
      sendBtn && sendBtn.classList.remove('hidden');
      if (sendText) sendText.textContent = `📤 Gửi tải ${n} video`;
      if (statusEl && !statusEl.dataset.locked) {
        statusEl.style.color = '#34d399';
        statusEl.textContent = `Live: ${n} video thu thập được. Cuộn thêm hoặc gửi ngay.`;
      }
    }
  }

  // ── Live count broadcast từ background ───────────────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'VG_LIVE_COUNT') updatePanelCount();
  });

  // ══════════════════════════════════════════════════════════════════
  // SINGLE VIDEO — Floating Button
  // ══════════════════════════════════════════════════════════════════
  function injectVideoButton() {
    if (document.getElementById('vg-float-btn')) return;

    const btn = document.createElement('button');
    btn.id = 'vg-float-btn';
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
           stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
      </svg>
      <span id="vg-btn-text">VidGrab</span>
    `;
    Object.assign(btn.style, {
      position: 'fixed', bottom: '80px', right: '20px', zIndex: '2147483647',
      display: 'flex', alignItems: 'center', gap: '6px', padding: '10px 16px',
      background: 'linear-gradient(135deg,#f97316,#eab308)',
      color: '#1a1a1a', border: 'none', borderRadius: '12px',
      fontWeight: '700', fontSize: '13px', cursor: 'pointer',
      boxShadow: '0 4px 20px rgba(249,115,22,0.45)',
      transition: 'transform 0.15s, opacity 0.15s',
      fontFamily: 'system-ui,-apple-system,sans-serif', lineHeight: '1',
    });

    btn.addEventListener('mouseover', () => { btn.style.transform = 'scale(1.05)'; });
    btn.addEventListener('mouseout',  () => { btn.style.transform = 'scale(1)'; });

    btn.addEventListener('click', async () => {
      const label = document.getElementById('vg-btn-text');
      label.textContent = 'Đang lấy link...';
      btn.style.opacity = '0.7';
      btn.disabled = true;

      try {
        const res = await fetch(`${API_BASE}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: window.location.href, quality: 'video', remove_watermark: true }),
        });
        const data = await res.json();

        if (data.success) {
          const dlUrl = buildProxyUrl(data.direct_mp4_url || data.local_file_path, data.title, 'mp4');
          if (dlUrl) {
            window.open(dlUrl, '_blank');
            label.textContent = 'Thành công!';
            btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
          } else throw new Error('Không lấy được link tải');
        } else throw new Error(data.detail || 'Server báo lỗi');
      } catch (err) {
        label.textContent = 'Lỗi!';
        btn.style.background = '#ef4444';
        console.error('[VidGrab]', err.message);
      }

      await sleep(3000);
      label.textContent = 'VidGrab';
      btn.style.opacity = '1';
      btn.style.background = 'linear-gradient(135deg,#f97316,#eab308)';
      btn.disabled = false;
    });

    document.body.appendChild(btn);
  }

  // ══════════════════════════════════════════════════════════════════
  // CHANNEL PANEL — Douyin profile/user pages
  // ══════════════════════════════════════════════════════════════════
  let isScraping = false;
  let domObserver = null;

  function injectChannelPanel() {
    if (document.getElementById('vg-channel-panel')) return;

    const panel = document.createElement('div');
    panel.id = 'vg-channel-panel';
    panel.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <div style="background:linear-gradient(135deg,#f97316,#eab308);color:#1a1a1a;font-weight:800;
                    padding:2px 8px;border-radius:6px;font-size:11px;letter-spacing:.5px">VG</div>
        <span style="font-weight:700;font-size:13px;color:#fff">Channel Scraper</span>
        <span id="vg-live-badge" style="margin-left:auto;font-size:10px;padding:2px 7px;
              background:#064e3b;color:#34d399;border-radius:999px">● LIVE</span>
        <button id="vg-panel-close" style="background:none;border:none;color:#6b7280;
                cursor:pointer;font-size:16px;line-height:1;padding:0;margin-left:4px">✕</button>
      </div>

      <div style="background:#111827;border-radius:8px;padding:8px 12px;margin-bottom:10px;
                  display:flex;justify-content:space-between;align-items:center">
        <div>
          <div style="font-size:10px;color:#6b7280;margin-bottom:2px">Video thu thập được</div>
          <div id="vg-ch-count" style="font-size:26px;font-weight:800;color:#f97316;line-height:1">0</div>
        </div>
        <div style="text-align:right">
          <div id="vg-source-badge" style="font-size:9px;color:#4b5563;margin-bottom:4px">DOM Observer</div>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#374151" stroke-width="1.5">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
          </svg>
        </div>
      </div>

      <div id="vg-ch-status" style="font-size:11px;color:#9ca3af;margin-bottom:10px;
                                    min-height:16px;line-height:1.4">
        Đang theo dõi trang... cuộn để load thêm video.
      </div>

      <div id="vg-scroll-wrap" style="display:none;margin-bottom:10px">
        <div style="height:3px;background:#1f2937;border-radius:999px;overflow:hidden;margin-bottom:4px">
          <div id="vg-scroll-bar" style="height:100%;background:linear-gradient(90deg,#f97316,#eab308);
                                          width:0%;transition:width 0.4s"></div>
        </div>
        <div id="vg-scroll-info" style="font-size:10px;color:#6b7280;text-align:right"></div>
      </div>

      <button id="vg-scrape-btn" style="width:100%;background:linear-gradient(135deg,#f97316,#eab308);
        color:#1a1a1a;font-weight:700;border:none;border-radius:8px;padding:9px;cursor:pointer;
        font-size:12px;margin-bottom:6px;transition:opacity .2s">
        🔄 Auto-Scroll &amp; Collect
      </button>

      <button id="vg-send-btn" style="width:100%;background:#22c55e;color:#fff;font-weight:700;
        border:none;border-radius:8px;padding:9px;cursor:pointer;font-size:12px;
        transition:opacity .2s;display:none">
        <span id="vg-send-text">📤 Gửi tải xuống server</span>
      </button>

      <div id="vg-send-result" style="font-size:10px;color:#6b7280;margin-top:6px;
                                       text-align:center;display:none"></div>
    `;

    Object.assign(panel.style, {
      position: 'fixed', bottom: '20px', right: '20px', zIndex: '2147483647',
      background: '#1f2937', color: '#fff', border: '1px solid #374151',
      borderRadius: '14px', padding: '14px', width: '230px',
      boxShadow: '0 10px 40px rgba(0,0,0,0.55)',
      fontFamily: 'system-ui,-apple-system,sans-serif',
    });

    document.body.appendChild(panel);

    const scrapeBtn  = document.getElementById('vg-scrape-btn');
    const sendBtn    = document.getElementById('vg-send-btn');
    const sendText   = document.getElementById('vg-send-text');
    const sendResult = document.getElementById('vg-send-result');
    const scrollWrap = document.getElementById('vg-scroll-wrap');
    const scrollBar  = document.getElementById('vg-scroll-bar');
    const scrollInfo = document.getElementById('vg-scroll-info');
    const statusEl   = document.getElementById('vg-ch-status');

    document.getElementById('vg-panel-close').addEventListener('click', () => {
      stopDomObserver();
      panel.remove();
    });

    // ── MutationObserver: theo dõi DOM thay đổi realtime ─────────
    let scanThrottle = null;

    function onDomChange() {
      if (scanThrottle) return;
      scanThrottle = setTimeout(() => {
        scanThrottle = null;
        const n = scanDOM();
        const countEl = document.getElementById('vg-ch-count');
        if (!countEl) return;
        countEl.textContent = n;
        if (n > 0) {
          sendBtn.style.display = 'block';
          sendText.textContent = `📤 Gửi tải ${n} video`;
          if (!statusEl.dataset.locked) {
            statusEl.style.color = '#34d399';
            statusEl.textContent = `Live: ${n} video trong DOM. Cuộn thêm để load thêm.`;
          }
        }
      }, 300);
    }

    domObserver = new MutationObserver(onDomChange);
    domObserver.observe(document.body, { childList: true, subtree: true });

    // Scan ngay lập tức khi inject
    const initialCount = scanDOM();
    if (initialCount > 0) {
      document.getElementById('vg-ch-count').textContent = initialCount;
      sendBtn.style.display = 'block';
      sendText.textContent = `📤 Gửi tải ${initialCount} video`;
      statusEl.style.color = '#34d399';
      statusEl.textContent = `Live: ${initialCount} video thu thập được. Cuộn để load thêm.`;
    }

    // ── Auto-Scroll button ─────────────────────────────────────────
    scrapeBtn.addEventListener('click', async () => {
      if (isScraping) return;
      isScraping = true;
      scrapeBtn.style.opacity = '0.5';
      scrapeBtn.textContent = '⏳ Đang cuộn...';
      scrapeBtn.disabled = true;
      scrollWrap.style.display = 'block';
      statusEl.dataset.locked = '1';

      let prevCount = videoSet.size;
      let stable = 0;
      const MAX_STABLE = 5;
      let round = 0;

      while (stable < MAX_STABLE) {
        round++;
        window.scrollBy({ top: window.innerHeight * 1.8, behavior: 'smooth' });
        await sleep(2200);

        const curr = videoSet.size;
        const pct = Math.min(90, Math.round((stable / MAX_STABLE) * 100));
        scrollBar.style.width = pct + '%';
        scrollInfo.textContent = `Cuộn #${round} • ${curr} video • stable ${stable}/${MAX_STABLE}`;

        if (curr === prevCount) stable++;
        else { stable = 0; prevCount = curr; }
      }

      scrollBar.style.width = '100%';
      const finalCount = videoSet.size;
      delete statusEl.dataset.locked;
      statusEl.style.color = finalCount > 0 ? '#34d399' : '#9ca3af';
      statusEl.textContent = finalCount > 0
        ? `Hoàn tất! ${finalCount} video. Nhấn nút xanh để gửi tải.`
        : 'Không tìm thấy video. Thử đăng nhập Douyin rồi thử lại.';

      scrapeBtn.style.opacity = '1';
      scrapeBtn.textContent = '🔄 Cuộn thêm';
      scrapeBtn.disabled = false;
      isScraping = false;
    });

    // ── Gửi tải ───────────────────────────────────────────────────
    sendBtn.addEventListener('click', async () => {
      const urls = Array.from(videoSet);
      if (urls.length === 0) {
        statusEl.textContent = 'Chưa có video — cuộn trang để load thêm.';
        return;
      }

      sendBtn.disabled = true;
      sendText.textContent = `⏳ Đang gửi ${urls.length} video...`;

      try {
        const res = await fetch(`${API_BASE}/api/v1/bulk-download`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ urls, quality: 'video', remove_watermark: true }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.success) {
          statusEl.style.color = '#34d399';
          statusEl.textContent = `✅ Đã xếp hàng ${urls.length} video!`;
          sendBtn.style.background = '#16a34a';
          sendText.textContent = '✅ Đã gửi thành công';
          sendResult.style.display = 'block';
          sendResult.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0,10)}…</span>`;
          window.open(`${API_BASE}?batch=${data.batch_id}`, '_blank');
        } else throw new Error(data.detail || 'Server lỗi');
      } catch (err) {
        statusEl.style.color = '#f87171';
        statusEl.textContent = `❌ Lỗi: ${err.message}`;
        sendBtn.disabled = false;
        sendText.textContent = `📤 Thử lại (${urls.length} video)`;
      }
    });
  }

  function stopDomObserver() {
    if (domObserver) { domObserver.disconnect(); domObserver = null; }
  }

  // ── Message handler từ popup ───────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === 'VG_GET_PAGE_INFO') {
      sendResponse({
        pageType: getPageType(),
        url: window.location.href,
        title: document.title,
        videoCount: videoSet.size,
      });
    }
    if (msg.type === 'VG_TRIGGER_SCROLL') {
      const btn = document.getElementById('vg-scrape-btn');
      if (btn && !btn.disabled) btn.click();
      sendResponse({ ok: true });
    }
    if (msg.type === 'VG_GET_COUNT') {
      sendResponse({ count: videoSet.size });
    }
    if (msg.type === 'VG_GET_URLS') {
      sendResponse({ urls: Array.from(videoSet) });
    }
    return true;
  });

  // ── SPA navigation handler ──────────────────────────────────────────
  let lastUrl = window.location.href;
  new MutationObserver(() => {
    if (window.location.href === lastUrl) return;
    lastUrl = window.location.href;
    document.getElementById('vg-float-btn')?.remove();
    document.getElementById('vg-channel-panel')?.remove();
    stopDomObserver();
    videoSet.clear();
    setTimeout(() => {
      const t = getPageType();
      if (t === 'video') injectVideoButton();
      if (t === 'channel') injectChannelPanel();
    }, 1200);
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ── Init ────────────────────────────────────────────────────────────
  const type = getPageType();
  if (type === 'video') injectVideoButton();
  if (type === 'channel') injectChannelPanel();
})();
