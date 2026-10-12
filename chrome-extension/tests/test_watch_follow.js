/**
 * "Theo dõi kênh này" in the popup (Phase 33B, task #6259).
 * Run with: node tests/test_watch_follow.js
 *
 * Runs the REAL popup.js watch block (cut out of the shipped file) against a
 * fake fetch / chrome.storage / DOM:
 *  [1] only TikTok profile pages are watchable (not videos, not other hosts)
 *  [2] guest → no status call, no button
 *  [3] status 404 watch_disabled / enabled:false / platform off → no button
 *  [4] enabled → button; click POSTs /watch/sources with the Bearer token
 *  [5] server refusals are shown verbatim; 401 → "Đăng nhập để theo dõi kênh."
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
const start = popup.indexOf('// ── Channel Watch ("Theo dõi kênh", Phase 33B');
const end = popup.indexOf('  btn.disabled = false;\n}\n', start);
if (start < 0 || end < 0) { console.error('watch block not found in popup.js'); process.exit(1); }
const block = popup.slice(start, end + '  btn.disabled = false;\n}\n'.length);

function extract(src, fnName) {
  const s = src.indexOf(`function ${fnName}(`);
  const from = src.lastIndexOf('\n', s) + 1;
  let i = src.indexOf('{', src.indexOf(')', s)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(from, i + 1); }
  }
  throw new Error(fnName);
}

function el() {
  const classes = new Set(['hidden']);
  const handlers = {};
  return {
    disabled: false, textContent: '', style: {},
    classList: { remove: (c) => classes.delete(c), add: (c) => classes.add(c), contains: (c) => classes.has(c) },
    addEventListener: (t, f) => { handlers[t] = f; },
    click: () => handlers.click && handlers.click(),
  };
}

// A fresh sandbox per scenario. routes: { 'GET /api/v1/watch/status': [status, body], 'POST /api/v1/watch/sources': [...] }
function sandbox({ token, routes, throwOn }) {
  const calls = [];
  const els = { 'ch-watch': el(), 'ch-watch-btn': el(), 'ch-watch-msg': el(), 'ch-watch-text': el() };
  const ctx = {
    console, URL, JSON, Array, Promise,
    DEFAULT_API_BASE: 'https://dvid-api.vibe1.tinhgon.xyz',
    API_BASE: 'https://dvid-api.vibe1.tinhgon.xyz',
    chrome: { storage: {
      local: { get: async () => (token ? { vg_auth_token: token } : {}) },
      sync: { get: async () => ({}) },
    } },
    document: { getElementById: (id) => els[id] || null },
    fetch: async (url, opts = {}) => {
      const method = opts.method || 'GET';
      const p = new URL(url).pathname;
      calls.push({ url, method, headers: opts.headers || {}, body: opts.body });
      if (throwOn === p) throw new TypeError('Failed to fetch');
      const [status, body] = routes[`${method} ${p}`] || [404, null];
      return { status, ok: status >= 200 && status < 300, json: async () => { if (body === null) throw new Error('no body'); return body; } };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(
    [extract(popup, 'normalizeApiBase'), extract(popup, 'migrateLegacyApiBase'), 'const LOCAL_HOSTS = ["localhost"]; const LEGACY_API_HOST = "x.invalid";',
     extract(popup, 'vgAuthHeaders'), block,
     'this.initWatchFollow = initWatchFollow; this.vgWatchTarget = vgWatchTarget; this.VG_WATCH_TEXT = VG_WATCH_TEXT;'].join('\n'),
    ctx,
  );
  return { ctx, calls, els };
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const PROFILE = 'https://www.tiktok.com/@meo.cute?lang=vi-VN';
const STATUS_ON = [200, { enabled: true, platforms: ['tiktok'], limits: { max_sources: 3 }, used: 0 }];

(async () => {
  console.log('\n[1] watchable pages');
  {
    const { ctx } = sandbox({ token: 't', routes: {} });
    const t = ctx.vgWatchTarget(PROFILE);
    assert(t && t.platform === 'tiktok' && t.url === 'https://www.tiktok.com/@meo.cute', 'TikTok profile → canonical URL (query dropped)');
    assert(ctx.vgWatchTarget('https://www.tiktok.com/@meo/video/123') === null, 'TikTok video page is not a channel');
    const yt = ctx.vgWatchTarget('https://www.youtube.com/@meo/videos?view=0');
    assert(yt && yt.platform === 'youtube' && yt.url === 'https://www.youtube.com/@meo', 'YouTube channel tab → channel URL');
    const yc = ctx.vgWatchTarget('https://m.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ');
    assert(yc && yc.url === 'https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ', 'YouTube /channel/UC… kept');
    assert(ctx.vgWatchTarget('https://www.youtube.com/watch?v=dQw4w9WgXcQ') === null, 'YouTube video is not a channel');
    assert(ctx.vgWatchTarget('https://youtube.com.evil.example/@meo') === null, 'YouTube look-alike host refused');
    assert(ctx.vgWatchTarget('https://tiktok.com.evil.example/@meo') === null, 'look-alike host refused');
    assert(ctx.vgWatchTarget('http://www.tiktok.com/@meo') === null, 'plain http refused');
  }

  console.log('\n[2] guest → no call, no button');
  {
    const { ctx, calls, els } = sandbox({ token: null, routes: { 'GET /api/v1/watch/status': STATUS_ON } });
    await ctx.initWatchFollow(PROFILE);
    assert(calls.length === 0, 'no API call for a guest');
    assert(els['ch-watch'].classList.contains('hidden'), 'button stays hidden');
  }

  console.log('\n[3] feature off for this user → no button');
  for (const [name, st] of [
    ['404 watch_disabled', [404, { error_code: 'watch_disabled', message: 'Tính năng theo dõi kênh đang tạm tắt.' }]],
    ['enabled:false (admin-only trial)', [200, { enabled: false, platforms: ['tiktok'], used: 0 }]],
    ['tiktok not in platforms', [200, { enabled: true, platforms: [], used: 0 }]],
  ]) {
    const { ctx, calls, els } = sandbox({ token: 'tok', routes: { 'GET /api/v1/watch/status': st } });
    await ctx.initWatchFollow(PROFILE);
    assert(els['ch-watch'].classList.contains('hidden'), `${name}: hidden`);
    assert(calls.length === 1 && calls[0].headers.Authorization === 'Bearer tok', `${name}: the status call carried the token`);
  }

  console.log('\n[4] enabled → follow with the token');
  {
    const { ctx, calls, els } = sandbox({ token: 'tok', routes: {
      'GET /api/v1/watch/status': STATUS_ON,
      'POST /api/v1/watch/sources': [201, { subscription: { id: 's1' }, already_subscribed: false }],
    } });
    await ctx.initWatchFollow(PROFILE);
    assert(!els['ch-watch'].classList.contains('hidden'), 'button shown');
    els['ch-watch-btn'].click();
    await flush(); await flush(); await flush();
    const post = calls.find((c) => c.method === 'POST');
    assert(!!post && post.url === 'https://dvid-api.vibe1.tinhgon.xyz/api/v1/watch/sources', 'POST /api/v1/watch/sources');
    assert(post && post.headers.Authorization === 'Bearer tok', 'POST carries Authorization: Bearer');
    assert(post && post.headers['Content-Type'] === 'application/json', 'JSON content type');
    assert(post && JSON.parse(post.body).url === 'https://www.tiktok.com/@meo.cute' && JSON.parse(post.body).mode === 'one_tap', 'body {url, mode}');
    assert(calls.every((c) => c.headers.Authorization === 'Bearer tok'), 'every call carried the token');
    assert(els['ch-watch-msg'].textContent === ctx.VG_WATCH_TEXT.done, 'success message');
    assert(els['ch-watch-btn'].disabled === true, 'button stays disabled after following');
  }

  console.log('\n[5] server messages shown verbatim');
  const LIMIT = 'Bạn đã theo dõi tối đa 3 kênh. Hãy bỏ theo dõi một kênh hoặc nâng cấp Pro để theo dõi thêm.';
  const COOLDOWN = 'Bạn vừa bỏ theo dõi một kênh. Bạn có thể thêm kênh mới sau 2 giờ.';
  for (const [name, answer, want] of [
    ['source_limit_reached', [409, { error_code: 'source_limit_reached', message: LIMIT, detail: LIMIT }], LIMIT],
    ['replace_cooldown_active', [429, { error_code: 'replace_cooldown_active', message: COOLDOWN, detail: COOLDOWN, retry_after_sec: 7200 }], COOLDOWN],
    ['watch_disabled', [503, { error_code: 'watch_disabled', message: 'Tính năng theo dõi kênh đang tạm tắt.' }], 'Tính năng theo dõi kênh đang tạm tắt.'],
    ['login_required (401)', [401, { error_code: 'login_required', message: 'Vui lòng đăng nhập để theo dõi kênh.' }], 'Đăng nhập để theo dõi kênh.'],
    ['<b>html</b> in message', [400, { message: '<img src=x onerror=alert(1)>' }], '<img src=x onerror=alert(1)>'],
    ['no body', [500, null], 'Có lỗi xảy ra. Vui lòng thử lại.'],
  ]) {
    const { ctx, els } = sandbox({ token: 'tok', routes: { 'GET /api/v1/watch/status': STATUS_ON, 'POST /api/v1/watch/sources': answer } });
    await ctx.initWatchFollow(PROFILE);
    els['ch-watch-btn'].click();
    await flush(); await flush(); await flush();
    assert(els['ch-watch-msg'].textContent === want, `${name}: "${want.slice(0, 50)}"`);
    assert(els['ch-watch-btn'].disabled === false, `${name}: button usable again`);
  }
  {
    const { ctx, els } = sandbox({ token: 'tok', routes: { 'GET /api/v1/watch/status': STATUS_ON }, throwOn: '/api/v1/watch/sources' });
    await ctx.initWatchFollow(PROFILE);
    els['ch-watch-btn'].click();
    await flush(); await flush(); await flush();
    assert(els['ch-watch-msg'].textContent === ctx.VG_WATCH_TEXT.offline, 'network error → offline text');
  }
  assert(!/innerHTML/.test(block), 'watch block never writes innerHTML');

  console.log(`\n── Summary: ${passed} passed, ${failed} failed ──\n`);
  process.exit(failed ? 1 : 0);
})();
