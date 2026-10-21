/**
 * 5.11.0: a channel opened by the VidGrab app / website ("#vg-relay=<token>")
 * scrolls by itself and hands its video list to /api/v1/channel-relay.
 * Run with: node tests/test_channel_relay.js
 *
 *  [1] interceptor.js keeps the token at document_start
 *  [2] the panel starts scrolling by itself and sends progress + the final list
 *  [3] vgRelayToken reads the hash, else sessionStorage
 *  [4] vgRelayPayload: video links only (image posts counted as skipped), titles
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
const icp = fs.readFileSync(path.join(__dirname, '..', 'interceptor.js'), 'utf8');

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
assert(/vg-relay=\(\[A-Za-z0-9_-\]\{32\}\)/.test(icp) && icp.includes("sessionStorage.setItem('__vgRelay'"), '[1] interceptor stores the token');

// [2]
const panel = src.slice(src.indexOf('function injectChannelPanel('), src.indexOf('function vgRelayToken('));
assert(panel.includes('const relayTok   = vgRelayToken()'), '[2] panel reads the token');
assert(/if \(relayTok\) \{[\s\S]*scrapeBtn\.click\(\)/.test(panel), '[2] scrolls without a click');
assert(panel.includes('vgRelaySend(relayTok, false)') && panel.includes('vgRelaySend(relayTok, true)'), '[2] progress + final list');

// [3] [4]
const TOK = 'a'.repeat(32);
function ctxFor(hash, stored) {
  const store = new Map(stored ? [['__vgRelay', stored]] : []);
  const ctx = {
    location: { hash },
    sessionStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) },
    videoSet: new Set([
      'https://www.douyin.com/video/7696450520369712419',
      'https://www.douyin.com/note/7600000000000000001',
      'https://www.douyin.com/video/7696450520369712420',
    ]),
    _vgDouyinDirect: new Map([['7696450520369712419', { url: 'x', title: '《夜盲》3' }]]),
    document: {
      querySelector: (sel) => {
        if (sel === 'h1') return { textContent: ' 凌晨睡不着 ' };
        if (sel.includes('7696450520369712420')) return { querySelector: () => ({ alt: 'from alt' }) };
        return null;
      },
    },
    store,
  };
  vm.createContext(ctx);
  vm.runInContext(`const VG_RELAY_KEY = '__vgRelay';\n${grab('vgRelayToken')}\n${grab('vgRelayPayload')}`, ctx);
  return ctx;
}

let c = ctxFor(`#vg-relay=${TOK}`);
assert(c.vgRelayToken() === TOK && c.store.get('__vgRelay') === TOK, '[3] hash → token, kept');
c = ctxFor('', TOK);
assert(c.vgRelayToken() === TOK, '[3] no hash (router rewrote it) → sessionStorage');
c = ctxFor('#vg-relay=short');
assert(c.vgRelayToken() === null, '[3] malformed token ignored');

const p = ctxFor('').vgRelayPayload(true);
assert(p.items.length === 2 && p.skipped === 1, '[4] videos only, image post skipped');
assert(p.items[0].title === '《夜盲》3' && p.items[1].title === 'from alt', '[4] titles from the API capture, else the cover alt');
assert(p.channelTitle === '凌晨睡不着' && p.done === true, '[4] channel title + done');

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──`);
process.exit(failed ? 1 : 0);
