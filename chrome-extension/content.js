/**
 * VidGrab Content Script v4.0
 *
 * CHANNEL SCRAPER: dùng MutationObserver theo dõi DOM real-time (primary)
 * + interceptor.js network data (bonus nếu hoạt động)
 *
 * SINGLE VIDEO: nút floating download + file size preview
 */
(function () {
  if (window.__vidgrab_injected) return;
  window.__vidgrab_injected = true;

  let API_BASE = 'https://dvid-api.cmc-1.vibenode.matbao.ai';
  // Web app (frontend) — separate domain from the API backend. Used only to
  // open the batch-progress page after a bulk send; never for API calls.
  const FRONTEND_BASE = 'https://dvid.cmc-1.vibenode.matbao.ai';
  // Load custom server URL từ storage (nếu có).
  // Same validation as background.js/popup.js — an unvalidated value here
  // would also desync us from the background worker, whose VG_API_FETCH now
  // only proxies requests aimed at its own normalized origin.
  const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
  function normalizeApiBase(value) {
    if (!value || typeof value !== 'string') return null;
    let u;
    try { u = new URL(value.trim()); } catch { return null; }
    const isLocal = LOCAL_HOSTS.includes(u.hostname);
    if (u.protocol !== 'https:' && !(u.protocol === 'http:' && isLocal)) return null;
    return (u.origin + u.pathname).replace(/\/+$/, '');
  }
  chrome.storage.sync.get('vg_api_base', (r) => {
    API_BASE = normalizeApiBase(r.vg_api_base) || API_BASE;
  });

  // Metadata like the "creator" field is scraped from the page's own meta
  // tags / title — the page (or a compromised/malicious one) controls that
  // string, so it must be escaped before going into innerHTML.
  function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // ── Page type ─────────────────────────────────────────────────────
  function getPageType() {
    const url = window.location.href;
    if (
      /tiktok\.com\/@[^/]+\/video\//.test(url) ||
      /youtube\.com\/watch/.test(url) ||
      /youtube\.com\/shorts\//.test(url) ||
      /facebook\.com\/.+\/videos\//.test(url) ||
      /facebook\.com\/watch/.test(url) ||
      /facebook\.com\/reel\//.test(url) ||
      /facebook\.com\/share\/r\//.test(url) ||
      /douyin\.com\/(video|note)\//.test(url) ||
      /douyin\.com\/.*modal_id=/.test(url) ||
      /open\.spotify\.com\/track\//.test(url) ||
      /instagram\.com\/(p|reel|reels)\//.test(url) ||
      /instagram\.com\/stories\//.test(url) ||
      /twitter\.com\/[\w.-]+\/status\/\d+/.test(url) ||
      /x\.com\/[\w.-]+\/status\/\d+/.test(url) ||
      /reddit\.com\/r\/[\w.-]+\/comments\/[\w]+/.test(url) ||
      /pinterest\.com\/pin\/\d+/.test(url) ||
      /threads\.(net|com)\/@[\w.-]+\/post\//.test(url)
    ) return 'video';
    
    if (/douyin\.com\/(user\/|@)/.test(url) && !url.includes('modal_id=')) return 'channel';
    
    if (
      /tiktok\.com\/@/.test(url) ||
      /youtube\.com\/(c|channel|user|@)/.test(url) ||
      /youtube\.com\/playlist\?list=/.test(url) ||
      /facebook\.com\/[^/]+\/?$/.test(url) ||
      /twitter\.com\/@?[\w.-]+\/?$/.test(url) ||
      /x\.com\/@?[\w.-]+\/?$/.test(url) ||
      /twitter\.com\/[\w.-]+\/media/.test(url) ||
      /x\.com\/[\w.-]+\/media/.test(url) ||
      /reddit\.com\/r\/[\w.-]+\/?$/.test(url) ||
      /reddit\.com\/user\/[\w.-]+\/?$/.test(url) ||
      /pinterest\.com\/[\w.-]+\/[\w.-]+\/?$/.test(url) ||
      /threads\.(net|com)\/@[\w.-]+\/?$/.test(url)
    ) return 'generic_channel';

    if (/open\.spotify\.com\/(playlist|album)\//.test(url)) return 'spotify_playlist';
    return null;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── Page metadata extraction (OG tags + schema.org) ───────────────
  function extractPageMetadata() {
    const meta = {};

    // Title
    meta.title =
      document.querySelector('meta[property="og:title"]')?.content ||
      document.querySelector('meta[name="title"]')?.content ||
      document.title ||
      '';

    // Thumbnail
    meta.thumbnail =
      document.querySelector('meta[property="og:image"]')?.content ||
      document.querySelector('meta[name="twitter:image"]')?.content ||
      '';

    // Description snippet
    meta.description =
      (document.querySelector('meta[property="og:description"]')?.content || '').slice(0, 150);

    // Creator / channel
    meta.creator =
      document.querySelector('meta[name="author"]')?.content ||
      document.querySelector('[itemprop="author"] [itemprop="name"]')?.textContent?.trim() ||
      // YouTube channel name from page title pattern "VideoTitle - Channel - YouTube"
      (document.title.includes(' - ') ? document.title.split(' - ').slice(-2, -1)[0]?.trim() : null) ||
      '';

    // Duration from structured data (schema.org VideoObject)
    const durationEl = document.querySelector('[itemprop="duration"]');
    if (durationEl) {
      const raw = durationEl.getAttribute('content') || durationEl.textContent || '';
      // Parse ISO 8601 duration: PT1H2M3S
      const match = raw.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
      if (match) {
        const h = parseInt(match[1] || 0);
        const m = parseInt(match[2] || 0);
        const s = parseInt(match[3] || 0);
        const total = h * 3600 + m * 60 + s;
        if (total > 0) {
          meta.duration_seconds = total;
          meta.duration_str = h > 0
            ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
            : `${m}:${String(s).padStart(2, '0')}`;
        }
      }
    }

    // View count from structured data
    const viewEl = document.querySelector('[itemprop="interactionCount"]');
    if (viewEl) {
      const v = parseInt(viewEl.getAttribute('content') || viewEl.textContent || '0');
      if (v > 0) {
        meta.view_count = v;
        meta.view_count_str = v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` :
                              v >= 1e3 ? `${Math.round(v / 1e3)}K` : String(v);
      }
    }

    return meta;
  }

  // ── Compact digest HTML builder ────────────────────────────────────
  function buildDigestHtml(meta) {
    const lines = [];

    if (meta.creator) {
      lines.push(`<div style="color:#9ca3af;font-size:10px;margin-bottom:2px;">@${escapeHtml(meta.creator)}</div>`);
    }

    const badges = [];
    if (meta.duration_str) badges.push(`⏱ ${escapeHtml(meta.duration_str)}`);
    if (meta.view_count_str) badges.push(`👁 ${escapeHtml(meta.view_count_str)}`);

    if (badges.length > 0) {
      lines.push(`<div style="display:flex;gap:8px;font-size:10px;color:#6b7280;">${badges.join('')}</div>`);
    }

    // Estimated sizes for top 3 qualities
    if (meta.duration_seconds) {
      const d = meta.duration_seconds;
      const sizes = [
        { q: '1080p', mb: Math.round(d * 8 * 0.125) },
        { q: '720p',  mb: Math.round(d * 4 * 0.125) },
        { q: 'Audio', mb: Math.round(d * 0.128 * 0.125) },
      ];
      const sizeHtml = sizes.map(({ q, mb }) =>
        `<span style="background:#1e293b;border:1px solid #334155;border-radius:4px;padding:1px 5px;font-size:9px;">` +
        `<span style="color:#94a3b8;">${q}</span>` +
        `<span style="color:#cbd5e1;margin-left:3px;">${mb >= 1024 ? (mb / 1024).toFixed(1) + 'GB' : mb + 'MB'}</span>` +
        `</span>`
      ).join('');
      lines.push(`<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;">${sizeHtml}</div>`);
    }

    if (lines.length === 0) return '';

    return `
      <div id="vg-digest" style="
        margin-top:6px;
        padding:8px 10px;
        background:#0f172a;
        border:1px solid #334155;
        border-radius:8px;
        font-family:system-ui,sans-serif;
        line-height:1.4;
      ">
        <div style="color:#f1f5f9;font-size:10px;font-weight:600;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center;">
          <span>📋 Thông tin</span>
          <span id="vg-digest-toggle" style="cursor:pointer;color:#64748b;font-size:9px;">▲</span>
        </div>
        <div id="vg-digest-body">
          ${lines.join('')}
        </div>
      </div>
    `;
  }

  function buildProxyUrl(rawUrl, title, ext) {
    if (!rawUrl) return null;
    if (rawUrl.includes('matbao.ai')) return rawUrl;
    if (rawUrl.startsWith('/app/downloads/'))
      return `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(title || 'video')}.${ext}`;
    return `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(title || 'video')}&ext=${ext}`;
  }

  // ── Shared video store (DOM + interceptor) ────────────────────────
  const videoSet = new Set(); // canonical video URLs

  function scanDOM() {
    document.querySelectorAll('a[href]').forEach((a) => {
      const href = a.href || '';
      // Douyin: /video/xxx or /note/xxx
      const dyMatch = href.match(/douyin\.com\/(video|note)\/(\d{15,25})/);
      if (dyMatch) {
        videoSet.add(`https://www.douyin.com/${dyMatch[1]}/${dyMatch[2]}`);
        return;
      }
      // TikTok: /@user/video/xxx
      const tkMatch = href.match(/tiktok\.com\/@[\w.-]+\/video\/(\d{15,25})/);
      if (tkMatch) {
        videoSet.add(href.split('?')[0]); // clean tracking params
        return;
      }
    });
    return videoSet.size;
  }

  // ── E2: media URLs captured by the page (user session/IP) ──────────
  const _vgCapturedMedia = new Set();   // direct media URLs seen on this page

  // ── Relay từ interceptor.js (MAIN world) ──────────────────────────
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (event.data?.__vg_source !== 'interceptor') return;

    // E2: generic media URL captured from page network (video/* or .mp4)
    if (event.data?.type === 'VG_MEDIA_FOUND' && event.data.url) {
      const u = event.data.url;
      if (/^https?:/i.test(u)) _vgCapturedMedia.add(u);
      return;
    }

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

    // Wrapper container — holds button + digest panel together
    const wrapper = document.createElement('div');
    wrapper.id = 'vg-float-wrapper';
    Object.assign(wrapper.style, {
      position: 'fixed', bottom: '80px', right: '20px', zIndex: '2147483647',
      display: 'flex', flexDirection: 'column', alignItems: 'stretch',
      width: '200px',
      fontFamily: 'system-ui,-apple-system,sans-serif',
    });

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
      display: 'flex', alignItems: 'center', gap: '6px', padding: '10px 16px',
      background: 'linear-gradient(135deg,#f97316,#eab308)',
      color: '#1a1a1a', border: 'none', borderRadius: '12px',
      fontWeight: '700', fontSize: '13px', cursor: 'pointer',
      boxShadow: '0 4px 20px rgba(249,115,22,0.45)',
      transition: 'transform 0.15s, opacity 0.15s',
      fontFamily: 'system-ui,-apple-system,sans-serif', lineHeight: '1',
      width: '100%',
    });

    btn.addEventListener('mouseover', () => { btn.style.transform = 'scale(1.05)'; });
    btn.addEventListener('mouseout',  () => { btn.style.transform = 'scale(1)'; });

    btn.addEventListener('click', async () => {
      const label = document.getElementById('vg-btn-text');
      label.textContent = 'Đang lấy link...';
      btn.style.opacity = '0.7';
      btn.disabled = true;

      try {
        // Gọi API qua background service worker để bypass CORS
        const resp = await new Promise((resolve) =>
          chrome.runtime.sendMessage({
            type: 'VG_API_FETCH',
            url: `${API_BASE}/api/v1/fetch-link`,
            method: 'POST',
            body: { 
               url: window.location.href.includes('spotify.com') ? (document.querySelector('a[data-testid="context-item-info-title"]')?.href || document.querySelector('a[data-testid="context-item-link"]')?.href || window.location.href) : window.location.href, 
               quality: window.location.href.includes('spotify.com') ? 'mp3_320' : 'video', 
               remove_watermark: true 
            },
          }, resolve)
        );
        if (!resp || !resp.ok) throw new Error(resp?.data?.detail || resp?.error || 'Server lỗi');
        const data = resp.data;

        if (data.success) {
          const targetUrl = data.direct_mp4_url || data.local_file_path;
          let ext = 'mp4';
          if (data.is_audio_only || (targetUrl && (targetUrl.endsWith('.mp3') || targetUrl.endsWith('.m4a')))) {
             ext = 'mp3';
          }
          const dlUrl = buildProxyUrl(targetUrl, data.title, ext);
          if (dlUrl) {
            window.open(dlUrl, '_blank');
            const sizeInfo = data.file_size_mb ? ` (${data.file_size_mb} MB)` : '';
            label.textContent = `✅ Đang tải${sizeInfo}`;
            btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
          } else throw new Error('Không lấy được link tải');
        } else throw new Error(data.detail || 'Server báo lỗi');
      } catch (err) {
        label.textContent = '❌ Thử lại';
        btn.style.background = '#ef4444';
        console.error('[VidGrab]', err.message);
        showVGToast(`❌ ${err.message}`);
      }

      await sleep(4000);
      label.textContent = 'VidGrab';
      btn.style.opacity = '1';
      btn.style.background = 'linear-gradient(135deg,#f97316,#eab308)';
      btn.disabled = false;
    });

    wrapper.appendChild(btn);

    // Digest panel — metadata preview, no API call
    try {
      const pageMeta = extractPageMetadata();
      const digestHtml = buildDigestHtml(pageMeta);
      if (digestHtml) {
        const digestWrapper = document.createElement('div');
        digestWrapper.innerHTML = digestHtml;
        const digestEl = digestWrapper.firstElementChild;
        wrapper.appendChild(digestEl);

        // Toggle body visibility on click
        const toggle = digestEl.querySelector('#vg-digest-toggle');
        const body = digestEl.querySelector('#vg-digest-body');
        if (toggle && body) {
          let collapsed = false;
          toggle.addEventListener('click', () => {
            collapsed = !collapsed;
            body.style.display = collapsed ? 'none' : '';
            toggle.textContent = collapsed ? '▼' : '▲';
          });
        }
      }
    } catch (e) {
      // Digest is optional — never break download flow
    }

    document.body.appendChild(wrapper);
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
        // Gọi API qua background service worker để bypass CORS
        const resp = await new Promise((resolve) =>
          chrome.runtime.sendMessage({
            type: 'VG_API_FETCH',
            url: `${API_BASE}/api/v1/bulk-download`,
            method: 'POST',
            body: { urls, quality: 'video', remove_watermark: true },
          }, resolve)
        );
        if (!resp || !resp.ok) throw new Error(resp?.data?.detail || resp?.error || `HTTP ${resp?.status || 'unknown'}`);
        const data = resp.data;

        if (data.success) {
          statusEl.style.color = '#34d399';
          statusEl.textContent = `✅ Đã xếp hàng ${urls.length} video!`;
          sendBtn.style.background = '#16a34a';
          sendText.textContent = '✅ Đã gửi thành công';
          sendResult.style.display = 'block';
          sendResult.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0,10)}…</span>`;
          window.open(`${FRONTEND_BASE}?batch=${data.batch_id}`, '_blank');
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

  // ── E2: scan the current page for directly-downloadable media URLs ──
  // Sources: network-captured (interceptor) + <video>/<source> + og:video +
  // JSON-LD contentUrl. Skips blob:/data: (MSE → no real URL) and HLS/DASH
  // manifests (need server merge). Returns best-first (.mp4 preferred).
  function _vgScanPageMedia() {
    const out = new Set();
    const add = (u) => {
      if (!u || typeof u !== 'string') return;
      if (!/^https?:\/\//i.test(u)) return;            // skip blob:/data:/relative
      if (/\.(m3u8|mpd|ts)(\?|$)/i.test(u)) return;     // manifests/segments → server
      if (/\.(jpg|jpeg|png|webp|gif|svg|heic)(\?|$)/i.test(u)) return; // images
      out.add(u.split('#')[0]);
    };
    _vgCapturedMedia.forEach(add);
    document.querySelectorAll('video').forEach((v) => {
      add(v.currentSrc); add(v.src);
      v.querySelectorAll('source').forEach((s) => add(s.src));
    });
    document.querySelectorAll('source[src]').forEach((s) => add(s.src));
    document.querySelectorAll(
      'meta[property="og:video"],meta[property="og:video:url"],meta[property="og:video:secure_url"],meta[name="twitter:player:stream"]'
    ).forEach((m) => add(m.content));
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try {
        const j = JSON.parse(s.textContent);
        (Array.isArray(j) ? j : [j]).forEach((o) => { if (o && o.contentUrl) add(o.contentUrl); });
      } catch {}
    });
    // E3: progressive URLs embedded inline in the page JSON (FB/IG reels etc.)
    try {
      const html = document.documentElement.innerHTML;
      if (/playable_url|browser_native|video_url|contentUrl/.test(html)) {
        const re = /"(?:playable_url(?:_quality_hd)?|browser_native_(?:hd|sd)_url|video_url|contentUrl)":"(https:\\?\/\\?\/[^"]+)"/g;
        let m, n = 0;
        while ((m = re.exec(html)) && n < 12) {
          n++;
          let u = m[1];
          try { u = JSON.parse('"' + u + '"'); } catch { u = u.replace(/\\\//g, '/'); }
          add(u);
        }
      }
    } catch {}
    return Array.from(out).sort((a, b) => (/\.mp4/i.test(b) ? 1 : 0) - (/\.mp4/i.test(a) ? 1 : 0));
  }

  // ── Message handler từ popup ───────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === 'VG_SCAN_PAGE_MEDIA') {
      sendResponse({ media: _vgScanPageMedia(), pageTitle: document.title });
      return true;
    }
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
    // Toast notification from keyboard shortcut
    if (msg.type === 'VG_SHOW_TOAST') {
      showVGToast(msg.text);
    }
    return true;
  });

  // ── SPA navigation handler ──────────────────────────────────────────
  let lastUrl = window.location.href;
  new MutationObserver(() => {
    if (window.location.href === lastUrl) return;
    lastUrl = window.location.href;
    document.getElementById('vg-float-wrapper')?.remove();
    document.getElementById('vg-channel-panel')?.remove();
    stopDomObserver();
    videoSet.clear();
    setTimeout(() => {
      const t = getPageType();
      // if (t === 'video' || window.location.href.includes('spotify.com')) injectVideoButton();
      if (t === 'channel' || t === 'spotify_playlist' || t === 'generic_channel') injectChannelPanel();
    }, 1200);
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ── Init ────────────────────────────────────────────────────────────
  const type = getPageType();
  // if (type === 'video' || window.location.href.includes('spotify.com') || window.location.href.includes('instagram.com')) injectVideoButton();
  if (type === 'channel' || type === 'spotify_playlist' || type === 'generic_channel') injectChannelPanel();

  // ── Toast notification helper (for keyboard shortcut feedback) ───
  function showVGToast(text) {
    let toast = document.getElementById('vg-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'vg-toast';
      Object.assign(toast.style, {
        position: 'fixed', top: '20px', right: '20px', zIndex: '2147483647',
        background: '#1f2937', color: '#fff', padding: '10px 18px',
        borderRadius: '12px', fontSize: '13px', fontWeight: '600',
        fontFamily: 'system-ui, sans-serif', boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
        border: '1px solid #374151', transition: 'opacity 0.3s, transform 0.3s',
        opacity: '0', transform: 'translateY(-10px)',
      });
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.style.opacity = '1'; toast.style.transform = 'translateY(0)';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.style.opacity = '0'; toast.style.transform = 'translateY(-10px)';
    }, 3000);
  }
})();
