/**
 * i18n consistency (5.21.0): _locales/vi and _locales/en carry the same keys,
 * every placeholder is declared, and every key the code asks for exists.
 *
 *  [1] vi and en have identical key sets; each entry has a non-empty message
 *  [2] every placeholder used in vi exists in en (and the other way round)
 *  [3] "$name$" tokens are declared in "placeholders"; contents are $1..$9
 *  [4] every t('…') / tn('…') in popup.js, content.js, background.js exists
 *  [5] every data-i18n / -placeholder / -title / -aria in popup.html exists
 *  [6] manifest __MSG_x__ keys exist; default_locale is "en"
 *  [7] a call passes at least as many substitutions as the message has $n
 *  [8] no unused key (dynamic hls_* keys are accounted for)
 */
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
let passed = 0, failed = 0;
function assert(cond, name, detail) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}${detail ? '   ' + detail : ''}`); failed++; }
}
const load = (l) => JSON.parse(fs.readFileSync(path.join(root, '_locales', l, 'messages.json'), 'utf8'));
const vi = load('vi'), en = load('en');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

console.log('[1] key sets');
const kv = Object.keys(vi).sort(), ke = Object.keys(en).sort();
assert(kv.length === ke.length && kv.every((k, i) => k === ke[i]), `vi and en have the same ${kv.length} keys`,
  JSON.stringify({ onlyVi: kv.filter((k) => !(k in en)), onlyEn: ke.filter((k) => !(k in vi)) }));
assert(kv.every((k) => typeof vi[k].message === 'string' && vi[k].message && typeof en[k].message === 'string' && en[k].message), 'every message is a non-empty string');
assert(kv.every((k) => /^[A-Za-z0-9_@]+$/.test(k)), 'keys use only [A-Za-z0-9_@]');

const toks = (m) => [...(m.message.match(/\$([A-Za-z0-9_@]+)\$/g) || [])].map((s) => s.slice(1, -1).toLowerCase());
const nums = (m) => [...new Set(toks(m).map((n) => (m.placeholders && m.placeholders[n] ? m.placeholders[n].content : '?')))].sort();

console.log('[2] placeholders match between languages');
const bad2 = kv.filter((k) => JSON.stringify(nums(vi[k])) !== JSON.stringify(nums(en[k])));
assert(bad2.length === 0, 'vi and en use the same $1..$n in every message', bad2.join(', '));
const viInEn = kv.filter((k) => !nums(vi[k]).every((n) => nums(en[k]).includes(n)));
assert(viInEn.length === 0, 'every placeholder in vi exists in en', viInEn.join(', '));

console.log('[3] declared placeholders');
const undeclared = [];
for (const k of kv) for (const l of [vi, en]) {
  for (const n of toks(l[k])) {
    const ph = l[k].placeholders && Object.keys(l[k].placeholders).map((x) => x.toLowerCase());
    if (!ph || !ph.includes(n)) undeclared.push(k + ':' + n);
  }
  for (const [n, p] of Object.entries(l[k].placeholders || {})) if (!/^\$[1-9]$/.test(p.content)) undeclared.push(k + ':content:' + n);
}
assert(undeclared.length === 0, 'every $name$ is declared and maps to $1..$9', undeclared.join(', '));

console.log('[4] keys used in code exist');
const used = new Map();   // key -> [{file, args}]
function argCount(src, openIdx) {
  // openIdx: index of "(" ; count top-level args up to the matching ")"
  let depth = 0, args = 0, seen = false, inStr = null;
  for (let i = openIdx; i < src.length; i++) {
    const c = src[i];
    if (inStr) { if (c === '\\') i++; else if (c === inStr) inStr = null; else if (inStr === '`' && c === '$' && src[i + 1] === '{') { let d = 0; for (; i < src.length; i++) { if (src[i] === '{') d++; else if (src[i] === '}' && --d === 0) break; } } continue; }
    if (c === "'" || c === '"' || c === '`') { inStr = c; seen = true; continue; }
    if (c === '(' || c === '[' || c === '{') { depth++; if (depth > 1) seen = true; continue; }
    if (c === ')' || c === ']' || c === '}') { depth--; if (depth === 0) return seen ? args + 1 : 0; continue; }
    if (c === ',' && depth === 1) { args++; continue; }
    if (!/\s/.test(c)) seen = true;
  }
  return -1;
}
for (const f of ['popup.js', 'content.js', 'background.js']) {
  const src = read(f);
  const re = /(?<![\w$.])(tn|t)\(\s*(['"])([A-Za-z0-9_@]+)\2/g;
  let m;
  while ((m = re.exec(src))) {
    const open = src.indexOf('(', m.index);
    const n = argCount(src, open) - 1;   // substitutions passed (for tn() the count is the first one)
    if (m[3].endsWith('_')) continue;   // dynamic: t('hls_' + reason)
    if (!used.has(m[3])) used.set(m[3], []);
    used.get(m[3]).push({ f, subs: n, tn: m[1] === 'tn' });
  }
}
// dynamic keys built as 'prefix' + variable in background.js
const dynamic = new Set(['hls_not_hls', 'hls_encrypted', 'hls_live', 'hls_empty', 'hls_too_long', 'hls_separate_audio', 'hls_remux_failed', 'hls_cancelled']);
// t(cond ? 'a' : 'b', …) forms
for (const f of ['popup.js', 'content.js', 'background.js']) {
  const src = read(f);
  const re = /(?<![\w$.])t\(\s*[^'"`)\s][^()]*?\?\s*'([A-Za-z0-9_]+)'\s*:\s*'([A-Za-z0-9_]+)'/g;
  let m;
  while ((m = re.exec(src))) { used.set(m[1], used.get(m[1]) || []); used.set(m[2], used.get(m[2]) || []); }
}
const missing = [...used.keys()].filter((k) => !(k in vi));
assert(missing.length === 0, `all ${used.size} keys used in JS exist`, missing.join(', '));
assert([...dynamic].every((k) => k in vi), 'the dynamic hls_* keys exist');

console.log('[5] keys used in popup.html exist');
const html = read('popup.html');
const hk = [...html.matchAll(/data-i18n(?:-placeholder|-title|-aria)?="([A-Za-z0-9_@]+)"/g)].map((m) => m[1]);
const missingH = [...new Set(hk)].filter((k) => !(k in vi));
assert(hk.length > 50 && missingH.length === 0, `all ${new Set(hk).size} popup.html keys exist`, missingH.join(', '));
const contentSrc = read('content.js');
const ck = [...contentSrc.matchAll(/data-i18n="([A-Za-z0-9_@]+)"/g)].map((m) => m[1]);
assert(ck.every((k) => k in vi), 'content.js panel data-i18n keys exist');

console.log('[6] manifest');
const man = JSON.parse(read('manifest.json'));
assert(man.default_locale === 'en', 'default_locale is "en"');
const mk = [...JSON.stringify(man).matchAll(/__MSG_([A-Za-z0-9_@]+)__/g)].map((m) => m[1]);
assert(mk.length >= 3 && mk.every((k) => k in vi && k in en), `manifest uses ${mk.join(', ')} — all defined in vi and en`);
assert(vi.ext_name.message === en.ext_name.message, 'the store name is the same in both languages');
assert(en.ext_description.message.length <= 132, 'en description fits the 132-character limit');

console.log('[7] substitutions passed');
const few = [];
for (const [k, calls] of used) {
  if (!(k in vi)) continue;
  const need = Math.max(0, ...nums(vi[k]).map((s) => Number(s.slice(1))).filter(Number.isFinite));
  for (const c of calls) if (c.subs >= 0 && c.subs < need) few.push(`${k}@${c.f} passes ${c.subs}, needs ${need}`);
}
assert(few.length === 0, 'every call passes enough substitutions', few.join('; '));

console.log('[8] unused keys');
const htmlSet = new Set(hk), ctSet = new Set(ck);
const unused = kv.filter((k) => !used.has(k) && !htmlSet.has(k) && !ctSet.has(k) && !dynamic.has(k) && !mk.includes(k) && !(k.endsWith('_one') && (used.has(k.slice(0, -4)) || htmlSet.has(k.slice(0, -4)))));
assert(unused.length === 0, 'no key is defined but unused', unused.join(', '));

console.log(`\n── Summary: ${passed} passed, ${failed} failed ──\n`);
process.exit(failed ? 1 : 0);
