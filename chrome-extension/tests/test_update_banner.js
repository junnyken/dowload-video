/**
 * Extension update banner (Phase 34D-5, task #6399).
 * Run with: node tests/test_update_banner.js
 *
 * Runs the REAL popup.js update-check block (cut out of the shipped file):
 *  [1] semver compare   [2] banner decision (newer / dismissed / forced)
 *  [3] fetch + 6 h cache + silent failures
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const popup = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');
const start = popup.indexOf('// ── Extension update check (Phase 34D-5');
const end = popup.indexOf('// ── /Extension update check');
if (start < 0 || end < 0) { console.error('update block not found in popup.js'); process.exit(1); }
const block = popup.slice(start, end);

function make({ store = {}, fetchImpl, version = '5.6.0' } = {}) {
  const calls = { fetch: 0 };
  const ctx = {
    chrome: {
      runtime: { getManifest: () => ({ version }) },
      storage: { local: {
        get: async (k) => ({ [k]: store[k] }),
        set: async (o) => { Object.assign(store, o); },
      } },
    },
    fetch: async (...a) => { calls.fetch++; return fetchImpl(...a); },
    vgApiBase: async () => 'https://api.example',
    vgAuthHeaders: async () => ({}),
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(block + '\nthis.api = { vgCompareVersions, vgUpdateBanner, vgFetchUpdateInfo, VG_UPDATE_TTL_MS };', ctx);
  return { api: ctx.api, calls, store };
}

console.log('[1] version compare');
{
  const { api } = make();
  const c = api.vgCompareVersions;
  assert(c('5.6.0', '5.6.1') === -1, '5.6.0 < 5.6.1');
  assert(c('5.10.0', '5.9.9') === 1, '5.10.0 > 5.9.9 (numeric, not lexical)');
  assert(c('5.6', '5.6.0') === 0, '5.6 == 5.6.0');
  assert(c('v6.0.0', '5.99.99') === 1, 'leading v tolerated');
  assert(c('', '5.0.0') === null && c('x', '1') === null && c(null, '1') === null, 'garbage -> null');
}

console.log('[2] banner decision');
{
  const { api } = make();
  const b = api.vgUpdateBanner;
  assert(!b('5.6.0', { version: '5.6.0', minSupported: null }, null).show, 'same version: no banner');
  assert(!b('5.7.0', { version: '5.6.0', minSupported: null }, null).show, 'ahead of server: no banner');
  let d = b('5.6.0', { version: '5.7.0', minSupported: null }, null);
  assert(d.show && !d.forced && d.version === '5.7.0', 'newer: dismissible banner');
  assert(!b('5.6.0', { version: '5.7.0' }, '5.7.0').show, 'dismissed 5.7.0: hidden');
  assert(b('5.6.0', { version: '5.8.0' }, '5.7.0').show, 'dismissed older: shows for the newer 5.8.0');
  d = b('5.4.0', { version: '5.7.0', minSupported: '5.5.0' }, '5.7.0');
  assert(d.show && d.forced, 'below minSupported: forced even if dismissed');
  assert(b('5.6.0', { version: '5.7.0', minSupported: '5.5.0' }, null).forced === false, 'at/above minSupported: dismissible');
  assert(!b('5.6.0', null, null).show && !b('5.6.0', { version: null, minSupported: null }, null).show, 'no info: no banner');
  d = b('5.4.0', { version: null, minSupported: '5.5.0' }, null);
  assert(d.show && d.forced && d.version === '5.5.0', 'zip unknown but min set: forced with min');
}

(async () => {
  console.log('[3] fetch + cache');
  {
    const ok = { ok: true, json: async () => ({ version: '5.7.0', minSupported: '5.5.0' }) };
    const m = make({ fetchImpl: async () => ok });
    const t0 = 1_000_000;
    let info = await m.api.vgFetchUpdateInfo(t0);
    assert(info.version === '5.7.0' && info.minSupported === '5.5.0' && m.calls.fetch === 1, 'first open fetches');
    await m.api.vgFetchUpdateInfo(t0 + 5 * 3600e3);
    assert(m.calls.fetch === 1, 'within 6 h: served from cache');
    await m.api.vgFetchUpdateInfo(t0 + 6 * 3600e3 + 1);
    assert(m.calls.fetch === 2, 'after 6 h: fetches again');
  }
  {
    const m = make({ fetchImpl: async () => { throw new Error('offline'); } });
    assert((await m.api.vgFetchUpdateInfo(1)) === null, 'network failure -> null, no throw');
    const m2 = make({ fetchImpl: async () => ({ ok: false, json: async () => ({}) }) });
    assert((await m2.api.vgFetchUpdateInfo(1)) === null, 'HTTP error -> null');
    assert(Object.keys(m2.store).length === 0, 'failures are not cached');
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
