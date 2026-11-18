/**
 * Multi-range parallel fetch tests for chrome-extension/blob-fetch.js
 * Run with: node chrome-extension/tests/test_multi_range_fetch.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');

const src = fs.readFileSync(path.join(__dirname, '..', 'blob-fetch.js'), 'utf8');
const ctx = {
  console,
  Blob,
  Uint8Array,
  URL,
  globalThis: null,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const BF = ctx.vgBlobFetch;

(async () => {
  // 1. parseContentRangeTotal & planRanges edge cases
  assert.equal(BF.parseContentRangeTotal('bytes 0-0/12582912'), 12582912);
  assert.equal(BF.parseContentRangeTotal('BYTES 0-100/4096'), 4096);
  assert.equal(BF.parseContentRangeTotal('bytes 0-0/*'), null);
  assert.equal(BF.parseContentRangeTotal(''), null);

  assert.deepEqual(JSON.parse(JSON.stringify(BF.planRanges(10, 4))), [
    { start: 0, end: 3 },
    { start: 4, end: 7 },
    { start: 8, end: 9 },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(BF.planRanges(0, 4))), []);

  // 2. Parallel 12 MiB download across 3 x 4 MiB chunks with out-of-order completion
  const TOTAL = 12 * 1024 * 1024;
  const CHUNK = 4 * 1024 * 1024;
  const payload = new Uint8Array(TOTAL);
  for (let i = 0; i < TOTAL; i += 4096) {
    payload[i] = (i >> 12) & 0xff;
    payload[i + 1] = ((i >> 12) * 7) & 0xff;
  }

  let active = 0;
  let maxActive = 0;
  let aborted = false;

  const mockFetch206 = async (_url, init = {}) => {
    const rangeHeader = init.headers?.Range || init.headers?.range || '';
    if (!rangeHeader) {
      return {
        status: 200,
        ok: true,
        headers: new Map(),
        blob: async () => new Blob([payload]),
      };
    }
    const m = rangeHeader.match(/^bytes=(\d+)-(\d+)$/);
    const start = Number(m[1]);
    const end = Number(m[2]);

    // Probe request
    if (start === 0 && end === 0) {
      return {
        status: 206,
        ok: true,
        headers: new Headers({ 'content-range': `bytes 0-0/${TOTAL}` }),
        body: { cancel: async () => {} },
      };
    }

    active += 1;
    if (active > maxActive) maxActive = active;
    // Invert delay so earlier chunk finishes after later chunk (proves out-of-order positional writes)
    await new Promise((r) => setTimeout(r, start === 0 ? 35 : 10));
    active -= 1;

    const slice = payload.slice(start, end + 1);
    return {
      status: 206,
      ok: true,
      headers: new Headers({ 'content-range': `bytes ${start}-${end}/${TOTAL}` }),
      arrayBuffer: async () => slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength),
    };
  };

  const memChunks = new Uint8Array(TOTAL);
  const resParallel = await BF.parallelRangeDownload('https://cdn.example/video.mp4', {
    fetchImpl: mockFetch206,
    pool: 3,
    chunkSize: CHUNK,
    minBytes: CHUNK,
    createSink: async () => ({
      write: async (offset, bytes) => { memChunks.set(bytes, offset); },
      finalize: async () => ({ blob: new Blob([memChunks]), opfsName: 'mock-opfs.bin' }),
      abort: async () => { aborted = true; },
    }),
  });

  assert.equal(resParallel.ok, true);
  assert.equal(resParallel.mode, 'parallel');
  assert.equal(resParallel.bytes, TOTAL);
  assert.equal(aborted, false);
  assert.ok(maxActive > 1, `expected concurrent workers > 1, got ${maxActive}`);
  const outBytes = new Uint8Array(await resParallel.blob.arrayBuffer());
  assert.equal(outBytes.byteLength, TOTAL);
  for (let i = 0; i < TOTAL; i += 4096) {
    assert.equal(outBytes[i], payload[i], `mismatch at ${i}`);
    assert.equal(outBytes[i + 1], payload[i + 1], `mismatch at ${i + 1}`);
  }
  console.log(`  ✓ Parallel 12 MiB multi-range verified (maxConcurrent=${maxActive})`);

  // 3. Server ignores Range (returns 200 OK) -> uses single response without crash
  const smallPayload = new Uint8Array(1024).fill(42);
  let probe200Calls = 0;
  const res200 = await BF.parallelRangeDownload('https://cdn.example/no-range.mp4', {
    fetchImpl: async () => {
      probe200Calls += 1;
      return {
        status: 200,
        ok: true,
        headers: new Headers(),
        blob: async () => new Blob([smallPayload]),
      };
    },
  });
  assert.equal(res200.ok, true);
  assert.equal(res200.mode, 'single');
  assert.equal(res200.bytes, 1024);
  assert.equal(probe200Calls, 1, 'should consume 200 probe once without re-fetching');
  console.log('  ✓ Fallback on HTTP 200 (no Range support) verified');

  // 4. Small file (< MIN_PARALLEL_BYTES) -> falls back to single fetch
  const tinyPayload = new Uint8Array(2048).fill(9);
  const resSmall = await BF.parallelRangeDownload('https://cdn.example/tiny.mp4', {
    fetchImpl: async (_url, init = {}) => {
      if (init.headers?.Range === 'bytes=0-0') {
        return {
          status: 206,
          ok: true,
          headers: new Headers({ 'content-range': 'bytes 0-0/2048' }),
          body: { cancel: async () => {} },
        };
      }
      return {
        status: 200,
        ok: true,
        headers: new Headers(),
        blob: async () => new Blob([tinyPayload]),
      };
    },
  });
  assert.equal(resSmall.ok, true);
  assert.equal(resSmall.mode, 'single');
  assert.equal(resSmall.bytes, 2048);
  console.log('  ✓ Small file fallback verified');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
