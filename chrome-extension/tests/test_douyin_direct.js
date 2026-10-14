/**
 * Douyin direct download from the user's browser (14/10).
 * Run with: node tests/test_douyin_direct.js
 *
 * Runs the REAL helpers cut out of the shipped content.js:
 *  [1] vgDouyinIdOf: /video/<id>, /note/<id>, ?modal_id=, non-Douyin → null
 *  [2] vgDouyinFindInRenderData: snake_case + camelCase (playAddr[0].src, //host), playwm → play
 *  [3] vgDouyinDirectForPage: interceptor cache first, then RENDER_DATA, then <video>; other hosts → null
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) { if (cond) { console.log(`  ✓ ${name}`); passed++; } else { console.error(`  ✗ ${name}`); failed++; } }

const src = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
const start = src.indexOf('// ── Douyin: download with the user\'s own browser/IP (14/10)');
const end = src.indexOf('\nfunction vgDouyinDirectForPage()', start);
const fnEnd = src.indexOf('\n}\n', end + 1) + 3;
if (start < 0 || end < 0) { console.error('douyin block not found'); process.exit(1); }
const block = src.slice(start, fnEnd);

function sandbox({ href, renderData, videoSrc }) {
  const ctx = {
    URL, JSON, String, decodeURIComponent, Object,
    location: { href },
    document: {
      title: 'Douyin page',
      getElementById: (id) => (id === 'RENDER_DATA' && renderData ? { textContent: encodeURIComponent(JSON.stringify(renderData)) } : null),
      querySelector: (sel) => (sel === 'video' && videoSrc ? { currentSrc: videoSrc, querySelector: () => null } : null),
    },
  };
  vm.createContext(ctx);
  vm.runInContext(block + '\n;globalThis.__t = { vgDouyinIdOf, vgDouyinFindInRenderData, vgDouyinDirectForPage, _vgDouyinDirect };', ctx);
  return ctx.__t;
}

const ID = '7677628923164790070';
console.log('[1] id');
let t = sandbox({ href: 'https://www.douyin.com/' });
assert(t.vgDouyinIdOf(`https://www.douyin.com/video/${ID}`) === ID, '/video/<id>');
assert(t.vgDouyinIdOf(`https://www.douyin.com/note/${ID}?x=1`) === ID, '/note/<id>');
assert(t.vgDouyinIdOf(`https://www.douyin.com/jingxuan?modal_id=${ID}`) === ID, '?modal_id=');
assert(t.vgDouyinIdOf(`https://www.tiktok.com/video/${ID}`) === null, 'other host → null');
assert(t.vgDouyinIdOf(`https://evil-douyin.com/video/${ID}`) === null, 'look-alike host → null');

console.log('[2] RENDER_DATA');
const camel = { app: { videoDetail: { awemeId: ID, desc: 'Bắc cực', video: { playAddr: [{ src: '//v26-web.douyinvod.com/abc/playwm/x.mp4' }] } } } };
let r = t.vgDouyinFindInRenderData(camel, ID);
assert(r && r.url === 'https://v26-web.douyinvod.com/abc/play/x.mp4' && r.title === 'Bắc cực', 'camelCase playAddr, // and playwm handled');
const snake = { a: [{ aweme_id: ID, desc: 'd', video: { play_addr: { url_list: ['https://api-play.amemv.com/aweme/v1/playwm/?video_id=v1'] } } }] };
r = t.vgDouyinFindInRenderData(snake, ID);
assert(r && r.url === 'https://api-play.amemv.com/aweme/v1/play/?video_id=v1', 'snake_case play_addr');
assert(t.vgDouyinFindInRenderData(snake, '1234567890123456') === null, 'other id → null');

console.log('[3] page resolution order');
t = sandbox({ href: `https://www.douyin.com/video/${ID}`, renderData: snake, videoSrc: 'https://v3-web.douyinvod.com/v.mp4' });
t._vgDouyinDirect.set(ID, { url: 'https://cache.douyinvod.com/c.mp4', title: 'cached' });
assert(t.vgDouyinDirectForPage().url === 'https://cache.douyinvod.com/c.mp4', 'interceptor cache first');
t = sandbox({ href: `https://www.douyin.com/video/${ID}`, renderData: snake, videoSrc: 'https://v3-web.douyinvod.com/v.mp4' });
assert(t.vgDouyinDirectForPage().url.startsWith('https://api-play.amemv.com'), 'then RENDER_DATA');
t = sandbox({ href: `https://www.douyin.com/video/${ID}`, videoSrc: 'https://v3-web.douyinvod.com/v.mp4' });
assert(t.vgDouyinDirectForPage().url === 'https://v3-web.douyinvod.com/v.mp4', 'then <video> on a Douyin media host');
t = sandbox({ href: `https://www.douyin.com/video/${ID}`, videoSrc: 'blob:https://www.douyin.com/123' });
assert(t.vgDouyinDirectForPage() === null, 'blob: video → null (server path)');
t = sandbox({ href: `https://www.douyin.com/video/${ID}`, videoSrc: 'https://evil.example/v.mp4' });
assert(t.vgDouyinDirectForPage() === null, 'foreign media host → null');
t = sandbox({ href: 'https://www.youtube.com/watch?v=x', renderData: snake });
assert(t.vgDouyinDirectForPage() === null, 'not a Douyin video page → null');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
