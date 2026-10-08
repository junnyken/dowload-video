/**
 * Every call the extension makes to our API carries the sign-in token.
 * Run with: node tests/test_auth_every_call.js
 *
 * 08/10 (task #6148): the server caps guests at 1080p and 5 downloads/day.
 * The quality rows in the popup went through VG_FETCH_LINK, which sent no
 * Authorization header, so a signed-in user who picked 4K was served as a
 * guest. Only the main "Tải" button (VG_DOWNLOAD_NOW) sent the token.
 *
 *  [1] every fetch() to /api/v1/ in background.js / popup.js sends the token
 *      (authHeaders / vgAuthHeaders / an explicit Bearer), except /ping
 *  [2] VG_API_FETCH (the content-script proxy) adds the token, and still
 *      only for our own API origin
 *  [3] authHeaders / vgAuthHeaders add Authorization only when a token exists
 */

const fs = require('fs');
const path = require('path');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const dir = path.join(__dirname, '..');
const files = {
  'background.js': fs.readFileSync(path.join(dir, 'background.js'), 'utf8'),
  'popup.js': fs.readFileSync(path.join(dir, 'popup.js'), 'utf8'),
};

// The fetch( call and its options object: from "fetch(" to the matching ")".
function callAt(src, i) {
  let depth = 0;
  for (let j = i + 'fetch'.length; j < src.length; j++) {
    if (src[j] === '(') depth++;
    else if (src[j] === ')') { depth--; if (depth === 0) return src.slice(i, j + 1); }
  }
  return src.slice(i);
}

console.log('\n[1] every /api/v1/ call sends the token');
const EXEMPT = ['/api/v1/ping'];   // reachability probe, no user data
for (const [name, src] of Object.entries(files)) {
  const re = /\bfetch\(/g;
  let m, seen = 0;
  while ((m = re.exec(src))) {
    const call = callAt(src, m.index);
    if (!call.includes('/api/v1/') || EXEMPT.some(e => call.includes(e))) continue;
    seen++;
    const line = src.slice(0, m.index).split('\n').length;
    assert(/authHeaders\(|vgAuthHeaders\(|Authorization:\s*`Bearer/.test(call),
           `${name}:${line} ${call.slice(0, 70).replace(/\s+/g, ' ')}…`);
  }
  assert(seen >= 5, `${name}: found the API calls (${seen})`);
}

console.log('\n[2] VG_API_FETCH adds the token, own origin only');
{
  const bg = files['background.js'];
  const i = bg.indexOf("msg.type === 'VG_API_FETCH'");
  const block = bg.slice(i, bg.indexOf('sendResponse({ ok: resp.ok', i));
  assert(/target\.origin !== new URL\(base\)\.origin/.test(block), 'origin check still before the fetch');
  assert(block.indexOf('target.origin') < block.indexOf('authHeaders('), 'token added after the origin check');
}

console.log('\n[3] helpers add Authorization only with a token');
async function check(fnSrc, fnName, store) {
  global.chrome = { storage: { local: { get: async () => store } } };
  // background.js reads the token through getAuthToken()
  const getAuthToken = async () => store.vg_auth_token || null;
  const AUTH_TOKEN_KEY = 'vg_auth_token';
  // eslint-disable-next-line no-new-func
  const fn = new Function('getAuthToken', 'AUTH_TOKEN_KEY', `${fnSrc}; return ${fnName};`)(getAuthToken, AUTH_TOKEN_KEY);
  return fn({ 'Content-Type': 'application/json' });
}
function extract(src, fnName) {
  const start = src.indexOf(`async function ${fnName}(`);
  if (start < 0) throw new Error(`${fnName} not found`);
  let i = src.indexOf('{', src.indexOf(')', start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(`${fnName} unterminated`);
}
(async () => {
  for (const [file, fnName] of [['background.js', 'authHeaders'], ['popup.js', 'vgAuthHeaders']]) {
    const src = extract(files[file], fnName);
    const signed = await check(src, fnName, { vg_auth_token: 'tok123' });
    const guest = await check(src, fnName, {});
    assert(signed.Authorization === 'Bearer tok123' && signed['Content-Type'] === 'application/json',
           `${fnName}: signed in → Bearer + keeps other headers`);
    assert(!('Authorization' in guest) && guest['Content-Type'] === 'application/json',
           `${fnName}: guest → no Authorization`);
  }
  console.log(`\n── Summary: ${passed} passed, ${failed} failed ──\n`);
  process.exit(failed ? 1 : 0);
})();
