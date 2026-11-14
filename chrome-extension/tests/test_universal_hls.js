/**
 * Universal HLS tests (RFC 8216 AES-128 decryption, implicit/explicit IV,
 * key rotation, DRM rejection, disguised TS image-header stripping, and
 * end-to-end encrypted offscreen download).
 * Run with: node chrome-extension/tests/test_universal_hls.js
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const H = require(path.join(__dirname, '..', 'hls-downloader.js'));

let passed = 0, failed = 0;
function assert(cond, name, detail) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}${detail !== undefined ? `   [${JSON.stringify(detail)}]` : ''}`); failed++; }
}

function makeTsPackets(count) {
  const buf = new Uint8Array(count * 188);
  for (let p = 0; p < count; p++) {
    buf[p * 188] = 0x47;
    buf[p * 188 + 1] = 0x40;
    buf[p * 188 + 2] = 0x11;
    buf[p * 188 + 3] = 0x10 | (p & 0x0f);
    for (let j = 4; j < 188; j++) buf[p * 188 + j] = (p + j) & 0xff;
  }
  return buf;
}

function encryptAes128Cbc(plainU8, keyU8, ivU8) {
  const cipher = crypto.createCipheriv('aes-128-cbc', Buffer.from(keyU8), Buffer.from(ivU8));
  return new Uint8Array(Buffer.concat([cipher.update(Buffer.from(plainU8)), cipher.final()]));
}

(async () => {
  console.log('[1] mediaPlan: AES-128 explicit IV, implicit sequence IV, key rotation, DRM block');
  const explicitM3u8 = `#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="keys/v1.key",IV=0x0102030405060708090a0b0c0d0e0f10
#EXTINF:6.0,
seg0.png
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.cdn.example/v2.key"
#EXTINF:6.0,
seg1.jpg
#EXT-X-KEY:METHOD=NONE
#EXTINF:4.0,
seg2.ts
#EXT-X-ENDLIST`;

  let plan = null;
  try { plan = H.mediaPlan(explicitM3u8, 'https://vod.cdn.example/stream/720/index.m3u8'); } catch (e) { plan = { err: e.reason }; }
  assert(plan && Array.isArray(plan.segs) && plan.segs.length === 3, 'AES-128 playlist accepted with 3 segments', plan && plan.err);
  assert(plan && plan.fmp4 === false, 'disguised .png/.jpg segments treated as MPEG-TS (fmp4=false)');
  assert(plan && plan.segs[0].key && plan.segs[0].key.uri === 'https://vod.cdn.example/stream/720/keys/v1.key', 'relative key URI resolved', plan && plan.segs && plan.segs[0].key);
  assert(plan && plan.segs[0].key && Buffer.from(plan.segs[0].key.iv).toString('hex') === '0102030405060708090a0b0c0d0e0f10', 'explicit 16-byte hex IV parsed');
  assert(plan && plan.segs[1].key && plan.segs[1].key.uri === 'https://keys.cdn.example/v2.key'
    && Buffer.from(plan.segs[1].key.iv).toString('hex') === '00000000000000000000000000000001', 'rotated key uses implicit media sequence IV (seq=1)');
  assert(plan && plan.segs[2].key === null, 'METHOD=NONE clears encryption for subsequent segments');

  const seqM3u8 = `#EXTM3U
#EXT-X-MEDIA-SEQUENCE:258
#EXT-X-KEY:METHOD=AES-128,URI="k.key"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:5.0,
a.m4s
#EXTINF:5.0,
b.m4s
#EXT-X-ENDLIST`;
  let seqPlan = null;
  try { seqPlan = H.mediaPlan(seqM3u8, 'https://vod.cdn.example/fmp4/index.m3u8'); } catch (e) { seqPlan = { err: e.reason }; }
  assert(seqPlan && seqPlan.fmp4 === true && seqPlan.map && seqPlan.map.key && Buffer.from(seqPlan.map.key.iv).toString('hex') === '00000000000000000000000000000102', 'EXT-X-MAP inherits AES-128 key + sequence IV (258 = 0x102)');
  assert(seqPlan && seqPlan.segs && Buffer.from(seqPlan.segs[1].key.iv).toString('hex') === '00000000000000000000000000000103', 'second segment gets sequence 259 (0x103)');

  let drmReason = null;
  try {
    H.mediaPlan('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://1",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://cdn.example/a.m3u8');
  } catch (e) { drmReason = e.reason; }
  assert(drmReason === 'drm', 'SAMPLE-AES / FairPlay rejected with reason=drm', drmReason);

  let wvReason = null;
  try {
    H.mediaPlan('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://cdn.example/a.m3u8');
  } catch (e) { wvReason = e.reason; }
  assert(wvReason === 'drm', 'Widevine KEYFORMAT rejected with reason=drm', wvReason);

  console.log('[2] WebCrypto AES-128-CBC round-trip decryption');
  assert(typeof H.decryptSegment === 'function', 'decryptSegment exported');
  if (typeof H.decryptSegment === 'function') {
    const rawKey = crypto.randomBytes(16);
    const iv = H.seqToIv(42);
    const plainTs = makeTsPackets(4);
    const encTs = encryptAes128Cbc(plainTs, rawKey, iv);
    const decTs = await H.decryptSegment(encTs, new Uint8Array(rawKey), iv);
    assert(Buffer.compare(Buffer.from(decTs), Buffer.from(plainTs)) === 0, 'decrypted TS matches original 752-byte payload byte-for-byte');
  }

  console.log('[3] stripDisguisedTsPrefix: fake PNG/JPEG header before 188-byte MPEG-TS');
  assert(typeof H.stripDisguisedTsPrefix === 'function', 'stripDisguisedTsPrefix exported');
  if (typeof H.stripDisguisedTsPrefix === 'function') {
    const fakePng = new Uint8Array(68);
    fakePng.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
    fakePng[25] = 0x47; // stray 0x47 inside fake PNG header must NOT fool the scanner!
    const ts3 = makeTsPackets(3);
    const disguised = new Uint8Array(fakePng.length + ts3.length);
    disguised.set(fakePng, 0);
    disguised.set(ts3, fakePng.length);
    const stripped = H.stripDisguisedTsPrefix(disguised);
    assert(stripped[0] === 0x47 && stripped.byteLength === ts3.byteLength, '68-byte fake PNG header (even containing a stray 0x47) stripped cleanly', stripped.byteLength);

    const ts2 = makeTsPackets(2);
    const fakeJpg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
    const disguised2 = new Uint8Array(fakeJpg.length + ts2.length);
    disguised2.set(fakeJpg, 0);
    disguised2.set(ts2, fakeJpg.length);
    const stripped2 = H.stripDisguisedTsPrefix(disguised2);
    assert(stripped2[0] === 0x47 && stripped2.byteLength === ts2.byteLength, '2-packet tail segment with fake JPEG header stripped cleanly');

    const cleanTs = makeTsPackets(3);
    assert(H.stripDisguisedTsPrefix(cleanTs) === cleanTs, 'clean MPEG-TS starting with 0x47 returned untouched');

    const fmp4Box = new Uint8Array([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d]);
    assert(H.stripDisguisedTsPrefix(fmp4Box) === fmp4Box, 'non-TS fMP4 buffer returned untouched');
  }

  console.log('[4] End-to-end offscreen HLS run: master -> 720p AES-128 + PNG-disguised TS + cross-host referer');
  const keyBytes = new Uint8Array(crypto.randomBytes(16));
  const pngPrefix = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d]);
  const seg0Plain = makeTsPackets(3);
  const seg1Plain = makeTsPackets(3);
  const seg0Disguised = new Uint8Array(pngPrefix.length + seg0Plain.length);
  seg0Disguised.set(pngPrefix, 0); seg0Disguised.set(seg0Plain, pngPrefix.length);
  const seg1Disguised = new Uint8Array(pngPrefix.length + seg1Plain.length);
  seg1Disguised.set(pngPrefix, 0); seg1Disguised.set(seg1Plain, pngPrefix.length);

  const iv0 = H.seqToIv ? H.seqToIv(10) : new Uint8Array(16);
  const iv1 = H.seqToIv ? H.seqToIv(11) : new Uint8Array(16);
  const seg0Enc = encryptAes128Cbc(seg0Disguised, keyBytes, iv0);
  const seg1Enc = encryptAes128Cbc(seg1Disguised, keyBytes, iv1);

  const routes = new Map([
    ['https://manifest.cdn-a.example/master.txt', new TextEncoder().encode(`#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080
https://variant.cdn-b.example/1080/index.txt
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
https://variant.cdn-b.example/720/index.txt
`)],
    ['https://variant.cdn-b.example/720/index.txt', new TextEncoder().encode(`#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="https://key.cdn-c.example/secret.key"
#EXTINF:6.0,
https://seg.cdn-d.example/chunk0.png
#EXTINF:6.0,
https://seg.cdn-d.example/chunk1.png
#EXT-X-ENDLIST
`)],
    ['https://key.cdn-c.example/secret.key', keyBytes],
    ['https://seg.cdn-d.example/chunk0.png', seg0Enc],
    ['https://seg.cdn-d.example/chunk1.png', seg1Enc],
  ]);

  let keyFetches = 0;
  const swMessages = [];
  const pushedChunks = [];
  const writtenParts = [];
  let listener = null;

  const sandbox = {
    crypto: crypto.webcrypto,
    TextDecoder, TextEncoder, Uint8Array, Promise, Map, Set, Error, Object, Number, String, Math, Array, RegExp,
    setTimeout, clearTimeout,
    URL: Object.assign(function (u, b) { return new URL(u, b); }, {
      createObjectURL: () => 'blob:chrome-extension://id/mock-mp4',
      revokeObjectURL: () => {},
    }),
    navigator: {
      storage: {
        getDirectory: async () => ({
          async *entries() {},
          removeEntry: async () => {},
          getFileHandle: async () => ({
            createWritable: async () => ({
              write: async (u8) => { writtenParts.push(new Uint8Array(u8)); },
              close: async () => {},
              abort: async () => {},
            }),
            getFile: async () => ({ size: writtenParts.reduce((s, x) => s + x.byteLength, 0) }),
          }),
        }),
      },
    },
    chrome: {
      runtime: {
        sendMessage: async (m) => { swMessages.push(m); return { ok: true }; },
        onMessage: { addListener: (fn) => { listener = fn; } },
      },
    },
    fetch: async (url) => {
      if (url === 'https://key.cdn-c.example/secret.key') keyFetches++;
      const body = routes.get(String(url));
      if (!body) return { ok: false, status: 404 };
      return { ok: true, status: 200, arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) };
    },
    muxjs: {
      mp4: {
        Transmuxer: function () {
          let onData = null;
          return {
            on: (ev, cb) => { if (ev === 'data') onData = cb; },
            push: (u8) => { pushedChunks.push(new Uint8Array(u8)); },
            flush: () => {
              const last = pushedChunks[pushedChunks.length - 1];
              if (onData && last && last[0] === 0x47) {
                onData({ initSegment: new Uint8Array([0, 0, 0, 8]), data: last });
              }
            },
          };
        },
      },
    },
  };
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'hls-downloader.js'), 'utf8'), sandbox);

  listener({ target: 'offscreen', type: 'HLS_START', job: { id: 'j1', url: 'https://manifest.cdn-a.example/master.txt', height: 720, title: 'Episode 1', referer: 'https://player.embed.example/v/1' } }, {}, () => {});
  for (let i = 0; i < 40 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 25));
  }
  const doneMsg = swMessages.find((m) => m.type === 'HLS_DONE');
  const failMsg = swMessages.find((m) => m.type === 'HLS_FAILED');
  assert(!!doneMsg && !failMsg, 'encrypted + PNG-disguised HLS job completes with HLS_DONE', failMsg || doneMsg);
  assert(keyFetches === 1, 'AES-128 key fetched only ONCE across multiple segments', keyFetches);
  assert(pushedChunks.length === 2 && pushedChunks.every((c) => c[0] === 0x47 && c.byteLength === seg0Plain.byteLength), 'both segments decrypted and PNG-stripped before mux.js');
  const refDomains = swMessages.filter((m) => m.type === 'VG_ENSURE_REFERER').map((m) => new URL(m.url).hostname);
  assert(refDomains.includes('manifest.cdn-a.example') && refDomains.includes('variant.cdn-b.example')
    && refDomains.includes('key.cdn-c.example') && refDomains.includes('seg.cdn-d.example'), 'VG_ENSURE_REFERER ensured for manifest, variant, key, and segment CDN hosts', refDomains);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
