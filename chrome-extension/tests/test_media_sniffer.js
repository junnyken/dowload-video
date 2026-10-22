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
assert(!p.master && p.segments === 3 && p.duration === 16 && p.live === false, 'media playlist: 3 segments, 16 s, ended', p);
const live = S.parseM3U8('#EXTM3U\n#EXTINF:2,\na.ts\n#EXTINF:2,\nb.ts\n', 'https://x/l.m3u8');
assert(live.live === true, 'no ENDLIST = live');
assert(S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST', 'https://x/').encryption === 'aes128', 'AES-128 flagged');
assert(S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://x",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:4,\na.ts', 'https://x/').encryption === 'drm', 'FairPlay = DRM');
assert(S.parseM3U8('#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES-CTR,KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8', 'https://x/').encryption === 'drm', 'Widevine session key = DRM');
assert(S.parseM3U8('#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:4,\na.ts', 'https://x/').encryption === 'none', 'METHOD=NONE is clear');
assert(S.parseM3U8('<html>nope</html>', 'https://x/').valid === false, 'not a playlist');

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
assert(dd.valid && !dd.drm && !dd.live, 'static clear MPD');
assert(dd.duration === 66, 'duration from PT1M5.5S', dd.duration);
assert(dd.videos.map((v) => v.height).join(',') === '1080,360' && dd.audios === 1, 'video heights best first + 1 audio', dd);
assert(S.parseMPD(mpd.replace('<Representation id="1"', '<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/><Representation id="1"')).drm === true, 'ContentProtection = DRM');
assert(S.parseMPD(mpd.replace('type="static"', 'type="dynamic"')).live === true, 'dynamic = live');
assert(S.parseMPD('{"not":"xml"}').valid === false, 'not an MPD');
assert(S.isoDuration('PT2H') === 7200 && S.isoDuration('P1DT1S') === 86401, 'ISO durations');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
