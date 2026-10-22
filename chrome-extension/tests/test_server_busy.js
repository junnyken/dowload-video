/**
 * Task #6397 — a busy server answers 503 server_busy at once; every extension
 * surface that calls /fetch-link must show the server's sentence and must not
 * retry on its own.
 * Run with: node tests/test_server_busy.js
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

const vgServerErrorText = new Function(`${extract(background, 'vgServerErrorText')}; return vgServerErrorText;`)();
const MSG = 'Hệ thống đang bận, vui lòng thử lại sau khoảng 30 giây.';
const busy = { error_code: 'server_busy', message: MSG, detail: MSG, retry_after: 30 };

console.log('[1] the busy sentence is shown as the server wrote it');
assert(vgServerErrorText(busy, 503) === MSG, 'server_busy → exact sentence, no "(HTTP 503)" suffix');
assert(vgServerErrorText({ error_code: 'server_busy', message: MSG }, 503) === MSG, 'top-level message alone is enough');

console.log('[2] other failures keep their old text');
assert(vgServerErrorText({ detail: 'Link lỗi' }, 400) === 'Link lỗi (HTTP 400)', 'string detail');
assert(vgServerErrorText({ detail: { message: 'Hết lượt' } }, 429) === 'Hết lượt (HTTP 429)', 'object detail.message');
assert(vgServerErrorText({ user_message: 'U', detail: 'D' }, 500) === 'U (HTTP 500)', 'top-level user_message first');
assert(vgServerErrorText(null, 502) === 'HTTP 502', 'no body (gateway)');

console.log('[3] every /fetch-link caller reads the body text');
// 5.17.0: the single "Tải" and the bulk queue share vgFetchAndSave (it keeps the HTTP status for 429)
assert(/if \(!resp\.ok\) throw Object\.assign\(new Error\(vgServerErrorText\(data, resp\.status\)\), \{ status: resp\.status \}\)/.test(background), 'vgFetchAndSave uses the helper');
// 5.18.0: VG_DOWNLOAD_NOW -> vgSaveVideo (browser first) -> vgFetchAndSave when the server is needed
assert(/msg\.type === 'VG_DOWNLOAD_NOW'[\s\S]{0,1200}vgSaveVideo\(/.test(background) && /async function vgSaveVideo[\s\S]{0,3000}vgFetchAndSave\(/.test(background), 'VG_DOWNLOAD_NOW -> vgSaveVideo -> vgFetchAndSave');
assert(/vgServerErrorText\(data, resp\.status\)\) \}\)/.test(background), 'context menu toast uses the helper');
assert(/result\?\.data\?\.detail \|\| result\?\.error/.test(popup), 'popup Spotify track shows detail');
assert(/dlBtn\.title = \(typeof d === 'string' \? d : d\?\.message\)/.test(popup), 'popup format row keeps the reason in its tooltip');
assert(/resp\?\.data\?\.detail \|\| resp\?\.error/.test(content), 'page button shows detail');

console.log('[4] no automatic retry of fetch-link');
const retryish = /(setTimeout|setInterval)\([^)]*fetch-link/;
assert(!retryish.test(background) && !retryish.test(popup) && !retryish.test(content), 'no timer re-sends fetch-link');
assert(!/retry_after/.test(background + popup + content), 'retry_after is not used to schedule a resend');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
