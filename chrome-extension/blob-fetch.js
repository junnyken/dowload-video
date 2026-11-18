// 5.22.1+: Files whose CDN wants a Referer (Douyin) or benefit from parallel HTTP Range
// transfers (multi-range OPFS streaming, like desktop chunking). chrome.downloads cannot
// send a Referer (Chrome refuses the header) and its requests pass no extension rule, but
// offscreen.html's fetch gets declarativeNetRequest Referer rules from background.js.
((root) => {
  'use strict';

  const POOL = 6;
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

    const singleFetch = async () => {
      const r = await fetchImpl(url, { credentials: 'omit' });
      if (!r.ok) return { ok: false, status: r.status };
      const blob = await r.blob();
      return { ok: true, blob, bytes: blob.size, opfsName: null, mode: 'single' };
    };

    let probe;
    try {
      probe = await fetchImpl(url, {
        method: 'GET',
        headers: { Range: 'bytes=0-0' },
        credentials: 'omit',
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
              credentials: 'omit',
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

  root.vgBlobFetch = {
    POOL,
    CHUNK_BYTES,
    MIN_PARALLEL_BYTES,
    parseContentRangeTotal,
    planRanges,
    parallelRangeDownload,
  };

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    const urls = new Map();
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.target !== 'offscreen') return false;
      if (msg.type === 'BLOB_FETCH') {
        (async () => {
          const res = await parallelRangeDownload(msg.url);
          if (!res.ok) return { ok: false, status: res.status };
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
