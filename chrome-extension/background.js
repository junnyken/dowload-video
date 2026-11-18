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
  return { ...extra, ...(await vgDeviceHeader()), Authorization: `Bearer ${token}` };
}

// ── 5.21.0: device linking (backend app/core/device_link.py) ──────────
// An account works on a few devices (Free 2, Pro 5); Free accounts on one
// device share one allowance. A device is X-VG-Device (64 hex), sent only with
// a signed-in request: the VidGrab app's machine id when the app is on this
// computer (bridge ping), else this install's own random id. The VidGrab site
// in this browser gets the same id (web-bridge.js), so one PC = one device.
const DEVICE_KEY = 'vg_device_id';
async function vgDeviceId() {
  try {
    const got = await chrome.storage.local.get(DEVICE_KEY);
    if (/^[0-9a-f]{64}$/.test(got[DEVICE_KEY] || '')) return got[DEVICE_KEY];
    const b = crypto.getRandomValues(new Uint8Array(32));
    const id = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    await chrome.storage.local.set({ [DEVICE_KEY]: id });
    return id;
  } catch { return null; }
}
async function vgDeviceHeader() {
  const id = await vgDeviceId();
  if (!id) return {};
  // the version tells the server this device entry is the extension ("Thiết bị của tôi")
  let ver = '';
  try { ver = chrome.runtime.getManifest().version || ''; } catch { /* tests */ }
  return ver ? { 'X-VG-Device': id, 'X-VG-Ext-Version': ver } : { 'X-VG-Device': id };
}
self.vgDeviceId = vgDeviceId;

// The account signed this device out (too many devices): drop the session,
// tell the popup why. Every API response passes here (fetch wrapper below).
async function vgMaybeSignedOut(resp) {
  try {
    const j = await resp.json();
    const d = j && j.detail;
    if (!d || d.error_code !== 'device_signed_out') return;
    await chrome.storage.local.remove([AUTH_TOKEN_KEY, 'vg_auth_email', 'vg_auth_exp']);
    await chrome.storage.local.set({ vg_signed_out_msg: String(d.user_message || '') });
    chrome.runtime.sendMessage({ type: 'VG_AUTH_CHANGED', authenticated: false, reason: 'device_signed_out', message: d.user_message }).catch(() => {});
  } catch { /* not JSON */ }
}
const _vgFetch = self.fetch.bind(self);
self.fetch = async (input, init) => {
  const r = await _vgFetch(input, init);
  if (r.status === 401) vgMaybeSignedOut(r.clone());
  return r;
};

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
// substring of it. Only these exact hostnames are ours - hostname equality,
// 5.22.2: + dowloadvideo.io.vn, the site's own domain (owner 27/10: signed in there, the extension
// never heard of it — web-bridge.js did not run on it and its sender was not trusted),
// no suffix rule of any kind (the old shared-suffix rule was removed in 5.2.4).
const FIRST_PARTY_EXACT_HOSTS = ['dvid.vibe1.tinhgon.xyz', 'dvid-api.vibe1.tinhgon.xyz', 'dowloadvideo.io.vn'];
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
  // Cloud Drives & Social platforms where user browser session unlocks private/quota-limited files:
  'google.com', 'terabox.com', '1024terabox.com', 'teraboxapp.com',
  'onedrive.live.com', 'sharepoint.com', 'dropbox.com', 'weibo.com', 'weibo.cn',
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

  // 5.20.1: the person's choice (popup settings). Default: on in the website
  // build, off in the Web Store build (no YouTube host in its manifest).
  let send;
  try { send = (await chrome.storage.sync.get('vg_send_cookies')).vg_send_cookies; } catch { /* default */ }
  if (typeof send !== 'boolean') send = Boolean(chrome.runtime.getManifest().key);
  if (!send) return undefined;

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
  const isGDriveStream = /(?:drive|docs)\.google\.com\/videoplayback\b/i.test(low);
  if (!isGDriveStream && (/\/videoplayback\b|[?&]ip=/i.test(low))) return false; // IP-locked stream
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

// 5.22.1 (owner 27/10): Douyin's CDN answers 403 without a douyin.com Referer (measured: the same
// v3-web.douyinvod.com link, 403 without, 206 video/mp4 with), and chrome.downloads sends none — so
// every direct Douyin download failed and went through the server 15 s later. Session rules add it to
// the extension's own requests (tabId -1: downloads, fetch) for those hosts only; pages are untouched.
const VG_REFERER_RULES = [
  { id: 9101, domains: ['douyinvod.com', 'douyincdn.com', 'zjcdn.com', 'bytecdn.cn', 'amemv.com', 'snssdk.com'], referer: 'https://www.douyin.com/' },
];
function vgRefererRules(list = VG_REFERER_RULES) {
  if (!chrome.declarativeNetRequest || !chrome.declarativeNetRequest.updateSessionRules) return Promise.resolve(false);
  return chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: list.map((r) => r.id),
    addRules: list.map((r) => ({
      id: r.id,
      priority: 1,
      action: { type: 'modifyHeaders', requestHeaders: [{ header: 'referer', operation: 'set', value: r.referer }] },
      condition: { requestDomains: r.domains, tabIds: [chrome.tabs.TAB_ID_NONE], resourceTypes: ['other', 'media', 'xmlhttprequest'] },
    })),
  }).then(() => true, (e) => { console.warn('[VidGrab] referer rules:', e && e.message); return false; });
}
vgRefererRules();

// Ephemeral TAB_ID_NONE Referer + Origin rules for sniffed HLS/DASH/file CDNs
// (IDs 9200..9231). Scoped strictly to extension background/offscreen fetches
// (tabId -1), so real browser tabs are never modified.
const VG_STREAM_RULE_BASE = 9200;
const VG_STREAM_RULE_MAX = 32;
const _vgStreamDomainSlot = new Map();
let _vgStreamRuleSeq = 0;
async function vgEnsureStreamReferer(targetUrl, refererUrl) {
  if (!chrome.declarativeNetRequest || !chrome.declarativeNetRequest.updateSessionRules) return false;
  let target, ref;
  try {
    target = new URL(String(targetUrl || ''));
    ref = new URL(String(refererUrl || ''));
  } catch { return false; }
  if (!/^https?:$/.test(target.protocol) || !/^https?:$/.test(ref.protocol)) return false;
  const host = target.hostname.toLowerCase();
  if (!host || host === 'localhost' || host === '127.0.0.1' || isFirstPartyHost(host)) return false;
  if (VG_REFERER_RULES.some((r) => r.domains.some((d) => host === d || host.endsWith('.' + d)))) return true;
  let id = _vgStreamDomainSlot.get(host);
  if (id == null) {
    id = VG_STREAM_RULE_BASE + (_vgStreamRuleSeq++ % VG_STREAM_RULE_MAX);
    for (const [k, v] of _vgStreamDomainSlot) if (v === id) _vgStreamDomainSlot.delete(k);
    _vgStreamDomainSlot.set(host, id);
  }
  const headers = [
    { header: 'referer', operation: 'set', value: ref.toString() },
    { header: 'origin', operation: 'set', value: ref.origin },
  ];
  try {
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [id],
      addRules: [{
        id,
        priority: 2,
        action: { type: 'modifyHeaders', requestHeaders: headers },
        condition: { requestDomains: [host], tabIds: [chrome.tabs.TAB_ID_NONE], resourceTypes: ['other', 'media', 'xmlhttprequest'] },
      }],
    });
    return true;
  } catch { return false; }
}
self.vgEnsureStreamReferer = vgEnsureStreamReferer;

function vgNeedsReferer(url) {
  let host = '';
  try { host = new URL(url).hostname; } catch { return false; }
  return VG_REFERER_RULES.some((r) => r.domains.some((d) => host === d || host.endsWith('.' + d)));
}

// The file fetched in the offscreen page (which gets the Referer), then saved from a blob: URL.
let vgBlobSaves = 0;
async function vgSaveViaPage(url, name) {
  vgBlobSaves++;
  let blobUrl = null;
  try {
    await ensureOffscreen();
    const r = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'BLOB_FETCH', url });
    if (!r || !r.ok) return false;
    blobUrl = r.url;
    return await vgSaveFile(blobUrl, name);
  } catch {
    return false;
  } finally {
    vgBlobSaves--;
    // the download reads the blob after "complete" is reported on some builds: release a little later
    if (blobUrl) setTimeout(() => chrome.runtime.sendMessage({ target: 'offscreen', type: 'BLOB_RELEASE', url: blobUrl }).catch(() => {}).finally(() => closeOffscreenIfIdle()), 60_000);
    else closeOffscreenIfIdle();
  }
}

// Download preferring the user's own IP; fall back to the server proxy if the
// direct download is interrupted (403 / CORS / referer-gated CDN).
function smartDownload({ rawUrl, base, safeName, finalExt, saveAs, subDir }) {
  const filename = subDir ? `VidGrab/${subDir}/${safeName}.${finalExt}` : `VidGrab/${safeName}.${finalExt}`;
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
        chrome.downloads.erase({ id: downloadId }, () => { void chrome.runtime.lastError; }); // see vgSaveFile
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
    vgNativeConnect(); // 5.22.0: the app link, when it is not up
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
      reasons: ['BLOBS', 'USER_MEDIA'],
      justification: 'Ghép các đoạn video HLS thành MP4 và quay tab (khi người dùng bấm Quay) ngay trong trình duyệt của người dùng.',
    });
  } catch (e) {
    if (!/single offscreen|already/i.test(String(e && e.message))) throw e;
  }
}
async function closeOffscreenIfIdle() {
  if (vgBlobSaves > 0) return;
  if ([...hlsJobs.values()].some((j) => j.state === 'running' || j.state === 'saving')) return;
  const rec = (await chrome.storage.session.get('vg_rec').catch(() => ({}))).vg_rec;
  if (rec && (rec.state === 'recording' || rec.state === 'saving')) return;
  try { if (await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument(); } catch { /* gone */ }
}
function hlsSafeName(t) {
  return String(t || 'video').replace(/[/\\?%*:|"<>\n\r\t]/g, '-').trim().slice(0, 120) || 'video';
}

function vgSafeFolder(raw) {
  const s = String(raw || '')
    .replace(/[/\\?%*:|"<>\n\r\t\x00-\x1f]/g, '-')
    .replace(/\s+/g, ' ')
    .replace(/^[.\s-]+|[.\s-]+$/g, '')
    .slice(0, 80);
  if (!s || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(s)) return '';
  return s;
}

function vgStreamPaths(title, suffix = '', allowSeriesFolder = true) {
  const safeName = hlsSafeName(title);
  const ep = allowSeriesFolder && self.VGSniff && typeof self.VGSniff.parseSeriesEpisode === 'function'
    ? self.VGSniff.parseSeriesEpisode(title)
    : null;
  const subDir = ep && ep.seriesTitle ? vgSafeFolder(ep.seriesTitle) : '';
  const stem = `${safeName}${suffix}`;
  return {
    safeName: stem,
    subDir,
    relBase: subDir ? `VidGrab/${subDir}/${stem}` : `VidGrab/${stem}`,
  };
}

async function vgFetchSubUrl(url, refererUrl, timeoutMs = 8000) {
  if (!url || !/^https?:/i.test(url)) return null;
  if (refererUrl) await vgEnsureStreamReferer(url, refererUrl).catch(() => {});
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      cache: 'no-store',
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
    if (!resp || !resp.ok) return null;
    return await resp.text();
  } catch {
    return null;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function vgFetchSubtitleContent(sub, refererUrl) {
  const text = await vgFetchSubUrl(sub && sub.url, refererUrl, 8000);
  if (!text || text.length > 2 * 1024 * 1024) return null;
  if (sub.format === 'hls' || /^\s*#EXTM3U/m.test(text)) {
    const segUrls = [];
    for (const rawLine of text.split(/\r?\n/)) {
      const l = rawLine.trim();
      if (!l || l.startsWith('#')) continue;
      try {
        segUrls.push(new URL(l, sub.url).toString());
      } catch { /* ignore */ }
      if (segUrls.length >= 300) break;
    }
    if (!segUrls.length) return null;
    const parts = [];
    let totalChars = 0;
    const SUB_BATCH = 6;
    for (let i = 0; i < segUrls.length && totalChars <= 2 * 1024 * 1024; i += SUB_BATCH) {
      const batch = segUrls.slice(i, i + SUB_BATCH);
      const texts = await Promise.all(batch.map((u) => vgFetchSubUrl(u, refererUrl, 8000)));
      for (const st of texts) {
        if (!st) continue;
        totalChars += st.length;
        if (totalChars > 2 * 1024 * 1024) break;
        parts.push(st);
      }
    }
    const srt = self.VGSniff && typeof self.VGSniff.vttToSrt === 'function'
      ? self.VGSniff.vttToSrt(parts.join('\n\n'))
      : parts.join('\n\n');
    return srt ? { content: srt, ext: 'srt' } : null;
  }
  if (sub.format === 'ass' || /^\s*\[Script Info\]/im.test(text)) {
    return { content: text, ext: 'ass' };
  }
  const srt = self.VGSniff && typeof self.VGSniff.vttToSrt === 'function'
    ? self.VGSniff.vttToSrt(text)
    : text;
  return srt ? { content: srt, ext: 'srt' } : null;
}

async function vgSaveCompanionSubtitles(subtitles, relBase, refererUrl) {
  if (!Array.isArray(subtitles) || !subtitles.length || !relBase) return 0;
  const usedTags = new Set();
  let saved = 0;
  for (const sub of subtitles.slice(0, 4)) {
    try {
      const got = await vgFetchSubtitleContent(sub, refererUrl);
      if (!got || !got.content) continue;
      const baseLang = String(sub.lang || 'sub').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 12) || 'sub';
      let langTag = baseLang;
      let idx = 2;
      while (usedTags.has(`${langTag}.${got.ext}`)) {
        langTag = `${baseLang}-${idx++}`;
      }
      usedTags.add(`${langTag}.${got.ext}`);
      // Use application/octet-stream (never text/plain), otherwise Chrome's MIME-to-extension
      // enforcer on Windows silently rewrites .srt / .ass filenames to .txt!
      const dataUrl = `data:application/octet-stream;charset=utf-8,${encodeURIComponent(got.content)}`;
      chrome.downloads.download({
        url: dataUrl,
        filename: `${relBase}.${langTag}.${got.ext}`,
        saveAs: false,
        conflictAction: 'uniquify',
      }, () => { void chrome.runtime.lastError; });
      saved += 1;
    } catch { /* non-fatal */ }
  }
  return saved;
}

async function vgGetStreamCookieJar(streamUrl, refererUrl) {
  if (!chrome.cookies || typeof chrome.cookies.getAll !== 'function') return undefined;
  let send;
  try { send = (await chrome.storage.sync.get('vg_send_cookies')).vg_send_cookies; } catch { /* default */ }
  if (typeof send !== 'boolean') {
    try { send = Boolean(chrome.runtime.getManifest().key); } catch { send = false; }
  }
  if (!send) return undefined;

  const urls = [];
  for (const cand of [streamUrl, refererUrl]) {
    if (!cand) continue;
    try {
      const u = new URL(cand);
      if (!/^https?:$/.test(u.protocol)) continue;
      if (LOCAL_HOSTS.includes(u.hostname.toLowerCase()) || isFirstPartyHost(u.hostname)) continue;
      const originSlash = `${u.origin}/`;
      if (!urls.includes(originSlash)) urls.push(originSlash);
    } catch { /* ignore */ }
  }
  if (!urls.length) return undefined;

  const merged = [];
  const seen = new Set();
  for (const u of urls) {
    try {
      const list = await chrome.cookies.getAll({ url: u });
      for (const c of list || []) {
        if (!c || !c.name || !c.domain) continue;
        const k = `${c.domain}|${c.path || '/'}|${c.name}`;
        if (seen.has(k)) continue;
        seen.add(k);
        merged.push(c);
        if (merged.length >= 60) break;
      }
    } catch { /* ignore */ }
    if (merged.length >= 60) break;
  }
  if (!merged.length) return undefined;
  const text = _toNetscapeCookieFile(merged);
  if (text.length > 12 * 1024) return undefined;
  return text;
}

function vgCleanStreamTitle(item) {
  const raw = (item && item.title) || '';
  const cleaned = self.VGSniff && typeof self.VGSniff.cleanMediaTitle === 'function'
    ? self.VGSniff.cleanMediaTitle(raw, item && item.pageUrl)
    : '';
  return cleaned || String(raw || 'video').trim() || 'video';
}

let _vgApiDownUntil = 0;
function vgNoteApiDown(ms = 30_000) { _vgApiDownUntil = Date.now() + ms; }
function vgNoteApiUp() { _vgApiDownUntil = 0; }

const _vgAiTitleCache = new Map();

async function vgRefineStreamTitleAi(item) {
  const localClean = vgCleanStreamTitle(item);
  if (!item || !item.title || localClean === 'video' || localClean.length < 6) return localClean;
  const tok = await getAuthToken();
  if (!tok) return localClean;
  const cacheKey = `${item.pageUrl || ''}|${localClean}`;
  if (_vgAiTitleCache.has(cacheKey)) return _vgAiTitleCache.get(cacheKey);
  if (Date.now() < _vgApiDownUntil) return localClean;
  try {
    const base = await getApiBase();
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 700) : null;
    const resp = await fetch(`${base}/api/v1/smart-metadata/clean`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify({
        title: String(item.title).slice(0, 300),
        platform: '',
        uploader: '',
      }),
      ...(ctrl ? { signal: ctrl.signal } : {}),
    }).finally(() => { if (timer) clearTimeout(timer); });
    vgNoteApiUp();
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const d = await resp.json();
    const cleanMedia = self.VGSniff && typeof self.VGSniff.cleanMediaTitle === 'function'
      ? self.VGSniff.cleanMediaTitle
      : null;
    const parseEp = self.VGSniff && typeof self.VGSniff.parseSeriesEpisode === 'function'
      ? self.VGSniff.parseSeriesEpisode
      : null;
    const rawAiTitle = d && (d.filename_cleaned || d.clean_title);
    let aiTitle = rawAiTitle
      ? (cleanMedia ? cleanMedia(rawAiTitle, item.pageUrl) : String(rawAiTitle).trim())
      : '';
    if (aiTitle && aiTitle.length >= 3) {
      if (parseEp && !parseEp(aiTitle)) {
        const localEp = parseEp(localClean);
        if (localEp && localEp.episodeLabel) {
          aiTitle = `${aiTitle} - ${localEp.episodeLabel}`;
        } else if (d.episode_tag && typeof d.episode_tag === 'string' && d.episode_tag.trim()) {
          aiTitle = `${aiTitle} - ${d.episode_tag.trim()}`;
        }
      }
      if (_vgAiTitleCache.size >= 100) {
        const firstKey = _vgAiTitleCache.keys().next().value;
        if (firstKey !== undefined) _vgAiTitleCache.delete(firstKey);
      }
      _vgAiTitleCache.set(cacheKey, aiTitle);
      return aiTitle;
    }
  } catch { vgNoteApiDown(45_000); }
  return localClean;
}

const UNIVERSAL_STREAM_TIERS = new Set(['team', 'enterprise', 'admin']);
const UNIVERSAL_PRO_TIERS = new Set(['pro', 'growth']);
let _vgUniversalTierCache = { token: '', at: 0, res: null };

function vgResetUniversalTierCache() {
  _vgUniversalTierCache = { token: '', at: 0, res: null };
}

function vgUniversalModeFromPayload(d, tier) {
  if (d && typeof d.universal_stream_mode === 'string') {
    const m = d.universal_stream_mode.toLowerCase();
    if (m === 'team' || m === 'pro' || m === 'none') return m;
  }
  if ((d && d.universal_stream === true) || UNIVERSAL_STREAM_TIERS.has(tier)) return 'team';
  if (UNIVERSAL_PRO_TIERS.has(tier)) return 'pro';
  return 'none';
}

async function vgCheckUniversalStreamTier(force = false) {
  const token = await getAuthToken();
  if (!token) {
    return { allowed: false, mode: 'none', isTeam: false, authenticated: false, tier: 'guest', error: 'signin_required' };
  }
  if (!force && _vgUniversalTierCache.res && _vgUniversalTierCache.token === token && Date.now() - _vgUniversalTierCache.at < 90_000) {
    return _vgUniversalTierCache.res;
  }
  try {
    const base = await getApiBase();
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 3500) : null;
    const r = await fetch(`${base}/api/v1/user/usage`, {
      headers: await authHeaders(),
      ...(ctrl ? { signal: ctrl.signal } : {}),
    }).finally(() => { if (timer) clearTimeout(timer); });
    vgNoteApiUp();
    if (r.status === 401 || r.status === 403) {
      const denied = { allowed: false, mode: 'none', isTeam: false, authenticated: false, tier: 'guest', error: 'signin_required' };
      _vgUniversalTierCache = { token, at: Date.now(), res: denied };
      return denied;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const tier = String((d && d.tier) || 'free').toLowerCase();
    const mode = vgUniversalModeFromPayload(d, tier);
    const allowed = mode !== 'none';
    const isTeam = mode === 'team';
    const res = { allowed, mode, isTeam, authenticated: true, tier, error: allowed ? null : 'tier_required' };
    _vgUniversalTierCache = { token, at: Date.now(), res };
    chrome.storage.local.set({
      vg_cached_tier: tier,
      vg_cached_universal: allowed,
      vg_cached_universal_mode: mode,
      vg_cached_tier_tok: token.slice(0, 16),
    }).catch(() => {});
    return res;
  } catch {
    vgNoteApiDown(30_000);
    try {
      const c = await chrome.storage.local.get(['vg_cached_tier', 'vg_cached_universal', 'vg_cached_universal_mode', 'vg_cached_tier_tok']);
      if (c && c.vg_cached_tier_tok === token.slice(0, 16)) {
        const tier = String(c.vg_cached_tier || 'free').toLowerCase();
        const mode = typeof c.vg_cached_universal_mode === 'string'
          ? c.vg_cached_universal_mode
          : (c.vg_cached_universal && UNIVERSAL_STREAM_TIERS.has(tier) ? 'team' : (UNIVERSAL_PRO_TIERS.has(tier) ? 'pro' : 'none'));
        const allowed = mode === 'pro' || mode === 'team';
        const res = { allowed, mode, isTeam: mode === 'team', authenticated: true, tier, error: allowed ? null : 'tier_required' };
        _vgUniversalTierCache = { token, at: Date.now() - 60_000, res };
        return res;
      }
    } catch { /* ignore */ }
    return { allowed: false, mode: 'none', isTeam: false, authenticated: true, tier: 'free', error: 'tier_required' };
  }
}

function vgCanonicalManifestIdentity(rawUrl) {
  try {
    const u = new URL(String(rawUrl || ''));
    return `${u.origin.toLowerCase()}${u.pathname}`;
  } catch {
    return String(rawUrl || '');
  }
}

async function hlsStart(item, height, opts = {}) {
  await hlsLoad();
  const referer = item.refererUrl || item.pageUrl || '';
  if (referer) await vgEnsureStreamReferer(item.url, referer);
  let maxEmptyPolls = 3;
  try {
    const cfg = await chrome.storage.sync.get('vg_live_max_idle_polls');
    const n = Number(cfg && cfg.vg_live_max_idle_polls);
    if (Number.isInteger(n) && n >= 1 && n <= 20) maxEmptyPolls = n;
  } catch { /* default 3 */ }
  const id = `h${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const cleanTitle = await vgRefineStreamTitleAi(item);
  const now = Date.now();
  const isLive = !!item.live;
  const isTeam = opts.isTeam !== false;
  const maxHeightCap = !isTeam;
  const effectiveHeight = maxHeightCap
    ? (height && height <= 1080 ? height : 1080)
    : (height || null);
  const audioTrack = (isTeam && opts.audioTrack && typeof opts.audioTrack === 'object')
    ? {
      lang: String(opts.audioTrack.lang || '').slice(0, 16),
      name: String(opts.audioTrack.name || '').slice(0, 64),
    }
    : null;
  const subtitles = Array.isArray(item.subtitles)
    ? item.subtitles.slice(0, 6).map((s) => ({ url: s.url, lang: s.lang || 'sub', format: s.format || 'vtt' }))
    : [];
  const job = {
    id, title: cleanTitle, url: item.url, referer, subtitles,
    tabId: Number.isInteger(opts.tabId) ? opts.tabId : null,
    isTeam, audioTrack,
    height: effectiveHeight, state: 'running',
    live: isLive, stopping: false, resumable: false, checkpoint: null,
    startedAt: now, elapsedSec: 0,
    done: 0, total: 0, bytes: 0, at: now, message: '',
  };
  console.info('[VidGrab][HLS] Starting job:', {
    id,
    url: item.url,
    live: isLive,
    encryption: item.encryption || 'none',
    reason: item.encryptionReason || 'unencrypted',
    audioTrack: audioTrack ? (audioTrack.name || audioTrack.lang) : 'default',
    maxEmptyPolls,
  });
  hlsJobs.set(id, job);
  hlsSave();
  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    target: 'offscreen',
    type: 'HLS_START',
    job: {
      id,
      url: item.url,
      height: job.height,
      maxHeightCap,
      audioTrack,
      title: job.title,
      referer,
      live: isLive,
      allowLive: true,
      maxEmptyPolls,
    },
  });
  return job;
}

async function hlsResume(id, allowTokenRebind = true) {
  await hlsLoad();
  const job = hlsJobs.get(id);
  if (!job || job.state !== 'failed' || !job.resumable || !job.checkpoint) {
    return { ok: false, error: 'not_resumable' };
  }
  let cp = { ...job.checkpoint };
  // #1 Auto Token-Refresh OPFS Resume: if the source tab is still open and refreshed its signed manifest URL,
  // rebind job.url to the fresh manifest while keeping 100% of already-written OPFS bytes.
  if (allowTokenRebind && Number.isInteger(job.tabId) && job.tabId >= 0) {
    try {
      if (typeof self.vgSniffProbe === 'function') {
        await self.vgSniffProbe(job.tabId).catch(() => null);
      }
      const tabItems = typeof self.vgSniffList === 'function' ? await self.vgSniffList(job.tabId).catch(() => []) : [];
      const wantCanon = vgCanonicalManifestIdentity(job.url);
      const candidates = tabItems.filter((x) => x && x.kind === 'hls' && x.encryption !== 'drm' && !x.live);
      const fresh = candidates.find((x) => x.url !== job.url && vgCanonicalManifestIdentity(x.url) === wantCanon)
        || candidates.find((x) => x.url !== job.url && vgCleanStreamTitle(x) === job.title);
      if (fresh && fresh.url) {
        job.url = fresh.url;
        if (fresh.refererUrl || fresh.pageUrl) job.referer = fresh.refererUrl || fresh.pageUrl;
        cp = { ...cp, rebindMaster: true };
      }
    } catch { /* keep existing job.url */ }
  }
  if (job.referer) await vgEnsureStreamReferer(job.url, job.referer).catch(() => {});
  Object.assign(job, {
    state: 'running',
    stopping: false,
    resumable: false,
    message: '',
    at: Date.now(),
  });
  hlsSave();
  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    target: 'offscreen',
    type: 'HLS_START',
    job: {
      id: job.id,
      url: job.url,
      height: cp.height || job.height || null,
      maxHeightCap: job.isTeam === false,
      audioTrack: job.audioTrack || cp.audioTrack || null,
      title: job.title,
      referer: job.referer || '',
      live: false,
      allowLive: false,
      resume: cp,
    },
  });
  return { ok: true, job, rebound: Boolean(cp.rebindMaster) };
}

const VG_AUTO_EP_KEY = 'vg_auto_ep_tabs';
const _vgAutoEpSeen = new Map(); // tabId -> Set<canonicalUrl>

async function vgAutoEpMap() {
  try {
    return (await chrome.storage.session.get(VG_AUTO_EP_KEY))[VG_AUTO_EP_KEY] || {};
  } catch {
    return {};
  }
}

async function vgAutoEpGet(tabId) {
  if (tabId == null) return false;
  const m = await vgAutoEpMap();
  return Boolean(m[String(tabId)]);
}

async function vgAutoEpSet(tabId, enabled) {
  if (tabId == null) return false;
  const m = await vgAutoEpMap();
  if (enabled) {
    m[String(tabId)] = Date.now();
  } else {
    delete m[String(tabId)];
    _vgAutoEpSeen.delete(tabId);
  }
  await chrome.storage.session.set({ [VG_AUTO_EP_KEY]: m }).catch(() => {});
  return Boolean(enabled);
}

self.vgOnSniffStreamReady = async (tabId, item) => {
  if (!item || item.hidden || item.encryption === 'drm' || item.live) return;
  if (!(await vgAutoEpGet(tabId))) return;
  const gate = await vgCheckUniversalStreamTier();
  if (!gate.authenticated) {    await vgAutoEpSet(tabId, false);
    return;
  }
  const canon = self.VGSniff && typeof self.VGSniff.canonicalUrl === 'function'
    ? self.VGSniff.canonicalUrl(item.url)
    : item.url;
  let seen = _vgAutoEpSeen.get(tabId);
  if (!seen) { seen = new Set(); _vgAutoEpSeen.set(tabId, seen); }
  if (seen.has(canon)) return;
  seen.add(canon);
  if (seen.size > 60) {
    const first = seen.values().next().value;
    seen.delete(first);
  }

  // Wait briefly for SPA episode title / <track> subtitle DOM updates before dispatching.
  await new Promise((r) => setTimeout(r, 450));
  if (typeof self.vgSniffProbe === 'function') {
    await self.vgSniffProbe(tabId).catch(() => {});
  }
  if (item.hidden || item.encryption === 'drm' || item.live) return;

  const appConnected = Boolean((vgNativePort && vgNativeApp.up) || (await vgFindApp().catch(() => ({ port: null }))).port);
  if (appConnected) {
    const res = await vgSendToApp(item, item.height || undefined).catch(() => ({ ok: false }));
    if (res && res.ok) return;
  }
  if (item.kind === 'hls') {
    await hlsStart(item, item.height || null, { tabId, isTeam: true });
  } else if (item.kind === 'file') {
    const ref = item.refererUrl || item.pageUrl || '';
    if (ref) await vgEnsureStreamReferer(item.url, ref);
    const base = await getApiBase();
    const ext = (item.url.split('?')[0].match(/\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i) || [])[1]
      || (/^audio\//.test(item.mime || '') ? 'm4a' : 'mp4');
    const { safeName, subDir, relBase } = vgStreamPaths(vgCleanStreamTitle(item), '', true);
    smartDownload({ rawUrl: item.url, base, safeName, finalExt: ext.toLowerCase(), saveAs: false, subDir });
    void vgSaveCompanionSubtitles(item.subtitles, relBase, ref);
  }
};

// ── #5 Floating In-Page <video> Hover Pill (Shadow-DOM, Non-DRM Only, Zero New Permissions) ──
const _vgPillTimers = new Map();
const VG_PILL_SKIP_HOST_RE = /(?:^|\.)(youtube\.com|youtu\.be|tiktok\.com|douyin\.com|facebook\.com|instagram\.com)$/i;

function vgScheduleFloatingPill(tabId) {
  if (tabId == null || tabId < 0) return;
  clearTimeout(_vgPillTimers.get(tabId));
  _vgPillTimers.set(tabId, setTimeout(() => {
    _vgPillTimers.delete(tabId);
    vgSyncFloatingPill(tabId).catch(() => {});
  }, 350));
}

async function vgSyncFloatingPill(tabId) {
  if (!chrome.scripting || typeof chrome.scripting.executeScript !== 'function') return false;
  let enabled = true;
  try {
    const cfg = await chrome.storage.sync.get('vg_floating_pill');
    if (cfg && cfg.vg_floating_pill === false) enabled = false;
  } catch { /* default on */ }
  if (!enabled) return false;

  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !tab.url || !/^https?:\/\//i.test(tab.url)) return false;
  let host = '';
  try { host = new URL(tab.url).hostname.toLowerCase(); } catch { return false; }
  if (!host || isFirstPartyHost(host) || VG_PILL_SKIP_HOST_RE.test(host)) return false;

  const items = typeof self.vgSniffList === 'function' ? await self.vgSniffList(tabId).catch(() => []) : [];
  if (!items.length) return false;
  const cleanItems = items.filter((x) => x && !x.hidden && !x.pending && !x.error && x.encryption !== 'drm');
  if (!cleanItems.length) return false;
  // Prefer HLS > DASH > File, highest height first
  const rankKind = (k) => (k === 'hls' ? 3 : k === 'dash' ? 2 : 1);
  const best = [...cleanItems].sort((a, b) => rankKind(b.kind) - rankKind(a.kind) || (b.height || 0) - (a.height || 0) || (b.at || 0) - (a.at || 0))[0];
  if (!best) return false;

  const qLbl = self.VGSniff && typeof self.VGSniff.qualityLabel === 'function'
    ? self.VGSniff.qualityLabel(best.height)
    : (best.height ? `${best.height}p` : '');
  const metaParts = [
    qLbl || (best.kind === 'hls' ? 'HLS' : best.kind === 'dash' ? 'DASH' : 'MP4'),
    Array.isArray(best.audioRenditions) && best.audioRenditions.length > 1 ? '🔊' : '',
    Array.isArray(best.subtitles) && best.subtitles.length > 0 ? 'CC' : '',
    best.live ? 'LIVE' : '',
  ].filter(Boolean);

  await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    args: [{
      itemId: best.id,
      label: t('pill_btn_label'),
      badge: metaParts.join(' · '),
      closeTitle: t('common_close'),
    }],
    func: (cfg) => {
      try {
        const findEligibleVideo = () => {
          const vids = document.querySelectorAll('video');
          let bestVid = null;
          let bestArea = 0;
          for (let i = 0; i < vids.length; i++) {
            const v = vids[i];
            if (v.mediaKeys) continue;
            const r = v.getBoundingClientRect();
            if (r.width >= 240 && r.height >= 135) {
              const area = r.width * r.height;
              if (area > bestArea) { bestArea = area; bestVid = v; }
            }
          }
          return bestVid;
        };

        if (window.__vgHoverPill) {
          window.__vgHoverPill.update(cfg);
          return true;
        }
        if (!document.querySelector('video')) return false;

        const state = {
          cfg,
          dismissed: false,
          activeVideo: null,
          hideTimer: null,
          overPill: false,
        };

        const hostEl = document.createElement('div');
        hostEl.id = 'vg-floating-pill-host';
        hostEl.setAttribute('data-vg-pill', '1');
        hostEl.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483646;pointer-events:none;';
        const shadow = hostEl.attachShadow({ mode: 'open' });
        const style = document.createElement('style');
        style.textContent = `
          .pill {
            position: fixed;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 10px 6px 11px;
            border-radius: 999px;
            background: rgba(15, 17, 26, 0.88);
            border: 1px solid rgba(99, 102, 241, 0.55);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
            backdrop-filter: blur(8px);
            color: #f8fafc;
            font: 600 12px/1.2 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
            pointer-events: auto;
            cursor: pointer;
            opacity: 0;
            transform: translateY(-4px);
            transition: opacity 0.18s ease, transform 0.18s ease, border-color 0.15s ease;
            user-select: none;
          }
          .pill.visible { opacity: 1; transform: translateY(0); }
          .pill:hover { border-color: #818cf8; background: rgba(22, 25, 38, 0.96); }
          .main-btn {
            all: unset;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            color: #f8fafc;
          }
          .dot {
            width: 7px; height: 7px; border-radius: 50%;
            background: linear-gradient(135deg, #6366f1, #22d3ee);
            box-shadow: 0 0 8px rgba(99, 102, 241, 0.8);
          }
          .badge {
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 999px;
            background: rgba(99, 102, 241, 0.22);
            color: #c7d2fe;
          }
          .close-btn {
            all: unset;
            margin-left: 2px;
            padding: 0 4px;
            font-size: 14px;
            line-height: 1;
            color: #94a3b8;
            cursor: pointer;
            border-radius: 4px;
          }
          .close-btn:hover { color: #f87171; background: rgba(248, 113, 113, 0.12); }
        `;
        const pill = document.createElement('div');
        pill.className = 'pill';
        const mainBtn = document.createElement('button');
        mainBtn.className = 'main-btn';
        const dot = document.createElement('span');
        dot.className = 'dot';
        const textSpan = document.createElement('span');
        textSpan.className = 'lbl';
        const badgeSpan = document.createElement('span');
        badgeSpan.className = 'badge';
        mainBtn.append(dot, textSpan, badgeSpan);
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.textContent = '×';
        pill.append(mainBtn, closeBtn);
        shadow.append(style, pill);
        (document.documentElement || document.body).appendChild(hostEl);

        const renderCfg = () => {
          textSpan.textContent = state.cfg.label || 'VidGrab';
          badgeSpan.textContent = state.cfg.badge || '';
          badgeSpan.style.display = state.cfg.badge ? 'inline-block' : 'none';
          closeBtn.title = state.cfg.closeTitle || 'Close';
        };
        renderCfg();

        const positionPill = (v) => {
          if (!v || state.dismissed || v.mediaKeys || document.fullscreenElement) {
            pill.classList.remove('visible');
            return;
          }
          const r = v.getBoundingClientRect();
          if (r.width < 240 || r.height < 135 || r.bottom < 40 || r.top > window.innerHeight - 40) {
            pill.classList.remove('visible');
            return;
          }
          const top = Math.max(8, Math.round(r.top + 12));
          const right = Math.max(8, Math.round(window.innerWidth - r.right + 12));
          pill.style.top = `${top}px`;
          pill.style.right = `${right}px`;
        };

        const showForVideo = (v) => {
          if (state.dismissed || !v || v.mediaKeys || document.fullscreenElement) return;
          state.activeVideo = v;
          positionPill(v);
          pill.classList.add('visible');
          clearTimeout(state.hideTimer);
          state.hideTimer = setTimeout(() => {
            if (!state.overPill) pill.classList.remove('visible');
          }, 4500);
        };

        pill.addEventListener('mouseenter', () => {
          state.overPill = true;
          clearTimeout(state.hideTimer);
        });
        pill.addEventListener('mouseleave', () => {
          state.overPill = false;
          state.hideTimer = setTimeout(() => pill.classList.remove('visible'), 900);
        });

        closeBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          state.dismissed = true;
          pill.classList.remove('visible');
          pill.style.display = 'none';
        });

        mainBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const origLabel = state.cfg.label;
          textSpan.textContent = '⏳ VidGrab...';
          chrome.runtime.sendMessage({ type: 'VG_PILL_CLICK', itemId: state.cfg.itemId }, (res) => {
            void chrome.runtime.lastError;
            textSpan.textContent = (res && res.statusText) || origLabel;
            setTimeout(() => { textSpan.textContent = state.cfg.label; }, 3200);
          });
        });

        document.addEventListener('mousemove', (ev) => {
          if (state.dismissed) return;
          const v = findEligibleVideo();
          if (!v) {
            if (!state.overPill) pill.classList.remove('visible');
            return;
          }
          const r = v.getBoundingClientRect();
          if (ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom) {
            showForVideo(v);
          }
        }, { passive: true });

        window.addEventListener('scroll', () => {
          if (state.activeVideo && pill.classList.contains('visible')) positionPill(state.activeVideo);
        }, { passive: true });
        document.addEventListener('fullscreenchange', () => {
          if (document.fullscreenElement) pill.classList.remove('visible');
          else if (state.activeVideo && !state.dismissed) positionPill(state.activeVideo);
        });

        const initialVid = findEligibleVideo();
        if (initialVid) showForVideo(initialVid);

        window.__vgHoverPill = {
          update(nextCfg) {
            state.cfg = nextCfg;
            renderCfg();
            const v = findEligibleVideo();
            if (v && !state.dismissed) positionPill(v);
          },
        };
        return true;
      } catch {
        return false;
      }
    },
  }).catch(() => {});
  return true;
}

self.vgOnSniffPillUpdate = (tabId) => {
  vgScheduleFloatingPill(tabId);
};

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
  if (!data?.success) throw new Error(data?.detail || t('err_server_short'));

  // Resolve download URL (same logic as popup resolveDownloadUrl)
  const ext = (quality || '').includes('mp3') || data.is_audio_only ? 'mp3' : 'mp4';
  let dlUrl = ext === 'mp3'
    ? (vgFileId(data.local_mp3_file_id, data.local_mp3_path) || vgFileId(data.local_file_id, data.local_file_path) || data.direct_mp4_url)
    : (data.direct_mp4_url || vgFileId(data.local_file_id, data.local_file_path) || vgFileId(data.local_mp3_file_id, data.local_mp3_path));

  const safeName = await vgFileName(data.title || 'video', url);

  // Photo Album / Carousel post (Douyin Note, TikTok Photo Mode, Xiaohongshu, Instagram, etc.)
  if (!dlUrl && Array.isArray(data.images) && data.images.length > 0) {
    const albumFolder = safeName.slice(0, 60) || 'Album';
    const photos = data.images.slice(0, 50);
    photos.forEach((imgUrl, idx) => {
      if (!imgUrl || typeof imgUrl !== 'string') return;
      const num = String(idx + 1).padStart(2, '0');
      const imgExt = ((imgUrl.split('?')[0].match(/\.(webp|png|jpeg|jpg)$/i) || [])[1] || 'jpg').toLowerCase();
      chrome.downloads.download({
        url: imgUrl,
        filename: `VidGrab/${albumFolder}/${albumFolder}_${num}.${imgExt}`,
        saveAs: false,
        conflictAction: 'uniquify',
      });
    });
    if (data.audio_url && /^https?:\/\//i.test(data.audio_url)) {
      chrome.downloads.download({
        url: data.audio_url,
        filename: `VidGrab/${albumFolder}/${albumFolder}_audio.mp3`,
        saveAs: false,
        conflictAction: 'uniquify',
      });
    }
    addToHistory({
      title: `${data.title || 'Album'} (${photos.length} photos)`,
      thumbnail: data.thumbnail_url || photos[0] || '',
      url,
      quality: `${photos.length} HD`,
      fileSize: '',
      noWatermark: true,
      hasSubtitle: false,
      subtitleLanguages: [],
      subtitleSource: 'none',
    });
    return { data, dlUrl: photos[0], finalExt: 'jpg', safeName };
  }

  if (!dlUrl) throw new Error(t('err_no_dl_link'));

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
      method: 'POST', headers: { ...(await vgDeviceHeader()), Authorization: `Bearer ${bearer}` },
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

async function vgClaim(url) {
  if (Date.now() < _vgApiDownUntil) return null;
  const base = await getApiBase();
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), 4000) : null;
  try {
    const r = await fetch(`${base}/api/v1/client/quota/claim`, {
      method: 'POST',
      headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url, route: 'local', clientVersion: `ext-${chrome.runtime.getManifest().version}` }),
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
    vgNoteApiUp();
    const d = await r.json().catch(() => null);
    if (r.status === 503 && d && d.error_code === 'client_quota_disabled') return { allowed: true, claimId: null };
    if (!r.ok || !d || d.allowed === false) {
      return { allowed: false, status: r.status, message: (d && (d.detail || d.message)) || `HTTP ${r.status}`, resetTimeVn: d && d.resetTimeVn };
    }
    return { allowed: true, claimId: d.claimId || null };
  } catch {
    vgNoteApiDown(30_000);
    return null; // server unreachable: the direct download may still go (counted later by nobody — rare)
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function vgSettle(claimId, outcome) {
  if (!claimId) return;
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), 4000) : null;
  try {
    const base = await getApiBase();
    await fetch(`${base}/api/v1/client/quota/settle`, {
      method: 'POST',
      headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ claimId, outcome }),
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
  } catch { /* the claim simply stays counted */ } finally {
    if (timer) clearTimeout(timer);
  }
}

// Each video opened for a list / bulk gets its OWN minimised window (closed
// after): with two at a time in one window the second tab was a background
// tab, throttled, and Facebook sometimes did not fill its page in 15 s (one
// reel in three runs fell back to the server, measured 22/10).
async function vgHelperTab(url) {
  const w = await chrome.windows.create({ url, focused: false, state: 'minimized' });
  return { tabId: w.tabs[0].id, windowId: w.id };
}

function vgNormalizeSocialUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return rawUrl;
  try {
    const u = new URL(rawUrl.trim());
    if (/(^|\.)douyin\.com$/i.test(u.hostname)) {
      const modalId = u.searchParams.get('modal_id');
      if (modalId && /^\d{15,25}$/.test(modalId) && !/^\/(?:video|note)\/\d+/.test(u.pathname)) {
        return `https://www.douyin.com/video/${modalId}`;
      }
    }
    return u.toString();
  } catch {
    return rawUrl;
  }
}

/** The file of the video page in `tabId`: the page's own data first, then the sniffer. */
async function vgFindMedia(tabId, targetUrl = '') {
  const isDouyinTarget = /(^|\.)douyin\.com$/i.test((() => { try { return new URL(targetUrl).hostname; } catch { return ''; } })());
  const until = Date.now() + self.vgDirect.FIND_TIMEOUT_MS;
  let attempts = 0;
  while (Date.now() < until) {
    attempts++;
    await new Promise((r) => setTimeout(r, 1200));
    // Douyin: the media link the page itself received (content.js, 14/10) — the
    // VidGrab server's IP is blocked by Douyin, so this is the way that works
    const dy = await chrome.tabs.sendMessage(tabId, { type: 'VG_DOUYIN_DIRECT' }).catch(() => null);
    if (dy && dy.media && dy.media.url) return { url: dy.media.url, title: dy.media.title, via: 'douyin' };
    if (isDouyinTarget) {
      // Never fall through to generic <video> or pickSniffed on Douyin SPA feeds,
      // because the tab holds preloaded/previous videos and would re-download the first video watched.
      if (attempts >= 2) return null;
      continue;
    }
    try {
      const [res] = await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', func: self.vgDirect.vgPageMedia });
      if (res && res.result && res.result.url) return res.result;
    } catch { /* page still loading / navigating */ }
    const list = await self.vgSniffList(tabId).catch(() => []);
    const sniffed = self.vgDirect.pickSniffed(list);
    if (sniffed) return { url: sniffed.url, title: sniffed.title, via: 'sniffer' };
    // 5.18.4: a page that only streams (HLS / DASH — Vimeo, Reddit, Dailymotion…,
    // measured 22/10) will not give a file: hand it to the server after 6 s, not 15
    if (Date.now() > until - self.vgDirect.FIND_TIMEOUT_MS + 6000 && list.some((x) => x.kind === 'hls' || x.kind === 'dash')) return null;
  }
  return null;
}

/** chrome.downloads of `url`; resolves true when the file is complete, false when it failed. */
function vgSaveFile(url, name) {
  return new Promise((resolve) => {
    let id = null;
    const done = (ok) => {
      chrome.downloads.onChanged.removeListener(onChanged); clearTimeout(timer);
      // 5.22.1 (owner 27/10): the caller falls back to the server, so the failed try must not stay in
      // Chrome's downloads as a red "Không thấy tệp trên trang" next to the file that did arrive
      if (!ok && id != null) chrome.downloads.erase({ id }, () => { void chrome.runtime.lastError; });
      resolve(ok);
    };
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
      } else if (delta.state.current === 'interrupted') {
        // 5.20.0: a dropped connection resumes where it stopped (up to 3 times),
        // instead of a half file / a failure (competitors' #1 complaint)
        chrome.downloads.search({ id }, ([it]) => {
          const err = (it && it.error) || '';
          if (it && it.canResume && /^NETWORK_|^SERVER_(FAILED|UNREACHABLE|CONTENT_LENGTH_MISMATCH)/.test(err) && tries < 3) {
            tries++;
            setTimeout(() => chrome.downloads.resume(id, () => { if (chrome.runtime.lastError) done(false); }), 2000 * tries);
          } else done(false);
        });
      }
    };
    let tries = 0;
    const timer = setTimeout(() => done(true), self.vgDirect.SAVE_TIMEOUT_MS); // still running: count it as started
    chrome.downloads.onChanged.addListener(onChanged);
    chrome.downloads.download({ url, filename: `VidGrab/${name}.mp4`, saveAs: false, conflictAction: 'uniquify' }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) { done(false); return; }
      id = downloadId;
    });
  });
}

// 5.20.0: file name template (popup settings, storage.sync vg_name_template):
// {title} {platform} {id} {date}. Default "{title}" = as before.
async function vgFileName(title, url) {
  let tpl = '{title}';
  try { tpl = (await chrome.storage.sync.get('vg_name_template')).vg_name_template || '{title}'; } catch { /* default */ }
  const host = (() => { try { return new URL(url).hostname.replace(/^www\.|^m\./, ''); } catch { return ''; } })();
  const platform = (host.split('.').slice(-2, -1)[0] || 'video');
  const id = ((url || '').match(/(\d{6,25})(?:\/?$|[/?#])/) || (url || '').match(/(?:v=|reel\/|shorts\/|video\/|p\/)([\w-]{5,40})/) || [])[1] || '';
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const name = String(tpl).replace(/\{title\}/g, title || 'video').replace(/\{platform\}/g, platform)
    .replace(/\{id\}/g, id).replace(/\{date\}/g, date);
  return self.vgDirect.safeName(name.replace(/\s*-\s*(?=-|$)/g, '').trim() || title || 'video');
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
// When the VidGrab desktop app runs on this computer, server-extracted links
// can be handed directly to the local app.
async function vgHandToApp(payload) {
  if (!payload || !payload.url || String(payload.quality || 'video').startsWith('mp3')) return null;
  let host = '';
  try { host = new URL(payload.url).hostname; } catch { return null; }
  if (!/(^|\.)tiktok\.com$/i.test(host) && self.vgDirect.directEligible({ url: payload.url, quality: 'video' })) return null;
  if ((await chrome.storage.local.get('vg_app_handoff').catch(() => ({}))).vg_app_handoff === false) return null;
  const app = await vgFindApp().catch(() => ({ port: null }));
  if (!app.port) return null;
  const r = await vgSendToApp({ url: payload.url, title: payload.title || '', pageUrl: payload.url }).catch(() => ({ ok: false }));
  if (!r.ok) return null;
  return { data: { success: true, title: payload.title || t('sent_to_app_title'), sent_to_app: true }, via: 'app' };
}

async function vgSaveVideo(payload, signal, pageTabId) {
  if (payload && payload.url) {
    payload = { ...payload, url: vgNormalizeSocialUrl(payload.url) };
  }
  const toApp = await vgHandToApp(payload);
  if (toApp) return toApp;
  // storage.local vg_direct === false: everything through the server (kill switch, tests)
  const off = (await chrome.storage.local.get('vg_direct').catch(() => ({}))).vg_direct === false;
  if (off || !self.vgDirect.directEligible(payload)) {
    return { ...(await vgFetchAndSave(payload, signal)), via: 'server' };
  }
  const claim = await vgClaim(payload.url);
  if (claim && !claim.allowed) {
    throw Object.assign(new Error(claim.resetTimeVn ? t('quota_claim_reset', claim.message, claim.resetTimeVn) : String(claim.message)), { status: 429 });
  }
  let tabId = null, ownTab = false, media = null, helper = null;
  try {
    if (pageTabId != null) {
      const pageTab = await chrome.tabs.get(pageTabId).catch(() => null);
      if (pageTab && (vgSamePage(pageTab.url || '', payload.url) || vgSamePage(vgNormalizeSocialUrl(pageTab.url || ''), payload.url))) tabId = pageTabId;
    }
    if (tabId == null) { helper = await vgHelperTab(payload.url); tabId = helper.tabId; ownTab = true; }
    media = await vgFindMedia(tabId, payload.url);
  } catch { media = null; }
  if (ownTab && helper) chrome.windows.remove(helper.windowId).catch(() => {});

  if (media && !(signal && signal.aborted)) {
    const name = await vgFileName(media.title, payload.url);
    if (await (vgNeedsReferer(media.url) ? vgSaveViaPage(media.url, name) : vgSaveFile(media.url, name))) {
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

// ── 5.20.0: preview before downloading (popup) ───────────────────────────
// The file the browser would save for the page the person is on, with its
// size when the CDN tells it. Only the person's own tab (no extra window).
async function vgProbe(url, pageTabId) {
  if (!self.vgDirect.directEligible({ url, quality: 'video' })) return { direct: false };
  const pageTab = pageTabId != null ? await chrome.tabs.get(pageTabId).catch(() => null) : null;
  if (!pageTab || !vgSamePage(pageTab.url || '', url)) return { direct: true, media: null, reason: 'not_this_tab' };
  const media = await vgFindMedia(pageTabId);
  if (!media) return { direct: true, media: null };
  // Content-Length is readable across origins (Content-Range usually is not)
  let size = null;
  try {
    const h = await fetch(media.url, { method: 'HEAD' });
    size = h.ok ? Number(h.headers.get('content-length')) || null : null;
  } catch { /* no answer to HEAD */ }
  if (!size) {
    try {
      const r = await fetch(media.url, { headers: { Range: 'bytes=0-0' } });
      size = Number(((r.headers.get('content-range') || '').match(/\/(\d+)$/) || [])[1]) || null;
      r.body && r.body.cancel && r.body.cancel();
    } catch { /* the CDN does not answer the extension: size unknown */ }
  }
  return { direct: true, media: { ...media, size } };
}

// ── 5.20.0: recording mode — the last resort when nothing can be downloaded ──
// (blob: players, a site with no file). The tab's picture and sound are
// recorded in the offscreen page (MediaRecorder, WebM) while it plays, then
// saved with chrome.downloads. DRM players give a black picture: that is the
// site's protection and it is not worked around. wording: BA review
const VG_REC_KEY = 'vg_rec';
async function vgRecStart(tabId) {
  const cur = (await chrome.storage.session.get(VG_REC_KEY))[VG_REC_KEY];
  if (cur && cur.state === 'recording') return { ok: false, error: t('rec_other_tab') };
  const tab = await chrome.tabs.get(tabId);
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  const r = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'REC_START', streamId });
  if (!r || !r.ok) return { ok: false, error: (r && r.error) || t('rec_start_fail') };
  await chrome.storage.session.set({ [VG_REC_KEY]: { state: 'recording', tabId, title: tab.title || 'video', url: tab.url || '', startedAt: Date.now() } });
  chrome.action.setBadgeText({ text: 'REC' }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color: '#dc2626' }).catch(() => {});
  return { ok: true };
}
async function vgRecStop() {
  const cur = (await chrome.storage.session.get(VG_REC_KEY))[VG_REC_KEY];
  if (!cur || cur.state !== 'recording') return { ok: false };
  await chrome.storage.session.set({ [VG_REC_KEY]: { ...cur, state: 'saving' } });
  const r = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'REC_STOP' }).catch(() => null);
  chrome.action.setBadgeText({ text: '' }).catch(() => {});
  if (!r || !r.url) { await chrome.storage.session.remove(VG_REC_KEY); return { ok: false, error: t('rec_lost') }; }
  const name = await vgFileName(`${cur.title} (quay)`, cur.url);
  return new Promise((resolve) => chrome.downloads.download({ url: r.url, filename: `VidGrab/${name}.webm`, saveAs: false, conflictAction: 'uniquify' }, (id) => {
    chrome.storage.session.remove(VG_REC_KEY);
    setTimeout(() => chrome.runtime.sendMessage({ target: 'offscreen', type: 'REC_RELEASE' }).catch(() => {}).finally(() => closeOffscreenIfIdle()), 60_000);
    resolve({ ok: !!id && !chrome.runtime.lastError, seconds: Math.round((Date.now() - cur.startedAt) / 1000), bytes: r.bytes });
  }));
}
// the recorded tab closed: stop and save what was recorded
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const cur = (await chrome.storage.session.get(VG_REC_KEY).catch(() => ({})))[VG_REC_KEY];
  if (cur && cur.state === 'recording' && cur.tabId === tabId) vgRecStop();
});

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

// The offscreen page cannot use chrome.i18n: it sends a reason code, the text is made here.
const VG_HLS_REASONS = ['not_hls', 'encrypted', 'live', 'empty', 'too_long', 'separate_audio', 'remux_failed', 'cancelled'];
function vgHlsMessage(msg) {
  if (msg.reason === 'drm') return t('sniff_drm_friendly', msg.drmSystem || 'DRM');
  if (VG_HLS_REASONS.includes(msg.reason)) return t('hls_' + msg.reason);
  if (msg.reason === 'error') return t('hls_error', msg.message || '');
  return msg.message || '';
}

async function vgBulkFinish() {
  const b = _vgBulk;
  if (!b || b.finishedAt) return;
  b.finishedAt = Date.now();
  await vgBulkSave();
  const c = vgBulkCounts(b);
  chrome.notifications.create(`vg-bulk-${b.finishedAt}`, {
    type: 'basic', iconUrl: 'assets/icon128.png',
    title: b.stopped ? t('bn_title_stopped', c.done, c.total) : t('bn_title_done', c.done, c.total),
    message: [c.error && tn('bn_err', c.error), c.skipped && (b.stopReason ? t('bn_skip_reason', c.skipped, b.stopReason) : tn('bn_skip', c.skipped))].filter(Boolean).join(' · ') || t('bn_files_in'),
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
        it.error = err.name === 'AbortError' ? t('err_server_slow') : String(err.message || err).slice(0, 200);
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
      if (typeof msg.live === 'boolean') job.live = msg.live;
      if (typeof msg.elapsedSec === 'number') job.elapsedSec = msg.elapsedSec;
      if (msg.height) job.height = msg.height;
    } else if (msg.type === 'HLS_FAILED') {
      if (msg.reason === 'drm') {
        console.warn('[VidGrab][DRM] Blocked HLS job:', {
          id: msg.id,
          drmSystem: msg.drmSystem || 'DRM',
          reason: msg.encryptionReason || 'DRM protected',
        });
      }
      const resumable = Boolean(msg.resumable && msg.checkpoint);
      Object.assign(job, {
        state: msg.reason === 'cancelled' ? 'cancelled' : 'failed',
        stopping: false,
        resumable,
        checkpoint: resumable ? msg.checkpoint : null,
        ...(resumable && typeof msg.done === 'number' ? { done: msg.done } : {}),
        ...(resumable && typeof msg.total === 'number' ? { total: msg.total } : {}),
        ...(resumable && typeof msg.bytes === 'number' ? { bytes: msg.bytes } : {}),
        drmSystem: msg.drmSystem || null,
        encryptionReason: msg.encryptionReason || null,
        message: vgHlsMessage(msg),
      });
      closeOffscreenIfIdle();
    } else if (msg.type === 'HLS_DONE') {
      Object.assign(job, {
        state: 'saving',
        stopping: false,
        resumable: false,
        checkpoint: null,
        bytes: msg.bytes,
        done: job.total || job.done,
      });
      if (typeof msg.elapsedSec === 'number') job.elapsedSec = msg.elapsedSec;
      const { relBase } = vgStreamPaths(job.title, job.height ? ` (${job.height}p)` : '', job.isTeam !== false);
      const filename = `${relBase}.${msg.ext || 'mp4'}`;
      chrome.downloads.download({ url: msg.url, filename, saveAs: false }, (downloadId) => {
        const finish = (state, message) => {
          Object.assign(job, { state, message: message || '' });
          hlsSave();
          chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_RELEASE', id: job.id })
            .catch(() => {}).finally(() => closeOffscreenIfIdle());
          if (state === 'done') {
            if (Array.isArray(job.subtitles) && job.subtitles.length) {
              void vgSaveCompanionSubtitles(job.subtitles, relBase, job.referer || '');
            }
            chrome.notifications.create(`vg-hls-${job.id}`, {
              type: 'basic', iconUrl: 'assets/icon128.png', title: t('hls_done_title'),
              message: t('hls_done_msg', job.title.slice(0, 80)),
            });
          }
        };
        if (chrome.runtime.lastError || !downloadId) { finish('failed', t('hls_save_failed')); return; }
        job.downloadId = downloadId;
        const onChanged = (delta) => {
          if (delta.id !== downloadId || !delta.state) return;
          if (delta.state.current === 'complete') { chrome.downloads.onChanged.removeListener(onChanged); finish('done'); }
          else if (delta.state.current === 'interrupted') { chrome.downloads.onChanged.removeListener(onChanged); finish('failed', t('hls_save_interrupted')); }
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
      const pingTimer = setTimeout(() => ctl.abort(), 700);
      const r = await fetch(`http://127.0.0.1:${port}/vidgrab/ping`, { signal: ctl.signal, cache: 'no-store' });
      clearTimeout(pingTimer);
      const j = r.ok ? await r.json() : null;
      if (j && j.app === 'VidGrab') {
        found = { at: Date.now(), port, version: j.version || null };
        // the app runs: its native host is likely registered now (app 0.20.0+)
        if (!vgNativePort) { vgNativeRetryAt = 0; vgNativeConnect(); }
        // app 0.19.4+: its machine id, so the app and this browser are one device
        if (/^[0-9a-f]{64}$/.test(j.device || '')) chrome.storage.local.set({ [DEVICE_KEY]: j.device }).catch(() => {});
        break;
      }
    } catch { /* not this port */ }
  }
  vgAppCache = found;
  return found;
}

async function vgSendToApp(item, height, audioLang) {
  const referer = item.refererUrl || item.pageUrl || '';
  const ua = (typeof navigator !== 'undefined' && navigator.userAgent) ? String(navigator.userAgent).slice(0, 512) : '';
  const h = Number.isInteger(height) && height >= 144 && height <= 4320 ? height : undefined;
  const cleanAudioLang = typeof audioLang === 'string' && /^[a-z0-9_-]{1,16}$/i.test(audioLang.trim())
    ? audioLang.trim()
    : undefined;
  const cleanTitle = item.title ? await vgRefineStreamTitleAi(item) : '';
  const isStream = Boolean(item.kind);
  const needsCookieJar = isStream || Boolean(_cookieGatedDomainFor(item.url) || _cookieGatedDomainFor(referer));
  const cookieJar = needsCookieJar ? await vgGetStreamCookieJar(item.url, referer).catch(() => undefined) : undefined;
  const subtitles = (isStream && Array.isArray(item.subtitles) && item.subtitles.length)
    ? item.subtitles.slice(0, 6).map((s) => ({
      url: s.url,
      lang: String(s.lang || 'sub').slice(0, 12),
      format: String(s.format || 'vtt').slice(0, 8),
    }))
    : undefined;
  const payload = {
    url: item.url,
    title: cleanTitle,
    referer,
    ...(ua ? { userAgent: ua } : {}),
    ...(h ? { height: h } : {}),
    ...(isStream ? { stream: true } : {}),
    ...(cookieJar ? { cookieJar } : {}),
    ...(subtitles ? { subtitles } : {}),
    ...(cleanAudioLang ? { audioLang: cleanAudioLang } : {}),
  };
  // 5.22.0: over the native link when the app is on it (only the app's own
  // host can be on that end; the HTTP port stays for apps before 0.20.0)
  if (vgNativePort && vgNativeApp.up) {
    try {
      vgNativePort.postMessage({ t: 'download', ...payload });
      return { ok: true };
    } catch { /* port just closed: HTTP below */ }
  }
  const app = await vgFindApp(true);
  if (!app.port) return { ok: false, error: 'no_app' };
  const r = await fetch(`http://127.0.0.1:${app.port}/vidgrab/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const j = await r.json().catch(() => null);
  return r.ok && j && j.ok ? { ok: true } : { ok: false, error: (j && j.error) || `HTTP ${r.status}` };
}

// ── VidGrab app link over native messaging (5.22.0, app 0.20.0) ──
// The app registers "xyz.tinhgon.vidgrab" for this extension's id only
// (manifest "key"); Chrome / Edge start that host when we connect and keep
// it while the port is open (which also keeps this worker alive). Through it
// the app knows this browser has the extension and asks it for things — the
// HTTP port above only lets us ask the app. Messages: desktop ext_link.rs.
const VG_NATIVE_HOST = 'xyz.tinhgon.vidgrab';
const VG_LOCAL_RELAYS_KEY = 'vg_local_relays'; // storage.session: relay token -> app request id
let vgNativePort = null;
let vgNativeApp = { up: false, version: null };
let vgNativeRetryAt = 0;

function vgBrowserBrand() {
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const named = brands.map((b) => b.brand).find((b) => !/chromium|not.?a.?brand/i.test(b));
  if (named) return named;
  return /\bEdg\//.test(navigator.userAgent) ? 'Microsoft Edge' : 'Chrome';
}

function vgNativeConnect() {
  if (vgNativePort || Date.now() < vgNativeRetryAt) return;
  let port;
  try {
    port = chrome.runtime.connectNative(VG_NATIVE_HOST);
  } catch {
    vgNativeRetryAt = Date.now() + 10 * 60_000;
    return;
  }
  vgNativePort = port;
  port.onMessage.addListener((m) => { vgNativeOnMessage(port, m); });
  port.onDisconnect.addListener(() => {
    const why = (chrome.runtime.lastError && chrome.runtime.lastError.message) || '';
    if (vgNativePort === port) vgNativePort = null;
    vgNativeApp = { up: false, version: null };
    // no app installed (host not registered) → much later; the host ended → soon
    vgNativeRetryAt = Date.now() + (/not found|forbidden/i.test(why) ? 10 * 60_000 : 30_000);
  });
  port.postMessage({ t: 'hello', ext: chrome.runtime.getManifest().version, brand: vgBrowserBrand() });
}

function vgNativeOnMessage(port, m) {
  if (!m || typeof m !== 'object') return;
  if (m.t === 'app') {
    vgNativeApp = { up: !!m.up, version: typeof m.version === 'string' ? m.version.slice(0, 20) : null };
    return;
  }
  if (m.t === 'req' && Number.isSafeInteger(m.id)) {
    vgNativeRequest(m).then(
      (data) => { try { port.postMessage({ t: 'res', id: m.id, ok: true, data }); } catch { /* closed */ } },
      (e) => { try { port.postMessage({ t: 'res', id: m.id, ok: false, error: String((e && e.message) || e).slice(0, 200) }); } catch { /* closed */ } },
    );
  }
}

// Only the channel pages the relay knows (the app checks them too: ext_link.rs clean_profile).
function vgRelayChannelUrl(raw) {
  let u;
  try { u = new URL(String(raw || '')); } catch { return null; }
  if (u.protocol !== 'https:' || u.hash || u.username || u.password || u.port) return null;
  if (u.hostname === 'www.douyin.com' && /^\/user\/MS4w[A-Za-z0-9_-]{10,120}$/.test(u.pathname)) return { url: u.href, platform: 'douyin' };
  if (u.hostname === 'www.facebook.com') return { url: u.href, platform: 'facebook' };
  return null;
}

// Signed in to the platform in this browser? Only whether the session cookie exists — never its value.
async function vgSignedIn(platform) {
  const c = platform === 'facebook'
    ? { url: 'https://www.facebook.com/', name: 'c_user' }
    : { url: 'https://www.douyin.com/', name: 'sessionid' };
  try { return !!(await chrome.cookies.get(c)); } catch { return null; }
}

function vgRelayToken() {
  const b = crypto.getRandomValues(new Uint8Array(24));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_'); // 32 chars, like the server's
}

async function vgLocalRelays() {
  try { return (await chrome.storage.session.get(VG_LOCAL_RELAYS_KEY))[VG_LOCAL_RELAYS_KEY] || {}; } catch { return {}; }
}

async function vgSetLocalRelay(token, entry) {
  const all = await vgLocalRelays();
  const now = Date.now();
  for (const [k, v] of Object.entries(all)) if (!v || now - v.at > 30 * 60_000) delete all[k];
  if (entry) all[token] = entry; else delete all[token];
  try { await chrome.storage.session.set({ [VG_LOCAL_RELAYS_KEY]: all }); } catch { /* none */ }
}

async function vgRefreshStream(m) {
  const rawUrl = String((m && m.url) || '').trim();
  if (!/^https?:\/\//i.test(rawUrl)) return { found: false };
  const rawRef = String((m && m.referer) || '').trim();
  const rawTitle = String((m && m.title) || '').trim();
  const cleanTargetTitle = rawTitle ? vgCleanStreamTitle({ title: rawTitle, pageUrl: rawRef }) : '';
  const wantCanon = vgCanonicalManifestIdentity(rawUrl);

  let refOrigin = '';
  try { if (rawRef) refOrigin = new URL(rawRef).origin.toLowerCase(); } catch { /* ignore */ }

  const queried = await chrome.tabs.query({}).catch(() => []);
  const tabs = Array.isArray(queried) ? queried : [];
  const sortedTabs = [...tabs].sort((a, b) => {
    const aMatch = (rawRef && a.url && vgSamePage(a.url, rawRef)) ? 2 : (refOrigin && a.url && a.url.toLowerCase().startsWith(refOrigin) ? 1 : 0);
    const bMatch = (rawRef && b.url && vgSamePage(b.url, rawRef)) ? 2 : (refOrigin && b.url && b.url.toLowerCase().startsWith(refOrigin) ? 1 : 0);
    return bMatch - aMatch;
  });

  for (const tab of sortedTabs) {
    if (!tab || typeof tab.id !== 'number' || tab.id < 0) continue;
    let tabOrigin = '';
    try { if (tab.url) tabOrigin = new URL(tab.url).origin.toLowerCase(); } catch { /* ignore */ }
    const sameRefTab = Boolean(rawRef && tab.url && (vgSamePage(tab.url, rawRef) || (refOrigin && tabOrigin === refOrigin)));
    if (sameRefTab && typeof self.vgSniffProbe === 'function') {
      await self.vgSniffProbe(tab.id).catch(() => null);
    }
    const list = typeof self.vgSniffList === 'function' ? await self.vgSniffList(tab.id).catch(() => []) : [];
    const candidates = (Array.isArray(list) ? list : [])
      .filter((x) => x && !x.hidden && !x.error && x.encryption !== 'drm' && !x.live && /^https?:\/\//i.test(x.url || ''))
      .sort((a, b) => (b.at || 0) - (a.at || 0));
    const byCanon = candidates.filter((x) => vgCanonicalManifestIdentity(x.url) === wantCanon);
    const freshCanon = byCanon.find((x) => x.url !== rawUrl) || byCanon[0];
    const byRef = rawRef
      ? candidates.filter((x) => (x.refererUrl === rawRef || x.pageUrl === rawRef)
        && (!cleanTargetTitle || vgCleanStreamTitle(x) === cleanTargetTitle))
      : [];
    const fresh = freshCanon || byRef.find((x) => x.url !== rawUrl) || byRef[0];
    if (fresh && fresh.url) {
      const nextRef = fresh.refererUrl || fresh.pageUrl || rawRef || '';
      const cookieJar = await vgGetStreamCookieJar(fresh.url, nextRef).catch(() => undefined);
      return {
        found: true,
        url: fresh.url,
        ...(nextRef ? { referer: nextRef } : {}),
        ...(cookieJar ? { cookieJar } : {}),
      };
    }
  }
  return { found: false };
}

async function vgNativeRequest(m) {
  if (m.op === 'open_relay') {
    const ch = vgRelayChannelUrl(m.url);
    if (!ch) throw new Error('unsupported_url');
    const token = vgRelayToken();
    await vgSetLocalRelay(token, { id: m.id, at: Date.now() });
    const signedIn = await vgSignedIn(ch.platform);
    const tab = await chrome.tabs.create({ url: `${ch.url}#vg-relay=${token}`, active: true });
    if (tab && tab.windowId != null) chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
    return { signedIn };
  }
  if (m.op === 'refresh_stream') {
    return vgRefreshStream(m);
  }
  throw new Error('unknown_op');
}

// A relay page's progress (content.js vgRelaySend): to the app when the app
// opened it over the link, else to the server's /channel-relay like before.
async function vgRelayPut(token, body) {
  if (!/^[A-Za-z0-9_-]{32}$/.test(token || '')) return { ok: false, error: 'bad token' };
  const local = (await vgLocalRelays())[token];
  if (local) {
    if (!vgNativePort) return { ok: false, error: 'app_link_lost' };
    vgNativePort.postMessage({ t: 'evt', id: local.id, data: body });
    if (body && body.done) await vgSetLocalRelay(token, null);
    return { ok: true, status: 200, data: { ok: true, count: Array.isArray(body && body.items) ? body.items.length : 0 } };
  }
  const base = await getApiBase();
  const resp = await fetch(`${base}/api/v1/channel-relay/${token}`, {
    method: 'PUT',
    headers: await authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => null);
  return { ok: resp.ok, status: resp.status, data };
}

vgNativeConnect();

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
    const isSupported = /tiktok\.com|facebook\.com|douyin\.com|instagram\.com|spotify\.com|twitter\.com|x\.com|reddit\.com|pinterest\.com|threads\.(net|com)|bilibili\.com|xiaohongshu\.com|drive\.google\.com|docs\.google\.com|1drv\.ms|onedrive\.live\.com|sharepoint\.com|mega\.(nz|co\.nz)|terabox\.com|1024terabox\.com|teraboxapp\.com|dropbox\.com|mypikpak\.com|t\.me|weibo\.(com|cn)|ixigua\.com/i.test(url)
      || /\/watch\?(?:[^#]*&)?v=[\w-]{6,}|\/shorts\/[\w-]{6,}/i.test(url);

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
      type: 'basic', iconUrl: 'assets/icon128.png',
      title: t('dl_notif_ok'), message: filename, priority: 1,
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
        iconUrl: 'assets/icon128.png',
        title: stopped > 0 ? t('batch_stopped', success, total)
          : failed > 0 ? t('batch_partial', success, total) : tn('batch_done', total),
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
      vgResetUniversalTierCache();
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
        try { await fetch(`${await getApiBase()}/api/v1/extension/session`, { method: 'DELETE', headers: { ...(await vgDeviceHeader()), Authorization: `Bearer ${tok}` } }); } catch { /* offline */ }
      }
      vgResetUniversalTierCache();
      chrome.storage.local.remove([AUTH_TOKEN_KEY, 'vg_auth_email', 'vg_auth_exp', 'vg_cached_tier', 'vg_cached_universal', 'vg_cached_tier_tok'], () => {
        chrome.runtime.sendMessage({ type: 'VG_AUTH_CHANGED', authenticated: false }).catch(() => {});
        sendResponse({ ok: true });
      });
    })();
    return true;
  }
  if (msg.type === 'VG_GET_DEVICE_ID') {
    (async () => {
      if (!(await _mayWriteAuthAsync())) { sendResponse({ id: null }); return; }
      await vgFindApp().catch(() => null);   // adopts the app's machine id when the app runs
      sendResponse({ id: await vgDeviceId() });
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
  if (msg.type === 'VG_SNIFF_PROBE') {
    const runProbe = typeof self.vgSniffProbe === 'function'
      ? self.vgSniffProbe(msg.tabId)
      : self.vgSniffList(msg.tabId).then((items) => ({ items, playing: null }));
    runProbe.then(sendResponse, () => sendResponse({ items: [], playing: null }));
    return true;
  }
  if (msg.type === 'VG_UNIVERSAL_TIER_STATUS') {
    vgCheckUniversalStreamTier(!!msg.force).then(sendResponse, () => sendResponse({ allowed: false, mode: 'none', isTeam: false, authenticated: false, tier: 'guest' }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_CLEAR') {
    self.vgSniffClear(msg.tabId).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_AUTO_EP_GET') {
    vgAutoEpGet(msg.tabId).then((enabled) => sendResponse({ ok: true, enabled }), () => sendResponse({ ok: false, enabled: false }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_AUTO_EP_SET') {
    (async () => {
      if (msg.enabled) {
        const gate = await vgCheckUniversalStreamTier();
        if (!gate.authenticated) { sendResponse({ ok: false, error: gate.error || 'signin_required', tier: gate.tier }); return; }
      }
      const enabled = await vgAutoEpSet(msg.tabId, Boolean(msg.enabled));
      sendResponse({ ok: true, enabled });
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg.type === 'VG_ENSURE_REFERER') {
    if (!_fromExtensionPage) { sendResponse({ ok: false }); return false; }
    vgEnsureStreamReferer(msg.url, msg.referer).then((ok) => sendResponse({ ok }), () => sendResponse({ ok: false }));
    return true;
  }
  if (msg.type === 'VG_SNIFF_DOWNLOAD') {
    (async () => {
      const gate = await vgCheckUniversalStreamTier();
      if (!gate.allowed) { sendResponse({ ok: false, error: gate.error || 'tier_required', tier: gate.tier }); return; }
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item || item.kind !== 'file') { sendResponse({ ok: false, error: 'not_a_file' }); return; }
      const ref = item.refererUrl || item.pageUrl || '';
      if (ref) await vgEnsureStreamReferer(item.url, ref);
      const base = await getApiBase();
      const ext = (item.url.split('?')[0].match(/\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i) || [])[1]
        || (/^audio\//.test(item.mime || '') ? 'm4a' : 'mp4');
      const refinedTitle = await vgRefineStreamTitleAi(item);
      const { safeName, subDir, relBase } = vgStreamPaths(refinedTitle, '', gate.isTeam);
      smartDownload({ rawUrl: item.url, base, safeName, finalExt: ext.toLowerCase(), saveAs: false, subDir });
      if (Array.isArray(item.subtitles) && item.subtitles.length) {
        void vgSaveCompanionSubtitles(item.subtitles, relBase, ref);
      }
      sendResponse({ ok: true });
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }

  if (msg.type === 'VG_HLS_START') {
    (async () => {
      const gate = await vgCheckUniversalStreamTier();
      if (!gate.allowed) { sendResponse({ ok: false, error: gate.error || 'tier_required', tier: gate.tier }); return; }
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item || item.kind !== 'hls') { sendResponse({ ok: false, error: 'not_hls' }); return; }
      if (item.encryption === 'drm') {
        console.warn('[VidGrab][DRM] Blocked VG_HLS_START:', {
          url: item.url,
          drmSystem: item.drmSystem || 'DRM',
          reason: item.encryptionReason || 'DRM protected',
          live: !!item.live,
        });
        sendResponse({ ok: false, error: 'drm_protected', drmSystem: item.drmSystem || 'DRM', reason: item.encryptionReason || '' });
        return;
      }
      if (!gate.isTeam && (item.live || msg.audioTrack || (Number(msg.height) > 1080))) {
        // When Desktop App is connected, allow any authenticated user to access live/4k/audioTrack via App
        const appConnected = Boolean((vgNativePort && vgNativeApp.up) || (await vgFindApp().catch(() => ({ port: null }))).port);
        if (!(gate.authenticated && appConnected)) {
          sendResponse({ ok: false, error: 'team_required', tier: gate.tier });
          return;
        }
      }
      const job = await hlsStart(item, msg.height, {
        tabId: msg.tabId,
        audioTrack: msg.audioTrack || null,
        isTeam: gate.isTeam,
      });
      sendResponse({ ok: true, job });
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === 'VG_HLS_RESUME') {
    (async () => {
      const gate = await vgCheckUniversalStreamTier();
      if (!gate.allowed) { sendResponse({ ok: false, error: gate.error || 'tier_required', tier: gate.tier }); return; }
      sendResponse(await hlsResume(msg.id, gate.isTeam));
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg.type === 'VG_PILL_CLICK') {
    (async () => {
      const tabId = sender && sender.tab ? sender.tab.id : null;
      if (tabId == null) { sendResponse({ ok: false }); return; }
      const gate = await vgCheckUniversalStreamTier();
      if (!gate.allowed && !gate.authenticated) {
        chrome.tabs.create({ url: 'https://dowloadvideo.io.vn/pricing' }).catch(() => {});
        sendResponse({ ok: false, error: gate.error || 'tier_required', statusText: t('pill_locked_toast') });
        return;
      }
      if (typeof self.vgSniffProbe === 'function') {
        await self.vgSniffProbe(tabId).catch(() => null);
      }
      const items = await self.vgSniffList(tabId).catch(() => []);
      const clean = items.filter((x) => x && !x.hidden && !x.pending && !x.error && x.encryption !== 'drm');
      const item = clean.find((x) => x.id === msg.itemId) || clean[clean.length - 1];
      if (!item) { sendResponse({ ok: false, error: 'not_found' }); return; }
      const appConnected = Boolean((vgNativePort && vgNativeApp.up) || (await vgFindApp().catch(() => ({ port: null }))).port);
      if (gate.authenticated && appConnected) {
        const r = await vgSendToApp(item, item.height || undefined);
        if (r && r.ok) {
          sendResponse({ ok: true, via: 'app', statusText: t('pill_sent_app') });
          return;
        }
      }
      if (!gate.isTeam && (item.live || item.kind === 'dash') && !appConnected) {
        chrome.tabs.create({ url: 'https://dowloadvideo.io.vn/pricing' }).catch(() => {});
        sendResponse({ ok: false, error: 'team_required', statusText: t('pill_team_only_toast') });
        return;
      }
      if (item.kind === 'hls') {
        const maxTierH = gate.tier === 'free' ? 720 : 1080;
        const capH = gate.isTeam ? (item.height || null) : Math.min(item.height || maxTierH, maxTierH);
        await hlsStart(item, capH, { tabId, isTeam: gate.isTeam });
        sendResponse({ ok: true, via: 'browser', statusText: t('pill_started_browser') });
        return;
      }
      if (item.kind === 'file') {
        const ref = item.refererUrl || item.pageUrl || '';
        if (ref) await vgEnsureStreamReferer(item.url, ref);
        const base = await getApiBase();
        const ext = (item.url.split('?')[0].match(/\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i) || [])[1]
          || (/^audio\//.test(item.mime || '') ? 'm4a' : 'mp4');
        const refinedTitle = await vgRefineStreamTitleAi(item);
        const { safeName, subDir, relBase } = vgStreamPaths(refinedTitle, '', gate.isTeam);
        smartDownload({ rawUrl: item.url, base, safeName, finalExt: ext.toLowerCase(), saveAs: false, subDir });
        if (Array.isArray(item.subtitles) && item.subtitles.length) {
          void vgSaveCompanionSubtitles(item.subtitles, relBase, ref);
        }
        sendResponse({ ok: true, via: 'file', statusText: t('pill_started_browser') });
        return;
      }
      sendResponse({ ok: false, error: 'unsupported' });
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg.type === 'VG_SOCIAL_PILL_CLICK') {
    (async () => {
      const tabId = sender && sender.tab ? sender.tab.id : null;
      const rawUrl = typeof msg.url === 'string' && /^https?:\/\//i.test(msg.url)
        ? msg.url
        : (sender && sender.tab && sender.tab.url) || '';
      const url = vgNormalizeSocialUrl(rawUrl);
      if (!url) { sendResponse({ ok: false, error: 'invalid_url' }); return; }
      let title = typeof msg.title === 'string' ? msg.title.slice(0, 200) : '';
      if (/^抖音(?:精选)?(?:电脑版)?$/i.test(title.trim())) title = '';
      const pageUrl = (typeof msg.pageUrl === 'string' && /^https?:\/\//i.test(msg.pageUrl)) ? vgNormalizeSocialUrl(msg.pageUrl) : url;
      const dy = msg.douyinDirect;
      const gd = msg.gdriveDirect;
      let host = '';
      try { host = new URL(url).hostname; } catch {}
      const isGDrive = /(^|\.)(drive|docs)\.google\.com$/i.test(host);

      // 1. Hand directly to running VidGrab Desktop App if available & enabled
      const handoffOff = (await chrome.storage.local.get('vg_app_handoff').catch(() => ({}))).vg_app_handoff === false;
      if (!handoffOff) {
        const appConnected = Boolean((vgNativePort && vgNativeApp.up) || (await vgFindApp().catch(() => ({ port: null }))).port);
        if (appConnected) {
          let appPayload = null;
          if (dy && typeof dy.url === 'string' && /^https:\/\//i.test(dy.url)) {
            appPayload = { url: dy.url, title: dy.title || title || 'Douyin Video', pageUrl: 'https://www.douyin.com/', refererUrl: 'https://www.douyin.com/', kind: 'file' };
          } else if (isGDrive) {
            appPayload = (gd && typeof gd.url === 'string' && /^https:\/\//i.test(gd.url))
              ? { url: gd.url, title: gd.title || title || 'Google Drive Video', pageUrl: url, refererUrl: 'https://drive.google.com/', kind: 'file' }
              : { url, title: title || 'Google Drive Video', pageUrl: url, refererUrl: 'https://drive.google.com/' };
          } else {
            appPayload = { url, title: title || 'Video', pageUrl: url };
          }
          const r = await vgSendToApp(appPayload).catch(() => ({ ok: false }));
          if (r && r.ok) {
            sendResponse({ ok: true, via: 'app', statusText: t('pill_sent_app') });
            return;
          }
        }
      }

      // 2. Direct stream from the user's browser session if provided (Douyin or Google Drive)
      const directStream = (dy && typeof dy.url === 'string' && /^https:\/\//i.test(dy.url))
        ? { url: dy.url, title: dy.title || title || 'douyin' }
        : (gd && typeof gd.url === 'string' && /^https:\/\//i.test(gd.url))
          ? { url: gd.url, title: gd.title || title || 'gdrive' }
          : null;

      if (directStream) {
        const safeName = await vgFileName(directStream.title, url);
        sendResponse({ ok: true, via: 'browser', statusText: t('pill_started_browser') });
        const ok = await vgSaveFile(directStream.url, safeName);
        if (ok) {
          addToHistory({
            title: directStream.title || title || 'Video', thumbnail: '', url,
            quality: 'HD', fileSize: '', noWatermark: true,
            hasSubtitle: false, subtitleLanguages: [], subtitleSource: 'none',
          });
          return;
        }
      } else {
        sendResponse({ ok: true, via: 'browser', statusText: t('pill_started_browser') });
      }

      // 3. Full download pipeline in background (vgSaveVideo: browser direct first, server fetch-link fallback)
      try {
        const controller = new AbortController();
        const abortTimer = setTimeout(() => controller.abort(), 150_000);
        const { data } = await vgSaveVideo(
          { url, title, quality: 'video', remove_watermark: true, download_subs: false },
          controller.signal,
          tabId,
        ).finally(() => clearTimeout(abortTimer));
        chrome.notifications.create(`vg-pill-${Date.now()}`, {
          type: 'basic', iconUrl: 'assets/icon128.png',
          title: data && data.sent_to_app ? t('sent_to_app') : t('bg_notif_ok', (data && data.title) || title || 'Video'),
          message: t('bg_notif_msg', new URL(url).hostname),
          priority: 1,
        });
      } catch (err) {
        if (tabId != null) {
          chrome.tabs.sendMessage(tabId, { type: 'VG_SHOW_TOAST', text: t('toast_err', err.message || String(err)) }).catch(() => {});
        }
      }
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg.type === 'VG_HLS_KEEP_FILES') {
    if (!_fromExtensionPage) { sendResponse({ ok: false, keep: [] }); return false; }
    hlsLoad().then(() => {
      const keep = [...hlsJobs.values()]
        .filter((j) => j.state === 'running' || j.state === 'saving' || (j.state === 'failed' && j.resumable))
        .map((j) => `hls-${j.id}.mp4`);
      sendResponse({ ok: true, keep });
    }, () => sendResponse({ ok: false, keep: [] }));
    return true;
  }
  if (msg.type === 'VG_APP_STATUS') {
    vgFindApp(!!msg.force).then((a) => sendResponse({ found: !!a.port, version: a.version, linked: !!(vgNativePort && vgNativeApp.up) }));
    return true;
  }
  // 5.22.0: a channel relay page's progress (content.js) — to the app over the link, or to the server
  if (msg.type === 'VG_RELAY_PUT') {
    vgRelayPut(msg.token, msg.body).then(sendResponse, (e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg.type === 'VG_APP_SEND') {
    (async () => {
      const gate = await vgCheckUniversalStreamTier();
      if (!gate.authenticated) { sendResponse({ ok: false, error: gate.error || 'signin_required', tier: gate.tier }); return; }
      const items = await self.vgSniffList(msg.tabId);
      const item = items.find((x) => x.id === msg.id);
      if (!item) { sendResponse({ ok: false, error: 'not_found' }); return; }
      if (item.encryption === 'drm') {
        console.warn('[VidGrab][DRM] Blocked VG_APP_SEND:', {
          url: item.url,
          drmSystem: item.drmSystem || 'DRM',
          reason: item.encryptionReason || 'DRM protected',
          live: !!item.live,
        });
        sendResponse({ ok: false, error: 'drm_protected', drmSystem: item.drmSystem || 'DRM' });
        return;
      }
      sendResponse(await vgSendToApp(item, msg.height, msg.audioLang));
    })().catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === 'VG_HLS_JOBS') {
    hlsLoad().then(() => sendResponse({ jobs: [...hlsJobs.values()].sort((a, b) => b.at - a.at) }));
    return true;
  }
  if (msg.type === 'VG_HLS_STOP') {
    hlsLoad().then(() => {
      const j = hlsJobs.get(msg.id);
      if (j && j.state === 'running') {
        j.stopping = true;
        hlsSave();
      }
      chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_STOP', id: msg.id }).catch(() => {});
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg.type === 'VG_HLS_CANCEL') {
    (async () => {
      await hlsLoad();
      const j = hlsJobs.get(msg.id);
      if (j && j.state === 'failed' && j.resumable) {
        Object.assign(j, { state: 'cancelled', resumable: false, checkpoint: null });
        hlsSave();
        await ensureOffscreen().catch(() => {});
        await chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_RELEASE', id: msg.id }).catch(() => {});
        closeOffscreenIfIdle();
      } else {
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'HLS_CANCEL', id: msg.id }).catch(() => {});
      }
      sendResponse({ ok: true });
    })().catch(() => sendResponse({ ok: true }));
    return true;
  }

  // ── 5.17.0: bulk download to this computer (popup channel list) ──
  if (msg.type === 'VG_BULK_START') {
    (async () => {
      const cur = await vgBulkLoad();
      if (cur && !cur.finishedAt && cur.items.some((x) => x.status === 'running' || x.status === 'queued')) {
        sendResponse({ ok: false, error: t('bulk_busy') }); // wording: BA review
        return;
      }
      const seen = new Set();
      const items = (Array.isArray(msg.items) ? msg.items : [])
        .filter((x) => x && typeof x.url === 'string' && /^https:\/\//.test(x.url) && !seen.has(x.url) && seen.add(x.url))
        .slice(0, 500)
        .map((x) => ({ url: x.url, title: typeof x.title === 'string' ? x.title.slice(0, 200) : '', status: 'queued' }));
      const skipped = (Array.isArray(msg.later) ? msg.later : []).slice(0, 500)
        .map((x) => ({ url: x.url, title: typeof x.title === 'string' ? x.title.slice(0, 200) : '', status: 'skipped' }));
      if (!items.length) { sendResponse({ ok: false, error: t('bulk_none_selected') }); return; }
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
        b.stopReason = b.stopReason || t('bulk_reason_user_stop');
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

  // ── 5.20.0: preview + recording (popup) ──
  if (msg.type === 'VG_PROBE') {
    vgProbe(msg.url, msg.pageTabId).then(sendResponse, () => sendResponse({ direct: false }));
    return true;
  }
  if (msg.type === 'VG_REC_START') {
    vgRecStart(msg.tabId).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === 'VG_REC_STOP') {
    vgRecStop().then(sendResponse, (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
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

      let notifTitle = t('bg_notif_processing');
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
        notifTitle = data.sent_to_app ? t('sent_to_app') : t('bg_notif_ok', data.title || 'Video');

        // Notify popup (may be closed — fails silently)
        chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_DONE', data, dlUrl, ext: finalExt, safeName }).catch(() => {});

        // Update pending store with success
        await chrome.storage.local.set({ [pendingKey]: { url, quality, status: 'success', title: data.title, thumbnail: data.thumbnail_url, dlUrl, ext: finalExt, safeName, doneAt: Date.now() } });
      } catch (err) {
        notifTitle = t('bg_notif_err');
        chrome.runtime.sendMessage({ type: 'VG_DOWNLOAD_DONE', error: err.message }).catch(() => {});
        await chrome.storage.local.set({ [pendingKey]: { url, quality, status: 'error', error: err.message, doneAt: Date.now() } });
      }

      // Show Chrome notification
      chrome.notifications.create(`vg-bg-${Date.now()}`, {
        type: 'basic', iconUrl: 'assets/icon128.png',
        title: notifTitle,
        message: t('bg_notif_msg', new URL(url).hostname),
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
            ? t('err_120')
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
chrome.tabs.onRemoved.addListener((tabId) => {
  clearStore(tabId);
  void vgAutoEpSet(tabId, false);
  clearTimeout(_vgPillTimers.get(tabId));
  _vgPillTimers.delete(tabId);
});

// ── Right-click Context Menu ─────────────────────────────────────
chrome.runtime.onInstalled.addListener((details) => {
  chrome.contextMenus.create({ id: 'vg-download-video', title: t('ctx_video'), contexts: ['page', 'link', 'video', 'audio'] });
  chrome.contextMenus.create({ id: 'vg-download-mp3', title: t('ctx_mp3'), contexts: ['page', 'link', 'video', 'audio'] });
  // E2: download media found on the current page using the user's own session/IP
  chrome.contextMenus.create({ id: 'vg-download-page-media', title: t('ctx_page_media'), contexts: ['page', 'video', 'audio'] });

  // 5.22.1 (owner 27/10): signed in on the site, the freshly loaded extension still said "Chưa đăng
  // nhập" — Chrome only runs content scripts in pages opened AFTER the install, so the site tab that
  // was already open never got web-bridge.js and never handed over its session. Put it there now.
  vgBridgeOpenSiteTabs();

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
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_scanning') }).catch(() => {});
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
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_page_media_dl') }).catch(() => {});
      return;
    }
    // Auto-fallback: no direct media on page → download via the server (VidGrab),
    // same as "Tải với VidGrab". One action that always works.
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_fallback') }).catch(() => {});
    // fall through to the server download flow below (isMP3 stays false → video)
  }

  const targetUrl = info.linkUrl || tab.url;
  const isMP3 = info.menuItemId === 'vg-download-mp3';
  chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_processing') }).catch(() => {});

  // 5.18.4: a video goes the same way as the popup's button — the browser first
  // (the page the person is on), the server when the browser cannot
  if (!isMP3) {
    try {
      const { data, via } = await vgSaveVideo({ url: targetUrl, quality: 'video', remove_watermark: true, download_subs: false }, null, info.linkUrl ? null : tab.id);
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: via === 'app' ? t('sent_to_app') : via && via.startsWith('direct') ? t('toast_dl_direct', (data && data.title) || 'video') : t('toast_dl', (data && data.title) || 'video') }).catch(() => {});
    } catch (err) {
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_err', err.name === 'AbortError' ? t('err_too_long') : err.message || t('err_connection')) }).catch(() => {});
    }
    return;
  }

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
        chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_err', t('err_no_dl_link')) }).catch(() => {});
        return;
      }
      // E1: prefer direct (user IP), fall back to server proxy on failure
      smartDownload({ rawUrl: targetDlUrl, base, safeName, finalExt: ext, saveAs: true });
      addToHistory({ title: data.title || 'Video', thumbnail: data.thumbnail_url || '', url: targetUrl, quality: isMP3 ? 'MP3' : 'HD', fileSize: data.file_size_mb ? `${data.file_size_mb} MB` : '' });
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_downloading') }).catch(() => {});
    } else {
      chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_err', resp.ok ? (data.detail || t('err_generic')) : vgServerErrorText(data, resp.status)) }).catch(() => {});
    }
  } catch (err) {
    const msg = err.name === 'AbortError' ? t('err_too_long') : t('err_connection');
    chrome.tabs.sendMessage(tab.id, { type: 'VG_SHOW_TOAST', text: t('toast_err', msg) }).catch(() => {});
  }
});

// The VidGrab site tabs already open when the extension is installed or updated (see onInstalled).
function vgBridgeOpenSiteTabs() {
  const cs = (chrome.runtime.getManifest().content_scripts || []).find((c) => (c.js || []).includes('web-bridge.js'));
  if (!cs) return;
  chrome.tabs.query({ url: cs.matches }, (tabs) => {
    if (chrome.runtime.lastError) return;
    for (const tab of tabs || []) {
      chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['web-bridge.js'] }).catch(() => {}); // no host access (localhost) or tab gone
    }
  });
}
