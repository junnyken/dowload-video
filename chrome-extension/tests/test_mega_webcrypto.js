/**
 * Verifies WebCrypto AES-128-CTR multi-chunk decryption in blob-fetch.js
 * against Node's OpenSSL AES-128-CTR cipher (bit-for-bit SHA-256 parity).
 */
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const code = fs.readFileSync(path.join(__dirname, '..', 'blob-fetch.js'), 'utf8');
const sandbox = {
  console,
  globalThis: null,
  URL,
  Blob,
  Uint8Array,
  DataView,
  BigInt,
  crypto: crypto.webcrypto,
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.runInNewContext(code, sandbox);

const { decryptMegaAesCtr, counterAtOffset } = sandbox.vgBlobFetch;

async function main() {
  // 1. Create a 96 KiB synthetic MP4 payload (valid ftyp header + random bytes)
  const header = Buffer.from('\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom', 'binary');
  const body = crypto.randomBytes(96 * 1024 - header.length);
  const plaintext = Buffer.concat([header, body]);
  const expectedSha = crypto.createHash('sha256').update(plaintext).digest('hex');

  // 2. Encrypt with Mega's exact AES-128-CTR scheme (iv = 8 random bytes + 8 zero bytes)
  const aesKey = crypto.randomBytes(16);
  const iv8 = crypto.randomBytes(8);
  const counterInit = Buffer.concat([iv8, Buffer.alloc(8)]);
  const cipher = crypto.createCipheriv('aes-128-ctr', aesKey, counterInit);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);

  // Verify counterAtOffset math at 32 KiB boundary
  const c0 = counterAtOffset(new Uint8Array(iv8), 0);
  const c32k = counterAtOffset(new Uint8Array(iv8), 32768);
  assert.strictEqual(new DataView(c0.buffer).getBigUint64(8, false), 0n, 'offset 0 -> block 0');
  assert.strictEqual(new DataView(c32k.buffer).getBigUint64(8, false), 2048n, 'offset 32768 -> block 2048');

  // 3. Mock HTTP Range fetch serving 32 KiB encrypted slices
  let rangeCalls = 0;
  const mockFetch = async (_url, opts = {}) => {
    const h = opts.headers && opts.headers.Range;
    if (h === 'bytes=0-0') {
      return {
        status: 206,
        headers: { get: (k) => (k.toLowerCase() === 'content-range' ? `bytes 0-0/${ciphertext.length}` : null) },
        body: { cancel: async () => {} },
      };
    }
    const m = /^bytes=(\d+)-(\d+)$/.exec(h || '');
    assert(m, `Expected Range header, got ${h}`);
    const s = Number(m[1]);
    const e = Number(m[2]);
    rangeCalls++;
    const slice = ciphertext.subarray(s, e + 1);
    return {
      status: 206,
      headers: { get: () => null },
      arrayBuffer: async () => slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength),
    };
  };

  // In-memory sink (simulates OPFS random-offset writes)
  const outBuf = new Uint8Array(ciphertext.length);
  const createSink = async () => ({
    async write(offset, bytes) { outBuf.set(bytes, offset); },
    async finalize() { return { blob: new Blob([outBuf]), opfsName: null }; },
    async abort() {},
  });

  const res = await decryptMegaAesCtr('https://gfs.mega.example/dl/test', {
    aesKeyHex: aesKey.toString('hex'),
    ivHex: iv8.toString('hex'),
    chunkSize: 32 * 1024, // 3 chunks -> tests multi-chunk counter offset math!
    pool: 3,
    fetchImpl: mockFetch,
    subtle: crypto.webcrypto.subtle,
    createSink,
  });

  assert.strictEqual(res.ok, true, `decryptMegaAesCtr failed: ${JSON.stringify(res)}`);
  assert.strictEqual(res.mode, 'mega_parallel');
  assert.strictEqual(rangeCalls, 3, 'Should fetch 3 parallel 32 KiB chunks');
  const actualBuf = Buffer.from(outBuf);
  const actualSha = crypto.createHash('sha256').update(actualBuf).digest('hex');
  assert.strictEqual(actualSha, expectedSha, 'Decrypted SHA-256 must match original MP4 plaintext 100%');
  assert.strictEqual(actualBuf.subarray(4, 12).toString('ascii'), 'ftypmp42', 'MP4 ftyp header preserved');

  console.log('  ✓ Mega AES-128-CTR WebCrypto multi-chunk decryption verified (SHA-256 100% match, ftypmp42 intact)');
}

main().catch((e) => { console.error(e); process.exit(1); });
