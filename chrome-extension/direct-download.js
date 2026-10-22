/**
 * VidGrab direct download (5.18.0, owner 22/10/2026)
 * ==================================================
 * "Tải trực tiếp trên tiện ích cho tất cả các nền tảng": the video is found
 * and saved by the person's own Chrome — the VidGrab server is not asked to
 * extract it. The server is only used to COUNT the download (POST
 * /client/quota/claim, the same daily allowance as the web and the Windows
 * app; refunded with /settle when the download fails) and as the fallback
 * when the browser cannot find a downloadable file.
 *
 * How a video page gives up its file:
 *   - the page is opened in the tab the person is on (single download from
 *     that page) or in a small minimised VidGrab window (lists / bulk),
 *     because Facebook / TikTok / Instagram only put the file link in the
 *     page after their own JavaScript ran (measured 22/10: a plain fetch of
 *     the HTML has no link);
 *   - vgPageMedia() runs in the page (chrome.scripting, MAIN world) and reads
 *     the platform's own data: Facebook browser_native_hd/sd_url (the muxed
 *     MP4 — the player itself streams video and sound as separate DASH
 *     pieces), TikTok's rehydration JSON (playAddr), Instagram video_versions,
 *     else og:video / <video src> / JSON-LD contentUrl;
 *   - otherwise the media sniffer's list for that tab (5.12.0): one complete
 *     video file, never a stream split into separate video and audio.
 * Not attempted (straight to the server): YouTube (Chrome Web Store policy,
 * IP-locked streams), TikTok (its CDN refuses the file outside the page),
 * Spotify / SoundCloud (audio through the server),
 * MP3 / chosen quality / subtitles (the server converts).
 *
 * The pure helpers are exported for tests/test_direct_download.js.
 */
(function (root) {
  'use strict';

  // TikTok (5.18.4): its CDN answers a file saved outside the page with a web
  // page (owner's "TikTok - Make Your Day.htm", 22/10) — trying it only cost
  // 15–20 s before the server did it anyway.
  const SERVER_ONLY = /(^|\.)(youtube\.com|youtu\.be|googlevideo\.com|spotify\.com|soundcloud\.com|tiktok\.com)$/i;
  const STREAM = /\.(m3u8|mpd)(\?|$)/i;
  const FIND_TIMEOUT_MS = 15000;
  const SAVE_TIMEOUT_MS = 10 * 60 * 1000;

  function hostOf(u) {
    try { return new URL(u).hostname.toLowerCase(); } catch { return ''; }
  }

  /** True when this download should be tried in the browser first. */
  function directEligible({ url, quality, download_subs }) {
    if (!/^https:\/\//i.test(url || '')) return false;
    if (SERVER_ONLY.test(hostOf(url))) return false;
    if (download_subs) return false;                       // the server fetches subtitles
    const q = String(quality || 'video').toLowerCase();
    return q === 'video' || q === 'best' || q === '';      // MP3 / a chosen height: the server converts
  }

  /**
   * Runs IN THE PAGE (chrome.scripting.executeScript, world MAIN): must be
   * self-contained. Returns {url, title, via} or null.
   */
  function vgPageMedia() {
    const host = location.hostname;
    const html = document.documentElement.innerHTML;
    const un = (s) => s.replace(/\\u0025/gi, '%').replace(/\\u0026/gi, '&').replace(/\\u003d/gi, '=')
      .replace(/\\\//g, '/').replace(/&amp;/g, '&');
    const meta = (p) => { const m = document.querySelector(`meta[property="${p}"], meta[name="${p}"]`); return m ? m.content || '' : ''; };
    const title = (meta('og:title') || document.title || '').trim().slice(0, 200);
    const ok = (u) => typeof u === 'string' && /^https:\/\//.test(u) && !/\.(m3u8|mpd)(\?|$)/i.test(u);

    if (/(^|\.)facebook\.com$/.test(host)) {
      for (const k of ['browser_native_hd_url', 'browser_native_sd_url', 'playable_url_quality_hd', 'playable_url']) {
        const m = html.match(new RegExp(k + '\\\\?"\\s*:\\s*\\\\?"(https:[^"\\\\]*(?:\\\\.[^"\\\\]*)*)\\\\?"'));
        if (m && ok(un(m[1]))) return { url: un(m[1]), title, via: k };
      }
      return null; // never the sniffed DASH pieces (no sound)
    }
    if (/(^|\.)tiktok\.com$/.test(host)) {
      try {
        const d = JSON.parse(document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__').textContent);
        const it = d.__DEFAULT_SCOPE__['webapp.video-detail'].itemInfo.itemStruct;
        const u = it.video.playAddr || it.video.downloadAddr;
        if (ok(u)) return { url: u, title: (it.desc || title).slice(0, 200), via: 'tiktok' };
      } catch { /* not a video page / no data */ }
      return null;
    }
    if (/(^|\.)instagram\.com$/.test(host)) {
      const m = html.match(/video_versions\\?"\s*:\s*\[\s*\{[^\]]*?\\?"url\\?"\s*:\s*\\?"(https:[^"\\]*(?:\\.[^"\\]*)*)\\?"/);
      if (m && ok(un(m[1]))) return { url: un(m[1]), title, via: 'instagram' };
    }
    const og = meta('og:video:secure_url') || meta('og:video:url') || meta('og:video');
    if (ok(og)) return { url: og, title, via: 'og:video' };
    for (const v of document.querySelectorAll('video')) {
      const s = v.currentSrc || v.src || '';
      if (ok(s)) return { url: s, title, via: 'video' };
    }
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const list = [].concat(JSON.parse(s.textContent));
        for (const o of list) {
          const u = o && (o.contentUrl || (o.video && o.video.contentUrl));
          if (ok(u)) return { url: u, title: (o.name || title).slice(0, 200), via: 'ld+json' };
        }
      } catch { /* not JSON */ }
    }
    return null;
  }

  /**
   * One complete video file among the sniffer's items, or null. A host that
   * also served an audio-only file streams split DASH pieces: a "video"
   * from there has no sound.
   */
  function pickSniffed(items) {
    const files = (items || []).filter((x) => x && x.kind === 'file' && x.encryption === 'none' && !STREAM.test(x.url));
    const audioHosts = new Set(files.filter((x) => /^audio\//.test(x.mime || '')).map((x) => hostOf(x.url)));
    const videos = files.filter((x) => (/^video\//.test(x.mime || '') || /\.(mp4|webm|mov|m4v)(\?|$)/i.test(x.url))
      && !audioHosts.has(hostOf(x.url)) && !/(^|\.)fbcdn\.net$/.test(hostOf(x.url)));
    if (!videos.length) return null;
    return videos.sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  }

  function safeName(t) {
    return String(t || 'video').replace(/[/\\?%*:|"<>\n\r\t]/g, '-').replace(/\s+/g, ' ').trim().slice(0, 120) || 'video';
  }

  root.vgDirect = { directEligible, vgPageMedia, pickSniffed, safeName, hostOf, FIND_TIMEOUT_MS, SAVE_TIMEOUT_MS };
})(typeof self !== 'undefined' ? self : globalThis);
