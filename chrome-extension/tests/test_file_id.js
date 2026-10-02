/**
 * The extension names server files by file id, never by server path.
 * Run with: node tests/test_file_id.js
 *
 *  [1] no extension script builds a download-local?filepath= link
 *  [2] the shipped helpers turn ids (and legacy paths) into file= links,
 *      percent-encoding '+' (real name: jNQXAC9IVRw_230+140_<token>.mp4)
 *  [3] resolveDownloadUrl works with only *_file_id (EXPOSE_LEGACY_PATHS=false)
 *      and with an older server that still sends only the path
 *  [4] what the extension SENDS back (analyze-media media_path) is the file id
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

const REAL = 'jNQXAC9IVRw_230+140_9a8b655acfdbb63a.mp4';
const BASE = 'https://api.example.test';

console.log('\n[1] no filepath= links anywhere');
for (const [name, src] of [['background.js', background], ['popup.js', popup], ['content.js', content]]) {
  assert(!src.includes('download-local?filepath='), `${name} never builds ?filepath=`);
}

console.log('\n[2] shipped helpers');
for (const [name, src] of [['background.js', background], ['popup.js', popup], ['content.js', content]]) {
  const vgFileId = eval(`(${extract(src, 'vgFileId')})`);
  const vgIsLocalRef = eval(`(${extract(src, 'vgIsLocalRef')})`);
  const vgLocalUrl = eval(`(${extract(src, 'vgLocalUrl').replace('vgFileId(', '(' + extract(src, 'vgFileId') + ')(')})`);
  assert(vgFileId(REAL, `/app/downloads/other.mp4`) === REAL, `${name}: *_file_id wins over the path`);
  assert(vgFileId(null, `/app/downloads/${REAL}`) === REAL, `${name}: legacy path reduced to its basename`);
  assert(vgFileId(null, null) === null, `${name}: nothing → null`);
  assert(vgIsLocalRef(REAL) && vgIsLocalRef('/app/downloads/x.mp4'), `${name}: ids and legacy paths are local refs`);
  assert(!vgIsLocalRef('https://cdn.example/v.mp4') && !vgIsLocalRef('/api/v1/download-local?file=x'),
         `${name}: URLs are not local refs`);
  const u = vgLocalUrl(BASE, REAL, 'My clip', 'mp4');
  assert(u === `${BASE}/api/v1/download-local?file=jNQXAC9IVRw_230%2B140_9a8b655acfdbb63a.mp4&filename=My%20clip.mp4`,
         `${name}: file= link, '+' encoded as %2B`);
  assert(new URL(u).searchParams.get('file') === REAL, `${name}: round-trips through URLSearchParams`);
  assert(!vgLocalUrl(BASE, `/app/downloads/${REAL}`, 'x', 'mp4').includes('/app/'),
         `${name}: a legacy path never reaches the link`);
}

console.log('\n[3] resolveDownloadUrl (popup) and _serverWrap (background)');
{
  const ctx = { API_BASE: BASE, isFirstPartyUrl: () => false };
  const helpers = ['vgFileId', 'vgIsLocalRef', 'vgLocalUrl'].map(n => extract(popup, n)).join('\n');
  const resolveDownloadUrl = new Function('API_BASE', 'isFirstPartyUrl',
    `${helpers}\nreturn (${extract(popup, 'resolveDownloadUrl')});`)(ctx.API_BASE, ctx.isFirstPartyUrl);

  const idOnly = resolveDownloadUrl({ title: 't', local_file_id: REAL, direct_mp4_url: '' }, 'mp4');
  assert(idOnly && idOnly.dlUrl.includes('file=jNQXAC9IVRw_230%2B140'), 'id-only response (legacy off) → file= link');
  const both = resolveDownloadUrl({ title: 't', local_file_id: REAL, local_file_path: `/app/downloads/${REAL}` }, 'mp4');
  assert(!both.dlUrl.includes('/app/') && !both.dlUrl.includes('filepath='), 'legacy-on response → still file=, no path');
  const oldServer = resolveDownloadUrl({ title: 't', local_mp3_path: '/app/downloads/a_b.mp3' }, 'mp3');
  assert(oldServer.dlUrl.includes('file=a_b.mp3') && oldServer.ext === 'mp3', 'older server (path only) → basename id');
  const cdn = resolveDownloadUrl({ title: 't', direct_mp4_url: 'https://cdn.example/v.mp4' }, 'mp4');
  assert(cdn.dlUrl.includes('/proxy-download?url='), 'CDN URLs still go through proxy-download');

  const bgHelpers = ['vgFileId', 'vgIsLocalRef', 'vgLocalUrl'].map(n => extract(background, n)).join('\n');
  const serverWrap = new Function('isFirstPartyUrl', `${bgHelpers}\nreturn (${extract(background, '_serverWrap')});`)(() => false);
  assert(serverWrap(REAL, BASE, 'x', 'mp4').includes('/download-local?file=jNQXAC9IVRw_230%2B140'),
         'background _serverWrap: id → file= link');
}

console.log('\n[4] what the extension sends back is the file id');
{
  const i = popup.indexOf('/api/v1/analyze-media');
  const block = popup.slice(popup.lastIndexOf("addEventListener('click'", i), popup.indexOf('pollAiAnalysis', i));
  assert(/media_path:\s*_aiFileId/.test(block), 'analyze-media media_path is the file id');
  assert(/const _aiFileId = vgFileId\(_lastDownloadData\?\.local_file_id/.test(block),
         '_aiFileId comes from local_file_id (path only as fallback)');
  assert(!/media_path:\s*_lastDownloadData\.local_file_path/.test(popup), 'no raw path sent as media_path');
  assert(!/url:\s*_lastDownloadData\.local_file_path/.test(popup), 'no raw path sent as url');
}

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──\n`);
process.exit(failed ? 1 : 0);
