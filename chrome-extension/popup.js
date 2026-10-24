const DEFAULT_API_BASE = 'https://dvid-api.vibe1.tinhgon.xyz';
const WEB_BASE = 'https://dvid.vibe1.tinhgon.xyz';
let API_BASE = DEFAULT_API_BASE;

// ── i18n (Chrome built-in: _locales/vi + _locales/en, default_locale "en") ──
// t(key, ...subs): one full sentence per message, "$1"-style placeholders.
const t = (key, ...subs) => {
  try { return chrome.i18n.getMessage(key, subs.map((s) => String(s))) || key; } catch { return key; }
};
const tn = (key, n, ...subs) => {
  // English singular ("1 video"): <key>_one when the count is 1; Vietnamese has the same text for both.
  try {
    if (Number(n) === 1) { const one = chrome.i18n.getMessage(`${key}_one`, [n, ...subs].map((s) => String(s))); if (one) return one; }
  } catch { /* fall back to the plural form */ }
  return t(key, n, ...subs);
};
// Static text in popup.html: data-i18n (textContent — never innerHTML),
// data-i18n-placeholder / -title / -aria.
function vgApplyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder)); });
  root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.setAttribute('title', t(el.dataset.i18nTitle)); });
  root.querySelectorAll('[data-i18n-aria]').forEach((el) => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
  try { document.documentElement.lang = chrome.i18n.getUILanguage().split('-')[0]; } catch { /* keep default */ }
}
vgApplyI18n();


// Every call to our API carries the sign-in token when there is one: the
// server caps guests at 1080p and 5 downloads/day, so a signed-in user's
// request sent without it was served as a guest's (08/10, task #6148).
async function vgAuthHeaders(extra = {}) {
  try {
    const r = await chrome.storage.local.get('vg_auth_token');
    let ver = '';
    try { ver = chrome.runtime.getManifest().version || ''; } catch { /* no manifest in tests */ }
    const h = ver ? { ...extra, 'X-VG-Ext-Version': ver } : extra;
    return r.vg_auth_token ? { ...h, Authorization: `Bearer ${r.vg_auth_token}` } : h;
  } catch { return extra; }
}

// Mirrors normalizeApiBase() in background.js. The Settings field used to
// accept anything starting with "http", including plain-http hosts — and the
// value syncs across every browser on the account. Require https (http only
// for localhost dev) so a bad paste can't downgrade the channel.
const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
// ── First-party host (keep identical in background.js / popup.js / content.js) ──
// vibe1.tinhgon.xyz is a SHARED multi-tenant domain: never trust a suffix or a
// substring of it. Only these two exact hostnames are ours - hostname equality,
// no suffix rule of any kind (the old shared-suffix rule was removed in 5.2.4).
const FIRST_PARTY_EXACT_HOSTS = ['dvid.vibe1.tinhgon.xyz', 'dvid-api.vibe1.tinhgon.xyz'];
const LEGACY_API_HOST = 'dvid-api.cmc-1.vibenode.matbao.ai';   // dead host; ONLY used to migrate a stored old base to DEFAULT_API_BASE, never trusted
function isFirstPartyHost(hostname) {
  const h = String(hostname || '').toLowerCase();
  return FIRST_PARTY_EXACT_HOSTS.includes(h);
}
// "Is this URL one of OUR servers?" — https + hostname equality, never includes().
function isFirstPartyUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    return u.protocol === 'https:' && isFirstPartyHost(u.hostname);
  } catch { return false; }
}
// One-time migration: a saved base that still points at the dead host -> new API.
function migrateLegacyApiBase(value) {
  try {
    if (new URL(String(value).trim()).hostname === LEGACY_API_HOST) return DEFAULT_API_BASE;
  } catch { /* not a URL */ }
  return value;
}
function normalizeApiBase(value) {
  if (!value || typeof value !== 'string') return null;
  value = migrateLegacyApiBase(value);
  let u;
  try { u = new URL(value.trim()); } catch { return null; }
  const isLocal = LOCAL_HOSTS.includes(u.hostname);
  if (u.protocol !== 'https:' && !(u.protocol === 'http:' && isLocal)) return null;
  return (u.origin + u.pathname).replace(/\/+$/, '');
}

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
  document.getElementById('ch-discovery-msg').textContent = t('ch_connecting');
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

// "Dừng": the server cancels every video of the batch not started yet (and a
// channel still being scanned); videos already downloading finish.
async function stopChannelBatch(btn) {
  if (!_chBatchId || btn.disabled) return;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = t('stopping');
  try {
    const resp = await fetch(`${API_BASE}/api/v1/jobs/${_chBatchId}/cancel`, {
      method: 'POST', headers: await vgAuthHeaders(),
    });
    if (resp.ok) btn.classList.add('hidden');
  } catch { /* the next poll shows the real state */ }
  btn.disabled = false;
  btn.textContent = label;
}
document.getElementById('ch-stop-scan')?.addEventListener('click', (e) => stopChannelBatch(e.currentTarget));
document.getElementById('ch-stop-batch')?.addEventListener('click', (e) => stopChannelBatch(e.currentTarget));

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
      const resp = await fetch(`${API_BASE}/api/v1/jobs/${batchId}`, { headers: await vgAuthHeaders() });
      if (!resp.ok) { _chJobPollTimer = setTimeout(poll, nextDelay()); return; }
      const data = await resp.json();
      const jobs = (data.jobs || []).filter(j => j.original_url !== 'batch_zip');

      // Discovery phase: only 1 processing job (the channel placeholder)
      const isDiscovering = jobs.length <= 1 && jobs.some(j => j.status === 'processing');
      document.getElementById('ch-stop-scan').classList.toggle('hidden', !isDiscovering);
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
      const failed = videoJobs.filter(j => j.status === 'failed').length;
      const stopped = videoJobs.filter(j => j.status === 'cancelled').length;
      const total = Math.max(videoJobs.length, 1);
      const pct = Math.round((success / total) * 100);
      // Finished = success, failed or stopped ("Dừng"); polling used to wait
      // for success on every job, so one failure kept it polling forever.
      const left = videoJobs.filter(j => !['success', 'failed', 'cancelled'].includes(j.status)).length;
      const waiting = videoJobs.some(j => ['pending', 'queued', 'retrying'].includes(j.status));
      document.getElementById('ch-stop-batch').classList.toggle('hidden', !waiting);

      document.getElementById('ch-job-stats').textContent = `${success} / ${total}`;
      document.getElementById('ch-job-bar').style.width = pct + '%';
      const extra = [failed ? t('job_n_failed', failed) : '', stopped ? t('job_n_stopped', stopped) : ''].filter(Boolean).join(', ');
      document.getElementById('ch-result-msg').textContent =
        left === 0 && videoJobs.length > 0
          ? (extra ? t('ch_done_extra', success, total, extra) : t('ch_done', success, total))
          : (extra ? t('ch_working_extra', left, extra) : t('ch_working', left));

      document.getElementById('ch-count').textContent = total;

      if (left > 0 || videoJobs.length === 0) {
        _chJobPollTimer = setTimeout(poll, nextDelay());
      }
    } catch { _chJobPollTimer = setTimeout(poll, nextDelay()); }
  };

  _chJobPollTimer = setTimeout(poll, 2_000);
}

// Khởi tạo API_BASE từ storage ngay khi popup mở
chrome.storage.sync.get('vg_api_base', (r) => {
  API_BASE = normalizeApiBase(r.vg_api_base) || DEFAULT_API_BASE;
  // One-time migration off the dead cmc-1 host (background.js does the same).
  if (typeof r.vg_api_base === 'string' && migrateLegacyApiBase(r.vg_api_base) !== r.vg_api_base) {
    chrome.storage.sync.set({ vg_api_base: DEFAULT_API_BASE });
  }
});

// ── Extension update check (Phase 34D-5, task #6399) ─────────────
// Manual (zip) installs never auto-update, so the popup asks the backend which
// version it serves and shows a banner when ours is older. Never blocks the
// popup; every failure is silent.
const VG_UPDATE_CACHE_KEY = 'vg_ext_update_check';
const VG_UPDATE_DISMISS_KEY = 'vg_ext_update_dismissed';
const VG_UPDATE_TTL_MS = 6 * 60 * 60 * 1000;

function vgExtVersion() {
  try { return chrome.runtime.getManifest().version || ''; } catch { return ''; }
}

// Pure. -1 / 0 / 1, or null when either side is not a dotted-number version.
function vgCompareVersions(a, b) {
  const parse = (v) => {
    const m = /^\s*v?(\d+(?:\.\d+)*)/.exec(String(v == null ? '' : v));
    return m ? m[1].split('.').map(Number) : null;
  };
  const pa = parse(a), pb = parse(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

// Pure. info = { version, minSupported } from /extension/version.
// -> { show, forced, version }. forced = below minSupported (cannot dismiss).
function vgUpdateBanner(current, info, dismissed) {
  const none = { show: false, forced: false, version: null };
  if (!info || !current) return none;
  const latest = info.version || null;
  const min = info.minSupported || null;
  if (min && vgCompareVersions(current, min) === -1) {
    return { show: true, forced: true, version: latest || min };
  }
  if (latest && vgCompareVersions(current, latest) === -1) {
    // a dismissal holds until a version NEWER than the dismissed one appears
    if (dismissed && vgCompareVersions(latest, dismissed) !== 1) return none;
    return { show: true, forced: false, version: latest };
  }
  return none;
}

async function vgFetchUpdateInfo(now = Date.now()) {
  try {
    const c = (await chrome.storage.local.get(VG_UPDATE_CACHE_KEY))[VG_UPDATE_CACHE_KEY];
    if (c && typeof c.at === 'number' && now - c.at >= 0 && now - c.at < VG_UPDATE_TTL_MS) return c;
  } catch { /* fall through to network */ }
  try {
    const base = await vgApiBase();
    const r = await fetch(`${base}/api/v1/extension/version`, { headers: await vgAuthHeaders(), cache: 'no-store' });
    if (!r.ok) return null;
    const d = await r.json();
    const info = { at: now, version: d.version || null, minSupported: d.minSupported || null };
    try { await chrome.storage.local.set({ [VG_UPDATE_CACHE_KEY]: info }); } catch { /* ignore */ }
    return info;
  } catch { return null; }
}

async function initUpdateBanner() {
  try {
    const el = document.getElementById('update-banner');
    if (!el) return;
    const info = await vgFetchUpdateInfo();
    const dismissed = (await chrome.storage.local.get(VG_UPDATE_DISMISS_KEY))[VG_UPDATE_DISMISS_KEY] || null;
    const d = vgUpdateBanner(vgExtVersion(), info, dismissed);
    if (!d.show) { el.classList.add('hidden'); return; }
    const text = document.getElementById('update-banner-text');
    text.textContent = d.forced
      ? t('update_outdated')                            // wording: BA review
      : t('update_new', d.version);                      // wording: BA review
    const link = document.getElementById('update-banner-link');
    link.onclick = () => { chrome.tabs.create({ url: `${WEB_BASE}/extension` }); };
    link.textContent = t('update_get');                     // wording: BA review
    const close = document.getElementById('update-banner-close');
    close.classList.toggle('hidden', d.forced);
    close.onclick = async () => {
      try { await chrome.storage.local.set({ [VG_UPDATE_DISMISS_KEY]: d.version }); } catch { /* ignore */ }
      el.classList.add('hidden');
    };
    el.classList.remove('hidden');
  } catch { /* silent */ }
}
// ── /Extension update check ──────────────────────────────────────

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
  dot.title = t('health_checking_server');

  const result = await new Promise((resolve) =>
    chrome.runtime.sendMessage({ type: 'VG_HEALTH_CHECK' }, resolve)
  );

  if (result?.ok) {
    dot.style.background = '#22c55e';
    dot.title = t('health_ok');
  } else {
    dot.style.background = '#ef4444';
    dot.title = t('health_fail_title');
    const statusEl = document.getElementById('statusMsg');
    if (statusEl) {
      statusEl.textContent = t('health_fail_status');
      statusEl.className = 'mt-2 text-xs text-center text-yellow-400 min-h-[18px]';
    }
  }
}

// ── Page detection ─────────────────────────────────────────────────
const BADGE_MAP = {
  video:   { label: t('badge_video'), cls: 'bg-green-900 text-green-400 border-green-800' },
  channel: { label: t('badge_channel'), cls: 'bg-orange-900 text-orange-400 border-orange-800' }, // 5.19.0: Douyin, Facebook, TikTok, YouTube, Instagram
  generic_channel: { label: t('badge_generic_channel'), cls: 'bg-orange-900 text-orange-400 border-orange-800' },
  spotify_playlist: { label: t('badge_spotify'), cls: 'bg-green-900 text-green-400 border-green-800' },
  instagram: { label: t('badge_instagram'), cls: 'bg-pink-900 text-pink-400 border-pink-800' },
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
  initUpdateBanner();   // fire-and-forget: never blocks the popup
  vgShowVersion();
  getActiveTab().then((tb) => {
    if (tb?.url && vgYouTubeBlocked(tb.url)) vgShowYouTubeNote(tb.url);
    void initSniff(tb);
  }).catch(() => {});
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
        pageBadge.textContent = t('badge_reload');
        pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-yellow-900 text-yellow-400 border border-yellow-700';
        return;
      }
    }

    const bm = BADGE_MAP[pageInfo.pageType];
    if (bm) { pageBadge.textContent = bm.label; pageBadge.className = `text-xs px-2 py-0.5 rounded-full border ${bm.cls}`; }
    else {
      // 5.18.3: a feed (TikTok For You…) has no page type — the badge stayed "Đang nhận diện..." (owner 22/10)
      const cur = await vgAskCurrentVideo(tab.id);
      pageBadge.textContent = cur && cur.url ? t('badge_current_video') : t('badge_this_page'); // wording: BA review
      pageBadge.className = `text-xs px-2 py-0.5 rounded-full border ${cur && cur.url ? BADGE_MAP.video.cls : 'bg-gray-800 text-gray-400 border-gray-700'}`;
    }

    if (pageInfo.pageType === 'channel' || pageInfo.pageType === 'spotify_playlist' || pageInfo.pageType === 'generic_channel') {
      showTab('channel');
      document.getElementById('ch-url').textContent = pageInfo.url;
      if (pageInfo.pageType === 'spotify_playlist') {
        document.getElementById('ch-btn-text').textContent = t('ch_btn_spotify');
        document.getElementById('ch-status').textContent = t('ch_status_spotify');
      } else if (pageInfo.pageType === 'generic_channel') {
        document.getElementById('ch-btn-text').textContent = t('ch_btn_generic');
        document.getElementById('ch-status').textContent = t('ch_status_generic');
        document.getElementById('ch-count').textContent = '—';
        document.getElementById('ch-count-selector').classList.remove('hidden');
        document.getElementById('ch-send-btn').classList.add('hidden');
        void initWatchFollow(pageInfo.url); // "Theo dõi kênh này" (Phase 33B)
      } else if (pageInfo.pageType === 'channel') {
        const countResp = await new Promise((r) => chrome.tabs.sendMessage(tab.id, { type: 'VG_GET_COUNT' }, r));
        const count = countResp?.count || 0;
        document.getElementById('ch-count').textContent = count;
        if (count > 0) {
          document.getElementById('ch-send-btn').classList.remove('hidden');
          document.getElementById('ch-send-text').textContent = tn('ch_send_n', count);
          document.getElementById('ch-status').textContent = tn('ch_collected_n', count);
          document.getElementById('ch-status').style.color = '#34d399';
        } else {
          document.getElementById('ch-status').textContent = t('ch_live_hint');
        }
        // 5.16.0: live list + stop button without having to press the tab
        document.getElementById('tab-channel').click();
        // 5.19.0: TikTok / YouTube channels moved here from generic_channel — keep "Theo dõi kênh này"
        void initWatchFollow(pageInfo.url);
      }
    } else {
      showTab('single');
      if (pageInfo?.url?.includes('spotify.com/track/')) document.getElementById('qualitySelect').value = 'mp3_320';
      updateWatermarkUI(tab.url || '');
    }
  } catch {
    pageBadge.textContent = t('badge_unsupported');
    pageBadge.className = 'text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-500 border border-gray-700';
    showUnsupportedPage();
  }
}
initPopup();

// ── Channel Watch ("Theo dõi kênh", Phase 33B, task #6259) ─────────
// On a supported channel page (TikTok profile, YouTube channel) a signed-in user whose
// account the server enables (GET /api/v1/watch/status) can follow the
// channel: POST /api/v1/watch/sources. The server's own Vietnamese message is
// shown verbatim (limit reached, cooldown, disabled…). Guests never see it:
// the server has no guest mode. wording: BA review
const VG_WATCH_TEXT = {
  login: t('watch_login'),
  checking: t('watch_checking'),
  done: t('watch_done'),
  already: t('watch_already'),
  following: t('watch_following'),
  offline: t('watch_offline'),
  generic: t('watch_generic'),
};

// Canonical profile URL when the page is a watchable channel, else null.
function vgWatchTarget(rawUrl) {
  try {
    const u = new URL(rawUrl);
    const h = u.hostname.toLowerCase();
    const m = u.pathname.match(/^\/@([\w.-]+)\/?$/);
    if (u.protocol === 'https:' && (h === 'tiktok.com' || h.endsWith('.tiktok.com')) && m) {
      return { platform: 'tiktok', url: `https://www.tiktok.com/@${m[1]}` };
    }
    // YouTube channel (any tab): the server resolves it to the channel id.
    const yt = u.pathname.match(/^\/(@[\w.-]{1,100}|channel\/UC[\w-]{22}|c\/[\w.-]{1,100}|user\/[\w.-]{1,100})(?:\/[\w]*)?\/?$/);
    if (u.protocol === 'https:' && ['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(h) && yt) {
      return { platform: 'youtube', url: `https://www.youtube.com/${yt[1]}` };
    }
  } catch { /* not a URL */ }
  return null;
}

function vgWatchMessage(status, data) {
  if (status === 401) return VG_WATCH_TEXT.login;
  if (data && typeof data.message === 'string' && data.message) return data.message;
  if (data && typeof data.detail === 'string' && data.detail) return data.detail;
  return VG_WATCH_TEXT.generic;
}

// API base straight from storage: the popup's API_BASE is filled asynchronously.
async function vgApiBase() {
  try {
    const r = await chrome.storage.sync.get('vg_api_base');
    return normalizeApiBase(r.vg_api_base) || DEFAULT_API_BASE;
  } catch { return API_BASE || DEFAULT_API_BASE; }
}

async function initWatchFollow(pageUrl) {
  const box = document.getElementById('ch-watch');
  const target = vgWatchTarget(pageUrl);
  if (!box || !target) return;
  if (!(await vgAuthHeaders()).Authorization) return;   // guest: no button
  const base = await vgApiBase();
  let st = null;
  try {
    const r = await fetch(`${base}/api/v1/watch/status`, { headers: await vgAuthHeaders(), cache: 'no-store' });
    if (!r.ok) return;   // 404 watch_disabled / 401 / 5xx: the feature is not there for this user
    st = await r.json();
  } catch { return; }
  if (!st || st.enabled !== true) return;
  if (Array.isArray(st.platforms) && !st.platforms.includes(target.platform)) return;
  box.classList.remove('hidden');
  const btn = document.getElementById('ch-watch-btn');
  btn.addEventListener('click', () => { void vgFollowChannel(target.url); });
}

async function vgFollowChannel(channelUrl) {
  const btn = document.getElementById('ch-watch-btn');
  const msg = document.getElementById('ch-watch-msg');
  const label = document.getElementById('ch-watch-text');
  btn.disabled = true;
  msg.style.color = '#94a3b8';
  msg.textContent = VG_WATCH_TEXT.checking;
  try {
    const base = await vgApiBase();
    const r = await fetch(`${base}/api/v1/watch/sources`, {
      method: 'POST',
      headers: await vgAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url: channelUrl, mode: 'one_tap' }),
    });
    let data = null;
    try { data = await r.json(); } catch { /* empty body */ }
    if (r.status === 200 || r.status === 201) {
      msg.style.color = '#34d399';
      msg.textContent = data && data.already_subscribed ? VG_WATCH_TEXT.already : VG_WATCH_TEXT.done;
      if (label) label.textContent = VG_WATCH_TEXT.following;
      return;   // stays disabled: already following
    }
    msg.style.color = '#f87171';
    msg.textContent = vgWatchMessage(r.status, data);   // textContent: server text is never parsed as HTML
  } catch {
    msg.style.color = '#f87171';
    msg.textContent = VG_WATCH_TEXT.offline;
  }
  btn.disabled = false;
}

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
  const tick = async () => {
    if (!_activeTabId) return;
    const res = await new Promise((r) => chrome.tabs.sendMessage(_activeTabId, { type: 'VG_GET_ITEMS' }, r)).catch(() => null);
    if (!res) return;
    const items = res.items || [];
    const count = items.length;
    document.getElementById('ch-count').textContent = count;
    if (count > 0) { document.getElementById('ch-send-btn').classList.remove('hidden'); document.getElementById('ch-send-text').textContent = tn('ch_send_n', count); }
    vgChannelState(!!res.scrolling);
    vgRenderChannelList(items);
  };
  void tick();
  pollTimer = setInterval(tick, 1500);
});
document.getElementById('tab-single').addEventListener('click', () => clearInterval(pollTimer));

// ── 5.16.0: stop the scan, and the list of collected videos ──────
// wording: BA review
document.getElementById('ch-stop-local')?.addEventListener('click', async (e) => {
  e.currentTarget.textContent = t('stopping_dots');
  await new Promise((r) => chrome.tabs.sendMessage(_activeTabId, { type: 'VG_STOP_SCROLL' }, r)).catch(() => null);
});

let _vgWasScrolling = false;
function vgChannelState(scrolling) {
  const stop = document.getElementById('ch-stop-local');
  const btn = document.getElementById('ch-scrape-btn');
  if (!stop || !btn) return;
  stop.classList.toggle('hidden', !scrolling);
  if (scrolling) {
    btn.disabled = true;
    document.getElementById('ch-spinner').style.display = 'block';
    document.getElementById('ch-btn-text').textContent = t('ch_scrolling');
  } else if (_vgWasScrolling) {
    stop.textContent = t('ch_stop_local');
    btn.disabled = false;
    document.getElementById('ch-spinner').style.display = 'none';
    document.getElementById('ch-btn-text').textContent = t('ch_scroll_more');
  }
  _vgWasScrolling = scrolling;
}

const _vgListed = new Set();
// 5.17.0: the rows in order, the chosen ones, and whether new rows join the choice.
const _vgItems = [];
const _vgPicked = new Set();
let _vgPickAuto = true;
let _vgBulkActive = false; // a bulk download is running (button disabled)
let _vgBulkDoc = null;      // last storage.local vg_bulk seen
function vgRenderChannelList(items) {
  const wrap = document.getElementById('ch-list-wrap');
  const list = document.getElementById('ch-list');
  if (!wrap || !list) return;
  if (!items.length) return;
  wrap.classList.remove('hidden');
  document.getElementById('ch-list-count').textContent = `(${items.length})`;
  items.forEach((it, i) => {
    if (_vgListed.has(it.url)) return;
    _vgListed.add(it.url);
    _vgItems.push(it);
    if (_vgPickAuto) _vgPicked.add(it.url); // new rows follow "Chọn tất cả"
    list.appendChild(vgChannelRow(it, i));
  });
  vgBulkRows(); // rows that arrive after the progress was drawn get their state too
  vgPickRefresh();
}

function vgChannelRow(it, i) {
  const row = document.createElement('div');
  row.className = 'vg-ch-row';
  row.dataset.url = it.url;
  row.style.cssText = 'display:flex;align-items:center;gap:8px;background:#0f1119;border:1px solid #1e2333;border-radius:10px;padding:6px;';
  const pick = document.createElement('input');
  pick.type = 'checkbox';
  pick.className = 'vg-ch-pick';
  pick.checked = _vgPicked.has(it.url);
  pick.setAttribute('aria-label', t('ch_pick_this')); // wording: BA review
  pick.style.cssText = 'flex-shrink:0;width:14px;height:14px;accent-color:#6366f1;cursor:pointer;';
  pick.onchange = () => {
    _vgPickAuto = false;
    if (pick.checked) _vgPicked.add(it.url); else _vgPicked.delete(it.url);
    vgPickRefresh();
  };
  const thumb = document.createElement(it.thumb ? 'img' : 'div');
  thumb.style.cssText = 'flex-shrink:0;width:44px;height:56px;border-radius:6px;object-fit:cover;background:#1e2333;';
  if (it.thumb) { thumb.src = it.thumb; thumb.referrerPolicy = 'no-referrer'; thumb.alt = ''; thumb.loading = 'lazy'; }
  const info = document.createElement('div');
  info.style.cssText = 'flex:1;min-width:0;';
  const title = document.createElement('div');
  title.className = 'vg-ch-title';
  title.style.cssText = 'font-size:10px;color:#e2e8f0;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;';
  const idTail = (it.url.match(/(\d{6,25})\/?$/) || it.url.match(/[?&]v=(\d{6,25})/) || [])[1];
  const fallback = /facebook\.com\/reel\//.test(it.url) ? 'Reel' : 'Video';
  title.textContent = it.title || (idTail ? `${fallback} …${idTail.slice(-6)}` : `${fallback} ${i + 1}`);
  title.title = it.url;
  const state = document.createElement('div');
  state.className = 'vg-ch-state';
  state.style.cssText = 'font-size:9px;color:#64748b;margin-top:2px;';
  info.append(title, state);
  const dl = document.createElement('button');
  dl.className = 'vg-ch-dl';
  dl.textContent = t('row_download');
  dl.style.cssText = 'flex-shrink:0;background:#6366f1;border:none;border-radius:8px;padding:6px 10px;color:#fff;font-size:10px;font-weight:700;cursor:pointer;';
  dl.onclick = () => {
    dl.disabled = true;
    dl.textContent = '…';
    chrome.runtime.sendMessage({
      type: 'VG_DOWNLOAD_NOW',
      payload: { url: it.url, quality: 'video', remove_watermark: true, download_subs: false },
    }, (ack) => {
      dl.textContent = ack?.queued ? '✓' : t('row_download');
      dl.disabled = !!ack?.queued;
      state.textContent = ack?.queued ? t('row_bg_queued') : t('row_send_failed');
      state.style.color = ack?.queued ? '#86efac' : '#fca5a5';
    });
  };
  row.append(pick, thumb, info, dl);
  return row;
}

// ── 5.17.0: choose videos, download them all to this computer ──────
// wording: BA review
function vgPickRefresh() {
  for (const box of document.querySelectorAll('#ch-list .vg-ch-pick')) box.checked = _vgPicked.has(box.closest('.vg-ch-row').dataset.url);
  const n = _vgPicked.size;
  document.getElementById('ch-pick-count').textContent = t('ch_picked_count', n, _vgItems.length);
  const btn = document.getElementById('ch-bulk-btn');
  btn.disabled = n === 0 || _vgBulkActive;
  document.getElementById('ch-bulk-text').textContent = n ? tn('ch_bulk_n', n) : t('ch_bulk_pick');
}
document.getElementById('ch-pick-all')?.addEventListener('click', () => {
  _vgPickAuto = true;
  for (const it of _vgItems) _vgPicked.add(it.url);
  vgPickRefresh();
});
document.getElementById('ch-pick-none')?.addEventListener('click', () => {
  _vgPickAuto = false;
  _vgPicked.clear();
  vgPickRefresh();
});
document.getElementById('ch-pick-10')?.addEventListener('click', () => {
  _vgPickAuto = false;
  _vgPicked.clear();
  for (const it of _vgItems.slice(0, 10)) _vgPicked.add(it.url);
  vgPickRefresh();
});

function vgPlatformOfUrl(u) {
  const h = (() => { try { return new URL(u).hostname.toLowerCase(); } catch { return ''; } })();
  if (/(^|\.)douyin\.com$/.test(h)) return 'douyin';
  if (/(^|\.)tiktok\.com$/.test(h)) return 'tiktok';
  if (/(^|\.)facebook\.com$/.test(h)) return 'facebook';
  return 'other';
}
// Downloads left today for `platform` (null = no limit / unknown) — same rule as content.js.
function vgRemainingFor(quota, platform) {
  const snap = quota && quota.platform_quota;
  if (!snap || snap.unlimited) return null;
  if (snap.scope === 'platform') {
    const p = (snap.platforms || []).find((x) => x.platform === platform);
    if (p) return p.remaining < 0 ? null : p.remaining;
  }
  return typeof snap.remaining_total === 'number' && snap.remaining_total >= 0 ? snap.remaining_total : null;
}

document.getElementById('ch-bulk-btn')?.addEventListener('click', async () => {
  const chosen = _vgItems.filter((it) => _vgPicked.has(it.url)).map((it) => ({ url: it.url, title: it.title || '' }));
  if (!chosen.length) return;
  const btn = document.getElementById('ch-bulk-btn');
  const msgEl = document.getElementById('ch-list-msg');
  btn.disabled = true;
  document.getElementById('ch-bulk-text').textContent = t('ch_bulk_checking');
  let cap = null, reset = '';
  try {
    const base = await getApiBaseLocal();
    const q = await new Promise((r) => chrome.runtime.sendMessage({ type: 'VG_API_FETCH', url: `${base}/api/v1/quota`, method: 'GET' }, r));
    if (q && q.ok && q.data && q.data.success) {
      cap = vgRemainingFor(q.data.quota, vgPlatformOfUrl(chosen[0].url));
      reset = (q.data.quota.platform_quota && q.data.quota.platform_quota.reset_time_vn) || '';
    }
  } catch { /* unknown: send all, the server still counts each one */ }
  if (cap === 0) {
    msgEl.style.color = '#fbbf24';
    msgEl.textContent = reset ? t('quota_out_reset', reset) : t('quota_out');
    vgPickRefresh();
    return;
  }
  const now = cap == null ? chosen : chosen.slice(0, cap);
  const later = chosen.slice(now.length);
  const res = await new Promise((r) => chrome.runtime.sendMessage({
    type: 'VG_BULK_START', items: now, later, quality: 'video',
    laterReason: later.length ? (reset ? t('bulk_reason_quota_reset', reset) : t('bulk_reason_quota')) : null,
  }, r));
  if (!res || !res.ok) {
    msgEl.style.color = '#fca5a5';
    msgEl.textContent = (res && res.error) || t('bulk_start_failed');
    vgPickRefresh();
    return;
  }
  msgEl.style.color = later.length ? '#fbbf24' : '#86efac';
  msgEl.textContent = later.length
    ? (reset ? t('bulk_started_later_reset', now.length, later.length, reset) : t('bulk_started_later', now.length, later.length))
    : tn('bulk_started', now.length);
});
document.getElementById('ch-bulk-stop')?.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'VG_BULK_STOP' }));
document.getElementById('ch-bulk-clear')?.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'VG_BULK_CLEAR' }));

// Progress of the background queue (storage.local vg_bulk), also after reopening the popup.
const VG_BULK_LABEL = { queued: t('bulk_st_queued'), running: t('bulk_st_running'), done: t('bulk_st_done'), error: t('bulk_st_error'), skipped: t('bulk_st_skipped') };
function vgBulkRender(b) {
  _vgBulkDoc = b;
  const box = document.getElementById('ch-bulk');
  if (!box) return;
  if (!b) { box.classList.add('hidden'); _vgBulkActive = false; vgPickRefresh(); return; }
  const c = { total: b.items.length, done: 0, error: 0, skipped: 0, queued: 0, running: 0 };
  for (const it of b.items) c[it.status] = (c[it.status] || 0) + 1;
  const finished = c.done + c.error + c.skipped;
  _vgBulkActive = !b.finishedAt && (c.running > 0 || (c.queued > 0 && !b.stopped));
  box.classList.remove('hidden');
  document.getElementById('ch-bulk-bar').style.width = `${c.total ? Math.round((finished / c.total) * 100) : 0}%`;
  document.getElementById('ch-bulk-info').textContent = _vgBulkActive
    ? `${t('bulk_info_running', c.done, c.total)}${c.error ? ` · ${t('bulk_part_error', c.error)}` : ''} — ${t('bulk_info_running_tail')}`
    : `${t(b.stopped ? 'bulk_head_stopped' : 'bulk_head_done', c.done, c.total)}${c.error ? ` · ${t('bulk_part_error', c.error)}` : ''}${c.skipped ? ` · ${b.stopReason ? t('bulk_part_skipped_reason', c.skipped, b.stopReason) : t('bulk_part_skipped', c.skipped)}` : ''}.`;
  document.getElementById('ch-bulk-stop').classList.toggle('hidden', !_vgBulkActive);
  document.getElementById('ch-bulk-clear').classList.toggle('hidden', _vgBulkActive);
  vgBulkRows();
  vgPickRefresh();
}
// each row: its own state in the last run
function vgBulkRows() {
  const b = _vgBulkDoc;
  if (!b) return;
  const byUrl = new Map(b.items.map((x) => [x.url, x]));
  for (const row of document.querySelectorAll('#ch-list .vg-ch-row')) {
    const x = byUrl.get(row.dataset.url);
    if (!x) continue;
    const st = row.querySelector('.vg-ch-state');
    st.textContent = x.status === 'error' ? t('row_error', x.error || '') : VG_BULK_LABEL[x.status];
    st.style.color = x.status === 'done' ? '#86efac' : x.status === 'error' ? '#fca5a5' : x.status === 'skipped' ? '#fbbf24' : '#a5b4fc';
  }
}
chrome.storage.local.get('vg_bulk', (r) => vgBulkRender(r.vg_bulk || null));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.vg_bulk) vgBulkRender(changes.vg_bulk.newValue || null);
});
window.addEventListener('unload', () => clearInterval(pollTimer));

// ── Elapsed timer (hiển thị số giây đang xử lý) ──────────────────
let _elapsedTimer = null;
let _elapsedSec = 0;

function startElapsedTimer(statusEl) {
  _elapsedSec = 0;
  clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    _elapsedSec++;
    statusEl.textContent = t('server_working', _elapsedSec);
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
      statusEl.textContent = t('server_working', msg.elapsed);
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

// Server files are named by a file id (their basename), never by the server
// path: prefer the response's *_file_id; a legacy path (older server) is
// reduced to its basename. Links use download-local?file=<id>, encoded so a
// '+' in a real name (yt-dlp "230+140") survives the query string.
function vgFileId(id, path) {
  if (id) return id;
  return (typeof path === 'string' && path) ? (path.split(/[\\/]/).pop() || null) : null;
}
function vgIsLocalRef(v) {
  return typeof v === 'string' && !!v && !/^[a-z][a-z0-9+.-]*:/i.test(v) && !v.startsWith('/api/');
}
function vgLocalUrl(base, ref, safeName, ext) {
  return `${base}/api/v1/download-local?file=${encodeURIComponent(vgFileId(null, ref))}&filename=${encodeURIComponent(safeName)}.${ext}`;
}
// ── Helper: resolve download URL ──────────────────────────────────
function resolveDownloadUrl(data, ext, base) {
  const apiBase = base || API_BASE;
  let dlUrl = ext === 'mp3'
    ? (vgFileId(data.local_mp3_file_id, data.local_mp3_path) || vgFileId(data.local_file_id, data.local_file_path) || data.direct_mp4_url)
    : (data.direct_mp4_url || vgFileId(data.local_file_id, data.local_file_path) || vgFileId(data.local_mp3_file_id, data.local_mp3_path));
  if (!dlUrl) return null;

  if (data.is_audio_only || (dlUrl.endsWith('.mp3') || dlUrl.endsWith('.m4a'))) ext = 'mp3';

  const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');

  if (vgIsLocalRef(dlUrl)) {
    dlUrl = vgLocalUrl(apiBase, dlUrl, safeName, ext);
  } else if (!isFirstPartyUrl(dlUrl)) {
    dlUrl = `${apiBase}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
  }
  return { dlUrl, safeName, ext };
}

// ══════════════════════════════════════════════════════════════════
// SINGLE VIDEO — Download + Preview + Progress
// ══════════════════════════════════════════════════════════════════

// ── Link to download ────────────────────────────────────────────────
// The popup used to read the active tab's URL and nothing else, so a feed page
// (x.com/home, a TikTok "For You" wall) offered no way to say what to fetch.
// Priority: whatever is in the box > the active tab.
const urlInput = document.getElementById('vg-url-input');
const urlPaste = document.getElementById('vg-url-paste');
const urlHint  = document.getElementById('vg-url-hint');

// Pages that are a site's own shell rather than a piece of media. Used only to
// decide whether to *offer* a clipboard link — never to block a download, since
// this list can never be complete and a wrong guess must not stop the user.
const FEED_PATHS = /^\/?(home|explore|foryou|for-you|feed|following|notifications|messages|search|settings)?\/?$/i;

/** The video on screen in tab `tabId` (content.js VG_CURRENT_VIDEO), or null. */
function vgAskCurrentVideo(tabId) {
  return new Promise((r) => {
    try {
      chrome.tabs.sendMessage(tabId, { type: 'VG_CURRENT_VIDEO' }, (res) => { void chrome.runtime.lastError; r((res && res.video) || null); });
    } catch { r(null); }
  });
}

function looksLikeFeed(rawUrl) {
  try {
    const u = new URL(rawUrl);
    return FEED_PATHS.test(u.pathname);
  } catch { return false; }
}

function isHttpUrl(v) {
  try { const u = new URL((v || '').trim()); return u.protocol === 'http:' || u.protocol === 'https:'; }
  catch { return false; }
}

function setUrlHint(msg, tone) {
  if (!urlHint) return;
  urlHint.textContent = msg || '';
  urlHint.style.color = tone === 'warn' ? '#fbbf24' : tone === 'ok' ? '#4ade80' : '#475569';
}

/** URL the download actions should use. Box wins; falls back to the tab. */
async function getTargetUrl() {
  const typed = (urlInput?.value || '').trim();
  if (typed) {
    if (!isHttpUrl(typed)) throw new Error(t('err_bad_link'));
    if (vgYouTubeBlocked(typed)) { vgShowYouTubeNote(typed); throw new Error(VG_YT_TEXT); }
    return typed;
  }
  const tab = await getActiveTab();
  // chrome-extension:// matters as well as chrome://: when the popup is opened
  // as a tab (or an extension page is focused) the "active tab" is our own
  // page, and without this we would hand our own URL to the downloader.
  if (!tab?.url || /^(chrome|chrome-extension|edge|about):/i.test(tab.url)) {
    throw new Error(t('err_no_tab_link'));
  }
  if (vgYouTubeBlocked(tab.url)) { vgShowYouTubeNote(tab.url); throw new Error(VG_YT_TEXT); }
  return tab.url;
}

// clipboardRead is an OPTIONAL permission, so a fresh install shows no
// "Read data you copy and paste" warning at all. It is requested the first time
// someone actually presses Dán, and never on popup open. Anyone who prefers not
// to grant it can still paste with Ctrl+V into the focused box, which needs no
// permission whatsoever — so the feature degrades instead of disappearing.
function hasClipboardPermission() {
  return new Promise((resolve) => {
    try {
      chrome.permissions.contains({ permissions: ['clipboardRead'] }, (granted) => {
        resolve(!!granted && !chrome.runtime.lastError);
      });
    } catch { resolve(false); }
  });
}

function requestClipboardPermission() {
  return new Promise((resolve) => {
    try {
      chrome.permissions.request({ permissions: ['clipboardRead'] }, (granted) => {
        resolve(!!granted && !chrome.runtime.lastError);
      });
    } catch { resolve(false); }
  });
}

async function readClipboardUrl({ allowPrompt = false } = {}) {
  let ok = await hasClipboardPermission();
  // Only ever prompt from a real click; never while the popup is opening.
  if (!ok && allowPrompt) ok = await requestClipboardPermission();
  if (!ok) return null;
  try {
    const text = (await navigator.clipboard.readText() || '').trim();
    return isHttpUrl(text) ? text : null;
  } catch {
    return null;
  }
}

urlPaste?.addEventListener('click', async () => {
  const granted = await hasClipboardPermission();
  const link = await readClipboardUrl({ allowPrompt: true });
  if (link) {
    urlInput.value = link;
    setUrlHint(t('hint_pasted'), 'ok');
    return;
  }
  // Distinguish "you said no" from "clipboard has nothing useful", and always
  // leave a route that works without the permission.
  if (!granted && !(await hasClipboardPermission())) {
    urlInput.focus();
    setUrlHint(t('hint_no_clip_perm'), 'warn');
  } else {
    setUrlHint(t('hint_clip_invalid'), 'warn');
  }
});

urlInput?.addEventListener('input', () => {
  const v = urlInput.value.trim();
  if (!v) { setUrlHint(''); return; }
  setUrlHint(isHttpUrl(v) ? t('hint_will_download') : t('hint_link_invalid'), isHttpUrl(v) ? 'ok' : 'warn');
});

// On open: show the tab's URL when it is a real page. When it is a feed, offer
// the clipboard instead of silently leaving the user with nothing to download.
(async () => {
  if (!urlInput) return;
  const tab = await getActiveTab().catch(() => null);
  const tabUrl = tab?.url && !/^(chrome|chrome-extension|edge|about):/i.test(tab.url) ? tab.url : '';
  if (tabUrl && !looksLikeFeed(tabUrl)) {
    urlInput.placeholder = tabUrl;
    setUrlHint(t('hint_using_tab'));
    return;
  }
  // 5.18.1: a feed (TikTok For You…) keeps one address — take the video on screen
  const cur = tabUrl ? await vgAskCurrentVideo(tab.id) : null;
  if (cur && cur.url) {
    urlInput.value = cur.url;
    setUrlHint(t('hint_watching', (cur.title || cur.url).slice(0, 90)), 'ok'); // wording: BA review
    return;
  }
  // No prompt on open: read the clipboard only if the user already granted it.
  const clip = await readClipboardUrl();
  if (clip) {
    urlInput.value = clip;
    setUrlHint(t('hint_copied_link'), 'ok');
  } else {
    // Focus the box so Ctrl+V works immediately, permission or not.
    urlInput.focus();
    setUrlHint(t('hint_not_video'), 'warn');
  }
})();

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
  btnText.textContent = t('btn_processing');
  statusMsg.textContent = t('server_working', 0);

  startElapsedTimer(statusMsg);

  try {
    const targetUrl = await getTargetUrl();

    // Send to background worker — download continues even if popup is closed
    chrome.runtime.sendMessage({
      type: 'VG_DOWNLOAD_NOW',
      // 5.18.0: the page the person is on — the browser reads its video there (no extra window)
      payload: { url: targetUrl, quality, remove_watermark: noWm, download_subs: dlSubs, subtitle_mode: subtitleMode, subtitle_language: 'auto', pageTabId: _activeTabId },
    }, (ack) => {
      if (ack?.queued) {
        stopElapsedTimer();
        statusMsg.textContent = t('status_bg');
        btn.disabled = false;
        iconDl.style.display = 'block'; spinner.style.display = 'none';
        btnText.textContent = t('btn_download_video');
      }
    });

    // Listen for completion from background
    // (fires if popup is still open when download finishes)
    const _dlDoneHandler = (msg) => {
      if (msg.type !== 'VG_DOWNLOAD_DONE') return;
      chrome.runtime.onMessage.removeListener(_dlDoneHandler);
      stopElapsedTimer();
      if (msg.error) {
        statusMsg.textContent = t('status_err', msg.error);
        const errCard = document.getElementById('error-card');
        const errText = document.getElementById('error-text');
        if (errCard && errText) { errText.textContent = msg.error; errCard.classList.remove('hidden'); }
        btn.disabled = false;
        iconDl.style.display = 'block'; spinner.style.display = 'none';
        btnText.textContent = quality && quality.startsWith('mp3') ? t('btn_download_mp3') : t('btn_download_video');
        return;
      }
      const data = msg.data;
      _lastDownloadData = data;
      if (msg.dlUrl) _lastDownloadData._resolvedUrl = msg.dlUrl;
      showPreviewCard(data, targetUrl);
      showMultiFormat(data);
      document.getElementById('error-card')?.classList.add('hidden');
      const isMp3Done = quality && quality.startsWith('mp3');
      const isCleanTikTok = isWatermarkPlatform(targetUrl) && noWm;
      statusMsg.textContent = isMp3Done
        ? t('done_mp3')
        : isCleanTikTok
          ? t('done_no_wm')
          : t('done_video');
      document.getElementById('dl-progress')?.classList.remove('hidden');

      const successCheck = document.getElementById('success-check');
      if (successCheck) { successCheck.classList.add('show'); setTimeout(() => successCheck.classList.remove('show'), 3000); }

      btn.disabled = false;
      iconDl.style.display = 'block'; spinner.style.display = 'none';
      btnText.textContent = isMp3Done ? t('btn_download_mp3') : t('btn_download_video');
    };
    chrome.runtime.onMessage.addListener(_dlDoneHandler);

  } catch (err) {
    stopElapsedTimer();
    statusMsg.textContent = t('status_err', err.message);
    const errCard = document.getElementById('error-card');
    const errText = document.getElementById('error-text');
    if (errCard && errText) { errText.textContent = err.message; errCard.classList.remove('hidden'); }
    btn.disabled = false;
    iconDl.style.display = 'block'; spinner.style.display = 'none';
    btnText.textContent = quality && quality.startsWith('mp3') ? t('btn_download_mp3') : t('btn_download_video');
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
    hint.textContent = t('wm_only_tiktok');
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
    const hasLocalFile = !!vgFileId(data.local_file_id, data.local_file_path);
    aiBtn.classList.toggle('hidden', !hasLocalFile);
    aiBtn.disabled = false;
    aiBtn.textContent = t('act_ai');
  }
  if (aiResults) aiResults.classList.add('hidden');
}

// ── Action buttons ────────────────────────────────────────────────
document.getElementById('action-copy-link')?.addEventListener('click', async () => {
  const btn = document.getElementById('action-copy-link');
  if (!_lastDownloadData?._resolvedUrl) return;
  try {
    await navigator.clipboard.writeText(_lastDownloadData._resolvedUrl);
    btn.textContent = t('act_copied');
    setTimeout(() => { btn.textContent = t('act_copy_link'); }, 2000);
  } catch { btn.textContent = t('act_error'); setTimeout(() => { btn.textContent = t('act_copy_link'); }, 2000); }
});

document.getElementById('action-open-web')?.addEventListener('click', async () => {
  const tab = await getActiveTab();
  // API_BASE is the backend (dvid-api…) — opening it shows a JSON root, not
  // the site. The web app is WEB_BASE, which is what reads ?url=.
  if (tab?.url) chrome.tabs.create({ url: `${WEB_BASE}/?url=${encodeURIComponent(tab.url)}` });
});

document.getElementById('action-dl-thumb')?.addEventListener('click', () => {
  if (!_lastDownloadData?.thumbnail_url) return;
  const safeName = (_lastDownloadData.title || 'thumbnail').replace(/[/\\?%*:|"<>]/g, '-');
  chrome.downloads.download({ url: _lastDownloadData.thumbnail_url, filename: `VidGrab/${safeName}_thumb.jpg`, saveAs: true });
  const btn = document.getElementById('action-dl-thumb');
  btn.textContent = t('act_downloading'); setTimeout(() => { btn.textContent = t('act_thumb'); }, 2000);
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
  // Built with DOM nodes (textContent): translated text and server text are never parsed as HTML.
  const parts = [];
  const addPart = (head, rest) => {
    const row = document.createElement('span');
    if (head) { const b = document.createElement('b'); b.textContent = head; row.appendChild(b); }
    if (rest) row.appendChild(document.createTextNode((head ? ' ' : '') + rest));
    parts.push(row);
  };
  if (trim.length) {
    const tr = trim[0];
    addPart(t('ai_trim_head'), `${t('ai_trim_range', fmtAiSeconds(tr.start), fmtAiSeconds(tr.end))}${tr.reason ? ` — ${tr.reason}` : ''}`);
  }
  if (clips.length) {
    addPart(tn('ai_clips_head', clips.length), `${t('ai_clips_best', fmtAiSeconds(clips[0].start), fmtAiSeconds(clips[0].end))}${clips[0].label ? ` (${clips[0].label})` : ''}`);
  }
  if (gifs.length) {
    addPart(tn('ai_gifs_head', gifs.length), t('ai_gifs_start', fmtAiSeconds(gifs[0].start)));
  }
  if (!parts.length) {
    addPart('', result.fallback_used ? t('ai_no_data') : t('ai_none'));
  }
  const linkWrap = document.createElement('div');
  linkWrap.style.marginTop = '6px';
  const link = document.createElement('a');
  link.href = '#'; link.id = 'ai-open-web-link'; link.style.cssText = 'color:#818cf8;font-weight:600;';
  link.textContent = t('ai_open_web');
  linkWrap.appendChild(link);
  box.textContent = '';
  parts.forEach((p, idx) => { if (idx) box.appendChild(document.createElement('br')); box.appendChild(p); });
  box.appendChild(document.createElement('br'));
  box.appendChild(linkWrap);
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
    const res = await fetch(`${API_BASE}/api/v1/analyze/${jobId}`, { headers: await vgAuthHeaders() });
    const data = await res.json();
    if (data.status === 'done' || data.status === 'cached') {
      renderAiAnalyzeResults(data);
      const aiBtn = document.getElementById('action-ai-analyze');
      if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = t('act_ai'); }
      return;
    }
    if (attempt >= AI_POLL_MAX_ATTEMPTS) {
      if (box) { box.textContent = t('ai_timeout'); box.classList.remove('hidden'); }
      const aiBtn = document.getElementById('action-ai-analyze');
      if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = t('act_ai'); }
      return;
    }
    setTimeout(() => pollAiAnalysis(jobId, attempt + 1), AI_POLL_INTERVAL_MS);
  } catch {
    if (box) { box.textContent = t('ai_result_fail'); box.classList.remove('hidden'); }
    const aiBtn = document.getElementById('action-ai-analyze');
    if (aiBtn) { aiBtn.disabled = false; aiBtn.textContent = t('act_ai'); }
  }
}

document.getElementById('action-ai-analyze')?.addEventListener('click', async () => {
  const btn = document.getElementById('action-ai-analyze');
  const box = document.getElementById('ai-analyze-results');
  const _aiFileId = vgFileId(_lastDownloadData?.local_file_id, _lastDownloadData?.local_file_path);
  if (!_aiFileId) return;
  btn.disabled = true; btn.textContent = t('ai_busy_btn');
  if (box) { box.textContent = t('ai_busy_box'); box.classList.remove('hidden'); }
  try {
    const res = await fetch(`${API_BASE}/api/v1/analyze-media`, {
      method: 'POST',
      headers: await vgAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        url: _lastDownloadData.original_url || _aiFileId,
        media_path: _aiFileId,
        analyses: ['trim', 'gif', 'metadata', 'clips'],
        duration_hint_s: _lastDownloadData.duration || null,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pollAiAnalysis(data.job_id);
  } catch {
    if (box) { box.textContent = t('ai_send_fail'); box.classList.remove('hidden'); }
    btn.disabled = false; btn.textContent = t('act_ai');
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
    fill.style.width = '100%'; pctEl.textContent = t('dl_done');
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
    pctEl.textContent = t('act_error'); fill.style.background = '#ef4444'; _dlStartTime = 0;
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
          >🗄</button>
      `;
      row.querySelector('.save-archive-btn').title = t('hist_save_archive');
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
  if (!token) { alert(t('archive_need_login')); return false; }
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
      const dateStr  = item.archived_at ? new Date(item.archived_at).toLocaleDateString(t('date_locale')) : '';
      row.innerHTML = `
        <img src="${escapeHtml(thumbSrc)}" style="width:40px;height:28px;border-radius:4px;object-fit:cover;flex-shrink:0;" loading="lazy">
        <div style="flex:1;overflow:hidden;min-width:0;">
          <div style="font-size:11px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(item.title || 'Video')}</div>
          <div style="font-size:9px;color:#6b7280;">${escapeHtml(item.platform || '')} • ${escapeHtml(item.creator_name || '')} ${dateStr ? '• ' + escapeHtml(dateStr) : ''}</div>
        </div>
        <button class="star-btn" data-id="${escapeHtml(item.id)}" data-starred="${item.is_starred ? '1':'0'}"
          style="flex-shrink:0;font-size:13px;background:none;border:none;cursor:pointer;color:${item.is_starred ? '#FBBF24' : '#374151'};"
          >★</button>
      `;
      row.querySelector('.star-btn').title = item.is_starred ? t('star_remove') : t('star_add');
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
  if (diff < 60000) return t('ago_now');
  if (diff < 3600000) return t('ago_min', Math.floor(diff / 60000));
  if (diff < 86400000) return t('ago_hour', Math.floor(diff / 3600000));
  return t('ago_day', Math.floor(diff / 86400000));
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
    settingsApiUrl.value = normalizeApiBase(r.vg_api_base) || DEFAULT_API_BASE;
  });
}
settingsBtn?.addEventListener('click', () => {
  const isOpen = !settingsPanel.classList.contains('hidden');
  isOpen ? closeSettingsPanel() : openSettingsPanel();
});

settingsSave?.addEventListener('click', () => {
  const val = normalizeApiBase(settingsApiUrl.value);
  if (!val) {
    settingsSave.textContent = t('settings_need_https');
    setTimeout(() => { settingsSave.textContent = t('common_save'); }, 2500);
    return;
  }
  chrome.storage.sync.set({ vg_api_base: val }, () => {
    API_BASE = val;
    settingsSave.textContent = t('settings_saved');
    setTimeout(() => { settingsSave.textContent = t('common_save'); closeSettingsPanel(); }, 1500);
    checkServerHealth(); // re-check với URL mới
  });
});

settingsReset?.addEventListener('click', () => {
  chrome.storage.sync.remove('vg_api_base', () => {
    API_BASE = DEFAULT_API_BASE;
    settingsApiUrl.value = DEFAULT_API_BASE;
    settingsReset.textContent = t('settings_reset_done');
    setTimeout(() => { settingsReset.textContent = t('settings_reset_plain'); }, 1500);
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
    const ok = document.createElement('span');
    ok.style.color = '#4ade80';
    ok.textContent = email ? t('auth_signed_in_email', email) : t('auth_signed_in');
    authStatusRow.textContent = '';
    authStatusRow.appendChild(ok);
    authConnectBtn.style.display = 'none';
    authDisconnectBtn.style.display = 'block';
  } else {
    authStatusRow.textContent = t('auth_signed_out');
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
  // Login lives on the web app, not on the API host.
  chrome.tabs.create({ url: `${WEB_BASE}/?connect_extension=1` });
});

authDisconnectBtn?.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'VG_CLEAR_AUTH_TOKEN' }, () => {
    chrome.storage.local.remove('vg_auth_email');
    updateAuthUI(false);
  });
});

// Account state changes.
//
// The token itself is delivered web app → web-bridge.js → background worker;
// the popup is normally closed while the user logs in, so it must not be on
// that path. It only listens for the resulting state change to repaint.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'VG_AUTH_CHANGED') {
    updateAuthUI(!!msg.authenticated, msg.email);
    if (msg.authenticated) loadTierQuota();
    return;
  }
  // Back-compat: older builds relayed the raw token through the popup.
  if (msg.type === 'VG_AUTH_TOKEN_FROM_WEB' && msg.token) {
    chrome.runtime.sendMessage({ type: 'VG_SET_AUTH_TOKEN', token: msg.token, email: msg.email });
    updateAuthUI(true, msg.email);
  }
});

// ── Tier + Quota display in settings panel ─────────────────────────
async function loadTierQuota() {
  const tierRow = document.getElementById('vg-tier-quota-row');
  if (!tierRow) return;
  tierRow.innerHTML = `<span style="color:#64748b;font-size:10px;">${escapeHtml(t('tier_checking'))}</span>`;

  const base = await getApiBaseLocal();
  // Get auth token from background
  chrome.runtime.sendMessage({ type: 'VG_GET_AUTH_TOKEN' }, async (res) => {
    const token = res?.token;
    if (!token) {
      tierRow.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#64748b;font-size:10px;">Tier</span>
          <span style="color:#94a3b8;font-size:10px;">${escapeHtml(t('tier_guest'))}</span>
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
          <span style="color:#94a3b8;font-size:10px;">${escapeHtml(t('tier_plan'))}</span>
          ${tierLabel}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#64748b;margin-bottom:3px;">
          <span>${escapeHtml(t('tier_quota_today'))}</span>
          <span style="color:#e2e8f0;font-family:monospace;">${used}/${limit}</span>
        </div>
        <div style="height:3px;background:rgba(148,163,184,0.15);border-radius:2px;overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:${barColor};border-radius:2px;transition:width 0.3s;"></div>
        </div>
        ${tier !== 'pro' ? `<div style="margin-top:5px;font-size:9px;color:#6366f1;cursor:pointer;" id="vg-upgrade-link">${escapeHtml(t('tier_upgrade'))}</div>` : ''}
      `;
      document.getElementById('vg-upgrade-link')?.addEventListener('click', () => {
        chrome.tabs.create({ url: `${base}/upgrade` });
      });
    } catch {
      tierRow.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#64748b;font-size:10px;">Tier</span>
          <span style="color:#64748b;font-size:10px;">${escapeHtml(t('tier_load_fail'))}</span>
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
      <span style="color:#374151;font-family:monospace;">${escapeHtml(t('backend_checking'))}</span>
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
        <span style="color:#34d399;font-weight:700;font-family:monospace;">${escapeHtml(ytdlpVer)}${disk ? ` · disk ${escapeHtml(disk)}` : ''}</span>
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
      resolve(normalizeApiBase(r.vg_api_base) || DEFAULT_API_BASE);
    });
  });
}

// ══════════════════════════════════════════════════════════════════
// CHANNEL — Trigger scroll / Fetch Spotify + Send to backend
// ══════════════════════════════════════════════════════════════════
let _spotifyTracksCache = [];

// The server's own sentence for a refused bulk request (e.g. "sign in to
// download a whole Xiaohongshu / Kuaishou channel", task #6171), else HTTP <n>.
async function bulkErrorText(resp) {
  try {
    const data = await resp.json();
    const d = data && data.detail;
    if (typeof d === 'string' && d) return d;
    if (d && typeof d === 'object' && (d.message || d.user_message)) return d.message || d.user_message;
  } catch { /* not JSON */ }
  return `HTTP ${resp.status}`;
}

document.getElementById('ch-scrape-btn').addEventListener('click', async () => {
  if (!_activeTabId) return;
  const btn = document.getElementById('ch-scrape-btn');
  const status = document.getElementById('ch-status');
  const tab = await getActiveTab();
  const pageInfo = await tryGetPageInfo(tab.id).catch(() => null);
  btn.disabled = true; document.getElementById('ch-spinner').style.display = 'block';

  // Same rule as the download tab: a link typed/pasted above wins over the tab,
  // so a feed page is not a dead end here either.
  let chUrl;
  try { chUrl = await getTargetUrl(); }
  catch (e) {
    btn.disabled = false; document.getElementById('ch-spinner').style.display = 'none';
    status.textContent = e.message; status.style.color = '#f87171';
    return;
  }

  if (pageInfo?.pageType === 'spotify_playlist') {
    document.getElementById('ch-btn-text').textContent = t('sp_getting');
    status.textContent = t('sp_calling');
    try {
      const res = await fetch(`${API_BASE}/api/v1/fetch-spotify`, { method: 'POST', headers: await vgAuthHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ url: chUrl }) });
      const data = await res.json();
      if (data.success && data.tracks) { _spotifyTracksCache = data.tracks.map(tk => tk.search_query); renderSpotifyPlaylist(data); }
      else throw new Error(data.detail || t('err_server'));
    } catch (e) { status.textContent = t('err_prefix', e.message); status.style.color = '#f87171'; }
    finally { btn.disabled = false; document.getElementById('ch-btn-text').textContent = t('ch_btn_spotify'); document.getElementById('ch-spinner').style.display = 'none'; }
    return;
  }

  if (pageInfo?.pageType === 'generic_channel') {
    // Directly trigger the bulk submit for generic channels (YouTube, etc.)
    btn.disabled = false; document.getElementById('ch-spinner').style.display = 'none';
    const urls = [chUrl];
    const status = document.getElementById('ch-status');
    const batchDiv = document.getElementById('ch-batch-info');
    try {
      const payload = { urls, quality: 'video', remove_watermark: true, channel_mode: true, max_videos: _chMaxVideos };
      const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, { method: 'POST', headers: await vgAuthHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(payload) });
      if (!resp.ok) throw new Error(await bulkErrorText(resp));
      const data = await resp.json();
      if (data.success) {
        batchDiv.classList.remove('hidden');
        batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;
        status.textContent = tn('ch_scanning_n', _chMaxVideos);
        status.style.color = '#fb923c';
        document.getElementById('ch-btn-text').textContent = t('ch_btn_scanning');
        btn.disabled = true;
        startChannelDiscovery();
        startJobPolling(data.batch_id, _chMaxVideos);
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: chUrl }).catch(() => {});
      } else throw new Error(data.detail || t('err_unknown'));
    } catch (err) {
      status.textContent = t('err_prefix_x', err.message); status.style.color = '#f87171';
      document.getElementById('ch-btn-text').textContent = t('ch_btn_generic');
    }
    return;
  }

  document.getElementById('ch-btn-text').textContent = t('ch_scrolling');
  status.textContent = t('ch_autoscroll');
  try { await chrome.tabs.sendMessage(_activeTabId, { type: 'VG_TRIGGER_SCROLL' }); }
  catch { status.textContent = t('ch_open_douyin_first'); status.style.color = '#f87171'; }
  setTimeout(() => { btn.disabled = false; document.getElementById('ch-btn-text').textContent = t('ch_scroll_more'); document.getElementById('ch-spinner').style.display = 'none'; }, 120000);
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

  if (urls.length === 0) { status.textContent = isSpotify ? t('sp_not_loaded') : t('ch_no_videos'); return; }
  sendBtn.disabled = true; sendText.textContent = tn('ch_sending_n', urls.length); status.textContent = tn('ch_sending_urls', urls.length);

  try {
    const payload = {
      urls,
      quality: isSpotify ? 'mp3_320' : 'video',
      remove_watermark: true,
      channel_mode: isGeneric,
      max_videos: isGeneric ? _chMaxVideos : (urls.length || 20),
    };
    const resp = await fetch(`${API_BASE}/api/v1/bulk-download`, {
      method: 'POST', headers: await vgAuthHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(await bulkErrorText(resp));
    const data = await resp.json();
    if (data.success) {
      batchDiv.classList.remove('hidden');
      batchDiv.innerHTML = `Batch: <span style="color:#f97316;font-family:monospace">${data.batch_id.slice(0, 12)}…</span>`;
      if (isGeneric) {
        // Show discovery progress in popup instead of opening new tab
        status.textContent = tn('ch_scanning_n', _chMaxVideos);
        status.style.color = '#fb923c';
        sendText.textContent = t('ch_btn_scanning');
        startChannelDiscovery();
        startJobPolling(data.batch_id, _chMaxVideos);
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: chUrl }).catch(() => {});
      } else {
        status.textContent = tn('ch_queued_n', urls.length); status.style.color = '#4ade80';
        sendText.textContent = t('ch_sent');
        chrome.runtime.sendMessage({ type: 'VG_TRACK_BATCH', batchId: data.batch_id, sourceUrl: chUrl }).catch(() => {});
        chrome.tabs.create({ url: `${WEB_BASE}/?batch=${encodeURIComponent(data.batch_id)}` });
      }
    } else throw new Error(data.detail || t('err_unknown'));
  } catch (err) {
    status.textContent = t('err_prefix_x', err.message); status.style.color = '#f87171';
    sendBtn.disabled = false; sendText.textContent = isGeneric ? t('ch_retry') : tn('ch_retry_n', urls.length);
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
  document.getElementById('sp-count').textContent = tn('sp_n_tracks', data.tracks.length);
  document.getElementById('sp-type-label').textContent = data.type === 'album' ? t('sp_album_label') : t('sp_playlist_label');
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
  document.getElementById('ch-send-text').textContent = t('sp_zip');
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
    let dlUrl = (vgFileId(data.local_mp3_file_id, data.local_mp3_path) || vgFileId(data.local_file_id, data.local_file_path) || data.direct_mp4_url);
    if (!dlUrl) throw new Error('No download URL');
    const safeName = (data.title || track.name).replace(/[/\\?%*:|"<>]/g, '-');

    if (vgIsLocalRef(dlUrl)) {
      dlUrl = vgLocalUrl(API_BASE, dlUrl, safeName, 'mp3');
    } else if (dlUrl.startsWith('http') && !isFirstPartyUrl(dlUrl)) {
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
function showQualityNote(note) {
  const el = document.getElementById('quality-note');
  if (!el) return;
  const msg = note && typeof note.message === 'string' ? note.message : '';
  el.textContent = msg; // textContent: server text, never HTML
  el.classList.toggle('hidden', !msg);
}

async function openSitePage(kind) {
  if (kind === 'signin') chrome.tabs.create({ url: `${WEB_BASE}/?connect_extension=1` });
  else chrome.tabs.create({ url: `${await getApiBaseLocal()}/upgrade` });
}

function showMultiFormat(data) {
  const panel = document.getElementById('formats-panel');
  const list = document.getElementById('formats-list');
  if (!panel || !list) return;

  showQualityNote(data.quality_note);

  const all = data.available_formats || [];
  const dlH = Number(data.downloaded_height) || 0;
  // Downloaded item is shown first as its own header row; skip the duplicate
  // video row for the same height (codec is not exposed per row, so height is
  // the match key; the recommended/universal row at that height is the one we got).
  const formats = all.filter((f) => !(f.type === 'video' && dlH && Number(f.height) === dlH && f.universal !== false));
  const videoRows = formats.filter((f) => f.type === 'video');
  // Single-output video: no other resolutions to offer -> no extra video rows.
  const visible = formats.filter((f) => f.type !== 'video' || videoRows.length > 0);

  const videoUrl = data.original_url || '';
  list.innerHTML = '';

  if (dlH) {
    const head = document.createElement('div');
    head.className = 'fmt-item';
    head.style.cursor = 'default';
    const gotTxt = data.downloaded_universal === true ? t('fmt_got_universal', dlH) : t('fmt_got', dlH); // wording: BA review
    head.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:14px;">🎬</span>
        <div><div class="fmt-label">${escapeHtml(gotTxt)}</div></div>
      </div>
      <span style="font-size:12px;">✅</span>`; // wording: BA review
    list.appendChild(head);
  }

  visible.forEach((fmt) => {
    const row = document.createElement('div');
    row.className = 'fmt-item';

    const isVideo = fmt.type === 'video';
    const icon = isVideo ? '🎬' : '🎵';
    const label = fmt.label || (isVideo ? `${fmt.height}p` : 'Audio');
    const needsMerge = fmt.requires_merge || !fmt.url;
    const mergeBadge = needsMerge ? `<span class="fmt-badge fmt-badge-merge">${escapeHtml(t('fmt_merge'))}</span>` : '';
    const sizeMB = fmt.filesize_mb || fmt.file_size_mb || 0;
    const sizeBadge = sizeMB ? `<span style="font-size:10px;color:#f97316;font-weight:600;">${escapeHtml(String(sizeMB))} MB</span>` : '';
    const sharperNote = isVideo && fmt.universal === false
      ? `<div style="font-size:9px;color:#fbbf24;">${escapeHtml(t('fmt_sharper'))}</div>` : ''; // wording: BA review
    const locked = fmt.locked === 'signin' || fmt.locked === 'plan' ? fmt.locked : '';
    const lockedTxt = locked === 'signin' ? t('fmt_locked_signin') : t('fmt_locked_plan'); // wording: BA review
    const actionHtml = locked
      ? `<span class="fmt-dl-btn" style="opacity:.75;">🔒 ${escapeHtml(lockedTxt)}</span>`
      : `<span class="fmt-dl-btn">${escapeHtml(t('fmt_dl'))}</span>`;

    row.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:14px;">${icon}</span>
        <div>
          <div class="fmt-label">${escapeHtml(label)}</div>
          <div class="fmt-meta">${mergeBadge}${sizeBadge}</div>
          ${sharperNote}
        </div>
      </div>
      ${actionHtml}
    `;

    if (locked) {
      // Disabled row: no download; the label itself leads to sign-in / upgrade.
      row.style.opacity = '0.6';
      row.setAttribute('aria-disabled', 'true');
      row.addEventListener('click', () => { openSitePage(locked); });
      list.appendChild(row);
      return;
    }

    row.addEventListener('click', async () => {
      const dlBtn = row.querySelector('span:last-child');
      const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');
      const ext = isVideo ? 'mp4' : 'mp3';

      if (needsMerge) {
        dlBtn.textContent = '⏳';
        try {
          const quality = isVideo ? `video_${fmt.height}` : 'mp3_320';
          const result = await apiFetchLink({ url: videoUrl, quality, remove_watermark: _lastNoWm });
          if (!result?.ok || !result.data?.success) {
            dlBtn.textContent = '❌';
            const d = result?.data?.detail;  // e.g. 503 server_busy: "Hệ thống đang bận…" (task #6397)
            dlBtn.title = (typeof d === 'string' ? d : d?.message) || result?.data?.message || result?.error || '';
            return;
          }

          const r = result.data;
          showQualityNote(r.quality_note);
          let finalUrl;
          const audioPath = ext === 'mp3'
            ? (vgFileId(r.local_mp3_file_id, r.local_mp3_path) || vgFileId(r.local_file_id, r.local_file_path))
            : vgFileId(r.local_file_id, r.local_file_path);
          if (audioPath) {
            finalUrl = vgLocalUrl(API_BASE, audioPath, safeName, ext);
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
        finalUrl = vgLocalUrl(API_BASE, dlUrl, safeName, ext);
      } else if (dlUrl && !isFirstPartyUrl(dlUrl)) {
        finalUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(safeName)}&ext=${ext}`;
      } else {
        finalUrl = dlUrl;
      }
      if (finalUrl) { chrome.downloads.download({ url: finalUrl, filename: `VidGrab/${safeName}_${label}.${ext}`, saveAs: true }); dlBtn.textContent = '✅'; }
    });

    list.appendChild(row);
  });

  if (list.children.length === 0 && !data.quality_note?.message) { panel.classList.add('hidden'); return; }
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
  btnText.textContent = val && val.startsWith('mp3') ? t('btn_download_mp3') : t('btn_download_video');
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


// ── YouTube (5.12.0 → 5.14.1) ─────────────────────────────────────
// The Chrome Web Store does not allow extensions that download YouTube. The
// build VidGrab hands out on its own website keeps YouTube (owner, 22/10);
// the Web Store build (`package-extension.py --store`) drops the YouTube host
// permission, and then the popup sends YouTube links to the web instead.
// Which build this is = whether the manifest has the YouTube host. wording: BA review
const VG_YT_TEXT = t('yt_text');

function vgIsYouTube(url) {
  try {
    const h = new URL(url).hostname.toLowerCase();
    return /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/.test(h);
  } catch { return false; }
}

function vgYouTubeInBuild() {
  return (chrome.runtime.getManifest().host_permissions || []).some((h) => /youtube\.com/.test(h));
}
function vgYouTubeBlocked(url) {
  return vgIsYouTube(url) && !vgYouTubeInBuild();
}

function vgShowYouTubeNote(url) {
  const box = document.getElementById('vg-yt-note');
  if (!box) return;
  box.style.display = 'block';
  const btn = document.getElementById('vg-yt-open');
  btn.onclick = () => chrome.tabs.create({ url: `${WEB_BASE}/?url=${encodeURIComponent(url)}` });
}

function vgShowVersion() {
  const el = document.getElementById('footer-version');
  if (el) el.textContent = `v${chrome.runtime.getManifest().version}`;
}

// ── Media found on the page (5.12.0, media-sniffer.js) ────────────
// What the tab actually played: direct files download from the user's own
// browser; HLS / DASH go through the VidGrab server (in-browser merging comes
// later). DRM / encrypted streams are shown but never downloaded. wording: BA review
const VG_SNIFF_TEXT = {
  ask: t('sniff_ask'),
  allow: t('sniff_allow'),
  reload: t('sniff_reload'),
  granted: t('sniff_granted'),
  denied: t('sniff_denied'),
  empty: t('sniff_empty'),
  reading: t('sniff_reading'),
  drm: t('sniff_drm'),
  aes: t('sniff_aes'),
  live: t('sniff_live'),
  download: t('sniff_download'),
  viaServer: t('sniff_viaServer'),
  local: t('sniff_local'),
  viaServerShort: t('sniff_viaServerShort'),
  jobRunning: t('sniff_jobRunning'),
  jobSaving: t('sniff_jobSaving'),
  jobDone: t('sniff_jobDone'),
  jobCancelled: t('sniff_jobCancelled'),
  cancel: t('sniff_cancel'),
  started2: t('sniff_started2'),
  viaApp: t('sniff_viaApp'),
  appSent: t('sniff_appSent'),
  appFailed: t('sniff_appFailed'),
  appHint: t('sniff_appHint'),
  copy: t('sniff_copy'),
  copied: t('sniff_copied'),
  started: t('sniff_started'),
  failed: t('sniff_failed'),
};

function vgFmtBytes(b) {
  if (!b) return '';
  if (b >= 1073741824) return `${(b / 1073741824).toFixed(1)} GB`;
  if (b >= 1048576) return `${(b / 1048576).toFixed(1)} MB`;
  return `${Math.round(b / 1024)} KB`;
}
function vgFmtDur(s) {
  if (!s) return '';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
}
function vgQ(h) { return !h ? '' : h >= 2160 ? '4K' : h >= 1440 ? '1440p' : `${h}p`; }

function vgOriginPattern(url) {
  try { const u = new URL(url); return /^https?:$/.test(u.protocol) ? `${u.protocol}//${u.hostname}/*` : null; }
  catch { return null; }
}
function vgHasOrigin(pattern) {
  return new Promise((r) => chrome.permissions.contains({ origins: [pattern] }, (g) => r(!!g && !chrome.runtime.lastError)));
}

let _vgSniffTimer = null;
let _vgAppFound = false;   // the VidGrab app answers on this computer

async function initSniff(tab) {
  const box = document.getElementById('vg-sniff');
  if (!box || !tab?.id || !tab.url || vgIsYouTube(tab.url)) return;
  // Video usually comes from another host than the page (a CDN), and Chrome
  // only shows the extension requests to hosts it may see — so one site's
  // permission lists nothing in practice (measured 22/10). The ask is for
  // every site, still optional and only on the user's click.
  if (!vgOriginPattern(tab.url)) return;
  const pattern = '*://*/*';
  const allow = document.getElementById('vg-sniff-allow');
  const empty = document.getElementById('vg-sniff-empty');
  const granted = await vgHasOrigin(vgOriginPattern(tab.url));

  if (!granted) {
    box.style.display = 'block';
    empty.style.display = 'block';
    empty.textContent = VG_SNIFF_TEXT.ask;
    allow.style.display = 'block';
    allow.textContent = VG_SNIFF_TEXT.allow;
    allow.onclick = () => {
      // must run inside the click: Chrome shows its own permission prompt
      chrome.permissions.request({ origins: [pattern] }, (ok) => {
        if (ok) {
          empty.textContent = VG_SNIFF_TEXT.granted;
          allow.textContent = VG_SNIFF_TEXT.reload;
          allow.onclick = () => { chrome.tabs.reload(tab.id); window.close(); };
        } else {
          empty.textContent = VG_SNIFF_TEXT.denied;
        }
      });
    };
    return;
  }

  document.getElementById('vg-sniff-refresh').onclick = () => renderSniff(tab);
  const app = await new Promise((r) => chrome.runtime.sendMessage({ type: 'VG_APP_STATUS' }, r));
  _vgAppFound = !!(app && app.found);
  await renderSniff(tab);
  renderHlsJobs();
}

function vgSendToApp(tab, it, msg) {
  chrome.runtime.sendMessage({ type: 'VG_APP_SEND', tabId: tab.id, id: it.id }, (r) => {
    msg.textContent = r && r.ok ? VG_SNIFF_TEXT.appSent : VG_SNIFF_TEXT.appFailed;
  });
}

function vgSendToServer(url) {
  showTab('single');
  if (urlInput) { urlInput.value = url; urlInput.dispatchEvent(new Event('input')); }
  document.getElementById('downloadBtn')?.click();
}

let _vgHlsTimer = null;
async function renderHlsJobs() {
  const box = document.getElementById('vg-hls-jobs');
  if (!box) return;
  const resp = await new Promise((r) => chrome.runtime.sendMessage({ type: 'VG_HLS_JOBS' }, r));
  const jobs = ((resp && resp.jobs) || []).slice(0, 5);
  box.textContent = '';
  box.style.display = jobs.length ? 'flex' : 'none';
  if (jobs.length) document.getElementById('vg-sniff').style.display = 'block';
  jobs.forEach((j) => {
    const row = document.createElement('div');
    row.className = 'vg-hls-job';
    row.dataset.state = j.state;
    row.style.cssText = 'background:#0f1119;border:1px solid #1e2333;border-radius:10px;padding:7px 8px;font-size:10px;color:#cbd5e1;';
    const pct = j.total ? Math.floor((j.done / j.total) * 100) : 0;
    const label = j.state === 'running' ? `${VG_SNIFF_TEXT.jobRunning} ${pct}% · ${vgFmtBytes(j.bytes) || '0 KB'}`
      : j.state === 'saving' ? VG_SNIFF_TEXT.jobSaving
      : j.state === 'done' ? `${VG_SNIFF_TEXT.jobDone} · ${vgFmtBytes(j.bytes)}`
      : j.state === 'cancelled' ? VG_SNIFF_TEXT.jobCancelled
      : (j.message || VG_SNIFF_TEXT.failed);
    const title = document.createElement('div');
    title.style.cssText = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#e2e8f0;';
    title.textContent = `${j.title}${j.height ? ` · ${vgQ(j.height)}` : ''}`;
    const line = document.createElement('div');
    line.className = 'vg-hls-label';
    line.style.cssText = `margin-top:3px;color:${j.state === 'failed' ? '#fca5a5' : j.state === 'done' ? '#86efac' : '#94a3b8'};`;
    line.textContent = label;
    row.append(title, line);
    if (j.state === 'running') {
      const bar = document.createElement('div');
      bar.style.cssText = 'height:4px;background:#1e2333;border-radius:4px;margin-top:5px;overflow:hidden;';
      const fill = document.createElement('div');
      fill.style.cssText = `height:100%;width:${pct}%;background:#6366f1;`;
      bar.appendChild(fill);
      const cancel = document.createElement('button');
      cancel.className = 'vg-hls-cancel';
      cancel.textContent = VG_SNIFF_TEXT.cancel;
      cancel.style.cssText = 'margin-top:5px;background:none;border:1px solid #2d3348;border-radius:6px;color:#94a3b8;font-size:9px;padding:2px 8px;cursor:pointer;';
      cancel.onclick = () => chrome.runtime.sendMessage({ type: 'VG_HLS_CANCEL', id: j.id });
      row.append(bar, cancel);
    }
    box.appendChild(row);
  });
  clearTimeout(_vgHlsTimer);
  if (jobs.some((j) => j.state === 'running' || j.state === 'saving')) _vgHlsTimer = setTimeout(renderHlsJobs, 1000);
}

async function renderSniff(tab) {
  const box = document.getElementById('vg-sniff');
  const list = document.getElementById('vg-sniff-list');
  const empty = document.getElementById('vg-sniff-empty');
  // 5.18.2: TikTok / Douyin / Facebook / Instagram preload the next videos and play
  // through blob: — the raw list could not say which file is which, and their CDN
  // refuses a file saved outside the page (TikTok gave an .htm). Those sites use
  // "Video đang xem" + "Tải Video Ngay" only (owner 22/10). wording: BA review
  const handledHost = /(^|\.)(tiktok|douyin|facebook|instagram)\.com$/i.test((() => { try { return new URL(tab.url).hostname; } catch { return ''; } })());
  if (handledHost) { box.style.display = 'none'; return; }
  const resp = await new Promise((r) => chrome.runtime.sendMessage({ type: 'VG_SNIFF_LIST', tabId: tab.id }, r));
  const items = (resp && resp.items) || [];
  if (!items.length) {
    // on a site VidGrab supports by link the panel stays out of the way
    if (document.getElementById('unsupported-page-state')?.style.display === 'block') {
      box.style.display = 'block';
      empty.style.display = 'block';
      empty.textContent = VG_SNIFF_TEXT.empty;
    }
    return;
  }
  box.style.display = 'block';
  empty.style.display = 'none';
  document.getElementById('vg-sniff-count').textContent = `(${items.length})`;
  list.textContent = '';
  // 5.18.1: which file is the video on screen (when its <video> plays a real URL)
  const cur = await vgAskCurrentVideo(tab.id);
  const canon = (u) => { try { const x = new URL(u); ['bytestart', 'byteend', 'range'].forEach((k) => x.searchParams.delete(k)); return x.origin + x.pathname; } catch { return u; } };
  const playing = cur && cur.src ? canon(cur.src) : null;
  const ordered = [...items].reverse().sort((a, b) => (canon(b.url) === playing) - (canon(a.url) === playing));
  ordered.forEach((it) => {
    const row = vgSniffRow(tab, it);
    if (playing && canon(it.url) === playing) {
      row.style.borderColor = '#6366f1';
      const mark = document.createElement('div');
      mark.style.cssText = 'font-size:10px;color:#a5b4fc;font-weight:700;margin-bottom:4px;';
      mark.textContent = cur.title ? t('sniff_playing_title', cur.title.slice(0, 60)) : t('sniff_playing');
      row.prepend(mark);
    }
    list.appendChild(row);
  });
  clearTimeout(_vgSniffTimer);
  if (items.some((x) => x.pending)) _vgSniffTimer = setTimeout(() => renderSniff(tab), 1200);
}

function vgSniffRow(tab, it) {
  const row = document.createElement('div');
  row.className = 'vg-sniff-row';
  row.style.cssText = 'background:#0f1119;border:1px solid #1e2333;border-radius:10px;padding:7px 8px;';
  const kind = it.kind === 'file' ? ((it.mime || '').startsWith('audio/') ? 'AUDIO' : 'MP4') : it.kind.toUpperCase();
  const quality = it.variants && it.variants.length
    ? it.variants.map((v) => vgQ(v.height)).filter(Boolean).filter((q, i, a) => a.indexOf(q) === i).slice(0, 4).join(' · ')
    : vgQ(it.height);
  const meta = [quality, vgFmtDur(it.duration), vgFmtBytes(it.size), it.pending ? VG_SNIFF_TEXT.reading : ''].filter(Boolean).join(' · ');

  const top = document.createElement('div');
  top.style.cssText = 'display:flex;align-items:center;gap:6px;';
  const tag = document.createElement('span');
  tag.className = 'vg-sniff-kind';
  tag.textContent = kind;
  tag.style.cssText = `flex-shrink:0;font-size:9px;font-weight:800;padding:1px 6px;border-radius:6px;background:${it.kind === 'file' ? '#14532d' : '#312e81'};color:${it.kind === 'file' ? '#86efac' : '#c7d2fe'};`;
  const info = document.createElement('span');
  info.className = 'vg-sniff-meta';
  info.style.cssText = 'flex:1;min-width:0;font-size:10px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
  info.textContent = meta || (it.title || '').slice(0, 60);
  info.title = it.url;
  top.append(tag, info);
  row.appendChild(top);

  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:6px;margin-top:6px;align-items:center;';
  const msg = document.getElementById('vg-sniff-msg');
  const blocked = it.encryption === 'drm' ? VG_SNIFF_TEXT.drm
    : it.encryption === 'aes128' ? VG_SNIFF_TEXT.aes
    : it.live ? VG_SNIFF_TEXT.live : '';
  if (blocked) {
    const note = document.createElement('span');
    note.className = 'vg-sniff-blocked';
    note.style.cssText = 'font-size:10px;color:#fca5a5;flex:1;';
    note.textContent = blocked;
    actions.appendChild(note);
  } else if (!it.pending) {
    const btn = document.createElement('button');
    btn.className = 'vg-sniff-dl';
    btn.style.cssText = 'flex:1;background:#6366f1;border:none;border-radius:8px;padding:5px;color:#fff;font-size:10px;font-weight:700;cursor:pointer;';
    if (it.kind === 'file') {
      btn.textContent = VG_SNIFF_TEXT.download;
      btn.onclick = () => chrome.runtime.sendMessage({ type: 'VG_SNIFF_DOWNLOAD', tabId: tab.id, id: it.id }, (r) => {
        msg.textContent = r && r.ok ? VG_SNIFF_TEXT.started : VG_SNIFF_TEXT.failed;
      });
    } else if (it.kind === 'hls') {
      // in this browser first; VidGrab's server only when that cannot work
      let pick = null;
      const heights = (it.variants || []).map((v) => v.height).filter(Boolean);
      if (heights.length > 1) {
        pick = document.createElement('select');
        pick.className = 'vg-sniff-quality';
        pick.style.cssText = 'background:#0f1923;border:1px solid #1e2333;border-radius:8px;color:#e2e8f0;font-size:10px;padding:4px;';
        [...new Set(heights)].forEach((h) => { const o = document.createElement('option'); o.value = h; o.textContent = vgQ(h); pick.appendChild(o); });
        actions.appendChild(pick);
      }
      btn.textContent = VG_SNIFF_TEXT.local;
      btn.onclick = () => chrome.runtime.sendMessage(
        { type: 'VG_HLS_START', tabId: tab.id, id: it.id, height: pick ? Number(pick.value) : null },
        (r) => { msg.textContent = r && r.ok ? VG_SNIFF_TEXT.started2 : VG_SNIFF_TEXT.failed; renderHlsJobs(); });
      actions.appendChild(btn);
      const srv = document.createElement('button');
      srv.className = 'vg-sniff-server';
      srv.textContent = VG_SNIFF_TEXT.viaServerShort;
      srv.title = VG_SNIFF_TEXT.viaServer;
      srv.style.cssText = 'background:#1e2333;border:1px solid #2d3348;border-radius:8px;padding:5px 8px;color:#cbd5e1;font-size:10px;cursor:pointer;';
      srv.onclick = () => vgSendToServer(it.url);
      actions.appendChild(srv);
      if (_vgAppFound) {
        const appBtn = document.createElement('button');
        appBtn.className = 'vg-sniff-app';
        appBtn.textContent = 'App';
        appBtn.title = VG_SNIFF_TEXT.viaApp;
        appBtn.style.cssText = srv.style.cssText;
        appBtn.onclick = () => vgSendToApp(tab, it, msg);
        actions.appendChild(appBtn);
      }
    } else if (_vgAppFound) {
      // DASH: the app's yt-dlp + ffmpeg join video and audio on this machine
      btn.textContent = VG_SNIFF_TEXT.viaApp;
      btn.classList.add('vg-sniff-app');
      btn.onclick = () => vgSendToApp(tab, it, msg);
      const srv = document.createElement('button');
      srv.className = 'vg-sniff-server';
      srv.textContent = VG_SNIFF_TEXT.viaServerShort;
      srv.title = VG_SNIFF_TEXT.viaServer;
      srv.style.cssText = 'background:#1e2333;border:1px solid #2d3348;border-radius:8px;padding:5px 8px;color:#cbd5e1;font-size:10px;cursor:pointer;';
      srv.onclick = () => vgSendToServer(it.url);
      actions.append(btn, srv);
    } else {
      btn.textContent = VG_SNIFF_TEXT.viaServer;
      btn.title = VG_SNIFF_TEXT.appHint;
      btn.onclick = () => vgSendToServer(it.url);
    }
    if (it.kind === 'file' || (it.kind === 'dash' && !_vgAppFound)) actions.appendChild(btn);
  }
  const copy = document.createElement('button');
  copy.className = 'vg-sniff-copy';
  copy.textContent = VG_SNIFF_TEXT.copy;
  copy.style.cssText = 'background:#1e2333;border:1px solid #2d3348;border-radius:8px;padding:5px 8px;color:#a5b4fc;font-size:10px;cursor:pointer;';
  copy.onclick = () => navigator.clipboard.writeText(it.url).then(() => { msg.textContent = VG_SNIFF_TEXT.copied; }).catch(() => {});
  actions.appendChild(copy);
  row.appendChild(actions);
  return row;
}


// ══════════════════════════════════════════════════════════════════
// 5.20.0 — preview before downloading · send to the phone (QR) ·
// record the tab (last resort) · file name template. wording: BA review
// ══════════════════════════════════════════════════════════════════
function vgFmtSize(b) {
  if (!b) return '';
  return b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`;
}

document.getElementById('vg-preview-btn')?.addEventListener('click', async () => {
  const box = document.getElementById('vg-preview');
  const info = document.getElementById('vg-preview-info');
  const video = document.getElementById('vg-preview-video');
  box.classList.remove('hidden');
  video.removeAttribute('src');
  video.style.display = 'none';
  info.style.color = '#cbd5e1';
  info.textContent = t('prev_searching');
  let url;
  try { url = await getTargetUrl(); } catch (e) { info.textContent = e.message; return; }
  const r = await new Promise((res) => chrome.runtime.sendMessage({ type: 'VG_PROBE', url, pageTabId: _activeTabId }, res));
  if (!r || !r.direct) {
    info.textContent = t('prev_server');
    return;
  }
  if (!r.media) {
    info.style.color = '#fbbf24';
    info.textContent = r.reason === 'not_this_tab'
      ? t('prev_not_this_tab')
      : t('prev_no_file');
    return;
  }
  video.style.display = 'block';
  video.src = r.media.url;
  const base = [r.media.title && `“${r.media.title.slice(0, 80)}”`, vgFmtSize(r.media.size)].filter(Boolean).join(' · ');
  info.textContent = t('prev_direct', base);
  video.onloadedmetadata = () => {
    const q = video.videoHeight ? `${video.videoWidth}×${video.videoHeight}` : '';
    const d = video.duration && isFinite(video.duration) ? `${Math.floor(video.duration / 60)}:${String(Math.round(video.duration % 60)).padStart(2, '0')}` : '';
    info.textContent = t('prev_direct_best', [base, q, d].filter(Boolean).join(' · '));
  };
  video.onerror = () => { video.style.display = 'none'; };
});

document.getElementById('vg-qr-btn')?.addEventListener('click', async () => {
  const box = document.getElementById('vg-qr');
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
  let url;
  try { url = await getTargetUrl(); } catch (e) { setUrlHint(e.message, 'warn'); return; }
  const link = `${WEB_BASE}/share-target?url=${encodeURIComponent(url)}`;
  const qr = qrcode(0, 'M');
  qr.addData(link);
  qr.make();
  const holder = document.getElementById('vg-qr-code');
  holder.textContent = '';
  const img = document.createElement('img');
  img.src = qr.createDataURL(4, 8);
  img.alt = 'QR';
  img.style.cssText = 'width:168px;height:168px;image-rendering:pixelated;';
  holder.appendChild(img);
  box.classList.remove('hidden');
});

async function vgRecRender() {
  const btn = document.getElementById('vg-rec-btn');
  const box = document.getElementById('vg-rec');
  if (!btn || !box) return;
  const cur = (await chrome.storage.session.get('vg_rec').catch(() => ({}))).vg_rec;
  if (cur && cur.state === 'recording') {
    const s = Math.round((Date.now() - cur.startedAt) / 1000);
    btn.textContent = t('rec_stop');
    box.classList.remove('hidden');
    box.textContent = t('rec_running', (cur.title || '').slice(0, 60), Math.floor(s / 60), String(s % 60).padStart(2, '0'));
    clearTimeout(vgRecRender._t);
    vgRecRender._t = setTimeout(vgRecRender, 1000);
  } else {
    btn.textContent = t('tool_record');
  }
}
document.getElementById('vg-rec-btn')?.addEventListener('click', async () => {
  const box = document.getElementById('vg-rec');
  const cur = (await chrome.storage.session.get('vg_rec').catch(() => ({}))).vg_rec;
  box.classList.remove('hidden');
  if (cur && cur.state === 'recording') {
    box.textContent = t('rec_saving');
    const r = await new Promise((res) => chrome.runtime.sendMessage({ type: 'VG_REC_STOP' }, res));
    box.textContent = r && r.ok ? t('rec_saved', r.seconds, vgFmtSize(r.bytes)) : t('status_err', (r && r.error) || t('rec_save_fail'));
    vgRecRender();
    return;
  }
  if (!_activeTabId) return;
  box.textContent = t('rec_intro');
  const r = await new Promise((res) => chrome.runtime.sendMessage({ type: 'VG_REC_START', tabId: _activeTabId }, res));
  if (!r || !r.ok) { box.textContent = t('status_err', (r && r.error) || t('rec_start_fail')); return; }
  vgRecRender();
});
vgRecRender();

// file name template
(async () => {
  const input = document.getElementById('settings-name-tpl');
  if (!input) return;
  try { input.value = (await chrome.storage.sync.get('vg_name_template')).vg_name_template || ''; } catch { /* none */ }
  document.getElementById('settings-name-save')?.addEventListener('click', async () => {
    const v = input.value.trim();
    const hint = document.getElementById('settings-name-hint');
    if (v && !/\{(title|id)\}/.test(v)) { hint.style.color = '#fbbf24'; hint.textContent = t('tpl_need_token'); return; }
    await chrome.storage.sync.set({ vg_name_template: v || '{title}' });
    hint.style.color = '#4ade80';
    hint.textContent = t('tpl_saved');
  });
})();
