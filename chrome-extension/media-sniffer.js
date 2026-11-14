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

  // Never listed: IP-locked playback chunks and the media pieces of a stream the
  // manifest already stands for.
  function isBlockedStream(url) {
    const h = hostOf(url), p = pathOf(url);
    if (p === '/videoplayback' || p.startsWith('/videoplayback/') || p.startsWith('/api/manifest/')) return true;
    if (/^rr\d+---sn-/i.test(h) || /^[a-z]{5}\.be$/i.test(h)) return true;
    if (/^(www\.|m\.)?y[o]ut/i.test(h) || /(^|\.)y[t]img\./i.test(h)) return true;
    return false;
  }
  const SEGMENT_EXT = /\.(ts|m4s|m4f|cmfv|cmfa|aac|vtt|webvtt|srt|ass|ssa|key|init)$/i;
  const SUBTITLE_EXT = /\.(vtt|webvtt|srt|ass|ssa)$/i;
  const SUBTITLE_MIMES = new Set(['text/vtt', 'application/x-subrip', 'text/srt', 'text/x-ssa', 'text/x-ass']);
  const MAX_SUBS_PER_TAB = 12;
  const SEGMENT_PATH_HINT = /(?:^|[/_.-])(seg|frag|chunk|part|piece)[-_]?\d+(?:[-_][va]\d+)*(?:\.[a-z0-9]+)?$/i;
  const FILE_EXT = /\.(mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)$/i;
  const NON_MANIFEST_EXT = /\.(js|mjs|cjs|css|png|jpe?g|gif|webp|avif|bmp|svg|ico|woff2?|ttf|otf|wasm|map|mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav|ts|m4s|m4f|cmfv|cmfa|aac|vtt|webvtt|srt|key|init|pdf|zip)$/i;
  const DISGUISED_HLS_MIMES = new Set(['', 'text/plain', 'application/octet-stream', 'binary/octet-stream', 'audio/mpegurl', 'audio/x-mpegurl', 'application/x-mpegurl']);
  const SOFT_HLS_MIMES = new Set(['text/html', 'application/json']);
  const HLS_URL_HINT = /(?:^|[/?&=_.-])(m3u8|hls\d*|playlist|master|manifest|chunklist|urlset|index[-_]v\d)(?:$|[/?&=_.-])/i;
  const STRONG_HLS_PATH_HINT = /(\.(m3u8?|txt)$|(?:^|[/_.-])(m3u8|playlist|master|chunklist|urlset)(?:$|[/_.-]))/i;
  const RANGE_PARAMS = ['bytestart', 'byteend', 'range', 'rn', 'rbuf'];
  const JUNK_TITLE_RE = /^(?:just a moment\.{0,3}|attention required!?.*|(?:video\s*|jw\s*|html5\s*)?player|embed(?:\s+player)?|loading\.{0,3}|untitled|index|playlist|master|manifest)$/i;
  const FILE_LIKE_TITLE_RE = /^[a-z0-9_-]+\.(?:m3u8?|mpd|mp4|ts|txt|html?|php)$/i;
  const GENERIC_TRAIL_RE = /^(?:watch(?:\s+online)?(?:\s+free)?(?:\s+hd)?|free\s+(?:hd|videos?|movies?|streaming)|full\s+hd(?:\s+online)?|stream(?:ing)?\s+online|xem\s+phim(?:\s+online)?(?:\s+(?:hd|miễn\s+phí|mới(?:\s+nhất)?|nhanh|vietsub))?|phim\s+(?:hd|mới(?:\s+nhất)?|hay|lẻ|bộ)|trang\s+chủ|home)$/i;
  const DOMAIN_TOKEN_RE = /\b[a-z0-9-]{2,}\.(?:com|net|org|io|vn|ws|tv|to|me|cc|co|xyz|pro|vip|club|top|site|online|stream|watch|live|today|one|fun|info|biz|asia|gg|la|sh|bz|su|icu|cyou|click|shop|lol|cfd)\b/i;
  const EPISODE_OR_CODE_RE = /(?:\b(?:tập|tap|ep(?:isode)?)\s*\d+|\b[a-z]{2,6}-\d{2,5}\b)/i;

  function pathOf(url) {
    try { return new URL(url).pathname.toLowerCase(); } catch { return ''; }
  }
  function hostOf(url) {
    try { return new URL(url).hostname.toLowerCase(); } catch { return ''; }
  }
  function httpUrl(raw) {
    if (!raw || typeof raw !== 'string') return '';
    const s = raw.trim();
    if (!/^https?:\/\//i.test(s)) return '';
    try { return new URL(s).toString(); } catch { return ''; }
  }
  function originOf(raw) {
    try { return new URL(raw).origin.toLowerCase(); } catch { return ''; }
  }

  /**
   * Picks the best Referer URL for a sniffed stream:
   *  1. Exact sub_frame URL when the request came from an embedded <iframe>;
   *  2. Cross-origin initiator origin (iframe loaded before service worker woke);
   *  3. Top-level tab pageUrl;
   *  4. Same-origin initiator fallback.
   */
  function pickReferer(ctx) {
    const c = ctx || {};
    if ((c.frameId || 0) > 0) {
      const f = httpUrl(c.frameUrl) || httpUrl(c.documentUrl);
      if (f) return f;
    }
    const init = httpUrl(c.initiator);
    const page = httpUrl(c.pageUrl);
    if (init && (!page || originOf(init) !== originOf(page))) return init;
    if (page) return page;
    return init || '';
  }

  function hostStems(pageUrl) {
    const h = hostOf(pageUrl).replace(/^(?:www\d*|m|web|v|play|player)\./, '');
    if (!h) return [];
    const first = h.split('.')[0] || '';
    const compact = first.replace(/[^a-z0-9]/g, '');
    const alpha = compact.replace(/\d+$/, '');
    return [h, first, compact, alpha.length >= 4 ? alpha : ''].filter((s) => s && s.length >= 3);
  }

  function isSiteSuffixSegment(seg, stems) {
    const s = String(seg || '').trim();
    if (!s) return true;
    if (GENERIC_TRAIL_RE.test(s) || DOMAIN_TOKEN_RE.test(s)) return true;
    const low = s.toLowerCase();
    const compact = low.replace(/[^a-z0-9]/g, '');
    if (!compact) return true;
    for (const st of stems) {
      if (low === st || compact === st) return true;
      if (st.length >= 5 && compact.length <= st.length + 4 && compact.startsWith(st)) return true;
    }
    return false;
  }

  function cleanSingleTitle(raw, stems) {
    let s = String(raw || '').replace(/[\x00-\x1f\x7f]+/g, ' ').trim();
    if (!s || /^https?:\/\//i.test(s)) return '';
    s = s.replace(/^(?:\(\d+\)\s*|▶\s*)+/, '').trim();
    if (!s || JUNK_TITLE_RE.test(s) || FILE_LIKE_TITLE_RE.test(s)) return '';

    const sepRe = /\s+(?:\||::|—|–|•|»|-)\s+/;
    const parts = s.split(sepRe).map((p) => p.trim()).filter(Boolean);
    while (parts.length > 1 && isSiteSuffixSegment(parts[parts.length - 1], stems)) {
      parts.pop();
    }
    while (parts.length > 1 && GENERIC_TRAIL_RE.test(parts[0])) {
      parts.shift();
    }
    s = parts.join(' - ');
    const strippedLead = s.replace(/^(?:xem\s+phim(?:\s+online)?|watch(?:\s+online)?(?:\s+free)?)\s*(?:[:\-–—]\s*|\s+)/i, '').trim();
    if (strippedLead.length >= 3) s = strippedLead;
    const strippedTail = s.replace(/(?:\s+(?:full\s*hd|fhd|hd|4k|vietsub|thuyết\s*minh|thuyet\s*minh|lồng\s*tiếng|long\s*tieng|online|miễn\s*phí|mien\s*phi|free))+\s*$/i, '').trim();
    if (strippedTail.length >= 3) s = strippedTail;

    s = s.replace(/[<>:"/\\|?*]+/g, ' ').replace(/\s+/g, ' ').trim().replace(/^[.\-\s]+|[.\-\s]+$/g, '');
    if (!s || JUNK_TITLE_RE.test(s) || FILE_LIKE_TITLE_RE.test(s)) return '';
    return s.length > 140 ? s.slice(0, 140).trim().replace(/[.\-\s]+$/, '') : s;
  }

  /**
   * Produces a clean movie/video title from either a raw title string or
   * `{ ogTitle, h1Title, tabTitle }` candidates, stripping site domain spam,
   * leading notification counters, and generic watch-online boilerplate while
   * preserving episode numbers and title codes.
   */
  function cleanMediaTitle(input, pageUrl) {
    const stems = hostStems(pageUrl);
    if (!input || typeof input === 'string') {
      return cleanSingleTitle(input, stems);
    }
    const og = cleanSingleTitle(input.ogTitle, stems);
    const h1 = cleanSingleTitle(input.h1Title, stems);
    const tab = cleanSingleTitle(input.tabTitle, stems);
    const primary = og || h1 || tab;
    if (!primary) return '';
    // Keep tabTitle when it carries an episode number or catalog code that h1/og omitted.
    if (tab && tab !== primary && EPISODE_OR_CODE_RE.test(tab) && !EPISODE_OR_CODE_RE.test(primary)) {
      return tab;
    }
    if (h1 && og && h1.length >= 4 && h1.length < og.length && (!EPISODE_OR_CODE_RE.test(og) || EPISODE_OR_CODE_RE.test(h1))) {
      return h1;
    }
    return primary;
  }

  /**
   * Splits a multi-episode anime/series title into `{ seriesTitle, episodeLabel }`,
   * e.g. `"Lên Đường Nhậm Chức - Tập 01"` -> `{ seriesTitle: "Lên Đường Nhậm Chức", episodeLabel: "Tập 01" }`.
   * Returns `null` when the title is a standalone movie or lacks a series name.
   */
  function parseSeriesEpisode(rawTitle) {
    const t = String(rawTitle || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length < 5) return null;
    const patterns = [
      /^(.+?)(?:\s*[-–—:|•]\s*|\s+)((?:(?:phần|phan|mùa|mua|season)\s*\d+\s*[-–—:|•]?\s*)?(?:tập|tap|episode|ep\.?)\s*\d+(?:\.\d+)?(?:\s*(?:end|cuối|cuoi|final))?)\b/i,
      /^(.+?)(?:\s*[-–—:|•]\s*|\s+)(s\d{1,2}\s*e\d{1,4})\b/i,
      /^(.+?)(?:\s*[-–—:|•]\s*|\s+)(第\s*\d+(?:\.\d+)?\s*[話话集])/u,
    ];
    for (const re of patterns) {
      const m = re.exec(t);
      if (!m) continue;
      const series = m[1].replace(/[\s\-–—:|•([{\/]+$/g, '').replace(/[<>:"/\\|?*]+/g, ' ').replace(/\s+/g, ' ').trim();
      const ep = m[2].replace(/\s+/g, ' ').trim();
      if (series.length >= 2 && series.length <= 90 && !/^\d+$/.test(series) && !DOMAIN_TOKEN_RE.test(series)) {
        return { seriesTitle: series, episodeLabel: ep };
      }
    }
    return null;
  }

  function sanitizeSubLang(raw) {
    const s = String(raw || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
    if (!s || s.length > 16) return '';
    const map = { vietnamese: 'vi', vietsub: 'vi', vie: 'vi', vn: 'vi', english: 'en', eng: 'en', japanese: 'ja', jpn: 'ja', korean: 'ko', kor: 'ko', chinese: 'zh', chi: 'zh', zho: 'zh', chs: 'zh-Hans', cht: 'zh-Hant', thai: 'th', tha: 'th', spanish: 'es', spa: 'es', french: 'fr', fre: 'fr', fra: 'fr', german: 'de', ger: 'de', deu: 'de', russian: 'ru', rus: 'ru', indonesian: 'id', ind: 'id' };
    return map[s] || s;
  }

  function inferSubtitleLang(url, explicitLang, explicitLabel) {
    const direct = sanitizeSubLang(explicitLang);
    if (direct) return direct;
    const lbl = String(explicitLabel || '').toLowerCase();
    if (/việt|viet|tiếng việt|vietsub/.test(lbl)) return 'vi';
    if (/english|tiếng anh/.test(lbl)) return 'en';
    if (/japan|nhật|nhat/.test(lbl)) return 'ja';
    if (/korea|hàn|han/.test(lbl)) return 'ko';
    if (/trung|chinese/.test(lbl)) return 'zh';
    try {
      const u = new URL(url);
      for (const k of ['lang', 'hl', 'srclang', 'locale', 'sub', 'ln']) {
        const q = sanitizeSubLang(u.searchParams.get(k));
        if (q) return q;
      }
      const base = (u.pathname.split('/').pop() || '').replace(/\.(vtt|webvtt|srt|ass|ssa|m3u8)$/i, '');
      const tokMatch = /(?:^|[._-])(vietsub|vietnamese|english|vi|vie|vn|en|eng|ja|jpn|ko|kor|zh(?:-[a-z]+)?|chs|cht|th|es|fr|de|ru|id)$/i.exec(base);
      if (tokMatch) return sanitizeSubLang(tokMatch[1]) || 'sub';
    } catch { /* ignore */ }
    return 'sub';
  }

  function subtitleFormatOf(url, mime) {
    const p = pathOf(url);
    if (/\.(ass|ssa)$/i.test(p) || /x-(?:ass|ssa)/.test(mime || '')) return 'ass';
    if (/\.srt$/i.test(p) || /subrip|text\/srt/.test(mime || '')) return 'srt';
    if (/\.m3u8$/i.test(p) || /mpegurl/.test(mime || '')) return 'hls';
    return 'vtt';
  }

  /**
   * Classifies standalone external subtitle responses (`.vtt`, `.webvtt`, `.srt`, `.ass`, `.ssa`),
   * ignoring segmented HLS WebVTT chunks (`seg-1.vtt`) so subtitles attach cleanly to the tab's video.
   */
  function classifySubtitle(details) {
    const url = details && details.url;
    if (!url || !/^https?:/i.test(url)) return null;
    if (details.tabId === undefined || details.tabId < 0) return null;
    if (details.statusCode && (details.statusCode < 200 || details.statusCode >= 300)) return null;
    if (isBlockedStream(url)) return null;
    if (details.initiator && /^chrome-extension:/i.test(details.initiator)) return null;
    const path = pathOf(url);
    const mime = header(details.responseHeaders, 'content-type').split(';')[0].trim().toLowerCase();
    const byExt = SUBTITLE_EXT.test(path);
    const byMime = SUBTITLE_MIMES.has(mime);
    if (!byExt && !byMime) return null;
    if (SEGMENT_PATH_HINT.test(path)) return null;
    const size = totalSize(details.responseHeaders, details.statusCode);
    if (size !== null && size > MAX_MANIFEST_BYTES) return null;
    const format = subtitleFormatOf(url, mime);
    const lang = inferSubtitleLang(url, '', '');
    return { url, format, lang, label: lang.toUpperCase() };
  }

  function toSrtTimestamp(h, m, s, ms) {
    const hh = String(Number(h || 0)).padStart(2, '0');
    const mm = String(Number(m || 0)).padStart(2, '0');
    const ss = String(Number(s || 0)).padStart(2, '0');
    const mmm = String(ms || '000').padEnd(3, '0').slice(0, 3);
    return `${hh}:${mm}:${ss},${mmm}`;
  }

  /**
   * Converts WebVTT text (including concatenated HLS WebVTT segments) into clean UTF-8 SubRip (`.srt`),
   * stripping `WEBVTT` / `STYLE` / `NOTE` / `REGION` metadata and deduplicating repeated boundary cues.
   */
  function vttToSrt(vttText) {
    const clean = String(vttText || '').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
    if (!clean.trim()) return '';
    // If input is already valid SRT (no WEBVTT header and uses comma timestamps), return normalized text.
    if (!/^\s*WEBVTT\b/i.test(clean) && /\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}/.test(clean)) {
      return clean.trim() + '\n';
    }
    const blocks = clean.split(/\n{2,}/);
    const cues = [];
    const seen = new Set();
    const timeRe = /^(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})(?:[ \t].*)?$/;
    for (const rawBlock of blocks) {
      const lines = rawBlock.split('\n').map((l) => l.trimEnd());
      while (lines.length && !lines[0].trim()) lines.shift();
      if (!lines.length) continue;
      if (/^(?:WEBVTT\b|STYLE\b|NOTE\b|REGION\b|X-TIMESTAMP-MAP=)/i.test(lines[0].trim())) continue;
      let timeIdx = lines.findIndex((l) => timeRe.test(l.trim()));
      if (timeIdx < 0) continue;
      const m = timeRe.exec(lines[timeIdx].trim());
      if (!m) continue;
      const timing = `${toSrtTimestamp(m[1], m[2], m[3], m[4])} --> ${toSrtTimestamp(m[5], m[6], m[7], m[8])}`;
      const bodyLines = lines
        .slice(timeIdx + 1)
        .map((l) => l
          .replace(/<\d{2}:\d{2}(?::\d{2})?[.,]\d{3}>/g, '')
          .replace(/<\/?(?:c|v|lang|ruby|rt)(?:\.[^>\s]+)?(?:\s+[^>]*)?>/gi, '')
          .trim())
        .filter(Boolean);
      if (!bodyLines.length) continue;
      const body = bodyLines.join('\n');
      const dedupeKey = `${timing}|${body}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      cues.push(`${cues.length + 1}\n${timing}\n${body}`);
    }
    return cues.length ? cues.join('\n\n') + '\n' : '';
  }

  function isDisguisedHls(url, path, mime, size) {
    if (NON_MANIFEST_EXT.test(path)) return false;
    if (size !== null && size > MAX_MANIFEST_BYTES) return false;
    if (DISGUISED_HLS_MIMES.has(mime)) {
      try {
        const u = new URL(url);
        return HLS_URL_HINT.test(u.pathname) || HLS_URL_HINT.test(u.search);
      } catch { return false; }
    }
    if (SOFT_HLS_MIMES.has(mime) && size !== null && size >= 16 && size <= 512 * 1024) {
      return STRONG_HLS_PATH_HINT.test(path);
    }
    return false;
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
    if (isBlockedStream(url)) return null;
    if (details.initiator && /^chrome-extension:/i.test(details.initiator)) return null;

    const path = pathOf(url);
    const mime = header(details.responseHeaders, 'content-type').split(';')[0].trim().toLowerCase();

    if (path.endsWith('.m3u8') || /mpegurl/.test(mime)) return { kind: 'hls', mime, size: null };
    if (path.endsWith('.mpd') || mime === 'application/dash+xml') return { kind: 'dash', mime, size: null };
    if (SEGMENT_EXT.test(path) || mime === 'video/mp2t' || mime === 'video/iso.segment') return null;

    const size = totalSize(details.responseHeaders, details.statusCode);
    if (isDisguisedHls(url, path, mime, size)) return { kind: 'hls', mime, size: null };
    if (details.type !== 'media' && SEGMENT_PATH_HINT.test(path) && (size === null || size < 15 * 1024 * 1024)) return null;

    const isOctet = !mime || mime === 'application/octet-stream' || mime === 'binary/octet-stream';
    const isMediaElementStream = details.type === 'media' && isOctet && !NON_MANIFEST_EXT.test(path)
      && (size >= MIN_FILE_BYTES || (details.statusCode === 200 && size === null));
    const isMedia = /^(video|audio)\//.test(mime) || (FILE_EXT.test(path) && isOctet) || isMediaElementStream;
    if (!isMedia) return null;
    if (size !== null && size < MIN_FILE_BYTES) return null;
    return { kind: 'file', mime: mime || 'video/mp4', size };
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

  /** Identifies a known DRM system from KEYFORMAT / schemeIdUri / URI / EME keySystem. */
  function detectDrmSystem(fmtOrScheme, uri) {
    const f = String(fmtOrScheme || '').toLowerCase();
    const u = String(uri || '').toLowerCase();
    if (f === 'com.apple.streamingkeydelivery' || /^skd:/.test(u) || f.includes('94ce86fb-07ff-4f43-adb8-93d2fa968ca2') || f.startsWith('com.apple.fps')) {
      return 'FairPlay';
    }
    if (f.includes('edef8ba9-79d6-4ace-a3c8-27dcd51d21ed') || f.includes('widevine') || u.includes('widevine')) {
      return 'Widevine';
    }
    if (f.includes('9a04f079-9840-4286-ab92-e65be0885f95') || f.includes('playready') || f.includes('com.microsoft')) {
      return 'PlayReady';
    }
    if (f.includes('e2719d58-a985-b3c9-781a-b030af78d30e') || f.includes('1077efec-c0b2-4d02-ace3-3c1e52e2fb4b') || f.includes('clearkey')) {
      return 'ClearKey DRM';
    }
    return null;
  }

  /** Classifies an EME `navigator.requestMediaKeySystemAccess(keySystem)` identifier as DRM. */
  function classifyEmeKeySystem(keySystem) {
    const ks = String(keySystem || '').trim();
    if (!ks) return { encryption: 'none', drmSystem: null, encryptionReason: 'no EME keySystem' };
    const sys = detectDrmSystem(ks, '') || 'EME DRM';
    return {
      encryption: 'drm',
      drmSystem: sys,
      encryptionReason: `${sys} (EME requestMediaKeySystemAccess: ${ks})`,
    };
  }

  /**
   * Classifies an `#EXT-X-KEY` / `#EXT-X-SESSION-KEY` attribute map:
   *  - METHOD=NONE -> 'none'
   *  - AES-128 or SAMPLE-AES with KEYFORMAT="identity" (or omitted) and http(s) URI -> 'aes128' | 'sample_aes' (NOT DRM)
   *  - FairPlay (skd:// or com.apple.streamingkeydelivery), Widevine/PlayReady UUIDs,
   *    SAMPLE-AES-CTR / SAMPLE-AES-CENC, or any KEYFORMAT !== "identity" / non-http(s) URI -> 'drm'
   */
  function classifyKey(a, baseUrl) {
    const method = String((a && a.METHOD) || 'NONE').trim().toUpperCase();
    if (method === 'NONE') {
      return { encryption: 'none', drmSystem: null, encryptionReason: 'unencrypted (METHOD=NONE)' };
    }
    const rawFmt = String((a && a.KEYFORMAT) || '').trim();
    const fmt = (rawFmt || 'identity').toLowerCase();
    const rawUri = String((a && a.URI) || '').trim();
    const resolvedUri = rawUri ? resolve(baseUrl || 'https://example.invalid/', rawUri) : '';

    if (fmt === 'com.apple.streamingkeydelivery' || /^skd:/i.test(rawUri)) {
      return {
        encryption: 'drm',
        drmSystem: 'FairPlay',
        encryptionReason: /^skd:/i.test(rawUri)
          ? 'FairPlay (URI skd://)'
          : 'FairPlay (KEYFORMAT=com.apple.streamingkeydelivery)',
      };
    }
    if (fmt !== 'identity') {
      const sys = detectDrmSystem(fmt, rawUri) || 'DRM';
      return {
        encryption: 'drm',
        drmSystem: sys,
        encryptionReason: `${sys} (KEYFORMAT=${rawFmt})`,
      };
    }
    if (method === 'SAMPLE-AES-CTR' || method === 'SAMPLE-AES-CENC') {
      const sys = detectDrmSystem(fmt, rawUri) || 'CENC DRM';
      return {
        encryption: 'drm',
        drmSystem: sys,
        encryptionReason: `${sys} (METHOD=${method})`,
      };
    }
    if (!rawUri || !/^https?:\/\//i.test(resolvedUri)) {
      const sys = detectDrmSystem(fmt, rawUri) || 'DRM';
      const scheme = (rawUri.match(/^([a-z0-9+.-]+):/i) || [])[1];
      return {
        encryption: 'drm',
        drmSystem: sys,
        encryptionReason: scheme ? `${sys} (non-HTTP key URI ${scheme}://)` : `${sys} (missing HTTP key URI for ${method})`,
      };
    }
    if (method === 'AES-128') {
      return {
        encryption: 'aes128',
        drmSystem: null,
        encryptionReason: `AES-128 (${rawFmt ? 'KEYFORMAT=identity' : 'default identity'}, HTTP/S key)`,
      };
    }
    if (method === 'SAMPLE-AES') {
      return {
        encryption: 'sample_aes',
        drmSystem: null,
        encryptionReason: `SAMPLE-AES (${rawFmt ? 'KEYFORMAT=identity' : 'default identity'}, HTTP/S key)`,
      };
    }
    return {
      encryption: 'drm',
      drmSystem: 'DRM',
      encryptionReason: `DRM (unsupported METHOD=${method})`,
    };
  }

  /** Encryption of a key line: 'none' | 'aes128' | 'sample_aes' | 'drm'. */
  function keyKind(a, baseUrl) {
    return classifyKey(a, baseUrl).encryption;
  }

  function worse(a, b) {
    const rank = { none: 0, aes128: 1, sample_aes: 2, drm: 3 };
    return (rank[b] || 0) > (rank[a] || 0) ? b : a;
  }

  function detectVideoCodec(codecs) {
    const c = String(codecs || '').toLowerCase();
    if (!c) return '';
    if (/\b(?:hvc1|hev1)\b/.test(c)) return 'hevc';
    if (/\bav01\b/.test(c)) return 'av1';
    if (/\bavc1\b/.test(c)) return 'avc';
    if (/\bvp0?9\b/.test(c)) return 'vp9';
    return '';
  }

  function parseM3U8(text, baseUrl) {
    const lines = String(text || '').split(/\r?\n/).map((l) => l.trim());
    if (!lines.length || !lines[0].startsWith('#EXTM3U')) return { valid: false };
    const out = {
      valid: true, master: false, variants: [], renditions: [], encryption: 'none',
      drmSystem: null, encryptionReason: 'unencrypted',
      videoCodec: '', hasSeparateAudio: false, audioTracks: 0, subtitles: [],
      targetDuration: 0, mediaSequence: 0, duration: 0, segments: 0, live: true,
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.startsWith('#EXT-X-STREAM-INF')) {
        out.master = true;
        const a = attrs(line);
        let j = i + 1;
        while (j < lines.length && (!lines[j] || lines[j].startsWith('#'))) j++;
        if (j < lines.length) {
          const [w, h] = (a.RESOLUTION || '').split('x').map(Number);
          const codecs = a.CODECS || '';
          out.variants.push({
            url: resolve(baseUrl, lines[j]),
            width: w || null,
            height: h || null,
            bandwidth: Number(a.BANDWIDTH) || null,
            codecs,
            videoCodec: detectVideoCodec(codecs),
            audio: a.AUDIO || '',
            subtitles: a.SUBTITLES || '',
          });
        }
      } else if (line.startsWith('#EXT-X-MEDIA:')) {
        const a = attrs(line);
        if (a.URI) {
          out.renditions.push({
            url: resolve(baseUrl, a.URI),
            type: (a.TYPE || '').toUpperCase(),
            groupId: a['GROUP-ID'] || '',
            name: a.NAME || '',
            language: a.LANGUAGE || '',
            default: a.DEFAULT === 'YES',
            autoselect: a.AUTOSELECT === 'YES',
          });
        }
      } else if (line.startsWith('#EXT-X-TARGETDURATION:')) {
        out.targetDuration = Number(line.slice(22).trim()) || out.targetDuration;
      } else if (line.startsWith('#EXT-X-MEDIA-SEQUENCE:')) {
        const seq = parseInt(line.slice(22).trim(), 10);
        if (Number.isFinite(seq) && seq >= 0) out.mediaSequence = seq;
      } else if (line.startsWith('#EXT-X-KEY') || line.startsWith('#EXT-X-SESSION-KEY')) {
        const ck = classifyKey(attrs(line), baseUrl);
        const nextEnc = worse(out.encryption, ck.encryption);
        if (nextEnc !== out.encryption || (ck.encryption === 'drm' && !out.drmSystem && ck.drmSystem)) {
          out.encryption = nextEnc;
          out.encryptionReason = ck.encryptionReason;
          if (ck.drmSystem) out.drmSystem = ck.drmSystem;
        }
      } else if (line.startsWith('#EXTINF')) {
        out.segments += 1;
        out.duration += parseFloat(line.slice(8)) || 0;
      } else if (line.startsWith('#EXT-X-ENDLIST')) {
        out.live = false;
      }
    }
    if (out.master) out.live = false;   // decided by the media playlists
    out.variants.sort((a, b) => (b.height || 0) - (a.height || 0) || (b.bandwidth || 0) - (a.bandwidth || 0));
    const firstWithCodec = out.variants.find((v) => v.videoCodec);
    out.videoCodec = firstWithCodec ? firstWithCodec.videoCodec : '';
    const extAudios = out.renditions.filter((r) => r.type === 'AUDIO' && r.url);
    out.audioTracks = extAudios.length;
    out.hasSeparateAudio = extAudios.some((r) => !out.variants.length
      || out.variants.some((v) => !v.audio || v.audio === r.groupId));
    out.subtitles = out.renditions
      .filter((r) => r.type === 'SUBTITLES' && r.url)
      .map((r) => {
        const lang = inferSubtitleLang(r.url, r.language, r.name);
        return {
          url: r.url,
          lang,
          label: r.name || (r.language || lang || 'SUB').toUpperCase(),
          format: subtitleFormatOf(r.url, ''),
        };
      });
    out.duration = Math.round(out.duration);
    return out;
  }

  // ── DASH (ISO/IEC 23009-1) — regex, no DOMParser in a service worker ────
  function isoDuration(s) {
    const m = /^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$/.exec(s || '');
    if (!m) return 0;
    return Math.round((+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0));
  }

  function detectPsshSystem(b64) {
    try {
      const clean = String(b64 || '').replace(/\s+/g, '');
      const bin = typeof atob === 'function' ? atob(clean) : Buffer.from(clean, 'base64').toString('binary');
      if (bin.length < 28 || bin.slice(4, 8) !== 'pssh') return 'PSSH DRM';
      let hex = '';
      for (let i = 12; i < 28; i++) hex += bin.charCodeAt(i).toString(16).padStart(2, '0');
      if (hex === 'edef8ba979d64acea3c827dcd51d21ed') return 'Widevine';
      if (hex === '9a04f07998404286ab92e65be0885f95') return 'PlayReady';
      if (hex === '94ce86fb07ff4f43adb893d2fa968ca2') return 'FairPlay';
      return 'PSSH DRM';
    } catch {
      return 'PSSH DRM';
    }
  }

  function parseMPD(text) {
    const t = String(text || '');
    if (!/<MPD[\s>]/.test(t)) return { valid: false };
    const cpTags = t.match(/<(?:\w+:)?ContentProtection\b[^>]*>/gi) || [];
    const psshMatches = [...t.matchAll(/<(?:\w+:)?pssh\b[^>]*>([\s\S]*?)<\/(?:\w+:)?pssh>/gi)];
    const systems = [];
    const addSys = (s) => { if (s && !systems.includes(s)) systems.push(s); };
    for (const tag of cpTags) {
      const scheme = ((/\bschemeIdUri="([^"]+)"/i.exec(tag) || [])[1]) || tag;
      addSys(detectDrmSystem(scheme, ''));
    }
    for (const pm of psshMatches) {
      const sys = detectPsshSystem(pm[1]);
      if (sys && sys !== 'PSSH DRM') addSys(sys);
    }
    const hasDrm = cpTags.length > 0 || psshMatches.length > 0;
    const drmSystem = hasDrm ? (systems.length ? systems.join(', ') : (psshMatches.length ? 'PSSH DRM' : 'DASH DRM')) : null;
    const encryptionReason = hasDrm
      ? `${drmSystem} (${cpTags.length ? 'MPD ContentProtection' : ''}${cpTags.length && psshMatches.length ? ' + ' : ''}${psshMatches.length ? 'PSSH' : ''})`
      : 'unencrypted MPD';
    const out = {
      valid: true, drm: hasDrm, drmSystem, drmSystems: systems, encryptionReason,
      live: /<MPD[^>]*\btype="dynamic"/.test(t),
      duration: isoDuration((/mediaPresentationDuration="([^"]+)"/.exec(t) || [])[1]),
      videos: [], audios: 0, videoCodec: '',
    };
    const sets = t.split(/<(?:\w+:)?AdaptationSet\b/).slice(1);
    for (const set of sets) {
      const head = set.slice(0, set.indexOf('>') + 1);
      const isAudio = /contentType="audio"|mimeType="audio\//.test(head) || /mimeType="audio\//.test(set);
      const headCodec = ((/\bcodecs="([^"]+)"/.exec(head) || [])[1]) || '';
      const reps = set.split(/<(?:\w+:)?Representation\b/).slice(1);
      for (const rep of reps) {
        const tag = rep.slice(0, rep.indexOf('>') + 1);
        if (isAudio || /mimeType="audio\//.test(tag)) { out.audios += 1; continue; }
        const h = Number((/\bheight="(\d+)"/.exec(tag) || /\bheight="(\d+)"/.exec(head) || [])[1]) || null;
        const bw = Number((/\bbandwidth="(\d+)"/.exec(tag) || [])[1]) || null;
        const repCodec = ((/\bcodecs="([^"]+)"/.exec(tag) || [])[1]) || headCodec;
        if (h || bw) out.videos.push({ height: h, bandwidth: bw, videoCodec: detectVideoCodec(repCodec) });
      }
    }
    out.videos.sort((a, b) => (b.height || 0) - (a.height || 0) || (b.bandwidth || 0) - (a.bandwidth || 0));
    const firstWithCodec = out.videos.find((v) => v.videoCodec);
    out.videoCodec = firstWithCodec ? firstWithCodec.videoCodec : '';
    return out;
  }

  function qualityLabel(height) {
    if (!height) return '';
    if (height >= 2160) return '4K';
    if (height >= 1440) return '1440p';
    return `${height}p`;
  }

  const VGSniff = {
    classify, canonicalUrl, totalSize, parseM3U8, parseMPD, classifyKey, detectDrmSystem,
    classifyEmeKeySystem, qualityLabel, isoDuration, pickReferer, cleanMediaTitle,
    parseSeriesEpisode, classifySubtitle, vttToSrt, inferSubtitleLang, detectVideoCodec,
    MAX_ITEMS, MIN_FILE_BYTES, MAX_MANIFEST_BYTES, isBlockedStream,
  };
  root.VGSniff = VGSniff;
  if (typeof module !== 'undefined' && module.exports) module.exports = VGSniff;

  // ── service-worker wiring ───────────────────────────────────────────────
  if (typeof chrome === 'undefined' || !chrome.webRequest || !chrome.storage || !chrome.storage.session) return;

  const KEY = (tabId) => `vg_sniff_${tabId}`;
  const lists = new Map();          // tabId -> items (memory copy of storage.session)
  const children = new Map();       // tabId -> Set of manifest URLs a master already stands for
  const subs = new Map();           // tabId -> Array<{ url, lang, label, format, at }>
  const frames = new Map();         // tabId -> Map(frameId -> frame URL)
  const saveTimers = new Map();

  function recordSubtitle(tabId, sub, at = Date.now()) {
    if (!sub || !sub.url || !/^https?:/i.test(sub.url)) return false;
    let list = subs.get(tabId);
    if (!list) { list = []; subs.set(tabId, list); }
    const existing = list.find((s) => s.url === sub.url);
    if (existing) {
      if (sub.lang && sub.lang !== 'sub' && existing.lang === 'sub') existing.lang = sub.lang;
      if (sub.label && (!existing.label || existing.label === 'SUB')) existing.label = sub.label;
      return false;
    }
    if (list.length >= MAX_SUBS_PER_TAB) return false;
    list.push({
      url: sub.url,
      lang: sanitizeSubLang(sub.lang) || inferSubtitleLang(sub.url, sub.lang, sub.label),
      label: String(sub.label || sub.lang || 'SUB').slice(0, 32),
      format: sub.format || subtitleFormatOf(sub.url, ''),
      at,
    });
    return true;
  }

  function attachSubtitles(tabId, items) {
    const tabSubs = subs.get(tabId) || [];
    let changed = false;
    for (const it of items || []) {
      const merged = [];
      const seen = new Set();
      const add = (s) => {
        if (!s || !s.url || seen.has(s.url) || merged.length >= 8) return;
        seen.add(s.url);
        merged.push({
          url: s.url,
          lang: sanitizeSubLang(s.lang) || 'sub',
          label: String(s.label || s.lang || 'SUB').slice(0, 32),
          format: s.format || 'vtt',
        });
      };
      (it.hlsSubtitles || []).forEach(add);
      tabSubs.forEach(add);
      const prevLen = Array.isArray(it.subtitles) ? it.subtitles.length : 0;
      if (merged.length !== prevLen) changed = true;
      it.subtitles = merged;
    }
    return changed;
  }

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
    subs.delete(tabId);
    frames.delete(tabId);
    navAt.delete(tabId);
    try { await chrome.storage.session.remove(KEY(tabId)); } catch { /* gone */ }
    root.vgSniffBadge && root.vgSniffBadge(tabId, 0);
  }

  // A real navigation drops what the previous page played — decided by
  // Chrome's own request timestamps rather than by the order events reach
  // the worker, which webRequest does not promise across event types.
  const navAt = new Map();
  async function navigated(tabId, ts, url) {
    if (ts <= (navAt.get(tabId) || 0)) return;
    navAt.set(tabId, ts);
    frames.delete(tabId);
    if (url && /^https?:/i.test(url)) frames.set(tabId, new Map([[0, url]]));
    const tabSubs = subs.get(tabId);
    if (tabSubs) subs.set(tabId, tabSubs.filter((s) => s.at >= ts));
    const items = await load(tabId);
    const keep = items.filter((x) => x.at >= ts);
    items.length = 0;
    items.push(...keep);
    children.delete(tabId);
    save(tabId);
  }

  async function fetchText(url, refererUrl) {
    if (refererUrl && typeof root.vgEnsureStreamReferer === 'function') {
      await root.vgEnsureStreamReferer(url, refererUrl).catch(() => {});
    }
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
      const ref = item.refererUrl || item.pageUrl || '';
      const text = await fetchText(item.url, ref);
      if (item.kind === 'hls') {
        const p = parseM3U8(text, item.url);
        if (!p.valid) { item.hidden = true; return; }
        item.encryption = p.encryption;
        item.drmSystem = p.drmSystem || null;
        item.encryptionReason = p.encryptionReason || '';
        item.videoCodec = p.videoCodec || '';
        item.hasSeparateAudio = Boolean(p.hasSeparateAudio);
        item.audioTracks = p.audioTracks || 0;
        item.hlsSubtitles = p.subtitles || [];
        item.targetDuration = p.targetDuration || 0;
        item.live = p.live;
        item.duration = p.duration || null;
        if (p.master) {
          item.master = true;
          item.variants = p.variants.slice(0, 8).map((v) => ({
            height: v.height,
            bandwidth: v.bandwidth,
            videoCodec: v.videoCodec || '',
          }));
          item.height = (p.variants[0] && p.variants[0].height) || null;
          const kids = children.get(tabId) || new Set();
          p.variants.forEach((v) => kids.add(v.url));
          p.renditions.forEach((r) => kids.add(r.url));
          children.set(tabId, kids);
          // the best variant tells the duration and whether its segments are encrypted
          if (p.variants[0] && item.encryption !== 'drm') {
            try {
              const v = parseM3U8(await fetchText(p.variants[0].url, ref), p.variants[0].url);
              if (v.valid) {
                const merged = worse(item.encryption, v.encryption);
                if (merged !== item.encryption || (v.encryption === 'drm' && v.drmSystem)) {
                  item.encryption = merged;
                  item.encryptionReason = v.encryptionReason || item.encryptionReason;
                  if (v.drmSystem) item.drmSystem = v.drmSystem;
                }
                item.duration = v.duration || item.duration;
                item.targetDuration = v.targetDuration || item.targetDuration;
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
        item.drmSystem = d.drmSystem || null;
        item.encryptionReason = d.encryptionReason || '';
        item.videoCodec = d.videoCodec || '';
        item.hasSeparateAudio = d.audios > 0;
        item.live = d.live;
        item.duration = d.duration || null;
        item.variants = d.videos.slice(0, 8);
        item.height = (d.videos[0] && d.videos[0].height) || null;
        item.audios = d.audios;
      }
      if (item.emeDrm && item.encryption !== 'drm') {
        item.encryption = 'drm';
        item.drmSystem = item.drmSystem || 'EME DRM';
        item.encryptionReason = item.encryptionReason || 'EME MediaKeys active on video element';
      }
      attachSubtitles(tabId, [item]);
      if (typeof console !== 'undefined' && typeof console.info === 'function') {
        console.info('[VidGrab][Sniffer] Classified stream:', {
          url: item.url,
          kind: item.kind,
          encryption: item.encryption,
          drmSystem: item.drmSystem || null,
          reason: item.encryptionReason || 'unencrypted',
          videoCodec: item.videoCodec || 'default',
          hasSeparateAudio: !!item.hasSeparateAudio,
          subtitles: (item.subtitles && item.subtitles.length) || 0,
          live: !!item.live,
        });
      }
    } catch (e) {
      item.error = String(e && e.message || e).slice(0, 80);
    } finally {
      item.pending = false;
      save(tabId);
      if (!item.hidden && !item.error && item.encryption !== 'drm' && !item.live
          && typeof root.vgOnSniffStreamReady === 'function') {
        Promise.resolve(root.vgOnSniffStreamReady(tabId, item)).catch(() => {});
      }
    }
  }

  async function onResponse(details) {
    const tabId = details.tabId;
    const ts = details.timeStamp || Date.now();
    const sub = classifySubtitle(details);
    if (sub) {
      if (ts < (navAt.get(tabId) || 0)) return;
      if (recordSubtitle(tabId, sub, ts)) {
        const items = await load(tabId);
        if (attachSubtitles(tabId, items)) save(tabId);
      }
      return;
    }
    const c = classify(details);
    if (!c) return;
    if (ts < (navAt.get(tabId) || 0)) return;          // the previous page's media
    const items = await load(tabId);
    const url = c.kind === 'file' ? canonicalUrl(details.url) : details.url;
    if (items.some((x) => x.url === url)) return;
    if (c.kind === 'hls' && children.get(tabId) && children.get(tabId).has(url)) return;
    if (items.length >= MAX_ITEMS) return;
    // pushed before any await, so the same URL arriving twice is listed once
    const item = {
      id: Math.random().toString(36).slice(2, 10),
      url, kind: c.kind, mime: c.mime, size: c.size,
      title: '', pageUrl: '', refererUrl: '', at: ts,
      pending: c.kind !== 'file', encryption: 'none', subtitles: [],
    };
    items.push(item);
    try {
      const t = await chrome.tabs.get(tabId);
      item.pageUrl = t.url || '';
      item.title = cleanMediaTitle(t.title || '', item.pageUrl) || (t.title || '').trim();
    } catch { /* closed */ }
    const tabFrames = frames.get(tabId);
    item.refererUrl = pickReferer({
      frameId: details.frameId || 0,
      frameUrl: (tabFrames && tabFrames.get(details.frameId)) || '',
      documentUrl: details.documentUrl || '',
      initiator: details.initiator || '',
      pageUrl: item.pageUrl,
    });
    attachSubtitles(tabId, [item]);
    save(tabId);
    if (item.pending) {
      enrich(tabId, item);
    } else if (c.kind === 'file' && (c.size || 0) >= 5 * 1024 * 1024
               && typeof root.vgOnSniffStreamReady === 'function') {
      setTimeout(async () => {
        try {
          const cur = await load(tabId);
          if (!cur.includes(item) || item.hidden) return;
          if (cur.some((x) => !x.hidden && (x.kind === 'hls' || x.kind === 'dash'))) return;
          await root.vgOnSniffStreamReady(tabId, item);
        } catch { /* ignore */ }
      }, 900);
    }
  }

  /**
   * Probes all frames (including nested cross-origin player iframes) for active
   * <video> sources, <track> subtitles, PerformanceResourceTiming manifest/media entries
   * missed by a cold-started service worker, and clean og:title / <h1> movie titles.
   */
  async function probeTab(tabId) {
    const items = await load(tabId);
    if (!chrome.scripting || typeof chrome.scripting.executeScript !== 'function') {
      attachSubtitles(tabId, items);
      return { items: visible(items), playing: null };
    }
    let tabMeta = null;
    try { tabMeta = await chrome.tabs.get(tabId); } catch { return { items: visible(items), playing: null }; }
    if (!tabMeta || !tabMeta.url || !/^https?:\/\//i.test(tabMeta.url)) {
      attachSubtitles(tabId, items);
      return { items: visible(items), playing: null };
    }

    let results = [];
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId, allFrames: true },
        func: () => {
          const out = {
            frameUrl: String(location.href || ''),
            isTop: window === window.top,
            docTitle: String(document.title || '').slice(0, 240),
            ogTitle: '',
            h1Title: '',
            videos: [],
            tracks: [],
            resources: [],
          };
          if (out.isTop) {
            const og = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
            out.ogTitle = og ? String(og.getAttribute('content') || '').slice(0, 240) : '';
            const h1 = document.querySelector('h1');
            out.h1Title = h1 ? String(h1.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 240) : '';
          }
          const vids = document.querySelectorAll('video');
          for (let i = 0; i < vids.length && out.videos.length < 6; i++) {
            const v = vids[i];
            const src = String(v.currentSrc || v.src || '');
            out.videos.push({
              src: /^https?:\/\//i.test(src) ? src : '',
              playing: !v.paused && !v.ended,
              hasMediaKeys: Boolean(v.mediaKeys),
              height: v.videoHeight || 0,
              duration: Number.isFinite(v.duration) && v.duration > 0 ? Math.round(v.duration) : 0,
            });
            const trs = v.querySelectorAll('track');
            for (let j = 0; j < trs.length && out.tracks.length < 12; j++) {
              const tr = trs[j];
              const kind = String(tr.kind || 'subtitles').toLowerCase();
              if (kind !== 'subtitles' && kind !== 'captions' && kind !== '') continue;
              const tSrc = String(tr.src || '');
              if (/^https?:\/\//i.test(tSrc)) {
                out.tracks.push({
                  url: tSrc,
                  srclang: String(tr.srclang || '').slice(0, 16),
                  label: String(tr.label || '').slice(0, 32),
                });
              }
            }
          }
          try {
            const entries = performance.getEntriesByType('resource') || [];
            for (let i = entries.length - 1; i >= 0 && out.resources.length < 35; i--) {
              const e = entries[i];
              const name = String((e && e.name) || '');
              if (!/^https?:\/\//i.test(name)) continue;
              const initType = String(e.initiatorType || '');
              const size = Number(e.decodedBodySize || e.transferSize || 0) || 0;
              if (/\.(m3u8|mpd)(\?|$)/i.test(name)
                  || /(?:^|[/?&=_.-])(m3u8|hls\d*|playlist|master|manifest|chunklist|urlset|index[-_]v\d)(?:$|[/?&=_.-])/i.test(name)
                  || initType === 'video'
                  || initType === 'track'
                  || /\.(mp4|m4v|webm|mov|mkv|vtt|webvtt|srt|ass|ssa)(\?|$)/i.test(name)) {
                out.resources.push({ url: name, initiatorType: initType, size });
              }
            }
          } catch { /* cross-origin timing restricted */ }
          return out;
        },
      });
    } catch {
      attachSubtitles(tabId, items);
      return { items: visible(items), playing: null };
    }

    let topFrame = null;
    let playing = null;
    const emeFrameUrls = new Set();
    for (const entry of results || []) {
      const r = entry && entry.result;
      if (!r) continue;
      if (r.isTop) topFrame = r;
      if (entry.frameId > 0 && /^https?:/i.test(r.frameUrl || '')) {
        let m = frames.get(tabId);
        if (!m) { m = new Map(); frames.set(tabId, m); }
        if (m.size < 64) m.set(entry.frameId, r.frameUrl);
      }
      for (const tr of r.tracks || []) {
        if (!isBlockedStream(tr.url)) {
          const lang = inferSubtitleLang(tr.url, tr.srclang, tr.label);
          recordSubtitle(tabId, {
            url: tr.url,
            lang,
            label: tr.label || lang.toUpperCase(),
            format: subtitleFormatOf(tr.url, ''),
          });
        }
      }
      for (const v of r.videos || []) {
        if (v.hasMediaKeys && r.frameUrl) emeFrameUrls.add(r.frameUrl);
        if (v.playing || !playing) {
          playing = { src: v.src || '', title: '', frameUrl: r.frameUrl, emeDrm: !!v.hasMediaKeys };
          if (v.playing) break;
        }
      }
    }

    const bestTitle = cleanMediaTitle({
      ogTitle: topFrame && topFrame.ogTitle,
      h1Title: topFrame && topFrame.h1Title,
      tabTitle: (topFrame && topFrame.docTitle) || tabMeta.title || '',
    }, tabMeta.url) || cleanMediaTitle(tabMeta.title || '', tabMeta.url) || (tabMeta.title || '').trim();

    if (playing) playing.title = bestTitle;

    let changed = false;
    for (const it of items) {
      if (bestTitle && (!it.title || it.title !== bestTitle)) {
        it.title = bestTitle;
        changed = true;
      }
      if (emeFrameUrls.size && (emeFrameUrls.has(it.refererUrl) || emeFrameUrls.has(it.pageUrl)) && it.encryption !== 'drm') {
        it.emeDrm = true;
        it.encryption = 'drm';
        it.drmSystem = it.drmSystem || 'EME DRM';
        it.encryptionReason = 'EME MediaKeys active on video element';
        changed = true;
      }
    }

    const toEnrich = [];
    const ingestUrl = (rawUrl, kindHint, sizeHint, frameId, frameUrl) => {
      if (!rawUrl || isBlockedStream(rawUrl) || items.length >= MAX_ITEMS) return;
      const p = pathOf(rawUrl);
      if (SUBTITLE_EXT.test(p) && !SEGMENT_PATH_HINT.test(p)) {
        const lang = inferSubtitleLang(rawUrl, '', '');
        recordSubtitle(tabId, { url: rawUrl, lang, label: lang.toUpperCase(), format: subtitleFormatOf(rawUrl, '') });
        return;
      }
      if (SEGMENT_EXT.test(p)) return;
      let kind = kindHint;
      if (!kind) {
        if (p.endsWith('.m3u8')) kind = 'hls';
        else if (p.endsWith('.mpd')) kind = 'dash';
        else if (isDisguisedHls(rawUrl, p, '', sizeHint || null)) kind = 'hls';
        else if (FILE_EXT.test(p) && (!sizeHint || sizeHint >= MIN_FILE_BYTES)) kind = 'file';
      }
      if (!kind) return;
      const url = kind === 'file' ? canonicalUrl(rawUrl) : rawUrl;
      if (items.some((x) => x.url === url)) return;
      if (kind === 'hls' && children.get(tabId) && children.get(tabId).has(url)) return;
      const emeDrm = Boolean(frameUrl && emeFrameUrls.has(frameUrl));
      const item = {
        id: Math.random().toString(36).slice(2, 10),
        url,
        kind,
        mime: kind === 'file' ? 'video/mp4' : '',
        size: kind === 'file' && sizeHint >= MIN_FILE_BYTES ? sizeHint : null,
        title: bestTitle,
        pageUrl: tabMeta.url || '',
        refererUrl: pickReferer({ frameId, frameUrl, pageUrl: tabMeta.url || '' }),
        at: Date.now(),
        pending: kind !== 'file',
        emeDrm,
        encryption: emeDrm ? 'drm' : 'none',
        drmSystem: emeDrm ? 'EME DRM' : null,
        encryptionReason: emeDrm ? 'EME MediaKeys active on video element' : '',
        subtitles: [],
      };
      items.push(item);
      changed = true;
      if (item.pending) toEnrich.push(item);
    };

    for (const entry of results || []) {
      const r = entry && entry.result;
      if (!r) continue;
      const fid = entry.frameId || 0;
      for (const v of r.videos || []) {
        if (v.src) ingestUrl(v.src, /\.(m3u8)(\?|$)/i.test(v.src) ? 'hls' : /\.(mpd)(\?|$)/i.test(v.src) ? 'dash' : 'file', null, fid, r.frameUrl);
      }
      for (const res of r.resources || []) {
        if (res.initiatorType === 'track') {
          if (!isBlockedStream(res.url)) {
            const lang = inferSubtitleLang(res.url, '', '');
            recordSubtitle(tabId, { url: res.url, lang, label: lang.toUpperCase(), format: subtitleFormatOf(res.url, '') });
          }
          continue;
        }
        const k = res.initiatorType === 'video' && !/\.(m3u8|mpd)(\?|$)/i.test(res.url) ? 'file' : '';
        ingestUrl(res.url, k, res.size || null, fid, r.frameUrl);
      }
    }

    if (attachSubtitles(tabId, items)) changed = true;
    if (changed) save(tabId);
    for (const item of toEnrich) enrich(tabId, item);
    return { items: visible(items), playing };
  }

  chrome.webRequest.onResponseStarted.addListener(
    (d) => { onResponse(d).catch(() => {}); },
    { urls: ['<all_urls>'], types: ['media', 'xmlhttprequest', 'other', 'object'] },
    ['responseHeaders'],
  );
  // A real navigation of the tab starts a new list; sub_frame navigations record
  // embedded player URLs so Referer/Origin match the iframe player rather than the outer shell.
  chrome.webRequest.onBeforeRequest.addListener(
    (d) => {
      if (d.tabId < 0) return;
      if (d.frameId === 0) {
        navigated(d.tabId, d.timeStamp || Date.now(), d.url).catch(() => {});
      } else if (d.frameId > 0 && /^https?:/i.test(d.url || '')) {
        let m = frames.get(d.tabId);
        if (!m) { m = new Map(); frames.set(d.tabId, m); }
        if (m.size < 64) m.set(d.frameId, d.url);
      }
    },
    { urls: ['<all_urls>'], types: ['main_frame', 'sub_frame'] },
  );
  chrome.tabs.onRemoved.addListener((tabId) => { clearTab(tabId); });

  root.vgSniffList = async (tabId) => {
    const items = await load(tabId);
    attachSubtitles(tabId, items);
    return visible(items);
  };
  root.vgSniffProbe = probeTab;
  root.vgSniffClear = clearTab;
})(typeof self !== 'undefined' ? self : globalThis);
