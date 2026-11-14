/**
 * VidGrab HLS downloader — runs in offscreen.html.
 * ================================================
 * Downloads an HLS stream in the user's own browser, the way the page's
 * player fetches it, and saves one MP4:
 *   - media playlist (the chosen variant of a master), RFC 8216
 *   - segments fetched in parallel (POOL at a time), each retried
 *   - standard RFC 8216 AES-128-CBC decryption (WebCrypto, cached per key URI)
 *   - fMP4 streams (EXT-X-MAP): init + segments, appended as they are
 *   - MPEG-TS streams (including disguised image/text segment extensions with
 *     fake headers before 0x47 sync bytes): remuxed to MP4 with mux.js
 *   - written to the extension's private disk (OPFS), not held in RAM
 * Never: DRM streams (SAMPLE-AES / Widevine / FairPlay), live streams,
 * or variants whose audio is a separate playlist (sent to VidGrab instead).
 *
 * Messages (chrome.runtime, target 'offscreen'):
 *   in : HLS_START {job}, HLS_CANCEL {id}, HLS_RELEASE {id}
 *   out: HLS_PROGRESS {id, done, total, bytes}, HLS_DONE {id, url, bytes, ext},
 *        HLS_FAILED {id, reason, message}
 */
(function (root) {
  'use strict';

  const POOL = 6;
  const RETRIES = 3;
  const MAX_SEGMENTS = 20000;
  const MAX_FAKE_HEADER_SCAN = 8192;
  const jobs = new Map();          // id -> {cancelled, url (blob), name}

  class JobError extends Error {
    constructor(reason, message) { super(message || reason); this.reason = reason; }
  }

  const hasChrome = typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.sendMessage === 'function';
  const send = (msg) => (hasChrome ? chrome.runtime.sendMessage({ ...msg, target: 'sw' }).catch(() => {}) : Promise.resolve());

  async function ensureHostReferer(url, job) {
    if (!hasChrome || !job || !job.referer) return;
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch { return; }
    if (!host) return;
    if (!job._seenHosts) job._seenHosts = new Set();
    if (job._seenHosts.has(host)) return;
    job._seenHosts.add(host);
    await chrome.runtime.sendMessage({ type: 'VG_ENSURE_REFERER', url, referer: job.referer }).catch(() => {});
  }

  async function fetchBytes(url, range, job) {
    await ensureHostReferer(url, job);
    let lastErr;
    for (let attempt = 0; attempt < RETRIES; attempt++) {
      if (job && job.cancelled) throw new JobError('cancelled');
      try {
        const headers = range ? { Range: `bytes=${range[0]}-${range[0] + range[1] - 1}` } : {};
        const r = await fetch(url, { credentials: 'include', headers, cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return new Uint8Array(await r.arrayBuffer());
      } catch (e) {
        lastErr = e;
        await new Promise((res) => setTimeout(res, 400 * (attempt + 1) ** 2));
      }
    }
    throw new JobError('segment_failed', String(lastErr && lastErr.message || lastErr));
  }

  async function fetchText(url, job) {
    return new TextDecoder().decode(await fetchBytes(url, null, job));
  }

  function resolve(base, ref) { return new URL(ref, base).toString(); }

  function attrs(line) {
    const out = {};
    const body = line.slice(line.indexOf(':') + 1);
    const re = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/g;
    let m;
    while ((m = re.exec(body))) out[m[1]] = m[2].replace(/^"|"$/g, '');
    return out;
  }

  /** "len@offset" -> [offset, len]; a missing offset continues the previous range. */
  function byterange(v, prevEnd) {
    const [len, off] = String(v).split('@').map(Number);
    return [Number.isFinite(off) ? off : prevEnd, len];
  }

  /** RFC 8216 §4.3.2.4: 128-bit big-endian IV from a segment's media sequence number. */
  function seqToIv(seq) {
    const iv = new Uint8Array(16);
    const n = Math.max(0, Math.floor(Number(seq) || 0));
    const hi = Math.floor(n / 0x100000000) >>> 0;
    const lo = n >>> 0;
    iv[8] = (hi >>> 24) & 0xff;
    iv[9] = (hi >>> 16) & 0xff;
    iv[10] = (hi >>> 8) & 0xff;
    iv[11] = hi & 0xff;
    iv[12] = (lo >>> 24) & 0xff;
    iv[13] = (lo >>> 16) & 0xff;
    iv[14] = (lo >>> 8) & 0xff;
    iv[15] = lo & 0xff;
    return iv;
  }

  /** Parses `0x...` hex IV into a 16-byte Uint8Array. */
  function parseHexIv(raw) {
    const m = /^0x([0-9a-f]{1,32})$/i.exec(String(raw || '').trim());
    if (!m) throw new JobError('encrypted', 'IV không hợp lệ.');
    const hex = m[1].padStart(32, '0');
    const out = new Uint8Array(16);
    for (let i = 0; i < 16; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    return out;
  }

  /** Returns null for METHOD=NONE, {uri, iv} for standard AES-128, or throws 'drm'/'encrypted'. */
  function parseKeyAttr(a, baseUrl) {
    const method = (a.METHOD || 'NONE').toUpperCase();
    const fmt = (a.KEYFORMAT || 'identity').toLowerCase();
    if (method === 'NONE') return null;
    if (method.startsWith('SAMPLE-AES') || fmt !== 'identity') {
      throw new JobError('drm', 'Có DRM — không tải được.');
    }
    if (method !== 'AES-128' || !a.URI) {
      throw new JobError('encrypted', 'Video được mã hoá — không tải được.');
    }
    return { uri: resolve(baseUrl, a.URI), iv: a.IV ? parseHexIv(a.IV) : null };
  }

  /** Decrypts an AES-128-CBC segment using WebCrypto (`CryptoKey` or raw 16-byte `Uint8Array`). */
  async function decryptSegment(u8, keyOrRaw, iv) {
    const subtle = root.crypto && root.crypto.subtle;
    if (!subtle) throw new JobError('encrypted', 'WebCrypto unavailable.');
    let ck = keyOrRaw;
    if (keyOrRaw instanceof Uint8Array || ArrayBuffer.isView(keyOrRaw)) {
      if (keyOrRaw.byteLength !== 16) throw new JobError('encrypted', 'Invalid AES-128 key length.');
      ck = await subtle.importKey('raw', keyOrRaw, { name: 'AES-CBC' }, false, ['decrypt']);
    }
    try {
      const plain = await subtle.decrypt({ name: 'AES-CBC', iv }, ck, u8);
      return new Uint8Array(plain);
    } catch {
      throw new JobError('encrypted', 'Không giải mã được đoạn video.');
    }
  }

  const ISO_BMFF_BOXES = new Set(['ftyp', 'styp', 'moof', 'sidx', 'emsg', 'free', 'skip']);

  /** True when `u8` starts with a valid ISO BMFF / fMP4 box (`ftyp`, `styp`, `moof`…) rather than MPEG-TS. */
  function isIsoBmffSegment(u8) {
    if (!u8 || u8.length < 12) return false;
    if (u8[0] === 0x47 && (u8.length < 189 || u8[188] === 0x47)) return false;
    const size = ((u8[0] << 24) >>> 0) + (u8[1] << 16) + (u8[2] << 8) + u8[3];
    if (size !== 1 && size < 8) return false;
    const box = String.fromCharCode(u8[4], u8[5], u8[6], u8[7]);
    return ISO_BMFF_BOXES.has(box);
  }

  /**
   * Strips a fake image/header prefix prepended before 188-byte MPEG-TS packets
   * (e.g. a PNG/JPEG/BMP header before `0x47` sync bytes). Leaves valid TS or
   * fMP4 buffers untouched.
   */
  function stripDisguisedTsPrefix(u8) {
    if (!u8 || u8.length < 188) return u8;
    if (u8[0] === 0x47 && (u8.length < 189 || u8[188] === 0x47)) return u8;
    const limit = Math.min(MAX_FAKE_HEADER_SCAN, u8.length - 188);
    for (let off = 1; off <= limit; off++) {
      if (u8[off] !== 0x47) continue;
      if (off + 376 < u8.length) {
        if (u8[off + 188] === 0x47 && u8[off + 376] === 0x47) return u8.subarray(off);
      } else if (u8[off + 188] === 0x47 && (u8.length - off) % 188 === 0) {
        return u8.subarray(off);
      }
    }
    return u8;
  }

  /** Segments of a media playlist, with its init map and AES-128 keys; throws on DRM/live. */
  function mediaPlan(text, baseUrl) {
    const lines = String(text || '').split(/\r?\n/).map((l) => l.trim());
    if (!lines[0] || !lines[0].startsWith('#EXTM3U')) throw new JobError('not_hls', 'Không phải danh sách HLS.');
    const segs = [];
    let map = null;
    let activeKey = null;
    let mediaSeq = 0;
    let pendingRange = null;
    let prevEnd = 0;
    let ended = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      if (line.startsWith('#EXT-X-MEDIA-SEQUENCE:')) {
        const n = parseInt(line.slice(22).trim(), 10);
        if (Number.isFinite(n) && n >= 0) mediaSeq = n;
      } else if (line.startsWith('#EXT-X-KEY') || line.startsWith('#EXT-X-SESSION-KEY')) {
        activeKey = parseKeyAttr(attrs(line), baseUrl);
      } else if (line.startsWith('#EXT-X-MAP')) {
        const a = attrs(line);
        const key = activeKey ? { uri: activeKey.uri, iv: activeKey.iv || seqToIv(mediaSeq) } : null;
        map = { url: resolve(baseUrl, a.URI), range: a.BYTERANGE ? byterange(a.BYTERANGE, 0) : null, key };
      } else if (line.startsWith('#EXT-X-BYTERANGE')) {
        pendingRange = byterange(line.slice(17), prevEnd);
      } else if (line.startsWith('#EXT-X-ENDLIST')) {
        ended = true;
      } else if (!line.startsWith('#')) {
        const seq = mediaSeq + segs.length;
        const key = activeKey ? { uri: activeKey.uri, iv: activeKey.iv || seqToIv(seq) } : null;
        segs.push({ url: resolve(baseUrl, line), range: pendingRange, key });
        if (pendingRange) prevEnd = pendingRange[0] + pendingRange[1];
        pendingRange = null;
      }
    }
    if (!ended) throw new JobError('live', 'Đang phát trực tiếp — chưa hỗ trợ.');
    if (!segs.length) throw new JobError('empty', 'Danh sách không có đoạn nào.');
    if (segs.length > MAX_SEGMENTS) throw new JobError('too_long', 'Video quá dài để tải trong trình duyệt.');
    const fmp4 = !!map || /\.(m4s|mp4|m4f|cmfv)(\?|$)/i.test(segs[0].url);
    return { segs, map, fmp4 };
  }

  /** The variant to download (by height) and whether its audio is a separate playlist. */
  async function pickMedia(job) {
    const text = await fetchText(job.url, job);
    if (!/#EXT-X-STREAM-INF/.test(text)) return { url: job.url, text };
    const lines = text.split(/\r?\n/).map((l) => l.trim());
    const audioWithUri = new Set();
    for (const l of lines) {
      if (l.startsWith('#EXT-X-SESSION-KEY')) parseKeyAttr(attrs(l), job.url);
    }
    const variants = [];
    lines.forEach((line, i) => {
      if (line.startsWith('#EXT-X-MEDIA')) {
        const a = attrs(line);
        if ((a.TYPE || '').toUpperCase() === 'AUDIO' && a.URI) audioWithUri.add(a['GROUP-ID']);
      } else if (line.startsWith('#EXT-X-STREAM-INF')) {
        const a = attrs(line);
        let j = i + 1;
        while (j < lines.length && (!lines[j] || lines[j].startsWith('#'))) j++;
        if (j < lines.length) {
          variants.push({ url: resolve(job.url, lines[j]), height: Number((a.RESOLUTION || '').split('x')[1]) || 0,
                          bandwidth: Number(a.BANDWIDTH) || 0, audio: a.AUDIO || '' });
        }
      }
    });
    if (!variants.length) throw new JobError('not_hls', 'Không thấy chất lượng nào trong danh sách.');
    variants.sort((a, b) => b.height - a.height || b.bandwidth - a.bandwidth);
    const v = (job.height && variants.find((x) => x.height === job.height)) || variants[0];
    if (v.audio && audioWithUri.has(v.audio)) {
      throw new JobError('separate_audio', 'Hình và tiếng nằm riêng — hãy dùng "App" (app VidGrab trên máy) hoặc "Qua VidGrab".');
    }
    return { url: v.url, text: await fetchText(v.url, job), height: v.height };
  }

  /** Writes chunks in order to an OPFS file. */
  async function opfsWriter(name) {
    const dir = await navigator.storage.getDirectory();
    const handle = await dir.getFileHandle(name, { create: true });
    const w = await handle.createWritable();
    let bytes = 0;
    return {
      async write(u8) { if (u8 && u8.byteLength) { await w.write(u8); bytes += u8.byteLength; } },
      async close() { await w.close(); return handle.getFile(); },
      async abort() { try { await w.abort(); } catch { /* closed */ } try { await dir.removeEntry(name); } catch { /* gone */ } },
      get bytes() { return bytes; },
    };
  }

  /** TS -> fragmented MP4 through mux.js; output is written as it comes. */
  function tsRemuxer(writer) {
    const t = new root.muxjs.mp4.Transmuxer({ remux: true });
    let initDone = false;
    let pending = Promise.resolve();
    let produced = 0;
    t.on('data', (seg) => {
      const parts = [];
      if (!initDone) { parts.push(seg.initSegment); initDone = true; }
      parts.push(seg.data);
      produced += 1;
      pending = pending.then(() => Promise.all(parts.map((p) => writer.write(new Uint8Array(p)))));
    });
    return {
      async push(u8) { t.push(u8); t.flush(); await pending; },
      async end() { await pending; if (!produced) throw new JobError('remux_failed', 'Không chuyển được sang MP4.'); },
    };
  }

  async function run(job) {
    const state = { cancelled: false };
    jobs.set(job.id, state);
    job.cancelled = false;
    Object.defineProperty(job, 'cancelled', { get: () => state.cancelled });
    const fileName = `hls-${job.id}.mp4`;
    const keyCache = new Map();
    const loadCryptoKey = (uri) => {
      if (!keyCache.has(uri)) {
        keyCache.set(uri, fetchBytes(uri, null, job).then((raw) => {
          if (raw.byteLength !== 16) throw new JobError('encrypted', 'Invalid AES-128 key.');
          return root.crypto.subtle.importKey('raw', raw, { name: 'AES-CBC' }, false, ['decrypt']);
        }));
      }
      return keyCache.get(uri);
    };
    const loadSegment = async (item) => {
      let u8 = await fetchBytes(item.url, item.range, job);
      if (item.key) {
        const ck = await loadCryptoKey(item.key.uri);
        u8 = await decryptSegment(u8, ck, item.key.iv);
      }
      return u8;
    };

    let writer = null;
    try {
      const media = await pickMedia(job);
      const plan = mediaPlan(media.text, media.url);
      writer = await opfsWriter(fileName);
      const total = plan.segs.length;
      send({ type: 'HLS_PROGRESS', id: job.id, done: 0, total, bytes: 0, height: media.height || null });

      const rawSink = { push: (u8) => writer.write(u8), end: async () => {} };
      let fmp4Mode = plan.fmp4;
      let sink = fmp4Mode ? rawSink : null;
      if (plan.map) {
        fmp4Mode = true;
        sink = rawSink;
        await sink.push(await loadSegment(plan.map));
      }

      // parallel fetch + decrypt, in-order write: at most POOL segments in flight or waiting
      const ready = new Map();
      let next = 0;          // next index to start
      let written = 0;       // next index to write
      let failure = null;
      await new Promise((done) => {
        const pump = () => {
          if (failure || state.cancelled) { done(); return; }
          while (next < total && next - written < POOL) {
            const i = next++;
            loadSegment(plan.segs[i]).then((u8) => {
              ready.set(i, u8);
              drain();
            }, (e) => { failure = failure || e; done(); });
          }
          if (written >= total) done();
        };
        let draining = false;
        const drain = async () => {
          if (draining) return;
          draining = true;
          try {
            while (ready.has(written)) {
              const rawU8 = ready.get(written);
              ready.delete(written);
              if (!sink) {
                fmp4Mode = fmp4Mode || isIsoBmffSegment(rawU8);
                sink = fmp4Mode ? rawSink : tsRemuxer(writer);
              }
              const u8 = fmp4Mode ? rawU8 : stripDisguisedTsPrefix(rawU8);
              await sink.push(u8);
              written += 1;
              if (written % 3 === 0 || written === total) {
                send({ type: 'HLS_PROGRESS', id: job.id, done: written, total, bytes: writer.bytes });
              }
            }
          } catch (e) { failure = failure || e; }
          draining = false;
          pump();
        };
        pump();
      });
      if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
      if (failure) throw failure;
      await sink.end();
      const file = await writer.close();
      writer = null;
      const url = URL.createObjectURL(file);
      jobs.set(job.id, { ...state, url, name: fileName });
      send({ type: 'HLS_DONE', id: job.id, url, bytes: file.size, ext: 'mp4' });
    } catch (e) {
      if (writer) await writer.abort();
      jobs.delete(job.id);
      send({ type: 'HLS_FAILED', id: job.id, reason: e.reason || 'error',
             message: e instanceof JobError ? e.message : String(e && e.message || e).slice(0, 120) });
    }
  }

  async function release(id) {
    const j = jobs.get(id);
    jobs.delete(id);
    if (!j) return;
    if (j.url) URL.revokeObjectURL(j.url);
    if (j.name && typeof navigator !== 'undefined' && navigator.storage && navigator.storage.getDirectory) {
      try { await (await navigator.storage.getDirectory()).removeEntry(j.name); } catch { /* gone */ }
    }
  }

  const exported = { mediaPlan, byterange, seqToIv, parseHexIv, decryptSegment, stripDisguisedTsPrefix, isIsoBmffSegment };
  root.__vgHls = exported;
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;

  if (hasChrome && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.target !== 'offscreen') return false;
      if (msg.type === 'HLS_START') { run(msg.job); sendResponse({ ok: true }); }
      else if (msg.type === 'HLS_CANCEL') { const j = jobs.get(msg.id); if (j) j.cancelled = true; sendResponse({ ok: true }); }
      else if (msg.type === 'HLS_RELEASE') { release(msg.id).then(() => sendResponse({ ok: true })); return true; }
      else if (msg.type === 'HLS_PING') { sendResponse({ ok: true, jobs: jobs.size }); }
      return false;
    });
  }

  if (typeof navigator !== 'undefined' && navigator.storage && typeof navigator.storage.getDirectory === 'function') {
    // leftovers of a run the browser closed mid-way
    navigator.storage.getDirectory().then(async (dir) => {
      for await (const [name] of dir.entries()) {
        if (/^hls-.*\.mp4$/.test(name) && !jobs.size) { try { await dir.removeEntry(name); } catch { /* busy */ } }
      }
    }).catch(() => {});
  }
})(typeof self !== 'undefined' ? self : globalThis);
