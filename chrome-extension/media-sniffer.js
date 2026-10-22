/**
 * VidGrab media sniffer (5.12.0, 22/10/2026)
 * ==========================================
 * Lists the media a tab actually plays — direct files (mp4/webm/m4a…), HLS
 * (.m3u8) and MPEG-DASH (.mpd) — by watching the tab's network responses
 * (chrome.webRequest, observe only: nothing is blocked or changed).
 *
 * It runs on the hosts in host_permissions plus any site the user allows
 * with "Bắt video trên trang này" (optional_host_permissions). Nothing leaves
 * the browser: the list lives in chrome.storage.session and dies with the tab.
 *
 * Built from the public HLS (RFC 8216) and DASH (ISO/IEC 23009-1) specs.
 * DRM / encrypted streams are flagged and never downloaded; YouTube is never
 * listed (Chrome Web Store policy — YouTube stays on the VidGrab web and app).
 *
 * The parsing half is pure (no chrome.*) and is loaded by tests/test_media_sniffer.js.
 */
(function (root) {
  'use strict';

  const MAX_ITEMS = 60;
  const MIN_FILE_BYTES = 300 * 1024;      // smaller "videos" are previews, ads, UI loops
  const MAX_MANIFEST_BYTES = 2 * 1024 * 1024;

  // Never listed: YouTube (store policy), and the media pieces of a stream the
  // manifest already stands for.
  const BLOCKED_HOSTS = /(^|\.)(youtube\.com|youtu\.be|googlevideo\.com|ytimg\.com|youtube-nocookie\.com)$/i;
  const SEGMENT_EXT = /\.(ts|m4s|m4f|cmfv|cmfa|aac|vtt|webvtt|key|init)$/i;
  const FILE_EXT = /\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i;
  const RANGE_PARAMS = ['bytestart', 'byteend', 'range', 'rn', 'rbuf'];

  function pathOf(url) {
    try { return new URL(url).pathname.toLowerCase(); } catch { return ''; }
  }
  function hostOf(url) {
    try { return new URL(url).hostname.toLowerCase(); } catch { return ''; }
  }

  /** The file a ranged media request belongs to (Facebook's bytestart/byteend…). */
  function canonicalUrl(url) {
    try {
      const u = new URL(url);
      RANGE_PARAMS.forEach((p) => u.searchParams.delete(p));
      u.hash = '';
      return u.toString();
    } catch { return url; }
  }

  function header(headers, name) {
    const h = (headers || []).find((x) => x && x.name && x.name.toLowerCase() === name);
    return h ? String(h.value || '') : '';
  }

  /** Full size of the resource from content-range ("bytes 0-99/12345") or content-length. */
  function totalSize(headers, statusCode) {
    const cr = header(headers, 'content-range');
    const m = cr.match(/\/(\d+)\s*$/);
    if (m) return Number(m[1]);
    const len = Number(header(headers, 'content-length'));
    if (statusCode === 206) return null;  // a piece without its total
    return Number.isFinite(len) && len > 0 ? len : null;
  }

  /**
   * {kind: 'hls'|'dash'|'file', mime, size} or null when the response is not
   * a listable media resource.
   */
  function classify(details) {
    const url = details && details.url;
    if (!url || !/^https?:/i.test(url)) return null;
    if (details.tabId === undefined || details.tabId < 0) return null;
    if (details.statusCode && (details.statusCode < 200 || details.statusCode >= 300)) return null;
    if (BLOCKED_HOSTS.test(hostOf(url))) return null;
    if (details.initiator && /^chrome-extension:/i.test(details.initiator)) return null;

    const path = pathOf(url);
    const mime = header(details.responseHeaders, 'content-type').split(';')[0].trim().toLowerCase();

    if (path.endsWith('.m3u8') || /mpegurl/.test(mime)) return { kind: 'hls', mime, size: null };
    if (path.endsWith('.mpd') || mime === 'application/dash+xml') return { kind: 'dash', mime, size: null };
    if (SEGMENT_EXT.test(path) || mime === 'video/mp2t' || mime === 'video/iso.segment') return null;

    const isMedia = /^(video|audio)\//.test(mime) || (FILE_EXT.test(path) && (!mime || mime === 'application/octet-stream'));
    if (!isMedia) return null;
    const size = totalSize(details.responseHeaders, details.statusCode);
    if (size !== null && size < MIN_FILE_BYTES) return null;
    return { kind: 'file', mime, size };
  }

  // ── HLS (RFC 8216) ──────────────────────────────────────────────────────
  function attrs(line) {
    const out = {};
    const body = line.slice(line.indexOf(':') + 1);
    const re = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/g;
    let m;
    while ((m = re.exec(body))) out[m[1]] = m[2].replace(/^"|"$/g, '');
    return out;
  }

  function resolve(base, ref) {
    try { return new URL(ref, base).toString(); } catch { return ref; }
  }

  /** Encryption of a key line: 'none' | 'aes128' | 'drm'. */
  function keyKind(a) {
    const method = (a.METHOD || 'NONE').toUpperCase();
    const fmt = (a.KEYFORMAT || 'identity').toLowerCase();
    if (method === 'NONE') return 'none';
    if (method.startsWith('SAMPLE-AES') || fmt !== 'identity') return 'drm';
    return 'aes128';
  }

  function worse(a, b) {
    const rank = { none: 0, aes128: 1, drm: 2 };
    return rank[b] > rank[a] ? b : a;
  }

  function parseM3U8(text, baseUrl) {
    const lines = String(text || '').split(/\r?\n/).map((l) => l.trim());
    if (!lines.length || !lines[0].startsWith('#EXTM3U')) return { valid: false };
    const out = { valid: true, master: false, variants: [], renditions: [], encryption: 'none',
                  duration: 0, segments: 0, live: true };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.startsWith('#EXT-X-STREAM-INF')) {
        out.master = true;
        const a = attrs(line);
        let j = i + 1;
        while (j < lines.length && (!lines[j] || lines[j].startsWith('#'))) j++;
        if (j < lines.length) {
          const [w, h] = (a.RESOLUTION || '').split('x').map(Number);
          out.variants.push({ url: resolve(baseUrl, lines[j]), width: w || null, height: h || null,
                              bandwidth: Number(a.BANDWIDTH) || null, codecs: a.CODECS || '' });
        }
      } else if (line.startsWith('#EXT-X-MEDIA')) {
        const a = attrs(line);
        if (a.URI) out.renditions.push({ url: resolve(baseUrl, a.URI), type: a.TYPE || '', name: a.NAME || '',
                                         language: a.LANGUAGE || '' });
      } else if (line.startsWith('#EXT-X-KEY') || line.startsWith('#EXT-X-SESSION-KEY')) {
        out.encryption = worse(out.encryption, keyKind(attrs(line)));
      } else if (line.startsWith('#EXTINF')) {
        out.segments += 1;
        out.duration += parseFloat(line.slice(8)) || 0;
      } else if (line.startsWith('#EXT-X-ENDLIST')) {
        out.live = false;
      }
    }
    if (out.master) out.live = false;   // decided by the media playlists
    out.variants.sort((a, b) => (b.height || 0) - (a.height || 0) || (b.bandwidth || 0) - (a.bandwidth || 0));
    out.duration = Math.round(out.duration);
    return out;
  }

  // ── DASH (ISO/IEC 23009-1) — regex, no DOMParser in a service worker ────
  function isoDuration(s) {
    const m = /^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$/.exec(s || '');
    if (!m) return 0;
    return Math.round((+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0));
  }

  function parseMPD(text) {
    const t = String(text || '');
    if (!/<MPD[\s>]/.test(t)) return { valid: false };
    const out = { valid: true, drm: /<(?:\w+:)?ContentProtection[\s>]/.test(t),
                  live: /<MPD[^>]*\btype="dynamic"/.test(t),
                  duration: isoDuration((/mediaPresentationDuration="([^"]+)"/.exec(t) || [])[1]),
                  videos: [], audios: 0 };
    const sets = t.split(/<(?:\w+:)?AdaptationSet\b/).slice(1);
    for (const set of sets) {
      const head = set.slice(0, set.indexOf('>') + 1);
      const isAudio = /contentType="audio"|mimeType="audio\//.test(head) || /mimeType="audio\//.test(set);
      const reps = set.split(/<(?:\w+:)?Representation\b/).slice(1);
      for (const rep of reps) {
        const tag = rep.slice(0, rep.indexOf('>') + 1);
        if (isAudio || /mimeType="audio\//.test(tag)) { out.audios += 1; continue; }
        const h = Number((/\bheight="(\d+)"/.exec(tag) || /\bheight="(\d+)"/.exec(head) || [])[1]) || null;
        const bw = Number((/\bbandwidth="(\d+)"/.exec(tag) || [])[1]) || null;
        if (h || bw) out.videos.push({ height: h, bandwidth: bw });
      }
    }
    out.videos.sort((a, b) => (b.height || 0) - (a.height || 0) || (b.bandwidth || 0) - (a.bandwidth || 0));
    return out;
  }

  function qualityLabel(height) {
    if (!height) return '';
    if (height >= 2160) return '4K';
    if (height >= 1440) return '1440p';
    return `${height}p`;
  }

  const VGSniff = { classify, canonicalUrl, totalSize, parseM3U8, parseMPD, qualityLabel, isoDuration,
                    MAX_ITEMS, MIN_FILE_BYTES, MAX_MANIFEST_BYTES, BLOCKED_HOSTS };
  root.VGSniff = VGSniff;
  if (typeof module !== 'undefined' && module.exports) module.exports = VGSniff;

  // ── service-worker wiring ───────────────────────────────────────────────
  if (typeof chrome === 'undefined' || !chrome.webRequest || !chrome.storage || !chrome.storage.session) return;

  const KEY = (tabId) => `vg_sniff_${tabId}`;
  const lists = new Map();          // tabId -> items (memory copy of storage.session)
  const children = new Map();       // tabId -> Set of manifest URLs a master already stands for
  const saveTimers = new Map();

  // One load per tab, shared: responses arrive together, and two loads racing
  // would each make their own array and the later one would drop the earlier
  // one's items.
  const loading = new Map();
  function load(tabId) {
    if (lists.has(tabId)) return Promise.resolve(lists.get(tabId));
    if (!loading.has(tabId)) {
      loading.set(tabId, chrome.storage.session.get(KEY(tabId))
        .then((r) => r[KEY(tabId)] || [], () => [])
        .then((items) => {
          loading.delete(tabId);
          if (!lists.has(tabId)) lists.set(tabId, items);
          return lists.get(tabId);
        }));
    }
    return loading.get(tabId);
  }

  function save(tabId) {
    clearTimeout(saveTimers.get(tabId));
    saveTimers.set(tabId, setTimeout(() => {
      saveTimers.delete(tabId);
      const items = lists.get(tabId) || [];
      chrome.storage.session.set({ [KEY(tabId)]: items }).catch(() => {});
      root.vgSniffBadge && root.vgSniffBadge(tabId, visible(items).length);
    }, 250));
  }

  function visible(items) {
    return items.filter((x) => !x.hidden);
  }

  async function clearTab(tabId) {
    lists.delete(tabId);
    children.delete(tabId);
    navAt.delete(tabId);
    try { await chrome.storage.session.remove(KEY(tabId)); } catch { /* gone */ }
    root.vgSniffBadge && root.vgSniffBadge(tabId, 0);
  }

  // A real navigation drops what the previous page played — decided by
  // Chrome's own request timestamps rather than by the order events reach
  // the worker, which webRequest does not promise across event types.
  const navAt = new Map();
  async function navigated(tabId, ts) {
    if (ts <= (navAt.get(tabId) || 0)) return;
    navAt.set(tabId, ts);
    const items = await load(tabId);
    const keep = items.filter((x) => x.at >= ts);
    items.length = 0;
    items.push(...keep);
    children.delete(tabId);
    save(tabId);
  }

  async function fetchText(url) {
    const r = await fetch(url, { credentials: 'include', cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const text = await r.text();
    return text.length > MAX_MANIFEST_BYTES ? text.slice(0, MAX_MANIFEST_BYTES) : text;
  }

  function hideChildren(tabId, items) {
    const kids = children.get(tabId);
    if (!kids) return;
    items.forEach((x) => { if (x.kind === 'hls' && kids.has(x.url)) x.hidden = true; });
  }

  async function enrich(tabId, item) {
    try {
      const text = await fetchText(item.url);
      if (item.kind === 'hls') {
        const p = parseM3U8(text, item.url);
        if (!p.valid) { item.hidden = true; return; }
        item.encryption = p.encryption;
        item.live = p.live;
        item.duration = p.duration || null;
        if (p.master) {
          item.master = true;
          item.variants = p.variants.slice(0, 8).map((v) => ({ height: v.height, bandwidth: v.bandwidth }));
          item.height = (p.variants[0] && p.variants[0].height) || null;
          const kids = children.get(tabId) || new Set();
          p.variants.forEach((v) => kids.add(v.url));
          p.renditions.forEach((r) => kids.add(r.url));
          children.set(tabId, kids);
          // the best variant tells the duration and whether its segments are encrypted
          if (p.variants[0] && item.encryption === 'none') {
            try {
              const v = parseM3U8(await fetchText(p.variants[0].url), p.variants[0].url);
              if (v.valid) {
                item.encryption = worse(item.encryption, v.encryption);
                item.duration = v.duration || item.duration;
                item.live = v.live;
              }
            } catch { /* keep what the master said */ }
          }
          hideChildren(tabId, await load(tabId));
        }
      } else if (item.kind === 'dash') {
        const d = parseMPD(text);
        if (!d.valid) { item.hidden = true; return; }
        item.encryption = d.drm ? 'drm' : 'none';
        item.live = d.live;
        item.duration = d.duration || null;
        item.variants = d.videos.slice(0, 8);
        item.height = (d.videos[0] && d.videos[0].height) || null;
        item.audios = d.audios;
      }
    } catch (e) {
      item.error = String(e && e.message || e).slice(0, 80);
    } finally {
      item.pending = false;
      save(tabId);
    }
  }

  async function onResponse(details) {
    const c = classify(details);
    if (!c) return;
    const tabId = details.tabId;
    const ts = details.timeStamp || Date.now();
    if (ts < (navAt.get(tabId) || 0)) return;          // the previous page's media
    const items = await load(tabId);
    const url = c.kind === 'file' ? canonicalUrl(details.url) : details.url;
    if (items.some((x) => x.url === url)) return;
    if (c.kind === 'hls' && children.get(tabId) && children.get(tabId).has(url)) return;
    if (items.length >= MAX_ITEMS) return;
    // pushed before any await, so the same URL arriving twice is listed once
    const item = { id: Math.random().toString(36).slice(2, 10), url, kind: c.kind, mime: c.mime, size: c.size,
                   title: '', pageUrl: '', at: ts, pending: c.kind !== 'file', encryption: 'none' };
    items.push(item);
    try { const t = await chrome.tabs.get(tabId); item.title = t.title || ''; item.pageUrl = t.url || ''; } catch { /* closed */ }
    save(tabId);
    if (item.pending) enrich(tabId, item);
  }

  chrome.webRequest.onResponseStarted.addListener(
    (d) => { onResponse(d).catch(() => {}); },
    { urls: ['<all_urls>'], types: ['media', 'xmlhttprequest', 'other', 'object'] },
    ['responseHeaders'],
  );
  // A real navigation of the tab starts a new list (an in-page route change does not).
  chrome.webRequest.onBeforeRequest.addListener(
    (d) => { if (d.tabId >= 0 && d.frameId === 0) navigated(d.tabId, d.timeStamp || Date.now()).catch(() => {}); },
    { urls: ['<all_urls>'], types: ['main_frame'] },
  );
  chrome.tabs.onRemoved.addListener((tabId) => { clearTab(tabId); });

  root.vgSniffList = async (tabId) => visible(await load(tabId));
  root.vgSniffClear = clearTab;
})(typeof self !== 'undefined' ? self : globalThis);
