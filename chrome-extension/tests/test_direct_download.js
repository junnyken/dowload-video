/**
 * 5.18.0 direct download — the pure parts of direct-download.js.
 * Run with: node tests/test_direct_download.js
 *
 *  [1] which downloads are tried in the browser (not YouTube / MP3 / subtitles)
 *  [2] vgPageMedia() reads each platform's own data from a page (fake DOM)
 *  [3] pickSniffed() takes one complete video file, never split DASH pieces
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'direct-download.js'), 'utf8');
const ctx = { URL, console };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const D = ctx.vgDirect;

console.log('[1] eligible');
assert(D.directEligible({ url: 'https://www.facebook.com/reel/910445711561956', quality: 'video' }), 'Facebook reel, video');
assert(D.directEligible({ url: 'https://www.tiktok.com/@a/video/7339598209432292640', quality: 'best' }), 'TikTok, best');
assert(!D.directEligible({ url: 'https://www.youtube.com/watch?v=abc', quality: 'video' }), 'YouTube -> server');
assert(!D.directEligible({ url: 'https://open.spotify.com/track/x', quality: 'video' }), 'Spotify -> server');
assert(!D.directEligible({ url: 'https://www.facebook.com/reel/1', quality: 'mp3' }), 'MP3 -> server converts');
assert(!D.directEligible({ url: 'https://www.facebook.com/reel/1', quality: '1080' }), 'a chosen height -> server');
assert(!D.directEligible({ url: 'https://www.facebook.com/reel/1', quality: 'video', download_subs: true }), 'subtitles -> server');
assert(!D.directEligible({ url: 'http://example.com/v', quality: 'video' }), 'plain http -> server');

console.log('[2] page data');
function page(host, html, { metas = {}, videos = [], ld = [], byId = {} } = {}) {
  const c = {
    location: { hostname: host },
    document: {
      title: 'Doc title',
      documentElement: { innerHTML: html },
      querySelector: (sel) => {
        for (const [k, v] of Object.entries(metas)) if (sel.includes(`"${k}"`)) return { content: v };
        return null;
      },
      querySelectorAll: (sel) => (sel === 'video' ? videos : sel.includes('ld+json') ? ld.map((t) => ({ textContent: t })) : []),
      getElementById: (id) => (byId[id] ? { textContent: byId[id] } : null),
    },
    JSON, RegExp,
  };
  vm.createContext(c);
  vm.runInContext(`result = (${D.vgPageMedia.toString()})()`, c);
  return c.result;
}
// as measured on a real reel page 22/10 (JSON inside a <script>)
const fbHtml = '<script>{"x":1,"browser_native_sd_url":"https:\\/\\/video-hkg1-2.xx.fbcdn.net\\/o1\\/v\\/sd.mp4?a=1&amp;b=2","browser_native_hd_url":"https:\\/\\/video-hkg1-2.xx.fbcdn.net\\/o1\\/v\\/t2\\/f2\\/m366\\/AQOT.mp4?_nc_cat=107&_nc_sid=5e9"}</script>';
let r = page('www.facebook.com', fbHtml, { metas: { 'og:title': 'Reel title' } });
assert(r && r.url === 'https://video-hkg1-2.xx.fbcdn.net/o1/v/t2/f2/m366/AQOT.mp4?_nc_cat=107&_nc_sid=5e9' && r.via === 'browser_native_hd_url',
  'Facebook: browser_native_hd_url, unescaped');
assert(r && r.title === 'Reel title', 'Facebook: og:title');
r = page('www.facebook.com', '<script>{"browser_native_sd_url":"https:\\/\\/v.fbcdn.net\\/sd.mp4?a=1&amp;b=2"}</script>');
assert(r && r.url === 'https://v.fbcdn.net/sd.mp4?a=1&b=2', 'Facebook: falls back to sd, &amp; decoded');
r = page('www.facebook.com', '<video src="https://v.fbcdn.net/dash-piece.mp4"></video>', { videos: [{ currentSrc: 'https://v.fbcdn.net/dash-piece.mp4' }] });
assert(r === null, 'Facebook: never a <video> DASH piece (no sound)');
const tk = JSON.stringify({ __DEFAULT_SCOPE__: { 'webapp.video-detail': { itemInfo: { itemStruct: { desc: 'tiktok desc', video: { playAddr: 'https://v16-webapp-prime.tiktok.com/video/tos/x/?mime_type=video_mp4', downloadAddr: 'https://v16.tiktok.com/wm' } } } } } });
r = page('www.tiktok.com', '', { byId: { __UNIVERSAL_DATA_FOR_REHYDRATION__: tk } });
assert(r && r.url.startsWith('https://v16-webapp-prime.tiktok.com/') && r.title === 'tiktok desc', 'TikTok: playAddr (no watermark) + desc');
assert(page('www.tiktok.com', '', {}) === null, 'TikTok: no data -> null (server)');
r = page('www.instagram.com', '<script>{"video_versions":[{"width":720,"height":1280,"url":"https:\\/\\/scontent.cdninstagram.com\\/v\\/r.mp4?x=1"}]}</script>');
assert(r && r.url === 'https://scontent.cdninstagram.com/v/r.mp4?x=1', 'Instagram: video_versions[0].url');
r = page('example.org', '', { metas: { 'og:video:secure_url': 'https://cdn.example.org/v.mp4' } });
assert(r && r.via === 'og:video', 'any site: og:video');
r = page('example.org', '', { metas: { 'og:video': 'https://cdn.example.org/live.m3u8' }, videos: [{ currentSrc: 'blob:https://example.org/1' }, { src: 'https://cdn.example.org/clip.webm' }] });
assert(r && r.url === 'https://cdn.example.org/clip.webm', 'any site: skips HLS / blob:, takes <video src>');
r = page('example.org', '', { ld: ['{"@type":"VideoObject","name":"LD","contentUrl":"https://cdn.example.org/ld.mp4"}'] });
assert(r && r.url === 'https://cdn.example.org/ld.mp4' && r.title === 'LD', 'any site: JSON-LD contentUrl');

console.log('[3] sniffed files');
const f = (url, mime, size) => ({ kind: 'file', url, mime, size, encryption: 'none' });
assert(D.pickSniffed([f('https://cdn.a.com/small.mp4', 'video/mp4', 1e6), f('https://cdn.a.com/big.mp4', 'video/mp4', 9e6)]).url === 'https://cdn.a.com/big.mp4', 'largest complete file');
assert(D.pickSniffed([f('https://cdn.b.com/v.mp4', 'video/mp4', 9e6), f('https://cdn.b.com/a.mp4', 'audio/mp4', 1e6)]) === null, 'video + audio from one host = split DASH: none');
assert(D.pickSniffed([f('https://video.xx.fbcdn.net/v.mp4', 'video/mp4', 9e6)]) === null, 'fbcdn pieces never');
assert(D.pickSniffed([{ kind: 'hls', url: 'https://c/x.m3u8' }, { ...f('https://c/d.mp4', 'video/mp4', 9e6), encryption: 'drm' }]) === null, 'no HLS / DRM');

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──`);
process.exit(failed ? 1 : 0);
