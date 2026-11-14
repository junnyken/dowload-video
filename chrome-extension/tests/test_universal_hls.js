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

  const sampleIdPlan = H.mediaPlan(
    '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://keys.cdn.example/sa.key",KEYFORMAT="identity"\n#EXTINF:4,\nseg7.ts\n#EXT-X-ENDLIST',
    'https://cdn.example/a.m3u8',
  );
  assert(
    sampleIdPlan && sampleIdPlan.segs.length === 1 && sampleIdPlan.segs[0].key.method === 'sample_aes'
      && Buffer.from(sampleIdPlan.segs[0].key.iv).toString('hex') === '00000000000000000000000000000007',
    'SAMPLE-AES with KEYFORMAT="identity" + https URI accepted as sample_aes with implicit sequence IV',
    sampleIdPlan,
  );

  const sampleImplicitPlan = H.mediaPlan(
    '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="sa.key"\n#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST',
    'https://cdn.example/vod/a.m3u8',
  );
  assert(
    sampleImplicitPlan && sampleImplicitPlan.segs[0].key.method === 'sample_aes'
      && sampleImplicitPlan.segs[0].key.uri === 'https://cdn.example/vod/sa.key',
    'SAMPLE-AES with omitted KEYFORMAT + relative https URI accepted as sample_aes',
  );

  let fpErr = null;
  try {
    H.mediaPlan('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://1",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://cdn.example/a.m3u8');
  } catch (e) { fpErr = e; }
  assert(fpErr && fpErr.reason === 'drm' && fpErr.drmSystem === 'FairPlay', 'FairPlay skd:// + com.apple.streamingkeydelivery rejected with reason=drm, drmSystem=FairPlay', fpErr);

  let skdOnlyErr = null;
  try {
    H.mediaPlan('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset-key"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://cdn.example/a.m3u8');
  } catch (e) { skdOnlyErr = e; }
  assert(skdOnlyErr && skdOnlyErr.reason === 'drm' && skdOnlyErr.drmSystem === 'FairPlay', 'FairPlay skd:// URI without KEYFORMAT rejected with reason=drm, drmSystem=FairPlay', skdOnlyErr);

  let wvErr = null;
  try {
    H.mediaPlan('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.example/k",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://cdn.example/a.m3u8');
  } catch (e) { wvErr = e; }
  assert(wvErr && wvErr.reason === 'drm' && wvErr.drmSystem === 'Widevine', 'Widevine KEYFORMAT rejected with reason=drm, drmSystem=Widevine', wvErr);

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
    Date, Boolean, parseInt, parseFloat, isFinite,
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

  console.log('[5] Disguised .jpeg self-initializing fMP4 segments (no EXT-X-MAP) bypass mux.js and write ISO-BMFF directly');
  assert(typeof H.isIsoBmffSegment === 'function', 'isIsoBmffSegment exported');
  const fmp4Seg0 = new Uint8Array([
    0x00, 0x00, 0x00, 0x10, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d, 0x00, 0x00, 0x02, 0x00,
    0x00, 0x00, 0x00, 0x08, 0x6d, 0x6f, 0x6f, 0x66,
  ]);
  const fmp4Seg1 = new Uint8Array([
    0x00, 0x00, 0x00, 0x08, 0x73, 0x74, 0x79, 0x70,
    0x00, 0x00, 0x00, 0x08, 0x6d, 0x6f, 0x6f, 0x66,
  ]);
  assert(H.isIsoBmffSegment(fmp4Seg0) === true && H.isIsoBmffSegment(fmp4Seg1) === true, 'ftyp/styp ISO-BMFF boxes recognised');
  assert(H.isIsoBmffSegment(seg0Plain) === false, 'MPEG-TS packets not misidentified as ISO-BMFF');

  routes.set('https://fmp4.cdn.example/playlist.m3u8', new TextEncoder().encode(`#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:4
#EXTINF:4.0,
https://fmp4.cdn.example/part0.jpeg
#EXTINF:4.0,
https://fmp4.cdn.example/part1.jpeg
#EXT-X-ENDLIST
`));
  routes.set('https://fmp4.cdn.example/part0.jpeg', fmp4Seg0);
  routes.set('https://fmp4.cdn.example/part1.jpeg', fmp4Seg1);

  swMessages.length = 0;
  pushedChunks.length = 0;
  writtenParts.length = 0;
  listener({ target: 'offscreen', type: 'HLS_START', job: { id: 'j2', url: 'https://fmp4.cdn.example/playlist.m3u8', height: 1080, title: 'Disguised fMP4', referer: 'https://cinema.example/watch/9' } }, {}, () => {});
  for (let i = 0; i < 40 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 25));
  }
  const doneMsg2 = swMessages.find((m) => m.type === 'HLS_DONE');
  assert(!!doneMsg2 && pushedChunks.length === 0 && writtenParts.length === 2,
    'disguised .jpeg fMP4 segments write directly to OPFS MP4 without mux.js TS transmuxer',
    { done: !!doneMsg2, muxPushes: pushedChunks.length, opfsWrites: writtenParts.length });

  console.log('[6] SAMPLE-AES (KEYFORMAT="identity") MPEG-TS H.264 RBSP + ADTS AAC + PMT CRC32 decryption & E2E');
  function encryptAes128CbcRaw(plainU8, keyU8, ivU8) {
    const c = crypto.createCipheriv('aes-128-cbc', Buffer.from(keyU8), Buffer.from(ivU8));
    c.setAutoPadding(false);
    return new Uint8Array(Buffer.concat([c.update(Buffer.from(plainU8)), c.final()]));
  }
  function rbspEscapeBody(uNal) {
    const out = [uNal[0]];
    let z = 0;
    for (let i = 1; i < uNal.length; i++) {
      const b = uNal[i];
      if (z === 2 && b <= 3) { out.push(3); z = 0; }
      out.push(b);
      z = b === 0 ? z + 1 : 0;
    }
    return new Uint8Array(out);
  }
  function makePsiPacket(pid, sectionWithoutCrc) {
    const crc = H.mpeg2Crc32(sectionWithoutCrc);
    const pkt = new Uint8Array(188).fill(0xff);
    pkt[0] = 0x47;
    pkt[1] = 0x40 | ((pid >> 8) & 0x1f);
    pkt[2] = pid & 0xff;
    pkt[3] = 0x10;
    pkt[4] = 0x00; // pointer_field
    pkt.set(sectionWithoutCrc, 5);
    const cOff = 5 + sectionWithoutCrc.length;
    pkt[cOff] = (crc >>> 24) & 0xff;
    pkt[cOff + 1] = (crc >>> 16) & 0xff;
    pkt[cOff + 2] = (crc >>> 8) & 0xff;
    pkt[cOff + 3] = crc & 0xff;
    return pkt;
  }
  function packetizeElementaryPes(pid, streamId, esPayload) {
    const pesLen = 3 + esPayload.length <= 0xffff ? 3 + esPayload.length : 0;
    const pes = new Uint8Array(9 + esPayload.length);
    pes.set([0x00, 0x00, 0x01, streamId, (pesLen >> 8) & 0xff, pesLen & 0xff, 0x80, 0x00, 0x00], 0);
    pes.set(esPayload, 9);
    const pkts = [];
    let off = 0, cc = 0;
    while (off < pes.length) {
      const pkt = new Uint8Array(188);
      pkt[0] = 0x47;
      pkt[1] = (off === 0 ? 0x40 : 0x00) | ((pid >> 8) & 0x1f);
      pkt[2] = pid & 0xff;
      const rem = pes.length - off;
      if (rem >= 184) {
        pkt[3] = 0x10 | (cc & 0x0f);
        pkt.set(pes.subarray(off, off + 184), 4);
        off += 184;
      } else {
        const stuff = 184 - rem;
        pkt[3] = 0x30 | (cc & 0x0f);
        if (stuff === 1) pkt[4] = 0;
        else { pkt[4] = stuff - 1; pkt[5] = 0; pkt.fill(0xff, 6, 4 + stuff); }
        pkt.set(pes.subarray(off), 188 - rem);
        off += rem;
      }
      cc = (cc + 1) & 0x0f;
      pkts.push(pkt);
    }
    return { pes, pkts };
  }
  function extractPidPes(tsBuf, targetPid) {
    const parts = [];
    for (let p = 0; p * 188 < tsBuf.length; p++) {
      const o = p * 188;
      const pid = ((tsBuf[o + 1] & 0x1f) << 8) | tsBuf[o + 2];
      if (pid !== targetPid) continue;
      const afc = (tsBuf[o + 3] >> 4) & 0x03;
      const pay = afc === 3 ? o + 5 + tsBuf[o + 4] : o + 4;
      if (pay < o + 188) parts.push(Buffer.from(tsBuf.subarray(pay, o + 188)));
    }
    return new Uint8Array(Buffer.concat(parts));
  }

  const saKey = new Uint8Array(crypto.randomBytes(16));
  const saIv = H.seqToIv(5);

  // Build H.264 unescaped IDR NAL (type 5, 250 bytes) containing 00 00 01 inside encrypted blocks so RBSP escape/unescape is exercised!
  const spsNal = new Uint8Array([0x67, 0x4d, 0x40, 0x1f, 0xe8, 0x80, 0x50, 0x17]);
  const idrUnescaped = new Uint8Array(250);
  idrUnescaped[0] = 0x65; // IDR slice (nal_unit_type = 5)
  for (let i = 1; i < idrUnescaped.length; i++) idrUnescaped[i] = (i * 17 + 9) & 0xff;
  // Inject 00 00 01 inside the first encrypted 16-byte block (32..47) and second encrypted block (192..207)
  idrUnescaped.set([0x00, 0x00, 0x01, 0xaa], 36);
  idrUnescaped.set([0x00, 0x00, 0x00, 0x02], 196);

  const clearIdrEscaped = rbspEscapeBody(idrUnescaped);
  const clearVideoEs = new Uint8Array(4 + spsNal.length + 4 + clearIdrEscaped.length);
  clearVideoEs.set([0, 0, 0, 1], 0);
  clearVideoEs.set(spsNal, 4);
  clearVideoEs.set([0, 0, 0, 1], 4 + spsNal.length);
  clearVideoEs.set(clearIdrEscaped, 8 + spsNal.length);
  const clearVideo = packetizeElementaryPes(0x0101, 0xe0, clearVideoEs);

  // Encrypt the IDR NAL per Apple SAMPLE-AES (32B clear, encrypt 16B at 32 and 192 in one CBC chain, leave trailing clear, then RBSP-escape)
  const encIdrUnescaped = new Uint8Array(idrUnescaped);
  const plainBlocks = new Uint8Array(32);
  plainBlocks.set(encIdrUnescaped.subarray(32, 48), 0);
  plainBlocks.set(encIdrUnescaped.subarray(192, 208), 16);
  const cipherBlocks = encryptAes128CbcRaw(plainBlocks, saKey, saIv);
  encIdrUnescaped.set(cipherBlocks.subarray(0, 16), 32);
  encIdrUnescaped.set(cipherBlocks.subarray(16, 32), 192);
  const encIdrEscaped = rbspEscapeBody(encIdrUnescaped);
  const encVideoEs = new Uint8Array(4 + spsNal.length + 4 + encIdrEscaped.length);
  encVideoEs.set([0, 0, 0, 1], 0);
  encVideoEs.set(spsNal, 4);
  encVideoEs.set([0, 0, 0, 1], 4 + spsNal.length);
  encVideoEs.set(encIdrEscaped, 8 + spsNal.length);
  const encVideo = packetizeElementaryPes(0x0101, 0xe0, encVideoEs);

  // Build 72-byte ADTS AAC frame (7B header + 16B clear + 3*16B encrypted + 1B trailing clear)
  const clearAdts = new Uint8Array(72);
  clearAdts[0] = 0xff;
  clearAdts[1] = 0xf1; // MPEG-4, Layer 0, protection_absent = 1 (7-byte header)
  clearAdts[2] = 0x50;
  clearAdts[3] = 0x80 | ((72 >> 11) & 0x03);
  clearAdts[4] = (72 >> 3) & 0xff;
  clearAdts[5] = ((72 & 0x07) << 5) | 0x1f;
  clearAdts[6] = 0xfc;
  for (let i = 7; i < 72; i++) clearAdts[i] = (i * 29 + 3) & 0xff;
  const encAdts = new Uint8Array(clearAdts);
  encAdts.set(encryptAes128CbcRaw(clearAdts.subarray(23, 71), saKey, saIv), 23);
  const clearAudio = packetizeElementaryPes(0x0102, 0xc0, clearAdts);
  const encAudio = packetizeElementaryPes(0x0102, 0xc0, encAdts);

  // PAT (program 1 -> PMT PID 0x0100) + PMT (stream_type 0xDB on 0x0101, 0xCF on 0x0102)
  const patSec = new Uint8Array([0x00, 0xb0, 0x0d, 0x00, 0x01, 0xc1, 0x00, 0x00, 0x00, 0x01, 0xe1, 0x00]);
  const pmtSec = new Uint8Array([
    0x02, 0xb0, 0x17, 0x00, 0x01, 0xc1, 0x00, 0x00, 0xe1, 0x01, 0xf0, 0x00,
    0xdb, 0xe1, 0x01, 0xf0, 0x00,
    0xcf, 0xe1, 0x02, 0xf0, 0x00,
  ]);
  const patPkt = makePsiPacket(0x0000, patSec);
  const pmtPkt = makePsiPacket(0x0100, pmtSec);
  const allEncPkts = [patPkt, pmtPkt, ...encVideo.pkts, ...encAudio.pkts];
  const sampleAesTs = new Uint8Array(allEncPkts.length * 188);
  allEncPkts.forEach((p, idx) => sampleAesTs.set(p, idx * 188));

  const decSampleTs = await H.decryptSampleAesTs(sampleAesTs, saKey, saIv);
  // Check PMT stream_types rewritten to 0x1B / 0x0F and CRC32 valid
  const decPmtPkt = decSampleTs.subarray(188, 376);
  const decPmtSec = decPmtPkt.subarray(5, 5 + pmtSec.length);
  const decPmtCrc = ((decPmtPkt[5 + pmtSec.length] << 24) >>> 0) + (decPmtPkt[6 + pmtSec.length] << 16) + (decPmtPkt[7 + pmtSec.length] << 8) + decPmtPkt[8 + pmtSec.length];
  assert(decPmtSec[12] === 0x1b && decPmtSec[17] === 0x0f && decPmtCrc === H.mpeg2Crc32(decPmtSec),
    'PMT stream_type 0xDB->0x1B and 0xCF->0x0F rewritten with valid MPEG-2 PSI CRC32');

  const recoveredVideoPes = extractPidPes(decSampleTs, 0x0101);
  const recoveredAudioPes = extractPidPes(decSampleTs, 0x0102);
  assert(Buffer.compare(Buffer.from(recoveredVideoPes), Buffer.from(clearVideo.pes)) === 0,
    'SAMPLE-AES H.264 IDR NAL (1:9 CBC + RBSP 00 00 03 xx unescape/escape) matches cleartext PES byte-for-byte');
  assert(Buffer.compare(Buffer.from(recoveredAudioPes), Buffer.from(clearAudio.pes)) === 0,
    'SAMPLE-AES ADTS AAC frame (hdr+16B clear + CBC) matches cleartext PES byte-for-byte');

  // End-to-end offscreen SAMPLE-AES job
  routes.set('https://vod.cdn.example/sample-aes.m3u8', new TextEncoder().encode(`#EXTM3U
#EXT-X-VERSION:5
#EXT-X-MEDIA-SEQUENCE:5
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://key.cdn-c.example/sa.key",KEYFORMAT="identity"
#EXTINF:6.0,
https://seg.cdn-d.example/sa-seg5.ts
#EXT-X-ENDLIST
`));
  routes.set('https://key.cdn-c.example/sa.key', saKey);
  routes.set('https://seg.cdn-d.example/sa-seg5.ts', sampleAesTs);
  swMessages.length = 0;
  pushedChunks.length = 0;
  writtenParts.length = 0;
  listener({ target: 'offscreen', type: 'HLS_START', job: { id: 'j-sa', url: 'https://vod.cdn.example/sample-aes.m3u8', title: 'SAMPLE-AES Episode' } }, {}, () => {});
  for (let i = 0; i < 40 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 25));
  }
  assert(!!swMessages.find((m) => m.type === 'HLS_DONE') && pushedChunks.length === 1,
    'end-to-end SAMPLE-AES (KEYFORMAT="identity") job decrypts and completes with HLS_DONE',
    swMessages.find((m) => m.type === 'HLS_FAILED'));

  console.log('[7] Non-DRM Livestream recording (sliding window, retry/backoff, Stop & save, 3 idle polls) & Live DRM block');
  // 7a: Sliding window + transient HTTP 503 disconnect + #EXT-X-ENDLIST
  for (let s = 100; s <= 103; s++) {
    const pkt = makeTsPackets(2);
    pkt[4] = s; // tag segment sequence in first payload byte
    routes.set(`https://live.cdn.example/seg${s}.ts`, pkt);
  }
  const liveSlides = [
    `#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:100\n#EXTINF:2.0,\nhttps://live.cdn.example/seg100.ts\n#EXTINF:2.0,\nhttps://live.cdn.example/seg101.ts\n`,
    `#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:101\n#EXTINF:2.0,\nhttps://live.cdn.example/seg101.ts\n#EXTINF:2.0,\nhttps://live.cdn.example/seg102.ts\n`,
    `#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:102\n#EXTINF:2.0,\nhttps://live.cdn.example/seg102.ts\n#EXTINF:2.0,\nhttps://live.cdn.example/seg103.ts\n#EXT-X-ENDLIST\n`,
  ];
  let livePollIdx = 0;
  let injected503 = false;
  const origFetch = sandbox.fetch;
  sandbox.fetch = async (url) => {
    const u = String(url);
    if (u === 'https://live.cdn.example/sliding.m3u8') {
      if (livePollIdx === 1 && !injected503) {
        injected503 = true;
        return { ok: false, status: 503 };
      }
      const txt = liveSlides[Math.min(livePollIdx++, liveSlides.length - 1)];
      const b = new TextEncoder().encode(txt);
      return { ok: true, status: 200, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    }
    return origFetch(url);
  };

  swMessages.length = 0;
  pushedChunks.length = 0;
  writtenParts.length = 0;
  listener({
    target: 'offscreen',
    type: 'HLS_START',
    job: { id: 'live-slide', url: 'https://live.cdn.example/sliding.m3u8', title: 'Live Event', live: true, pollIntervalMs: 25, retryBaseMs: 10 },
  }, {}, () => {});
  for (let i = 0; i < 60 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 25));
  }
  const liveDone = swMessages.find((m) => m.type === 'HLS_DONE');
  const pushedSeqs = pushedChunks.map((c) => c[4]);
  assert(
    !!liveDone && liveDone.live === true && injected503 && pushedSeqs.join(',') === '100,101,102,103',
    'live sliding-window recording deduplicates media sequence (100..103), retries transient HTTP 503, and saves MP4 on #EXT-X-ENDLIST',
    { liveDone, injected503, pushedSeqs, failed: swMessages.find((m) => m.type === 'HLS_FAILED') },
  );

  // 7b: User clicks "Dừng & lưu" (HLS_STOP) on ongoing live stream
  let stopStreamSeq = 200;
  sandbox.fetch = async (url) => {
    const u = String(url);
    if (u === 'https://live.cdn.example/endless.m3u8') {
      const s = stopStreamSeq++;
      const pkt = makeTsPackets(2);
      pkt[4] = s & 0xff;
      routes.set(`https://live.cdn.example/endless-${s}.ts`, pkt);
      const txt = `#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:${s}\n#EXTINF:4.0,\nhttps://live.cdn.example/endless-${s}.ts\n`;
      const b = new TextEncoder().encode(txt);
      return { ok: true, status: 200, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    }
    return origFetch(url);
  };
  swMessages.length = 0;
  pushedChunks.length = 0;
  writtenParts.length = 0;
  listener({
    target: 'offscreen',
    type: 'HLS_START',
    job: { id: 'live-user-stop', url: 'https://live.cdn.example/endless.m3u8', title: 'Live Stop Test', live: true, pollIntervalMs: 5000, retryBaseMs: 10 },
  }, {}, () => {});
  for (let i = 0; i < 30 && !swMessages.some((m) => m.type === 'HLS_PROGRESS' && m.done >= 1); i++) {
    await new Promise((r) => setTimeout(r, 20));
  }
  listener({ target: 'offscreen', type: 'HLS_STOP', id: 'live-user-stop' }, {}, () => {});
  for (let i = 0; i < 30 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 20));
  }
  assert(
    !!swMessages.find((m) => m.type === 'HLS_DONE') && pushedChunks.length >= 1,
    'HLS_STOP ("Dừng & lưu") wakes poll immediately and saves MP4 cleanly',
    swMessages.find((m) => m.type === 'HLS_FAILED'),
  );

  // 7c: Auto-stop after 3 consecutive polls with no new segment
  let stalledPolls = 0;
  routes.set('https://live.cdn.example/stalled-50.ts', makeTsPackets(2));
  sandbox.fetch = async (url) => {
    const u = String(url);
    if (u === 'https://live.cdn.example/stalled.m3u8') {
      stalledPolls++;
      const txt = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:50\n#EXTINF:2.0,\nhttps://live.cdn.example/stalled-50.ts\n';
      const b = new TextEncoder().encode(txt);
      return { ok: true, status: 200, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    }
    return origFetch(url);
  };
  swMessages.length = 0;
  pushedChunks.length = 0;
  writtenParts.length = 0;
  listener({
    target: 'offscreen',
    type: 'HLS_START',
    job: { id: 'live-idle', url: 'https://live.cdn.example/stalled.m3u8', title: 'Stalled Live', live: true, maxEmptyPolls: 3, pollIntervalMs: 20, retryBaseMs: 10 },
  }, {}, () => {});
  for (let i = 0; i < 60 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 20));
  }
  assert(
    !!swMessages.find((m) => m.type === 'HLS_DONE') && stalledPolls === 4 && pushedChunks.length === 1,
    'live stream stops & saves MP4 automatically after 3 consecutive polls with no new segments (1 initial + 3 empty = 4)',
    { stalledPolls, chunks: pushedChunks.length, msgs: swMessages },
  );

  // 7d: Live stream WITH DRM is strictly blocked
  sandbox.fetch = origFetch;
  routes.set('https://live.cdn.example/drm-live.m3u8', new TextEncoder().encode(`#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:900
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://live-fairplay-key",KEYFORMAT="com.apple.streamingkeydelivery"
#EXTINF:4.0,
https://live.cdn.example/seg100.ts
`));
  swMessages.length = 0;
  pushedChunks.length = 0;
  listener({
    target: 'offscreen',
    type: 'HLS_START',
    job: { id: 'live-drm', url: 'https://live.cdn.example/drm-live.m3u8', title: 'Protected Live', live: true },
  }, {}, () => {});
  for (let i = 0; i < 30 && !swMessages.some((m) => m.type === 'HLS_DONE' || m.type === 'HLS_FAILED'); i++) {
    await new Promise((r) => setTimeout(r, 20));
  }
  const liveDrmFail = swMessages.find((m) => m.type === 'HLS_FAILED');
  assert(
    !!liveDrmFail && liveDrmFail.reason === 'drm' && liveDrmFail.drmSystem === 'FairPlay' && pushedChunks.length === 0,
    'live stream WITH DRM is strictly blocked with HLS_FAILED (reason=drm, drmSystem=FairPlay)',
    liveDrmFail,
  );

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
