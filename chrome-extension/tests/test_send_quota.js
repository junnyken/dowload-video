/**
 * 5.10.0 (owner 21/10): the channel panel sends only what today's allowance
 * covers; the rest stays on the page for a later press.
 * Run with: node tests/test_send_quota.js
 *
 *  [1] the send handler asks /api/v1/quota first and sends the capped list
 *  [2] vgRemainingFor reads the per-platform or total allowance
 *  [3] vgSplitByCap keeps collection order; null = no cap
 *  [4] vgPlatformOfHost maps the page host to the server's platform slug
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const src = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');

function grab(name) {
  const start = src.indexOf(`function ${name}(`);
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) break;
  }
  return src.slice(start, i + 1);
}

// [1]
const handler = src.slice(src.indexOf("sendBtn.addEventListener('click'"), src.indexOf('function vgRemainingFor('));
assert(handler.includes('/api/v1/quota'), '[1] asks the allowance first');
assert(handler.indexOf('/api/v1/quota') < handler.indexOf('/api/v1/bulk-download'), '[1] before sending');
assert(/vgSplitByCap\(pending, cap\)/.test(handler) && /body: \{ urls,/.test(handler), '[1] sends the capped list');
assert(handler.includes('vgSentUrls.add'), '[1] remembers what was sent');

const ctx = {};
vm.createContext(ctx);
vm.runInContext([grab('vgRemainingFor'), grab('vgSplitByCap'), grab('vgPlatformOfHost')].join('\n'), ctx);

// [2]
const snap = (o) => ({ platform_quota: { scope: 'total', unlimited: false, remaining_total: 7, platforms: [{ platform: 'douyin', remaining: 3 }], ...o } });
assert(ctx.vgRemainingFor(snap({}), 'douyin') === 7, '[2] total scope → remaining_total');
assert(ctx.vgRemainingFor(snap({ scope: 'platform' }), 'douyin') === 3, '[2] platform scope → that platform');
assert(ctx.vgRemainingFor(snap({ unlimited: true }), 'douyin') === null, '[2] unlimited → no cap');
assert(ctx.vgRemainingFor(null, 'douyin') === null, '[2] unknown → no cap');

// [3]
assert(eq(ctx.vgSplitByCap(['a', 'b', 'c'], 2), { now: ['a', 'b'], later: ['c'] }), '[3] first two now');
assert(eq(ctx.vgSplitByCap(['a', 'b'], null), { now: ['a', 'b'], later: [] }), '[3] null = all now');

// [4]
assert(ctx.vgPlatformOfHost('www.douyin.com') === 'douyin', '[4] douyin');
assert(ctx.vgPlatformOfHost('www.kuaishou.com') === 'kuaishou', '[4] kuaishou');
assert(ctx.vgPlatformOfHost('www.rednote.com') === 'xiaohongshu', '[4] rednote');
assert(ctx.vgPlatformOfHost('space.bilibili.com') === 'bilibili', '[4] bilibili');
assert(ctx.vgPlatformOfHost('douyin.com.evil.example') === 'other', '[4] lookalike host');

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──`);
process.exit(failed ? 1 : 0);
