/**
 * The channel panel's auto-scroll moves the element that really scrolls.
 * Run with: node tests/test_channel_scroll.js
 *
 * 20/10: a Douyin profile sets body { overflow: hidden } and scrolls
 * `.route-scroll-container`; window.scrollBy moved it 0 px, so the panel
 * stopped after the first ~18 videos of a 194-video channel.
 *
 *  [1] the auto-scroll loop goes through vgScrollPage(), not window.scrollBy
 *  [2] vgScrollTarget() prefers .route-scroll-container, else the tallest
 *      scrollable box, else the window
 *  [3] the loop stops on Douyin's own end-of-list line
 *  [4] 5.16.1 Facebook: brings the last reel to the bottom of the screen (a
 *      blind step jumped past the reels grid: 31 sent of 110), 8 empty rounds
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');

// [1]
const loop = src.slice(src.indexOf('while (stable < MAX_STABLE)'), src.indexOf("scrollBar.style.width = '100%'"));
assert(loop.includes('vgScrollPage()'), '[1] loop scrolls through vgScrollPage()');
assert(!loop.includes('window.scrollBy'), '[1] loop no longer calls window.scrollBy directly');
assert(loop.includes('vgReachedListEnd()'), '[3] loop checks the end-of-list line');

// [2] run the real helpers against a fake DOM
function grab(name) {
  const start = src.indexOf(`function ${name}(`);
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) break;
  }
  return src.slice(start, i + 1);
}
const code = ['vgScrollTarget', 'vgScrollPage', 'vgReachedListEnd'].map(grab).join('\n');

function box(cls, scrollHeight, clientHeight, overflowY = 'auto') {
  return { className: cls, scrollHeight, clientHeight, overflowY, scrolled: 0,
           scrollBy(o) { this.scrolled += o.top; } };
}
function sandbox({ named = null, boxes = [], docHeight = 1000, bodyOverflow = 'hidden', text = '', host = 'www.douyin.com', links = [] }) {
  const win = { innerHeight: 1000, scrolled: 0, scrollBy(o) { this.scrolled += o.top; } };
  const body = { overflowY: bodyOverflow, innerText: text };
  const ctx = {
    window: win,
    location: { hostname: host },
    getComputedStyle: (el) => ({ overflowY: el.overflowY }),
    document: {
      body,
      scrollingElement: { scrollHeight: docHeight },
      documentElement: { scrollHeight: docHeight },
      querySelector: (sel) => (sel === '.route-scroll-container' ? named : null),
      querySelectorAll: (sel) => (/reel/.test(sel) ? links : boxes),
    },
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return ctx;
}

let s = sandbox({ named: box('route-scroll-container', 5000, 1000) });
s.vgScrollPage();
assert(s.document.querySelector('.route-scroll-container').scrolled === 1800 && s.window.scrolled === 0,
  '[2] Douyin profile: scrolls .route-scroll-container, not the window');

const tall = box('feed', 9000, 900), short = box('side', 2000, 900), tiny = box('menu', 900, 200);
s = sandbox({ boxes: [short, tiny, tall] });
assert(s.vgScrollTarget() === tall, '[2] no named container: the tallest scrollable box');

s = sandbox({ docHeight: 8000, bodyOverflow: 'visible' });
s.vgScrollPage();
assert(s.window.scrolled === 1800, '[2] a normal page still scrolls the window');

assert(sandbox({ text: '……暂时没有更多了' }).vgReachedListEnd() === true, '[3] end line detected');
assert(sandbox({ text: '作品 194' }).vgReachedListEnd() === false, '[3] no end line, keep going');

// [4]
const reel = (id) => ({ id, into: null, scrollIntoView(o) { this.into = o.block; } });
const reels = [reel(1), reel(2), reel(3)];
s = sandbox({ host: 'www.facebook.com', docHeight: 8000, bodyOverflow: 'visible', links: reels });
s.vgScrollPage();
assert(reels[2].into === 'end' && reels[0].into === null, '[4] Facebook: the last reel is scrolled to the bottom of the screen');
assert(s.window.scrolled === 600, '[4] Facebook: then a little further, so the next row starts loading');
s = sandbox({ host: 'www.facebook.com', docHeight: 8000, bodyOverflow: 'visible', links: [] });
s.vgScrollPage();
assert(s.window.scrolled === 1800, '[4] Facebook page without reels yet: normal step');
assert(/MAX_STABLE = \/\(\^\|\\\.\)facebook\\\.com\$\/i\.test\(location\.hostname\) \? 8 : 5/.test(src), '[4] Facebook waits 8 empty rounds, others 5');

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──`);
process.exit(failed ? 1 : 0);
