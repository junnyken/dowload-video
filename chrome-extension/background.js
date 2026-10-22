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

// Media seen in each tab (5.12.0): HLS / DASH / direct files, listed in the popup.
importScripts('media-sniffer.js', 'direct-download.js');

const DEFAULT_API_BASE = 'https://dvid-api.vibe1.tinhgon.xyz';
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
// `vg_api_base` is user-writable (Settings) and lives in chrome.storage.sync,
// so it rides along to every browser signed into the same Google account.
// It used to be accepted on `startsWith('http')` alone, which meant a single
// bad value turned this extension into a cleartext exfiltration channel —
// see getEphemeralCookiesB64 below. Two rules now:
//   • normalizeApiBase()  — must be https (http only for localhost dev)
//   • isCookieTrustedBase() — cookies go ONLY to first-party hosts
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

// Is this base first-party enough to be handed the user's live session cookies?
function isCookieTrustedBase(base) {
  try {
    const u = new URL(base);
    if (isLocalDevBase(base)) return true;   // dev only: the configured base itself is loopback
    if (u.protocol !== 'https:') return false;
    return isFirstPartyHost(u.hostname);
  } catch { return false; }
}
// True only when the (user-configured) API base is itself a loopback host.
// getApiBase() falls back to the https first-party default, so this is false
// unless the user explicitly set vg_api_base to localhost.
function isLocalDevBase(base) {
  try { return LOCAL_HOSTS.includes(new URL(base).hostname); } catch { return false; }
}

let _apiBase = DEFAULT_API_BASE;

async function getApiBase() {
  try {
    const r = await chrome.storage.sync.get('vg_api_base');
    return normalizeApiBase(r.vg_api_base) || DEFAULT_API_BASE;
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
  'reddit.com', 'bilibili.com', 'xiaohongshu.com', 'rednote.com', 'lemon8-app.com',
  // Douyin gates EVERY video behind signature cookies (ttwid/__ac_nonce/msToken),
  // public ones included — no account needed, just a live browser session.
  'douyin.com',
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

  // Hard stop: these are the user's LIVE Facebook/Instagram/X/Reddit session
  // cookies. They only ever go to a first-party endpoint. If someone has
  // pointed the extension at a custom backend, extraction silently falls back
  // to the server-side cookie pool instead of handing that host the session.
  const base = await getApiBase();
  if (!isCookieTrustedBase(base)) {
    console.warn('[VidGrab] Custom API base is not first-party — skipping browser-cookie handoff.');
    return undefined;
  }

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
  if (isFirstPartyUrl(u)) return false;              // already our server
  if (low.includes('googlevideo.com')) return false; // YouTube — IP-locked
  if (low.includes('.m3u8')) return false;           // HLS — needs server merge
  return true;
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
function _serverWrap(rawUrl, base, safeName, finalExt) {
  if (vgIsLocalRef(rawUrl)) return vgLocalUrl(base, rawUrl, safeName, finalExt);
  if (isFirstPartyUrl(rawUrl)) return rawUrl;
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
    _apiBase = normalizeApiBase(changes.vg_api_base.newValue) || DEFAULT_API_BASE;
  }
});

// One-time migration of a saved base that points at the dead cmc-1 host.
chrome.storage.sync.get('vg_api_base').then((r) => {
  if (r && typeof r.vg_api_base === 'string' && migrateLegacyApiBase(r.vg_api_base) !== r.vg_api_base) {
    chrome.storage.sync.set({ vg_api_base: DEFAULT_API_BASE });
  }
}).catch(() => {});

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

// ── HLS in the browser (5.13.0): offscreen.html + hls-downloader.js ──
// The service worker keeps the job list (storage.session, so a reopened popup
// sees it), owns the hidden page, and saves the finished MP4 with
// chrome.downloads. wording: BA review
const HLS_JOBS_KEY = 'vg_hls_jobs';
const hlsJobs = new Map();

async function hlsLoad() {
  if (hlsJobs.size) return;
  try {
    const arr = (await chrome.storage.session.get(HLS_JOBS_KEY))[HLS_JOBS_KEY] || [];
    arr.forEach((j) => hlsJobs.set(j.id, j));
  } catch { /* empty */ }
}
function hlsSave() {
  const arr = [...hlsJobs.values()].sort((a, b) => b.at - a.at).slice(0, 20);
  chrome.storage.session.set({ [HLS_JOBS_KEY]: arr }).catch(() => {});
}
async function ensureOffscreen() {
  const has = chrome.offscreen.hasDocument ? await chrome.offscreen.hasDocument() : false;
  if (has) return;
  try {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['BLOBS'],
      justification: 'Ghép các đoạn video HLS thành một tệp MP4 ngay trong trình duyệt của người dùng.',
    });
  } catch (e) {
    if (!/single offscreen|already/i.test(String(e && e.message))) throw e;
  }
}
async function closeOffscreenIfIdle() {
  if ([...hlsJobs.values()].some((j) => j.state === 'running' || j.state === 'saving')) return;
  try { if (await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument(); } catch { /* gone */ }
}
function hlsSafeName(t) {
  return String(t || 'video').replace(/[/\\?%*:|"<>\n\r\t]/g, '-').trim().slice(0, 120) || 'video';
}

async function hlsStart(item, height) {
  await hlsLoad();
  const id = `h${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const job = { id, title: item.title || 'video', url: item.url, height: height || null, state: 'running',
                done: 0, total: 0, bytes: 0, at: Date.now(), message: '' };
  hlsJobs.set(id, job);
  hlsSave();
  await ensureOffscreen();
  await chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_START',
                                     job: { id, url: item.url, height: job.height, title: job.title } });
  return job;
}

// One video: server fetch-link -> the file straight to this computer (smartDownload),
// subtitles, history. Shared by VG_DOWNLOAD_NOW and the 5.17.0 bulk queue.
// Throws an Error (with .status when the server answered) on failure.
async function vgFetchAndSave({ url, quality, remove_watermark, download_subs }, signal) {
  const base = await getApiBase();
  const user_cookies_b64 = await getEphemeralCookiesB64(url);
  const resp = await fetch(`${base}/api/v1/fetch-link`, {
    method: 'POST',
    headers: await authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ url, quality, remove_watermark, download_subs, user_cookies_b64 }),
    signal,
  });

  // The server explains every failure in the body (detail / user_message);
  // "HTTP 500" alone told the user nothing (08/10, task #6134).
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw Object.assign(new Error(vgServerErrorText(data, resp.status)), { status: resp.status });
  if (!data?.success) throw new Error(data?.detail || 'Server lỗi');

  // Resolve download URL (same logic as popup resolveDownloadUrl)
  const ext = (quality || '').includes('mp3') || data.is_audio_only ? 'mp3' : 'mp4';
  let dlUrl = ext === 'mp3'
    ? (vgFileId(data.local_mp3_file_id, data.local_mp3_path) || vgFileId(data.local_file_id, data.local_file_path) || data.direct_mp4_url)
    : (data.direct_mp4_url || vgFileId(data.local_file_id, data.local_file_path) || vgFileId(data.local_mp3_file_id, data.local_mp3_path));

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
  return { data, dlUrl, finalExt, safeName };
}

// ── 5.18.3: the extension's own 30-day session (backend app/core/extension_session.py) ──
// `bearer`: the web's Supabase token (first sign-in) or the current vgx_ token
// (renewal). Null when the server cannot issue one (old server, offline).
async function vgExtSession(bearer) {
  try {
    const r = await fetch(`${await getApiBase()}/api/v1/extension/session`, {
      method: 'POST', headers: { Authorization: `Bearer ${bearer}` },
    });
    const d = await r.json().catch(() => null);
    return r.ok && d && typeof d.token === 'string' && d.token.startsWith('vgx_') ? d : null;
  } catch { return null; }
}

// Renew a week before it ends; a Supabase token kept from before 5.18.3 is
// swapped while it still works; an ended session is dropped (shown signed out).
async function vgRenewSession() {
  const got = await chrome.storage.local.get([AUTH_TOKEN_KEY, 'vg_auth_exp']).catch(() => ({}));
  const tok = got[AUTH_TOKEN_KEY];
  if (!tok) return;
  const now = Math.floor(Date.now() / 1000);
  const exp = Number(got.vg_auth_exp || 0);
  if (tok.startsWith('vgx_') && exp && exp - now > 7 * 86400) return;
  const sess = await vgExtSession(tok);
  if (sess) {
    await chrome.storage.local.set({ [AUTH_TOKEN_KEY]: sess.token, vg_auth_exp: sess.expiresAt, ...(sess.email ? { vg_auth_email: sess.email } : {}) });
  } else if (tok.startsWith('vgx_') && exp && exp <= now) {
    await chrome.storage.local.remove([AUTH_TOKEN_KEY, 'vg_auth_exp']);
    chrome.runtime.sendMessage({ type: 'VG_AUTH_CHANGED', authenticated: false }).catch(() => {});
  }
}
chrome.alarms.create('vg-session-renew', { periodInMinutes: 12 * 60 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'vg-session-renew') vgRenewSession(); });
vgRenewSession();

// ── 5.18.0: download in the browser first, the server only counts ──────────
// (direct-download.js has the why.) vgSaveVideo is what VG_DOWNLOAD_NOW and
// the bulk queue call: claim one download (same daily allowance as the web),
// find the file in the page, save it from this computer; if the browser
// cannot, hand the claim back and let the server do it (vgFetchAndSave).
const VG_HELPER_KEY = 'vg_helper_window';

async function vgClaim(url) {
  const base = await getApiBase();
  try {
    const r = await fetch(`${base}/api/v1/client/quota/claim`, {
      method: 'POST',
      headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url, route: 'local', clientVersion: `ext-${chrome.runtime.getManifest().version}` }),
    });
    const d = await r.json().catch(() => null);
    if (r.status === 503 && d && d.error_code === 'client_quota_disabled') return { allowed: true, claimId: null };
    if (!r.ok || !d || d.allowed === false) {
      return { allowed: false, status: r.status, message: (d && (d.detail || d.message)) || `HTTP ${r.status}`, resetTimeVn: d && d.resetTimeVn };
    }
    return { allowed: true, claimId: d.claimId || null };
  } catch {
    return null; // server unreachable: the direct download may still go (counted later by nobody — rare)
  }
}

async function vgSettle(claimId, outcome) {
  if (!claimId) return;
  try {
    const base = await getApiBase();
    await fetch(`${base}/api/v1/client/quota/settle`, {
      method: 'POST', headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ claimId, outcome }),
    });
  } catch { /* the claim simply stays counted */ }
}

// A minimised VidGrab window holds the pages opened for lists / bulk.
async function vgHelperTab(url) {
  const got = await chrome.storage.session.get(VG_HELPER_KEY).catch(() => ({}));
  let winId = got[VG_HELPER_KEY];
  if (winId != null) {
    try { await chrome.windows.get(winId); } catch { winId = null; }
  }
  if (winId == null) {
    const w = await chrome.windows.create({ url, focused: false, state: 'minimized' });
    await chrome.storage.session.set({ [VG_HELPER_KEY]: w.id }).catch(() => {});
    return w.tabs[0].id;
  }
  const t = await chrome.tabs.create({ windowId: winId, url, active: false });
  return t.id;
}

async function vgCloseHelperIfIdle() {
  const got = await chrome.storage.session.get(VG_HELPER_KEY).catch(() => ({}));
  const winId = got[VG_HELPER_KEY];
  if (winId == null) return;
  try {
    const w = await chrome.windows.get(winId, { populate: true });
    const busy = w.tabs.some((t) => t.url && t.url !== 'about:blank' && t.status === 'loading');
    if (!busy && w.tabs.length <= 1) { await chrome.windows.remove(winId); await chrome.storage.session.remove(VG_HELPER_KEY); }
  } catch { await chrome.storage.session.remove(VG_HELPER_KEY).catch(() => {}); }
}

/** The file of the video page in `tabId`: the page's own data first, then the sniffer. */
async function vgFindMedia(tabId) {
  const until = Date.now() + self.vgDirect.FIND_TIMEOUT_MS;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 1200));
    try {
      const [res] = await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', func: self.vgDirect.vgPageMedia });
      if (res && res.result && res.result.url) return res.result;
    } catch { /* page still loading / navigating */ }
    const sniffed = self.vgDirect.pickSniffed(await self.vgSniffList(tabId).catch(() => []));
    if (sniffed) return { url: sniffed.url, title: sniffed.title, via: 'sniffer' };
  }
  return null;
}

/** chrome.downloads of `url`; resolves true when the file is complete, false when it failed. */
function vgSaveFile(url, name) {
  return new Promise((resolve) => {
    let id = null;
    const done = (ok) => { chrome.downloads.onChanged.removeListener(onChanged); clearTimeout(timer); resolve(ok); };
    const onChanged = (delta) => {
      if (delta.id !== id || !delta.state) return;
      if (delta.state.current === 'complete') {
        // 5.18.2: a CDN that refuses answers with a web page (TikTok saved
        // "TikTok - Make Your Day.htm", owner 22/10) — that is not a video:
        // remove it and let the caller fall back to the server.
        chrome.downloads.search({ id }, ([it]) => {
          const mime = (it && it.mime) || '';
          const ok = !!it && (it.fileSize || it.totalBytes || 0) >= 30 * 1024 && !/^(text\/|application\/(json|xml|xhtml))/i.test(mime);
          if (!ok) chrome.downloads.removeFile(id, () => { void chrome.runtime.lastError; chrome.downloads.erase({ id }); });
          done(ok);
        });
      } else if (delta.state.current === 'interrupted') done(false);
    };
    const timer = setTimeout(() => done(true), self.vgDirect.SAVE_TIMEOUT_MS); // still running: count it as started
    chrome.downloads.onChanged.addListener(onChanged);
    chrome.downloads.download({ url, filename: `VidGrab/${name}.mp4`, saveAs: false, conflictAction: 'uniquify' }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) { done(false); return; }
      id = downloadId;
    });
  });
}

/** Same tab URL, ignoring the hash and trailing slash. */
function vgSamePage(a, b) {
  const n = (u) => { try { const x = new URL(u); x.hash = ''; return x.toString().replace(/\/$/, ''); } catch { return u; } };
  return n(a) === n(b);
}

/**
 * One video, browser first. `pageTabId`: the tab the person is on (single
 * download from that page — no extra window). Returns the same shape as
 * vgFetchAndSave plus `via`; throws with .status like it.
 */
async function vgSaveVideo(payload, signal, pageTabId) {
  // storage.local vg_direct === false: everything through the server (kill switch, tests)
  const off = (await chrome.storage.local.get('vg_direct').catch(() => ({}))).vg_direct === false;
  if (off || !self.vgDirect.directEligible(payload)) {
    return { ...(await vgFetchAndSave(payload, signal)), via: 'server' };
  }
  const claim = await vgClaim(payload.url);
  if (claim && !claim.allowed) {
    throw Object.assign(new Error(`${claim.message}${claim.resetTimeVn ? ` — lượt mới lúc ${claim.resetTimeVn}` : ''}`), { status: 429 });
  }
  let tabId = null, ownTab = false, media = null;
  try {
    if (pageTabId != null) {
      const t = await chrome.tabs.get(pageTabId).catch(() => null);
      if (t && vgSamePage(t.url || '', payload.url)) tabId = pageTabId;
    }
    if (tabId == null) { tabId = await vgHelperTab(payload.url); ownTab = true; }
    media = await vgFindMedia(tabId);
  } catch { media = null; }
  if (ownTab && tabId != null) chrome.tabs.remove(tabId).catch(() => {});
  if (ownTab) setTimeout(() => { vgCloseHelperIfIdle(); }, 3000);

  if (media && !(signal && signal.aborted)) {
    const name = self.vgDirect.safeName(media.title);
    if (await vgSaveFile(media.url, name)) {
      if (claim && claim.claimId) vgSettle(claim.claimId, 'completed');
      addToHistory({ title: media.title || 'Video', thumbnail: '', url: payload.url, quality: 'HD', fileSize: '',
                     noWatermark: true, hasSubtitle: false, subtitleLanguages: [], subtitleSource: 'none' });
      return { data: { success: true, title: media.title || 'Video', thumbnail_url: '', direct_mp4_url: media.url },
               dlUrl: media.url, finalExt: 'mp4', safeName: name, via: `direct:${media.via}` };
    }
  }
  // the browser could not: give the claim back, the server counts its own download
  if (claim && claim.claimId) await vgSettle(claim.claimId, 'failed');
  return { ...(await vgFetchAndSave(payload, signal)), via: 'server' };
}

// ── 5.17.0: download many collected videos to this computer (owner 22/10) ──
// The popup's channel list sends the chosen videos; two at a time go through
// vgFetchAndSave (each counts one download on the server, like the single
// "Tải" button). The queue lives in storage.local, so it goes on with the
// popup closed and picks up again if Chrome stops this service worker.
// A 429 (no downloads left today / too fast) stops the queue; the rest stay
// "skipped" with the server's message. wording: BA review
const VG_BULK_KEY = 'vg_bulk';
const VG_BULK_PARALLEL = 2;
let _vgBulk = null;        // in-memory copy of storage[VG_BULK_KEY]
let _vgBulkWorkers = 0;

async function vgBulkLoad() {
  if (_vgBulk) return _vgBulk;
  const got = await chrome.storage.local.get(VG_BULK_KEY);
  _vgBulk = got[VG_BULK_KEY] || null;
  return _vgBulk;
}
function vgBulkSave() {
  return chrome.storage.local.set({ [VG_BULK_KEY]: _vgBulk });
}
function vgBulkCounts(b) {
  const c = { total: b.items.length, done: 0, error: 0, skipped: 0, queued: 0, running: 0 };
  for (const it of b.items) c[it.status] = (c[it.status] || 0) + 1;
  return c;
}

async function vgBulkFinish() {
  const b = _vgBulk;
  if (!b || b.finishedAt) return;
  b.finishedAt = Date.now();
  await vgBulkSave();
  const c = vgBulkCounts(b);
  chrome.notifications.create(`vg-bulk-${b.finishedAt}`, {
    type: 'basic', iconUrl: 'assets/icon.png',
    title: b.stopped ? `VidGrab — đã dừng: ${c.done}/${c.total} video` : `✅ VidGrab — đã tải ${c.done}/${c.total} video`,
    message: [c.error && `${c.error} video lỗi`, c.skipped && `${c.skipped} video chưa tải${b.stopReason ? ` (${b.stopReason})` : ''}`].filter(Boolean).join(' · ') || 'Tệp nằm trong thư mục Tải xuống / VidGrab.',
    priority: 1,
  });
}

async function vgBulkWorker() {
  _vgBulkWorkers++;
  try {
    for (;;) {
      const b = await vgBulkLoad();
      if (!b || b.stopped || b.finishedAt) return;
      const it = b.items.find((x) => x.status === 'queued');
      if (!it) return;
      it.status = 'running';
      await vgBulkSave();
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 150_000);
      try {
        const { data, via } = await vgSaveVideo({ url: it.url, quality: b.quality, remove_watermark: true, download_subs: false }, controller.signal, null);
        it.status = 'done';
        it.via = via;
        if (data && data.title) it.title = String(data.title).slice(0, 200);
      } catch (err) {
        it.status = 'error';
        it.error = err.name === 'AbortError' ? 'Máy chủ xử lý quá lâu' : String(err.message || err).slice(0, 200);
        if (err.status === 429) {
          // no downloads left today (or too fast): keep the rest for later
          b.stopped = true;
          b.stopReason = it.error;
          it.status = 'skipped';
          for (const x of b.items) if (x.status === 'queued') x.status = 'skipped';
        }
      } finally {
        clearTimeout(timer);
      }
      await vgBulkSave();
    }
  } finally {
    _vgBulkWorkers--;
    const b = _vgBulk;
    if (_vgBulkWorkers === 0 && b && !b.items.some((x) => x.status === 'running' || (x.status === 'queued' && !b.stopped))) {
      await vgBulkFinish();
    }
  }
}

function vgBulkPump() {
  while (_vgBulkWorkers < VG_BULK_PARALLEL) void vgBulkWorker();
}

// After a service-worker restart: what was running is queued again, then go on.
(async () => {
  const b = await vgBulkLoad();
  if (!b || b.finishedAt) return;
  let changed = false;
  for (const it of b.items) if (it.status === 'running') { it.status = 'queued'; changed = true; }
  if (changed) await vgBulkSave();
  if (!b.stopped && b.items.some((x) => x.status === 'queued')) vgBulkPump();
  else if (b.stopped) await vgBulkFinish();
})();

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'sw') return false;
  (async () => {
    await hlsLoad();
    const job = hlsJobs.get(msg.id);
    if (!job) return;
    if (msg.type === 'HLS_PROGRESS') {
      Object.assign(job, { done: msg.done, total: msg.total, bytes: msg.bytes });
      if (msg.height) job.height = msg.height;
    } else if (msg.type === 'HLS_FAILED') {
      Object.assign(job, { state: msg.reason === 'cancelled' ? 'cancelled' : 'failed', message: msg.message || '' });
      closeOffscreenIfIdle();
    } else if (msg.type === 'HLS_DONE') {
      Object.assign(job, { state: 'saving', bytes: msg.bytes, done: job.total });
      const filename = `VidGrab/${hlsSafeName(job.title)}${job.height ? ` (${job.height}p)` : ''}.${msg.ext || 'mp4'}`;
      chrome.downloads.download({ url: msg.url, filename, saveAs: false }, (downloadId) => {
        const finish = (state, message) => {
          Object.assign(job, { state, message: message || '' });
          hlsSave();
          chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_RELEASE', id: job.id })
            .catch(() => {}).finally(() => closeOffscreenIfIdle());
          if (state === 'done') {
            chrome.notifications.create(`vg-hls-${job.id}`, {
              type: 'basic', iconUrl: 'assets/icon.png', title: 'VidGrab — Đã tải xong',
              message: `${job.title.slice(0, 80)} đã lưu vào thư mục Tải về.`,
            });
          }
        };
        if (chrome.runtime.lastError || !downloadId) { finish('failed', 'Trình duyệt không lưu được tệp.'); return; }
        job.downloadId = downloadId;
        const onChanged = (delta) => {
          if (delta.id !== downloadId || !delta.state) return;
          if (delta.state.current === 'complete') { chrome.downloads.onChanged.removeListener(onChanged); finish('done'); }
          else if (delta.state.current === 'interrupted') { chrome.downloads.onChanged.removeListener(onChanged); finish('failed', 'Lưu tệp bị gián đoạn.'); }
        };
        chrome.downloads.onChanged.addListener(onChanged);
      });
    }
    hlsSave();
  })();
  sendResponse({ ok: true });
  return false;
});

// ── VidGrab app on this computer (5.14.0, app 0.19.0 ext_bridge.rs) ──
// The app listens on 127.0.0.1 (first free of these ports) and takes the
// streams the browser cannot finish alone — DASH, split audio — to download
// them with its own yt-dlp + ffmpeg. Our own machine first, the server last.
const VG_APP_PORTS = [17325, 17326, 17327];
let vgAppCache = { at: 0, port: null, version: null };

async function vgFindApp(force = false) {
  if (!force && Date.now() - vgAppCache.at < 30_000) return vgAppCache;
  let found = { at: Date.now(), port: null, version: null };
  for (const port of VG_APP_PORTS) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 700);
      const r = await fetch(`http://127.0.0.1:${port}/vidgrab/ping`, { signal: ctl.signal, cache: 'no-store' });
      clearTimeout(t);
      const j = r.ok ? await r.json() : null;
      if (j && j.app === 'VidGrab') { found = { at: Date.now(), port, version: j.version || null }; break; }
    } catch { /* not this port */ }
  }
  vgAppCache = found;
  return found;
}

async function vgSendToApp(item) {
  const app = await vgFindApp(true);
  if (!app.port) return { ok: false, error: 'no_app' };
  const r = await fetch(`http://127.0.0.1:${app.port}/vidgrab/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: item.url, title: item.title || '', referer: item.pageUrl || '' }),
  });
  const j = await r.json().catch(() => null);
  return r.ok && j && j.ok ? { ok: true } : { ok: false, error: (j && j.error) || `HTTP ${r.status}` };
}

// ── Badge Count ───────────────────────────────────────────────────
// Media the sniffer found on the tab win: the badge shows how many.
self.vgSniffBadge = (tabId, count) => {
  if (count > 0) {
    chrome.action.setBadgeText({ tabId, text: count > 99 ? '99+' : String(count) }).catch(() => {});
    chrome.action.setBadgeBackgroundColor({ tabId, color: '#6366f1' }).catch(() => {});
  } else {
    updateBadgeForTab(tabId, true);
  }
};

async function updateBadgeForTab(tabId, skipSniff = false) {
  try {
    const tab = tabId
      ? await chrome.tabs.get(tabId).catch(() => null)
      : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    if (!tab?.url) { chrome.action.setBadgeText({ text: '' }); return; }
    if (!skipSniff && self.vgSniffList) {
      const found = await self.vgSniffList(tab.id).catch(() => []);
      if (found.length) { self.vgSniffBadge(tab.id, found.length); return; }
    }

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
  if (item.filename?.includes('VidGrab') || isFirstPartyUrl(item.url)) {
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

// ── Batch/channel background tracking ───────────────────────────────
// The popup's live polling (see popup.js startJobPolling, every 2s) only
// runs while the popup is open — closing it kills that setInterval and the
// user loses all progress visibility even though the batch keeps running
// server-side. This registers the batch for a coarser (1 min, the practical
// floor for chrome.alarms) background check that survives popup close and
// SW suspension, and fires a native notification when the whole batch is
// actually done.
const BATCH_TRACK_KEY = 'vg_active_batches';
const BATCH_MAX_AGE_MS = 2 * 60 * 60 * 1000; // give up tracking after 2h (stuck job)

async function getActiveBatches() {
  const res = await chrome.storage.local.get(BATCH_TRACK_KEY);
  return res[BATCH_TRACK_KEY] || {};
}

async function saveActiveBatches(batches) {
  await chrome.storage.local.set({ [BATCH_TRACK_KEY]: batches });
}

async function trackBatch(batchId, sourceUrl) {
  const batches = await getActiveBatches();
  batches[batchId] = { startedAt: Date.now(), sourceUrl: sourceUrl || '' };
  await saveActiveBatches(batches);
}

chrome.alarms.create('vg-batch-poll', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'vg-batch-poll') return;

  const batches = await getActiveBatches();
  const ids = Object.keys(batches);
  if (ids.length === 0) return;

  const base = await getApiBase();
  let changed = false;

  for (const batchId of ids) {
    const meta = batches[batchId];
    if (Date.now() - meta.startedAt > BATCH_MAX_AGE_MS) {
      delete batches[batchId];
      changed = true;
      continue;
    }
    try {
      const resp = await fetch(`${base}/api/v1/jobs/${batchId}`, { headers: await authHeaders() });
      if (!resp.ok) continue;
      const data = await resp.json();
      const jobs = (data.jobs || []).filter((j) => j.original_url !== 'batch_zip');
      if (jobs.length === 0) continue; // still discovering channel videos

      const success = jobs.filter((j) => j.status === 'success').length;
      const failed = jobs.filter((j) => j.status === 'failed').length;
      const total = jobs.length;
      const stopped = jobs.filter((j) => j.status === 'cancelled').length;
      const stillRunning = jobs.some((j) => ['processing', 'pending', 'queued', 'retrying'].includes(j.status));
      if (stillRunning) continue;

      // Batch reached a terminal state (all done or failed) — notify + stop tracking.
      // Parsing sourceUrl is best-effort: a bad/empty URL must not suppress the
      // notification or skip un-tracking the batch (it would otherwise sit
      // tracked, silently retried every minute, until BATCH_MAX_AGE_MS).
      let hostname = 'VidGrab';
      try { if (meta.sourceUrl) hostname = new URL(meta.sourceUrl).hostname; } catch { /* keep default */ }
      chrome.notifications.create(`vg-batch-${batchId}`, {
        type: 'basic',
        iconUrl: 'assets/icon.png',
        title: stopped > 0 ? `⏹ Đã dừng (${success}/${total} video tải được)`
          : failed > 0 ? `⚠️ Batch hoàn tất (${success}/${total} thành công)` : `✅ Batch hoàn tất (${total} video)`,
        message: hostname,
        priority: 1,
      });
      delete batches[batchId];
      changed = true;
    } catch { /* transient network error — retry next alarm tick */ }
  }

  if (changed) await saveActiveBatches(batches);
});

// ── Message handler ───────────────────────────────────────────────
// The server explains every failure in its body: a plain `detail` string, a
// `detail` object ({message|user_message}), or a top-level message (503
// server_busy: "Hệ thống đang bận, vui lòng thử lại sau khoảng N giây.",
// task #6397). Never retried automatically: the user presses the button again.
function vgServerErrorText(data, status) {
  const d = data?.detail;
  const why = data?.user_message || (typeof d === 'string' ? d : d?.message || d?.user_message)
    || data?.message;
  if (data?.error_code === 'server_busy' && why) return why;
  return why ? `${why} (HTTP ${status})` : `HTTP ${status}`;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  if (msg.type === 'VG_TRACK_BATCH') {
    trackBatch(msg.batchId, msg.sourceUrl).then(() => sendResponse({ ok: true }));
    return true;
  }

  // ── Auth token management ─────────────────────────────────────────────────────────────────
  // Who is allowed to read/write the auth token.
  //
  // Chrome (not the page) fills in sender.origin/sender.url, so a page cannot
  // claim to be somewhere it is not. We compare origins rather than asking
  // "is this a tab?": an extension page reports chrome-extension://<our id>
  // whatever surface it is on, while a content script reports the host page's
  // origin even though it shares our extension id. The earlier `!sender.tab`
  // form conflated "runs in a tab" with "untrusted" and so denied our own
  // popup whenever it was opened as a tab — caught by driving the real
  // extension in a real browser, where VG_GET_AUTH_TOKEN came back null while
  // VG_GET_AUTH_STATUS said authenticated.
  function _senderOrigin() {
    if (sender.origin) return sender.origin;
    try { return new URL(sender.url || '').origin; } catch { return null; }
  }

  const _fromExtensionPage =
    sender.id === chrome.runtime.id &&
    _senderOrigin() === `chrome-extension://${chrome.runtime.id}`;
  // Exact first-party web origin (sync). Localhost is NOT here: it is dev-only
  // and gated separately by _isLocalDevSender() on the user's explicit vg_api_base.
  function _isFirstPartyWebSender() {
    if (sender.id !== chrome.runtime.id) return false;
    const origin = _senderOrigin();
    if (!origin) return false;
    try {
      const u = new URL(origin);
      return u.protocol === 'https:' && isFirstPartyHost(u.hostname);
    } catch { return false; }
  }
  // Loopback sender: honoured only when the user has explicitly pointed
  // vg_api_base at a loopback host (unpacked/dev use). With the default base a
  // random local server on localhost:PORT can neither set nor clear the token.
  async function _isLocalDevSender() {
    if (sender.id !== chrome.runtime.id) return false;
    const origin = _senderOrigin();
    if (!origin) return false;
    try {
      if (!LOCAL_HOSTS.includes(new URL(origin).hostname)) return false;
      return isLocalDevBase(await getApiBase());
    } catch { return false; }
  }
  async function _mayWriteAuthAsync() {
    return _fromExtensionPage || _isFirstPartyWebSender() || (await _isLocalDevSender());
  }
  if (msg.type === 'VG_SET_AUTH_TOKEN') {
    (async () => {
      if (!(await _mayWriteAuthAsync()) || typeof msg.token !== 'string' || !msg.token) {
        sendResponse({ ok: false, error: 'Blocked: auth token may only be set by the extension UI or the VidGrab web app' });
        return;
      }
      // 5.18.3: the web's Supabase token lives one hour and was never renewed —
      // the extension silently became a guest (5 a day) for a signed-in Pro
      // user (owner 22/10). Swap it for the extension's own 30-day session.
      // the site sends its session on every visit: keep a 30-day session of the same
      // account that has more than a week left instead of asking for a new one
      if (!msg.token.startsWith('vgx_')) {
        const cur = await chrome.storage.local.get([AUTH_TOKEN_KEY, 'vg_auth_exp', 'vg_auth_email']).catch(() => ({}));
        const left = Number(cur.vg_auth_exp || 0) - Math.floor(Date.now() / 1000);
        if ((cur[AUTH_TOKEN_KEY] || '').startsWith('vgx_') && left > 7 * 86400 && msg.email && cur.vg_auth_email === msg.email) {
          sendResponse({ ok: true, kept: true });
          return;
        }
      }
      const sess = await vgExtSession(msg.token);
      const _write = sess ? { [AUTH_TOKEN_KEY]: sess.token, vg_auth_exp: sess.expiresAt } : { [AUTH_TOKEN_KEY]: msg.token };
      const email = (sess && sess.email) || (typeof msg.email === 'string' && msg.email) || '';
      if (email) _write.vg_auth_email = email;
      chrome.storage.local.set(_write, () => {
        // Refresh the popup's account row if it happens to be open.
        chrome.runtime.sendMessage({ type: 'VG_AUTH_CHANGED', authenticated: true, email: msg.email }).catch(() => {});
        sendResponse({ ok: true });
      });
    })();
    return true;
  }

  // Read side of the same pair — popup.js has always sent this, but no handler
  // existed, so the callback fired with `undefined` + a lastError and the
  // tier/quota panel reported "Khách" even for signed-in Pro accounts.
  if (msg.type === 'VG_GET_AUTH_TOKEN') {
    if (!_fromExtensionPage) { sendResponse({ token: null }); return true; }
    getAuthToken().then((token) => sendResponse({ token }));
    return true;
  }
  if (msg.type === 'VG_CLEAR_AUTH_TOKEN') {
    (async () => {
      if (!(await _mayWriteAuthAsync())) { sendResponse({ ok: false }); return; }
      // sign-out: the extension session stops working on the server too
      const tok = await getAuthToken();
      if (tok && tok.startsWith('vgx_')) {
        try { await fetch(`${await getApiBase()}/api/v1/extension/session`, { method: 'DELETE', headers: { Authorization: `Bearer ${tok}` } }); } catch { /* offline */ }
      }
      chrome.storage.local.remove([AUTH_TOKEN_KEY, 'vg_auth_email', 'vg_auth_exp'], () => {
        chrome.runtime.sendMessage({ type: 'VG_AUTH_CHANGED', authenticated: false }).catch(() => {});
        sendResponse({ ok: true });
      });
    })();
    return true;
  }
  if (msg.type === 'VG_GET_AUTH_STATUS') {
    getAuthToken().then(token => sendResponse({ authenticated: !!token }));
    return true;
  }

  // ── VG_DIRECT_DOWNLOAD (14/10): a media link the page itself holds (Douyin)
  // downloaded with the user's browser/IP — no server, not blocked. If Chrome
  // interrupts it (403 / expired link), the tab is told to use the server path.
  if (msg.type === 'VG_DIRECT_DOWNLOAD') {
    const url = String(msg.url || '');
    if (!/^https:\/\//i.test(url)) { sendResponse({ ok: false }); return false; }
    const safeName = String(msg.title || 'video').replace(/[/\\?%*:|"<>\n\r\t]/g, '-').trim().slice(0, 120) || 'video';
    const tabId = sender?.tab?.id;
    chrome.downloads.download({ url, filename: `VidGrab/${safeName}.mp4`, saveAs: false }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) { sendResponse({ ok: false }); return; }
      sendResponse({ ok: true, downloadId });
      const onChanged = (delta) => {
        if (delta.id !== downloadId || !delta.state) return;
        if (delta.state.current === 'interrupted') {
          chrome.downloads.onChanged.removeListener(onChanged);
          if (tabId != null) chrome.tabs.sendMessage(tabId, { type: 'VG_DIRECT_FAILED', pageUrl: msg.pageUrl }).catch(() => {});
        } else if (delta.state.current === 'complete') {
          chrome.downloads.onChanged.removeListener(onChanged);
          addToHistory({ title: msg.title || 'Video', thumbnail: '', url: msg.pageUrl || url, quality: 'HD', fileSize: '', noWatermark: true, hasSubtitle: false, subtitleLanguages: [], subtitleSource: 'none' });
        }
      };
      chrome.downloads.onChanged.addListener(onChanged);
    });
    return true;
  }

  // ── VG_DOWNLOAD_NOW: full download pipeline in background — survives popup close ──────────
  // popup sends this instead of VG_FETCH_LINK so the download completes even if popup closes.
  // Flow: fetch-link API → resolve URL → chrome.downloads.download (saveAs:false) → notify.
  // ── Media found on the page (media-sniffer.js) ──
  if (msg.type === 'VG_SNIFF_LIST') {
    self.vgSniffList(msg.tabId).then((items) => sendResponse({ items })).catch(() => sendResponse({ items: [] }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_CLEAR') {
    self.vgSniffClear(msg.tabId).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_DOWNLOAD') {
    (async () => {
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item || item.kind !== 'file') { sendResponse({ ok: false, error: 'not_a_file' }); return; }
      const base = await getApiBase();
      const ext = (item.url.split('?')[0].match(/\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i) || [])[1]
        || (/^audio\//.test(item.mime || '') ? 'm4a' : 'mp4');
      const safeName = String(item.title || 'video').replace(/[/\\?%*:|"<>\n\r\t]/g, '-').trim().slice(0, 120) || 'video';
      smartDownload({ rawUrl: item.url, base, safeName, finalExt: ext.toLowerCase(), saveAs: false });
      sendResponse({ ok: true });
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }

  if (msg.type === 'VG_HLS_START') {
    (async () => {
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item || item.kind !== 'hls') { sendResponse({ ok: false, error: 'not_hls' }); return; }
      if (item.encryption !== 'none' || item.live) { sendResponse({ ok: false, error: 'unsupported' }); return; }
      const job = await hlsStart(item, msg.height);
      sendResponse({ ok: true, job });
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === 'VG_APP_STATUS') {
    vgFindApp(!!msg.force).then((a) => sendResponse({ found: !!a.port, version: a.version }));
    return true;
  }
  if (msg.type === 'VG_APP_SEND') {
    (async () => {
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item || item.encryption !== 'none' || item.live) { sendResponse({ ok: false, error: 'unsupported' }); return; }
      sendResponse(await vgSendToApp(item));
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === 'VG_HLS_JOBS') {
    hlsLoad().then(() => sendResponse({ jobs: [...hlsJobs.values()].sort((a, b) => b.at - a.at) }));
    return true;
  }
  if (msg.type === 'VG_HLS_CANCEL') {
    chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_CANCEL', id: msg.id }).catch(() => {});
    sendResponse({ ok: true });
    return false;
  }

  // ── 5.17.0: bulk download to this computer (popup channel list) ──
  if (msg.type === 'VG_BULK_START') {
    (async () => {
      const cur = await vgBulkLoad();
      if (cur && !cur.finishedAt && cur.items.some((x) => x.status === 'running' || x.status === 'queued')) {
        sendResponse({ ok: false, error: 'Đang có một lượt tải hàng loạt chạy — hãy chờ xong hoặc bấm Dừng.' }); // wording: BA review
        return;
      }
      const seen = new Set();
      const items = (Array.isArray(msg.items) ? msg.items : [])
        .filter((x) => x && typeof x.url === 'string' && /^https:\/\//.test(x.url) && !seen.has(x.url) && seen.add(x.url))
        .slice(0, 500)
        .map((x) => ({ url: x.url, title: typeof x.title === 'string' ? x.title.slice(0, 200) : '', status: 'queued' }));
      const skipped = (Array.isArray(msg.later) ? msg.later : []).slice(0, 500)
        .map((x) => ({ url: x.url, title: typeof x.title === 'string' ? x.title.slice(0, 200) : '', status: 'skipped' }));
      if (!items.length) { sendResponse({ ok: false, error: 'Chưa chọn video nào.' }); return; }
      _vgBulk = { startedAt: Date.now(), quality: msg.quality === 'mp3' ? 'mp3' : 'video', items: [...items, ...skipped],
                  stopped: false, stopReason: msg.laterReason || null, finishedAt: null };
      await vgBulkSave();
      vgBulkPump();
      sendResponse({ ok: true, count: items.length });
    })();
    return true;
  }
  if (msg.type === 'VG_BULK_STOP') {
    (async () => {
      const b = await vgBulkLoad();
      if (b && !b.finishedAt) {
        b.stopped = true;
        b.stopReason = b.stopReason || 'bạn đã bấm Dừng';
        for (const x of b.items) if (x.status === 'queued') x.status = 'skipped';
        await vgBulkSave();
        if (!b.items.some((x) => x.status === 'running')) await vgBulkFinish();
      }
      sendResponse({ ok: true });
    })();
    return true;
  }
  if (msg.type === 'VG_BULK_CLEAR') {
    (async () => {
      const b = await vgBulkLoad();
      if (b && !b.items.some((x) => x.status === 'running' || (x.status === 'queued' && !b.stopped))) {
        _vgBulk = null;
        await chrome.storage.local.remove(VG_BULK_KEY);
      }
      sendResponse({ ok: true });
    })();
    return true;
  }

  if (msg.type === 'VG_DOWNLOAD_NOW') {
    (async () => {
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

        const { data, dlUrl, finalExt, safeName } = await vgSaveVideo({ url, quality, remove_watermark, download_subs }, controller.signal, msg.payload.pageTabId)
          .finally(() => { clearTimeout(abortTimer); clearInterval(pingInterval); });
        notifTitle = `✅ ${data.title || 'Video'} — đang tải`;

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
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
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
        // This ran `fetch(msg.url, {method, headers, body})` verbatim, with the
        // service worker's privileges — no same-origin policy, host_permissions
        // for 25 domains, and callable from every content script we inject.
        // It is a proxy for our own API, so it is now scoped to our own API.
        const base = await getApiBase();
        let target;
        try { target = new URL(msg.url, base); } catch { target = null; }
        if (!target || target.origin !== new URL(base).origin) {
          sendResponse({ ok: false, error: 'Blocked: VG_API_FETCH is restricted to the configured API origin' });
          return;
        }
        const resp = await fetch(target.toString(), {
          method: msg.method || 'POST',
          // our own API only (checked above) — a signed-in user is not a guest
          headers: await authHeaders(msg.headers || { 'Content-Type': 'application/json' }),
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
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
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
      headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url: targetUrl, quality: isMP3 ? 'mp3_320' : 'video', remove_watermark: true, user_cookies_b64 }),
      signal: controller.signal,
    });
    const data = await resp.json();

    if (resp.ok && data.success) {
      let ext = isMP3 ? 'mp3' : 'mp4';
      if (data.is_audio_only) ext = 'mp3';
      // For MP3: prioritize local converted file, never the video stream URL
      const targetDlUrl = isMP3
        ? (vgFileId(data.local_mp3_file_id, data.local_mp3_path) || vgFileId(data.local_file_id, data.local_file_path) || data.direct_mp4_url)
        : (data.direct_mp4_url || vgFileId(data.local_file_id, data.local_file_path));
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
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: '❌ ' + (resp.ok ? (data.detail || 'Lỗi') : vgServerErrorText(data, resp.status)) }).catch(() => {});
    }
  } catch (err) {
    const msg = err.name === 'AbortError' ? 'Xử lý quá lâu' : 'Lỗi kết nối';
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: `❌ ${msg}` }).catch(() => {});
  }
});
