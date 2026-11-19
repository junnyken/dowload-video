// 5.22.1+: Files whose CDN wants a Referer (Douyin) or benefit from parallel HTTP Range
// transfers (multi-range OPFS streaming, like desktop chunking). chrome.downloads cannot
// send a Referer (Chrome refuses the header) and its requests pass no extension rule, but
// offscreen.html's fetch gets declarativeNetRequest Referer rules from background.js.
((root) => {
  'use strict';

  const POOL = 8;
  const CHUNK_BYTES = 4 * 1024 * 1024;
  const MIN_PARALLEL_BYTES = 4 * 1024 * 1024;
  const MAX_PARALLEL_BYTES = 1536 * 1024 * 1024;
  const MAX_MEM_PARALLEL_BYTES = 128 * 1024 * 1024;

  function parseContentRangeTotal(header) {
    if (typeof header !== 'string') return null;
    const m = header.trim().match(/^bytes\s+\d+-\d+\/(\d+)$/i);
    if (!m) return null;
    const total = Number(m[1]);
    return Number.isSafeInteger(total) && total > 0 ? total : null;
  }

  function planRanges(total, chunkSize = CHUNK_BYTES) {
    if (!Number.isSafeInteger(total) || total <= 0 || !Number.isSafeInteger(chunkSize) || chunkSize <= 0) {
      return [];
    }
    const ranges = [];
    for (let start = 0; start < total; start += chunkSize) {
      ranges.push({ start, end: Math.min(total - 1, start + chunkSize - 1) });
    }
    return ranges;
  }

  async function createBrowserSink(total) {
    if (typeof navigator !== 'undefined' && navigator.storage && typeof navigator.storage.getDirectory === 'function') {
      const dir = await navigator.storage.getDirectory();
      const opfsName = `vg-blob-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.bin`;
      const handle = await dir.getFileHandle(opfsName, { create: true });
      const writable = await handle.createWritable();
      return {
        async write(offset, uint8) {
          await writable.write({ type: 'write', position: offset, data: uint8 });
        },
        async finalize() {
          await writable.close();
          const file = await handle.getFile();
          return { blob: file, opfsName };
        },
        async abort() {
          await writable.abort().catch(() => {});
          await dir.removeEntry(opfsName).catch(() => {});
        },
      };
    }

    if (total <= MAX_MEM_PARALLEL_BYTES) {
      const buf = new Uint8Array(total);
      return {
        async write(offset, uint8) {
          buf.set(uint8, offset);
        },
        async finalize() {
          return { blob: new Blob([buf]), opfsName: null };
        },
        async abort() {
          // Memory buffer is collected automatically
        },
      };
    }
    return null;
  }

  async function parallelRangeDownload(url, opts = {}) {
    const fetchImpl = opts.fetchImpl || globalThis.fetch.bind(globalThis);
    const pool = Math.max(1, opts.pool || POOL);
    const chunkSize = opts.chunkSize || CHUNK_BYTES;
    const minBytes = opts.minBytes ?? MIN_PARALLEL_BYTES;
    const maxBytes = opts.maxBytes ?? MAX_PARALLEL_BYTES;
    const createSink = opts.createSink || createBrowserSink;
    const credentials = opts.credentials || 'omit';

    const singleFetch = async () => {
      const r = await fetchImpl(url, { credentials });
      if (!r.ok) return { ok: false, status: r.status };
      const blob = await r.blob();
      return { ok: true, blob, bytes: blob.size, opfsName: null, mode: 'single' };
    };

    let probe;
    try {
      probe = await fetchImpl(url, {
        method: 'GET',
        headers: { Range: 'bytes=0-0' },
        credentials,
      });
    } catch {
      return singleFetch();
    }

    // Server ignored Range and already sent full 200 payload — consume once without re-fetching.
    if (probe.status === 200) {
      const blob = await probe.blob();
      return { ok: true, blob, bytes: blob.size, opfsName: null, mode: 'single' };
    }

    const contentRange = probe.headers?.get?.('content-range') || '';
    if (probe.body && typeof probe.body.cancel === 'function') {
      await probe.body.cancel().catch(() => {});
    }

    const total = probe.status === 206 ? parseContentRangeTotal(contentRange) : null;
    if (!total || total < minBytes || total > maxBytes) {
      return singleFetch();
    }

    const ranges = planRanges(total, chunkSize);
    const sink = await createSink(total);
    if (!sink) {
      return singleFetch();
    }

    let nextIdx = 0;
    let forceSingle = false;
    let failed = null;

    const worker = async () => {
      while (!forceSingle && !failed) {
        const idx = nextIdx++;
        if (idx >= ranges.length) return;
        const range = ranges[idx];
        const expectedLen = range.end - range.start + 1;

        let lastErr = null;
        let wrote = false;
        for (let attempt = 0; attempt < 2; attempt++) {
          if (forceSingle || failed) return;
          try {
            const res = await fetchImpl(url, {
              method: 'GET',
              headers: { Range: `bytes=${range.start}-${range.end}` },
              credentials,
            });
            if (res.status === 200) {
              if (res.body && typeof res.body.cancel === 'function') {
                await res.body.cancel().catch(() => {});
              }
              forceSingle = true;
              return;
            }
            if (res.status !== 206) {
              throw new Error(`HTTP ${res.status}`);
            }
            const ab = await res.arrayBuffer();
            const bytes = new Uint8Array(ab);
            if (bytes.byteLength !== expectedLen) {
              throw new Error(`Range length mismatch: expected ${expectedLen}, got ${bytes.byteLength}`);
            }
            await sink.write(range.start, bytes);
            wrote = true;
            break;
          } catch (err) {
            lastErr = err;
          }
        }
        if (!wrote && !forceSingle) {
          failed = lastErr || new Error('Range chunk failed');
          return;
        }
      }
    };

    try {
      const workers = Array.from({ length: Math.min(pool, ranges.length) }, () => worker());
      await Promise.all(workers);
      if (forceSingle) {
        await sink.abort?.();
        return singleFetch();
      }
      if (failed) {
        await sink.abort?.();
        return singleFetch();
      }
      const out = await sink.finalize();
      return {
        ok: true,
        blob: out.blob,
        bytes: out.blob?.size ?? total,
        opfsName: out.opfsName || null,
        mode: 'parallel',
      };
    } catch {
      await sink.abort?.();
      return singleFetch();
    }
  }

  function hexToBytes(hex) {
    const clean = String(hex || '').trim().replace(/^0x/i, '');
    const out = new Uint8Array(Math.floor(clean.length / 2));
    for (let i = 0; i < out.length; i++) {
      out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    }
    return out;
  }

  /** Computes Mega's 16-byte AES-CTR counter at a 16-byte-aligned byte offset. */
  function counterAtOffset(iv8, byteOffset) {
    const counter = new Uint8Array(16);
    counter.set(iv8.subarray(0, 8), 0);
    const blockIdx = BigInt(Math.floor(byteOffset / 16));
    const view = new DataView(counter.buffer);
    view.setBigUint64(8, blockIdx, false);
    return counter;
  }

  /**
   * Downloads and decrypts a Mega.nz AES-128-CTR stream in parallel 4 MB chunks
   * directly into OPFS (zero full-file RAM buffering), using the user's own IP.
   */
  async function decryptMegaAesCtr(url, opts = {}) {
    const fetchImpl = opts.fetchImpl || globalThis.fetch.bind(globalThis);
    const subtle = opts.subtle || (globalThis.crypto && globalThis.crypto.subtle);
    if (!subtle) return { ok: false, error: 'webcrypto_unavailable' };
    const aesKeyBytes = opts.aesKey instanceof Uint8Array ? opts.aesKey : hexToBytes(opts.aesKeyHex);
    const iv8 = opts.iv instanceof Uint8Array ? opts.iv : hexToBytes(opts.ivHex);
    if (aesKeyBytes.byteLength !== 16 || iv8.byteLength < 8) {
      return { ok: false, error: 'invalid_mega_key' };
    }
    const cryptoKey = await subtle.importKey('raw', aesKeyBytes, { name: 'AES-CTR' }, false, ['decrypt']);
    const pool = Math.max(1, opts.pool || 4);
    const chunkSize = (opts.chunkSize || CHUNK_BYTES) - ((opts.chunkSize || CHUNK_BYTES) % 16);
    const createSink = opts.createSink || createBrowserSink;

    let total = Number(opts.totalBytes || 0);
    if (!total) {
      const probe = await fetchImpl(url, { method: 'GET', headers: { Range: 'bytes=0-0' }, credentials: 'omit' });
      if (probe.status === 509) return { ok: false, status: 509, error: 'share_quota_exceeded' };
      if (probe.status === 206) {
        total = parseContentRangeTotal(probe.headers?.get?.('content-range') || '') || 0;
        if (probe.body?.cancel) await probe.body.cancel().catch(() => {});
      } else if (probe.status === 200) {
        const enc = new Uint8Array(await probe.arrayBuffer());
        const plain = new Uint8Array(await subtle.decrypt({ name: 'AES-CTR', counter: counterAtOffset(iv8, 0), length: 64 }, cryptoKey, enc));
        return { ok: true, blob: new Blob([plain], { type: 'video/mp4' }), bytes: plain.byteLength, opfsName: null, mode: 'mega_single' };
      } else {
        return { ok: false, status: probe.status };
      }
    }
    if (!total) return { ok: false, error: 'unknown_size' };

    const ranges = planRanges(total, chunkSize);
    const sink = await createSink(total);
    if (!sink) return { ok: false, error: 'sink_unavailable' };

    let nextIdx = 0;
    let failed = null;
    const worker = async () => {
      while (!failed) {
        const idx = nextIdx++;
        if (idx >= ranges.length) return;
        const r = ranges[idx];
        const expected = r.end - r.start + 1;
        for (let attempt = 0; attempt < 2; attempt++) {
          if (failed) return;
          try {
            const res = await fetchImpl(url, {
              method: 'GET',
              headers: { Range: `bytes=${r.start}-${r.end}` },
              credentials: 'omit',
            });
            if (res.status === 509) throw new Error('share_quota_exceeded');
            if (res.status !== 206 && res.status !== 200) throw new Error(`HTTP ${res.status}`);
            const enc = new Uint8Array(await res.arrayBuffer());
            if (enc.byteLength !== expected) throw new Error(`short read ${enc.byteLength} != ${expected}`);
            const counter = counterAtOffset(iv8, r.start);
            const plain = new Uint8Array(await subtle.decrypt({ name: 'AES-CTR', counter, length: 64 }, cryptoKey, enc));
            await sink.write(r.start, plain);
            break;
          } catch (err) {
            if (attempt === 1) { failed = err; return; }
          }
        }
      }
    };

    try {
      await Promise.all(Array.from({ length: Math.min(pool, ranges.length) }, () => worker()));
      if (failed) {
        await sink.abort?.();
        return { ok: false, error: String(failed.message || failed) };
      }
      const out = await sink.finalize();
      return { ok: true, blob: out.blob, bytes: out.blob?.size ?? total, opfsName: out.opfsName || null, mode: 'mega_parallel' };
    } catch (e) {
      await sink.abort?.();
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  root.vgBlobFetch = {
    POOL,
    CHUNK_BYTES,
    MIN_PARALLEL_BYTES,
    parseContentRangeTotal,
    planRanges,
    parallelRangeDownload,
    hexToBytes,
    counterAtOffset,
    decryptMegaAesCtr,
  };

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    const urls = new Map();
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.target !== 'offscreen') return false;
      if (msg.type === 'BLOB_FETCH') {
        (async () => {
          const res = await parallelRangeDownload(msg.url, {
            ...(msg.credentials ? { credentials: msg.credentials } : {}),
            ...(msg.pool ? { pool: msg.pool } : {}),
          });
          if (!res.ok) return { ok: false, status: res.status };
          const url = URL.createObjectURL(res.blob);
          urls.set(url, res.opfsName || null);
          return { ok: true, url, bytes: res.bytes, mode: res.mode };
        })().then(sendResponse, (e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
        return true;
      }
      if (msg.type === 'MEGA_DECRYPT_FETCH') {
        (async () => {
          const res = await decryptMegaAesCtr(msg.url, {
            aesKeyHex: msg.aesKeyHex,
            ivHex: msg.ivHex,
            totalBytes: msg.totalBytes,
            pool: msg.pool || 4,
          });
          if (!res.ok) return { ok: false, status: res.status, error: res.error };
          const url = URL.createObjectURL(res.blob);
          urls.set(url, res.opfsName || null);
          return { ok: true, url, bytes: res.bytes, mode: res.mode };
        })().then(sendResponse, (e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
        return true;
      }
      if (msg.type === 'BLOB_RELEASE') {
        if (urls.has(msg.url)) {
          const opfsName = urls.get(msg.url);
          urls.delete(msg.url);
          URL.revokeObjectURL(msg.url);
          if (opfsName && typeof navigator !== 'undefined' && navigator.storage?.getDirectory) {
            navigator.storage.getDirectory()
              .then((dir) => dir.removeEntry(opfsName))
              .catch(() => {});
          }
        }
        sendResponse({ ok: true, left: urls.size });
      }
      return false;
    });
  }
})(typeof self !== 'undefined' ? self : globalThis);
