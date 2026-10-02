/**
 * Security regression tests for the extension hardening pass.
 * Run with: node tests/test_security_hardening.js
 *
 * Each block maps to a concrete hole that existed before:
 *  [1] vg_api_base accepted any "http*" string → cleartext/attacker backend
 *  [2] browser session cookies were POSTed to whatever that base pointed at
 *  [3] VG_API_FETCH proxied ANY url/method/headers with SW privileges
 *  [4] VG_SET_AUTH_TOKEN accepted a token from any sender
 *  [5] account/web links opened the JSON API host instead of the web app
 */

const fs = require('fs');
const path = require('path');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const dir = path.join(__dirname, '..');
const background = fs.readFileSync(path.join(dir, 'background.js'), 'utf8');
const popup = fs.readFileSync(path.join(dir, 'popup.js'), 'utf8');
const content = fs.readFileSync(path.join(dir, 'content.js'), 'utf8');

// Pull the real implementations out of background.js so we test the shipped
// code rather than a copy that can drift.
function extract(src, fnName) {
  const start = src.indexOf(`function ${fnName}(`);
  if (start < 0) throw new Error(`${fnName} not found`);
  let i = src.indexOf('{', start), depth = 0, end = -1;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
  }
  return src.slice(start, end);
}

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
// Pull the shipped constants/helpers verbatim (no hand-copied lists to drift).
const grabLine = (src, re) => { const m = src.match(re); if (!m) throw new Error('missing ' + re); return m[0]; };
const FIRST_PARTY_EXACT_HOSTS = eval(grabLine(background, /(?<=const FIRST_PARTY_EXACT_HOSTS = )\[[^\]]*\]/));
const FIRST_PARTY_SUFFIXES = eval(grabLine(background, /(?<=const FIRST_PARTY_SUFFIXES = )\[[^\]]*\]/));
const LEGACY_API_HOST = eval(grabLine(background, /(?<=const LEGACY_API_HOST = )'[^']*'/));
const DEFAULT_API_BASE = eval(grabLine(background, /(?<=const DEFAULT_API_BASE = )'[^']*'/));
const isFirstPartyHost = eval(`(${extract(background, 'isFirstPartyHost')})`);
const isFirstPartyUrl = eval(`(${extract(background, 'isFirstPartyUrl')})`);
const migrateLegacyApiBase = eval(`(${extract(background, 'migrateLegacyApiBase')})`);
const normalizeApiBase = eval(`(${extract(background, 'normalizeApiBase')})`);
const isCookieTrustedBase = eval(`(${extract(background, 'isCookieTrustedBase')})`);

console.log('\n[1] normalizeApiBase rejects unsafe bases');
assert(normalizeApiBase('http://evil.example.com') === null, 'plain http remote host rejected');
assert(normalizeApiBase('http://attacker.tld/collect') === null, 'http exfil endpoint rejected');
assert(normalizeApiBase('javascript:alert(1)') === null, 'javascript: rejected');
assert(normalizeApiBase('ftp://x.tld') === null, 'ftp: rejected');
assert(normalizeApiBase('httpfoo') === null, '"httpfoo" rejected (old check was startsWith("http"))');
assert(normalizeApiBase('') === null, 'empty rejected');
assert(normalizeApiBase(null) === null, 'null rejected');
assert(normalizeApiBase(undefined) === null, 'undefined rejected');

console.log('\n[1b] normalizeApiBase accepts and canonicalises valid bases');
assert(normalizeApiBase('https://dvid-api.vibe1.tinhgon.xyz') === 'https://dvid-api.vibe1.tinhgon.xyz', 'prod base kept');
assert(DEFAULT_API_BASE === 'https://dvid-api.vibe1.tinhgon.xyz', 'default API base is the new host');
assert(normalizeApiBase('https://dvid-api.cmc-1.vibenode.matbao.ai') === DEFAULT_API_BASE, 'legacy cmc-1 base migrated to new API base');
assert(migrateLegacyApiBase('https://dvid-api.cmc-1.vibenode.matbao.ai/') === DEFAULT_API_BASE, 'migrate: legacy host (trailing slash) rewritten');
assert(migrateLegacyApiBase('https://x.matbao.ai') === 'https://x.matbao.ai', 'migrate: other hosts untouched');
assert(migrateLegacyApiBase('https://dvid-api.cmc-1.vibenode.matbao.ai.evil.com') === 'https://dvid-api.cmc-1.vibenode.matbao.ai.evil.com', 'migrate: hostname equality, not prefix');
assert(normalizeApiBase('https://x.matbao.ai/') === 'https://x.matbao.ai', 'trailing slash stripped');
assert(normalizeApiBase('  https://x.matbao.ai  ') === 'https://x.matbao.ai', 'whitespace trimmed');
assert(normalizeApiBase('http://localhost:8000') === 'http://localhost:8000', 'localhost http allowed for dev');
assert(normalizeApiBase('http://127.0.0.1:8000') === 'http://127.0.0.1:8000', '127.0.0.1 http allowed for dev');

console.log('\n[2] cookies only leave for a first-party base');
assert(isCookieTrustedBase('https://dvid-api.vibe1.tinhgon.xyz') === true, 'new prod API host (exact) is cookie-trusted');
assert(isCookieTrustedBase('https://dvid.vibe1.tinhgon.xyz') === true, 'new web host (exact) is cookie-trusted');
assert(isCookieTrustedBase('https://x.matbao.ai') === true, 'legacy .matbao.ai suffix still trusted (unchanged)');
assert(isCookieTrustedBase('https://evil.vibe1.tinhgon.xyz') === false, 'SHARED DOMAIN: sibling tenant evil.vibe1.tinhgon.xyz REJECTED');
assert(isCookieTrustedBase('https://dvid.vibe1.tinhgon.xyz.evil.com') === false, 'dvid.vibe1.tinhgon.xyz.evil.com REJECTED');
assert(isCookieTrustedBase('https://dvid-api.vibe1.tinhgon.xyz.evil.com') === false, 'dvid-api...xyz.evil.com REJECTED');
assert(isCookieTrustedBase('https://xdvid.vibe1.tinhgon.xyz') === false, 'prefix-spoof xdvid.vibe1.tinhgon.xyz REJECTED');
assert(isCookieTrustedBase('https://tinhgon.xyz') === false, 'bare tinhgon.xyz REJECTED');
assert(isCookieTrustedBase('http://dvid-api.vibe1.tinhgon.xyz') === false, 'http on first-party host REJECTED');
assert(isCookieTrustedBase('https://dvid-api.cmc-1.vibenode.matbao.ai') === true, 'old matbao.ai suffix behaviour unchanged');

console.log('\n[2b] isFirstPartyHost / isFirstPartyUrl');
assert(isFirstPartyHost('dvid.vibe1.tinhgon.xyz') && isFirstPartyHost('dvid-api.vibe1.tinhgon.xyz'), 'exact hosts accepted');
assert(isFirstPartyHost('DVID.vibe1.tinhgon.xyz') === true, 'case-insensitive exact match');
assert(!isFirstPartyHost('evil.vibe1.tinhgon.xyz'), 'evil.vibe1.tinhgon.xyz rejected');
assert(!isFirstPartyHost('dvid.vibe1.tinhgon.xyz.evil.com'), 'dvid.vibe1.tinhgon.xyz.evil.com rejected');
assert(!isFirstPartyHost('a.dvid.vibe1.tinhgon.xyz'), 'subdomain of exact host rejected');
assert(!isFirstPartyHost('tinhgon.xyz') && !isFirstPartyHost('vibe1.tinhgon.xyz'), 'parent domains rejected');
assert(isFirstPartyUrl('https://dvid-api.vibe1.tinhgon.xyz/api/v1/x') === true, 'URL on exact host accepted');
assert(isFirstPartyUrl('https://evil.vibe1.tinhgon.xyz/x') === false, 'URL on sibling tenant rejected');
assert(isFirstPartyUrl('https://dvid.vibe1.tinhgon.xyz.evil.com/x') === false, 'URL on lookalike rejected');
assert(isFirstPartyUrl('https://evil.com/?u=dvid.vibe1.tinhgon.xyz') === false, 'host name in query string does NOT count (no substring match)');
assert(isFirstPartyUrl('https://evil.com/?u=matbao.ai') === false, 'matbao.ai in query string does NOT count');
assert(isFirstPartyUrl('http://dvid.vibe1.tinhgon.xyz/x') === false, 'plain http rejected');
assert(isFirstPartyUrl('/app/downloads/x.mp4') === false, 'relative path is not a first-party URL');
for (const [name, src] of [['background.js', background], ['popup.js', popup], ['content.js', content]]) {
  assert(!/includes\(['"]matbao\.ai['"]\)/.test(src), `${name} has no substring includes('matbao.ai')`);
  assert(!/includes\(['"][^'"]*tinhgon/.test(src), `${name} has no substring includes('tinhgon')`);
  assert(!/['"]\.[^'"]*tinhgon\.xyz['"]/.test(src), `${name} has no tinhgon suffix rule`);
}
// the three duplicated helper blocks must agree
const helperBlock = (src) => ['FIRST_PARTY_EXACT_HOSTS = ', 'FIRST_PARTY_SUFFIXES = ', 'LEGACY_API_HOST = '].map(k => grabLine(src, new RegExp(k.replace(/[$]/g,'\\$') + "[^;]*;"))).join('|');
assert(helperBlock(background) === helperBlock(popup) && helperBlock(popup) === helperBlock(content), 'helper constants identical in background/popup/content');
assert(isCookieTrustedBase('http://localhost:8000') === true, 'localhost is cookie-trusted (dev)');
assert(isCookieTrustedBase('https://evil.example.com') === false, 'third-party https base NOT cookie-trusted');
assert(isCookieTrustedBase('http://evil.example.com') === false, 'third-party http base NOT cookie-trusted');
assert(isCookieTrustedBase('https://matbao.ai.evil.com') === false, 'suffix-spoofing host NOT cookie-trusted');
assert(isCookieTrustedBase('not a url') === false, 'garbage NOT cookie-trusted');
assert(
  background.includes('if (!isCookieTrustedBase(base))') &&
  background.indexOf('isCookieTrustedBase(base)') < background.indexOf('chrome.cookies.getAll'),
  'cookie trust check runs BEFORE chrome.cookies.getAll'
);

console.log('\n[3] VG_API_FETCH is scoped to the configured API origin');
assert(background.includes("target.origin !== new URL(base).origin"), 'origin equality check present');
assert(background.includes('VG_API_FETCH is restricted'), 'rejects with an explicit error');
// Strip line comments first — the fix leaves a comment quoting the old call.
const backgroundCode = background.replace(/^\s*\/\/.*$/gm, '');
assert(!/fetch\(msg\.url,/.test(backgroundCode), 'no longer fetches msg.url verbatim');
assert(/await fetch\(target\.toString\(\),/.test(backgroundCode), 'fetches the origin-checked target instead');

console.log('\n[4] auth token writes are gated to extension pages');
// Origin-based, not tab-based. `!sender.tab` denied our own popup whenever it
// was opened as a tab — found by driving the real extension in Chromium, where
// VG_GET_AUTH_TOKEN returned null while VG_GET_AUTH_STATUS said authenticated.
assert(/_senderOrigin\(\) === `chrome-extension:\/\/\$\{chrome\.runtime\.id\}`/.test(background),
       'extension-page trust is decided by sender origin');
assert(!/_fromExtensionPage\s*=\s*!sender\.tab/.test(background),
       'no longer conflates "runs in a tab" with "untrusted"');
assert(background.includes("if (msg.type === 'VG_SET_AUTH_TOKEN')") && background.includes('auth token may only be set by the extension UI'), 'VG_SET_AUTH_TOKEN gated');
assert(background.includes("msg.type === 'VG_GET_AUTH_TOKEN'"), 'VG_GET_AUTH_TOKEN handler now exists (popup has always sent it)');

console.log('\n[5] account/web links point at the web app, not the API host');
assert(popup.includes('`${WEB_BASE}/?connect_extension=1`'), 'connect flow uses WEB_BASE');
assert(!popup.includes('`${API_BASE}/?connect_extension=1`'), 'connect flow no longer uses API_BASE');
assert(!/\$\{API_BASE\}\?url=/.test(popup), '"open on web" no longer uses API_BASE');
assert(!/\$\{API_BASE\}\?batch=/.test(popup), 'batch link no longer uses API_BASE');

console.log('\n[6] all three scripts validate the stored base consistently');
for (const [name, src] of [['background.js', background], ['popup.js', popup], ['content.js', content]]) {
  assert(src.includes('function normalizeApiBase('), `${name} defines normalizeApiBase`);
  assert(!/vg_api_base\?\.trim\(\)\)?\s*\)?\s*(\|\||;)/.test(src.replace(/normalizeApiBase\([^)]*\)/g, '')),
         `${name} no longer trusts raw vg_api_base`);
}

console.log('\n[7] untrusted strings are escaped before innerHTML');
assert(content.includes('escapeHtml(meta.duration_str)'), 'duration_str escaped');
assert(content.includes('escapeHtml(meta.view_count_str)'), 'view_count_str escaped');
assert(popup.includes('escapeHtml(ytdlpVer)'), 'backend-reported version escaped');

console.log('\n[8] web \u21c4 extension auth bridge is wired end-to-end');
const bridge = fs.readFileSync(path.join(dir, 'web-bridge.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));

const bridgeEntry = manifest.content_scripts.find(cs => (cs.js || []).includes('web-bridge.js'));
assert(!!bridgeEntry, 'web-bridge.js is registered as a content script');
assert(
  bridgeEntry.matches.every(m => /^\*:\/\/(dvid\.vibe1\.tinhgon\.xyz|localhost|127\.0\.0\.1)\/\*$/.test(m)),
  'bridge is scoped to the web app origin only, not the 24 third-party sites'
);
assert(
  !manifest.content_scripts.some(cs => (cs.js || []).includes('content.js') && cs.matches.some(m => m.includes('matbao.ai'))),
  'content.js is NOT granted the web app origin'
);
assert(!JSON.stringify(manifest).includes('cmc-1'), 'manifest has no dead cmc-1 host');
assert(manifest.host_permissions.includes('*://dvid-api.vibe1.tinhgon.xyz/*') && manifest.host_permissions.includes('*://dvid.vibe1.tinhgon.xyz/*'), 'host_permissions has the two exact new hosts');
assert(!manifest.host_permissions.concat(...manifest.content_scripts.map(c => c.matches)).some(m => /\*[^/]*tinhgon/.test(m)), 'no wildcard on tinhgon.xyz anywhere in manifest');
assert(manifest.version === '5.2.3', 'manifest version 5.2.3');
assert(bridge.includes('event.source !== window'), 'bridge rejects cross-window messages');
assert(bridge.includes('event.origin !== window.location.origin'), 'bridge rejects cross-origin messages');
assert(bridge.includes("VG_SET_AUTH_TOKEN"), 'bridge forwards the token to the service worker directly (popup is closed during login)');

console.log('\n[9] background re-verifies the bridge sender independently');
assert(background.includes('function _isTrustedWebAppSender()'), 'sender origin is re-derived in the worker');
assert(background.includes('sender.origin') && background.includes('sender.url'), 'uses Chrome-provided sender fields, not message content');
assert(background.includes('const _mayWriteAuth = _fromExtensionPage || _isTrustedWebAppSender()'), 'write gate combines both trusted sources');
assert(background.includes("if (!_mayWriteAuth) { sendResponse({ ok: false }); return true; }"), 'logout is gated too');

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──`);
process.exit(failed === 0 ? 0 : 1);
