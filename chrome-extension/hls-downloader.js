/**
 * VidGrab HLS downloader (5.13.0, 22/10/2026) — runs in offscreen.html.
 * =====================================================================
 * Downloads an HLS stream in the user's own browser, the way the page's
 * player fetches it, and saves one MP4:
 *   - media playlist (the chosen variant of a master), RFC 8216
 *   - segments fetched in parallel (POOL at a time), each retried
 *   - fMP4 streams (EXT-X-MAP): init + segments, appended as they are
 *   - MPEG-TS streams: remuxed to MP4 with mux.js (Apache-2.0, lib/)
 *   - written to the extension's private disk (OPFS), not held in RAM
 * Never: encrypted (EXT-X-KEY other than NONE) or DRM streams, live streams,
 * or variants whose audio is a separate playlist (sent to VidGrab instead).
 *
 * Messages (chrome.runtime, target 'offscreen'):
 *   in : HLS_START {job}, HLS_CANCEL {id}, HLS_RELEASE {id}
 *   out: HLS_PROGRESS {id, done, total, bytes}, HLS_DONE {id, url, bytes, ext},
 *        HLS_FAILED {id, reason, message}
 */
(function () {
  'use strict';

  const POOL = 6;
  const RETRIES = 3;
  const MAX_SEGMENTS = 20000;
  const jobs = new Map();          // id -> {cancelled, url (blob), name}

  class JobError extends Error {
    constructor(reason, message) { super(message || reason); this.reason = reason; }
  }

  const send = (msg) => chrome.runtime.sendMessage({ ...msg, target: 'sw' }).catch(() => {});

  async function fetchBytes(url, range, job) {
    let lastErr;
    for (let attempt = 0; attempt < RETRIES; attempt++) {
      if (job.cancelled) throw new JobError('cancelled');
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

  /** Segments of a media playlist, with its init map; throws on what we never download. */
  function mediaPlan(text, baseUrl) {
    const lines = text.split(/\r?\n/).map((l) => l.trim());
    if (!lines[0] || !lines[0].startsWith('#EXTM3U')) throw new JobError('not_hls', 'Không phải danh sách HLS.');
    const segs = [];
    let map = null;
    let pendingRange = null;
    let prevEnd = 0;
    let ended = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      if (line.startsWith('#EXT-X-KEY')) {
        const a = attrs(line);
        if ((a.METHOD || 'NONE').toUpperCase() !== 'NONE') throw new JobError('encrypted', 'Video được mã hoá — không tải được.');
      } else if (line.startsWith('#EXT-X-MAP')) {
        const a = attrs(line);
        map = { url: resolve(baseUrl, a.URI), range: a.BYTERANGE ? byterange(a.BYTERANGE, 0) : null };
      } else if (line.startsWith('#EXT-X-BYTERANGE')) {
        pendingRange = byterange(line.slice(17), prevEnd);
      } else if (line.startsWith('#EXT-X-ENDLIST')) {
        ended = true;
      } else if (!line.startsWith('#')) {
        segs.push({ url: resolve(baseUrl, line), range: pendingRange });
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
    if (/#EXT-X-SESSION-KEY/.test(text)) {
      const k = attrs(lines.find((l) => l.startsWith('#EXT-X-SESSION-KEY')));
      if ((k.METHOD || 'NONE').toUpperCase() !== 'NONE') throw new JobError('encrypted', 'Video được mã hoá — không tải được.');
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
    const t = new self.muxjs.mp4.Transmuxer({ remux: true });
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
    let writer = null;
    try {
      const media = await pickMedia(job);
      const plan = mediaPlan(media.text, media.url);
      writer = await opfsWriter(fileName);
      const total = plan.segs.length;
      send({ type: 'HLS_PROGRESS', id: job.id, done: 0, total, bytes: 0, height: media.height || null });

      const sink = plan.fmp4 ? { push: (u8) => writer.write(u8), end: async () => {} } : tsRemuxer(writer);
      if (plan.map) await sink.push(await fetchBytes(plan.map.url, plan.map.range, job));

      // parallel fetch, in-order write: at most POOL segments in flight or waiting
      const ready = new Map();
      let next = 0;          // next index to start
      let written = 0;       // next index to write
      let failure = null;
      await new Promise((done) => {
        const pump = () => {
          if (failure || state.cancelled) { done(); return; }
          while (next < total && next - written < POOL) {
            const i = next++;
            fetchBytes(plan.segs[i].url, plan.segs[i].range, job).then((u8) => {
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
              const u8 = ready.get(written);
              ready.delete(written);
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
             message: e instanceof JobError ? e.message : `Lỗi: ${String(e && e.message || e).slice(0, 120)}` });
    }
  }

  async function release(id) {
    const j = jobs.get(id);
    jobs.delete(id);
    if (!j) return;
    if (j.url) URL.revokeObjectURL(j.url);
    if (j.name) {
      try { await (await navigator.storage.getDirectory()).removeEntry(j.name); } catch { /* gone */ }
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.target !== 'offscreen') return false;
    if (msg.type === 'HLS_START') { run(msg.job); sendResponse({ ok: true }); }
    else if (msg.type === 'HLS_CANCEL') { const j = jobs.get(msg.id); if (j) j.cancelled = true; sendResponse({ ok: true }); }
    else if (msg.type === 'HLS_RELEASE') { release(msg.id).then(() => sendResponse({ ok: true })); return true; }
    else if (msg.type === 'HLS_PING') { sendResponse({ ok: true, jobs: jobs.size }); }
    return false;
  });

  // leftovers of a run the browser closed mid-way
  navigator.storage.getDirectory().then(async (dir) => {
    for await (const [name] of dir.entries()) {
      if (/^hls-.*\.mp4$/.test(name) && !jobs.size) { try { await dir.removeEntry(name); } catch { /* busy */ } }
    }
  }).catch(() => {});

  self.__vgHls = { mediaPlan, byterange };   // for tests
})();
