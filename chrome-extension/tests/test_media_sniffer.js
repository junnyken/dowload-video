/**
 * media-sniffer.js — the pure half: what counts as listable media, and what
 * the HLS / DASH manifests say (qualities, duration, live, encryption / DRM).
 * Run with: node tests/test_media_sniffer.js
 */
const path = require('path');
const S = require(path.join(__dirname, '..', 'media-sniffer.js'));

let passed = 0, failed = 0;
function assert(cond, name, got) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}${got !== undefined ? `   [got ${JSON.stringify(got)}]` : ''}`); failed++; }
}
const H = (obj) => Object.entries(obj).map(([name, value]) => ({ name, value: String(value) }));
const d = (url, headers = {}, extra = {}) => ({ url, tabId: 3, statusCode: 200, responseHeaders: H(headers), ...extra });

console.log('classify');
assert(S.classify(d('https://cdn.x/v/master.m3u8?t=1', { 'content-type': 'application/vnd.apple.mpegurl' })).kind === 'hls', 'm3u8 by path');
assert(S.classify(d('https://cdn.x/play?id=9', { 'content-type': 'application/x-mpegURL' })).kind === 'hls', 'HLS by content-type only');
assert(S.classify(d('https://cdn.x/hls/ep01/index.txt', { 'content-type': 'text/plain', 'content-length': 4200 }))?.kind === 'hls', 'disguised HLS index.txt under /hls/');
assert(S.classify(d('https://cdn.x/vod/playlist?token=abc', { 'content-type': 'application/octet-stream', 'content-length': 1800 }))?.kind === 'hls', 'disguised HLS playlist with octet-stream');
assert(S.classify(d('https://cdn.x/uuid-a1b2/720p/chunklist_b2000.txt', { 'content-type': 'text/html; charset=utf-8', 'content-length': 6400 }))?.kind === 'hls', 'disguised HLS chunklist served with text/html');
assert(S.classify(d('https://cdn.x/vod/urlset/master', { 'content-type': 'application/json', 'content-length': 900 }))?.kind === 'hls', 'disguised HLS urlset/master served with application/json');
assert(S.classify(d('https://cdn.x/api/stream?format=m3u8', { 'content-type': 'text/plain' }))?.kind === 'hls', 'disguised HLS m3u8 query hint');
assert(S.classify(d('https://cdn.x/hls/seg_001.png', { 'content-type': 'image/png', 'content-length': 250000 })) === null, 'PNG segment under /hls/ is NOT classified as manifest');
assert(S.classify(d('https://cdn.x/vod/seg-12-v1-a1.mp4', { 'content-type': 'video/mp4', 'content-length': 1500000 })) === null, 'small numbered video chunk (seg-12-v1-a1.mp4) suppressed');
assert(S.classify(d('https://stream.cdn.x/play/998877?sig=abc', { 'content-type': 'application/octet-stream', 'content-length': 45000000 }, { type: 'media' }))?.kind === 'file', 'extensionless <video> media stream with application/octet-stream classified as file');
assert(S.classify(d('https://stream.cdn.x/api/blob?sig=abc', { 'content-type': 'application/octet-stream', 'content-length': 45000000 }, { type: 'xmlhttprequest' })) === null, 'XHR octet-stream without media element not falsely listed as file');
assert(S.classify(d('https://cdn.x/hls/player.js', { 'content-type': 'application/javascript', 'content-length': 12000 })) === null, 'JS under /hls/ is NOT classified as manifest');
assert(S.classify(d('https://cdn.x/robots.txt', { 'content-type': 'text/plain', 'content-length': 500 })) === null, 'plain txt without HLS hint ignored');
assert(S.classify(d('https://cdn.x/hls/index.txt', { 'content-type': 'text/plain', 'content-length': 5000000 })) === null, 'oversized disguised txt ignored');
assert(S.classify(d('https://cdn.x/a/stream.mpd')).kind === 'dash', 'mpd by path');
assert(S.classify(d('https://cdn.x/manifest', { 'content-type': 'application/dash+xml' })).kind === 'dash', 'DASH by content-type');
const f = S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-length': 5e6 }));
assert(f && f.kind === 'file' && f.size === 5e6, 'mp4 file with its size', f);
const ranged = S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-range': 'bytes 0-1023/9000000', 'content-length': 1024 }, { statusCode: 206 }));
assert(ranged && ranged.size === 9000000, 'ranged piece reports the whole file size', ranged);
assert(S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-length': 120000 })) === null, 'tiny video skipped (preview / ad loop)');
assert(S.classify(d('https://cdn.x/seg_001.ts', { 'content-type': 'video/mp2t', 'content-length': 2e6 })) === null, 'HLS segment skipped');
assert(S.classify(d('https://cdn.x/chunk-5.m4s', { 'content-type': 'video/iso.segment' })) === null, 'DASH segment skipped');
assert(S.classify(d('https://cdn.x/a.m4a', { 'content-type': 'audio/mp4', 'content-length': 4e6 })).kind === 'file', 'audio file listed');
assert(S.classify(d('https://cdn.x/page', { 'content-type': 'text/html' })) === null, 'html ignored');
assert(S.classify(d('https://rr3---sn.googlevideo.com/videoplayback?x=1', { 'content-type': 'video/mp4', 'content-length': 9e6 })) === null, 'YouTube CDN never listed');
assert(S.classify(d('https://www.youtube.com/x.m3u8')) === null, 'youtube.com never listed');
assert(S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-length': 9e6 }, { tabId: -1 })) === null, 'requests outside a tab ignored');
assert(S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-length': 9e6 }, { statusCode: 404 })) === null, 'errors ignored');
assert(S.classify(d('https://cdn.x/v.mp4', { 'content-type': 'video/mp4', 'content-length': 9e6 }, { initiator: 'chrome-extension://abc' })) === null, "the extension's own fetches ignored");
assert(S.canonicalUrl('https://fb.cdn/v.mp4?oh=1&bytestart=0&byteend=999') === 'https://fb.cdn/v.mp4?oh=1', 'range params stripped', S.canonicalUrl('https://fb.cdn/v.mp4?oh=1&bytestart=0&byteend=999'));

console.log('HLS');
const master = `#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",URI="audio/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
https://other.cdn/1080.m3u8
`;
const m = S.parseM3U8(master, 'https://cdn.x/show/master.m3u8?sig=1');
assert(m.valid && m.master, 'master playlist recognised');
assert(m.variants.map((v) => v.height).join(',') === '1080,720,360', 'variants best first', m.variants.map((v) => v.height));
assert(m.variants[1].url === 'https://cdn.x/show/720/index.m3u8', 'relative variant resolved', m.variants[1].url);
assert(m.variants[0].url === 'https://other.cdn/1080.m3u8', 'absolute variant kept');
assert(m.renditions[0].url === 'https://cdn.x/show/audio/en.m3u8' && m.renditions[0].language === 'en', 'audio rendition found');
assert(m.encryption === 'none', 'master not encrypted');

const media = `#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6.0,
s1.ts
#EXTINF:6.0,
s2.ts
#EXTINF:3.5,
s3.ts
#EXT-X-ENDLIST`;
const p = S.parseM3U8(media, 'https://cdn.x/720/index.m3u8');
assert(!p.master && p.segments === 3 && p.duration === 16 && p.live === false && p.targetDuration === 6, 'media playlist: 3 segments, 16 s, targetDuration 6, ended', p);
const live = S.parseM3U8('#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:120\n#EXTINF:2,\na.ts\n#EXTINF:2,\nb.ts\n', 'https://x/l.m3u8');
assert(live.live === true && live.encryption === 'none' && live.mediaSequence === 120 && live.targetDuration === 4, 'live non-DRM HLS: no ENDLIST, clear encryption, mediaSequence=120', live);

const aesStd = S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/');
assert(aesStd.encryption === 'aes128' && aesStd.drmSystem === null && /AES-128/i.test(aesStd.encryptionReason), 'standard AES-128 (implicit identity + https) = aes128 (not DRM)', aesStd);

const sampleAesId = S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://keys.x/s.key",KEYFORMAT="identity"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/');
assert(sampleAesId.encryption === 'sample_aes' && sampleAesId.drmSystem === null && /SAMPLE-AES.*KEYFORMAT=identity/i.test(sampleAesId.encryptionReason), 'SAMPLE-AES with KEYFORMAT="identity" + https URI = sample_aes (NOT DRM)', sampleAesId);

const sampleAesOmitted = S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="s.key"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/');
assert(sampleAesOmitted.encryption === 'sample_aes' && sampleAesOmitted.drmSystem === null, 'SAMPLE-AES with omitted KEYFORMAT + https URI = sample_aes (NOT DRM)', sampleAesOmitted);

const fpKeyformat = S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset-1",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/');
assert(fpKeyformat.encryption === 'drm' && fpKeyformat.drmSystem === 'FairPlay' && /FairPlay/i.test(fpKeyformat.encryptionReason), 'FairPlay (com.apple.streamingkeydelivery + skd://) = DRM FairPlay', fpKeyformat);

const fpSkdOnly = S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://license.example/k"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/');
assert(fpSkdOnly.encryption === 'drm' && fpSkdOnly.drmSystem === 'FairPlay' && /skd:\/\//i.test(fpSkdOnly.encryptionReason), 'FairPlay skd:// URI even without KEYFORMAT = DRM FairPlay', fpSkdOnly);

const wvHls = S.parseM3U8('#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,AAA=",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8', 'https://x/');
assert(wvHls.encryption === 'drm' && wvHls.drmSystem === 'Widevine', 'Widevine session key = DRM Widevine', wvHls);

const liveDrmHls = S.parseM3U8('#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://live-ch1",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:4,\nseg1.ts\n', 'https://x/live.m3u8');
assert(liveDrmHls.live === true && liveDrmHls.encryption === 'drm' && liveDrmHls.drmSystem === 'FairPlay', 'live HLS WITH DRM = live:true + encryption:drm (blocked)', liveDrmHls);

assert(S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:4,\na.ts', 'https://x/').encryption === 'none', 'METHOD=NONE is clear');
assert(S.parseM3U8('<html>nope</html>', 'https://x/').valid === false, 'not a playlist');
if (typeof S.classifyEmeKeySystem === 'function') {
  const emeWv = S.classifyEmeKeySystem('com.widevine.alpha');
  const emePr = S.classifyEmeKeySystem('com.microsoft.playready');
  assert(emeWv.encryption === 'drm' && emeWv.drmSystem === 'Widevine', 'EME requestMediaKeySystemAccess Widevine classified', emeWv);
  assert(emePr.encryption === 'drm' && emePr.drmSystem === 'PlayReady', 'EME requestMediaKeySystemAccess PlayReady classified', emePr);
}

console.log('DASH');
const mpd = `<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT1M5.5S">
 <Period>
  <AdaptationSet mimeType="video/mp4" contentType="video">
   <Representation id="1" bandwidth="800000" width="640" height="360"/>
   <Representation id="2" bandwidth="3000000" width="1920" height="1080"/>
  </AdaptationSet>
  <AdaptationSet mimeType="audio/mp4" lang="en"><Representation id="a" bandwidth="128000"/></AdaptationSet>
 </Period>
</MPD>`;
const dd = S.parseMPD(mpd);
assert(dd.valid && !dd.drm && !dd.live && dd.drmSystem === null, 'static clear MPD');
assert(dd.duration === 66, 'duration from PT1M5.5S', dd.duration);
assert(dd.videos.map((v) => v.height).join(',') === '1080,360' && dd.audios === 1, 'video heights best first + 1 audio', dd);
const wvMpd = S.parseMPD(mpd.replace('<Representation id="1"', '<ContentProtection value="cenc" schemeIdUri="urn:mpeg:dash:mp4protection:2011"/><ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/><Representation id="1"'));
assert(wvMpd.drm === true && wvMpd.drmSystem === 'Widevine' && /Widevine/i.test(wvMpd.encryptionReason), 'MPD Widevine ContentProtection = DRM Widevine', wvMpd);

// Build a synthetic 32-byte ISO BMFF PSSH box with PlayReady SystemID (9a04f079-9840-4286-ab92-e65be0885f95)
const prPsshBuf = Buffer.concat([
  Buffer.from([0, 0, 0, 32]),
  Buffer.from('pssh'),
  Buffer.from([0, 0, 0, 0]),
  Buffer.from('9a04f07998404286ab92e65be0885f95', 'hex'),
  Buffer.from([0, 0, 0, 0]),
]);
const psshOnlyMpd = S.parseMPD(mpd.replace('<Representation id="1"', `<cenc:pssh>${prPsshBuf.toString('base64')}</cenc:pssh><Representation id="1"`));
assert(psshOnlyMpd.drm === true && psshOnlyMpd.drmSystem === 'PlayReady', 'MPD PSSH box SystemID = DRM PlayReady', psshOnlyMpd);

const liveDrmMpd = S.parseMPD(mpd.replace('type="static"', 'type="dynamic"').replace('<Representation id="1"', '<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/><Representation id="1"'));
assert(liveDrmMpd.live === true && liveDrmMpd.drm === true && liveDrmMpd.drmSystem === 'Widevine', 'live MPD WITH Widevine DRM = live:true + drm:true', liveDrmMpd);
assert(S.parseMPD(mpd.replace('type="static"', 'type="dynamic"')).live === true, 'dynamic = live');
assert(S.parseMPD('{"not":"xml"}').valid === false, 'not an MPD');
assert(S.isoDuration('PT2H') === 7200 && S.isoDuration('P1DT1S') === 86401, 'ISO durations');

console.log('pickReferer');
assert(typeof S.pickReferer === 'function', 'pickReferer exported');
if (typeof S.pickReferer === 'function') {
  assert(
    S.pickReferer({ frameId: 2, frameUrl: 'https://player.embed.example/e/99?k=1', initiator: 'https://player.embed.example', pageUrl: 'https://site.example/watch/ep-1' })
      === 'https://player.embed.example/e/99?k=1',
    'sub_frame URL beats outer pageUrl',
  );
  assert(
    S.pickReferer({ frameId: 2, frameUrl: '', initiator: 'https://player.embed.example', pageUrl: 'https://site.example/watch/ep-1' })
      === 'https://player.embed.example/',
    'cross-origin iframe initiator beats outer pageUrl when sub_frame URL was not seen',
  );
  assert(
    S.pickReferer({ frameId: 0, frameUrl: '', initiator: 'https://site.example', pageUrl: 'https://site.example/watch/ep-1' })
      === 'https://site.example/watch/ep-1',
    'same-origin main frame keeps full pageUrl',
  );
  assert(
    S.pickReferer({ frameId: 0, frameUrl: 'javascript:alert(1)', initiator: 'chrome-extension://abc', pageUrl: 'https://site.example/v' })
      === 'https://site.example/v',
    'non-http(s) frameUrl/initiator ignored',
  );
}

console.log('cleanMediaTitle');
assert(typeof S.cleanMediaTitle === 'function', 'cleanMediaTitle exported');
if (typeof S.cleanMediaTitle === 'function') {
  assert(
    S.cleanMediaTitle('(3) Xem phim Lên Đường Nhậm Chức Tập 01 Full HD Vietsub | cinema-stream.example', 'https://cinema-stream.example/phim/len-duong-nham-chuc/tap-01')
      === 'Lên Đường Nhậm Chức Tập 01',
    'strips notification count, Xem phim prefix, Full HD Vietsub & domain suffix while keeping episode',
  );
  assert(
    S.cleanMediaTitle('[DOC-451] Midnight Harbor Story - Watch Online Free - vodhub.example', 'https://www.vodhub.example/v/doc-451')
      === '[DOC-451] Midnight Harbor Story',
    'preserves catalog code [DOC-451] and strips English Watch Online Free + host suffix',
  );
  assert(
    S.cleanMediaTitle('Just a moment...', 'https://cinema.example/v/1') === '',
    'rejects anti-bot / generic player placeholder titles',
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
