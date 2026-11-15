/**
 * 5.22.0: the native messaging link to the VidGrab app (desktop ext_link.rs).
 * Loads the real background.js against a mocked `chrome` and plays the app.
 * Run with: node tests/test_native_link.js
 *
 *  [1] connects to the host at start and says hello (version, browser)
 *  [2] open_relay: opens the channel with a fresh token, answers signedIn
 *  [3] the relay page's progress goes to the app (evt), not the server
 *  [4] a token the app did not make still goes to the server's /channel-relay
 *  [5] links the relay does not know are refused; unknown ops answer an error
 *  [6] downloads go over the link while the app is on it, HTTP otherwise
 *  [7] host missing → retry much later; host ended → soon
 *  [8] manifest: pinned key + nativeMessaging; store build drops the key
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0, failed = 0;
function assert(cond, name) {
  if (cond) { console.log(`  ✓ ${name}`); passed++; }
  else { console.error(`  ✗ ${name}`); failed++; }
}

const dir = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(dir, 'background.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));

// Any chrome.* we do not care about: callable, awaitable, has addListener.
function anything() {
  const fn = function () { return Promise.resolve(undefined); };
  return new Proxy(fn, {
    get(_t, k) {
      if (k === 'then') return undefined;
      if (k === 'addListener') return () => {};
      return anything();
    },
    apply() { return Promise.resolve(undefined); },
  });
}

function makeChrome() {
  const posted = [];
  const ports = [];
  const tabs = [];
  const fetches = [];
  const listeners = [];
  const session = {};
  const local = {};
  const sync = {};
  let cookie = null;
  let nativeError = null;
  let usagePayload = { tier: 'free', universal_stream: false };
  const downloads = [];
  let cookieJarEntries = [];
  const chrome = new Proxy({
    downloads: new Proxy({
      download: (opts, cb) => {
        const id = downloads.length + 1;
        downloads.push(opts);
        if (cb) cb(id);
        return Promise.resolve(id);
      },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    runtime: new Proxy({
      lastError: undefined,
      id: 'gkoepflfmpdmkhmccekmajlfdnfklmgi',
      getManifest: () => ({ version: '5.22.0' }),
      onMessage: { addListener: (f) => listeners.push(f) },
      connectNative: (name) => {
        const port = {
          name, msg: [], disc: [],
          postMessage: (m) => posted.push(JSON.parse(JSON.stringify(m))),
          onMessage: { addListener: (f) => port.msg.push(f) },
          onDisconnect: { addListener: (f) => port.disc.push(f) },
          // the host / app side
          deliver: (m) => port.msg.forEach((f) => f(m)),
          drop: (why) => { chrome.runtime.lastError = why ? { message: why } : undefined; port.disc.forEach((f) => f()); chrome.runtime.lastError = undefined; },
        };
        if (nativeError) throw new Error(nativeError);
        ports.push(port);
        return port;
      },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    storage: new Proxy({
      local: {
        get: async (k, cb) => {
          let r = {};
          if (typeof k === 'string') { if (k in local) r[k] = local[k]; }
          else if (Array.isArray(k)) { for (const x of k) if (x in local) r[x] = local[x]; }
          else r = { ...local };
          if (cb) cb(r);
          return r;
        },
        set: async (o) => { Object.assign(local, JSON.parse(JSON.stringify(o))); },
        remove: async (k) => { for (const x of (Array.isArray(k) ? k : [k])) delete local[x]; },
      },
      session: {
        get: async (k) => ({ [k]: session[k] }),
        set: async (o) => { Object.assign(session, JSON.parse(JSON.stringify(o))); },
      },
      sync: {
        get: async (k) => (typeof k === 'string' ? (k in sync ? { [k]: sync[k] } : {}) : { ...sync }),
        set: async (o) => { Object.assign(sync, JSON.parse(JSON.stringify(o))); },
      },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    tabs: new Proxy({
      create: async (o) => { tabs.push(o); return { id: tabs.length, windowId: 7 }; },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    cookies: new Proxy({
      get: async (q) => (cookie && cookie.name === q.name && q.url.includes(cookie.host) ? { name: q.name, value: 'secret' } : null),
      getAll: async (q) => cookieJarEntries.filter((c) => !q || !q.url || q.url.includes(c.matchHost || '')),
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
  }, { get: (t, k) => (k in t ? t[k] : anything()) });
  return {
    chrome, posted, ports, tabs, fetches, listeners, session, local, sync, downloads,
    setCookie: (c) => { cookie = c; },
    setCookieJar: (arr) => { cookieJarEntries = arr; },
    setNativeError: (e) => { nativeError = e; },
    getUsagePayload: () => usagePayload,
    setUsagePayload: (u) => { usagePayload = u; },
  };
}

const env = makeChrome();
const sandbox = {
  chrome: env.chrome,
  console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
  setTimeout, clearTimeout, setInterval, clearInterval, URL, AbortController, TextEncoder, TextDecoder,
  crypto: require('crypto').webcrypto, btoa, atob,
  navigator: { userAgent: 'Mozilla/5.0 Chrome/141 Edg/141', userAgentData: { brands: [{ brand: 'Not=A?Brand' }, { brand: 'Chromium' }, { brand: 'Microsoft Edge' }] } },
  fetch: async (url, init = {}) => {
    const s = String(url);
    env.fetches.push({ url: s, init });
    if (s.endsWith('/api/v1/user/usage')) {
      return { ok: true, status: 200, json: async () => env.getUsagePayload() };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true, count: 1 }) };
  },
  self: {},
  importScripts() {},
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'background.js' });

const tick = () => new Promise((r) => setTimeout(r, 10));
  const send = (msg, senderOverride) => new Promise((resolve) => {
  let answered = false;
  const done = (v) => { if (!answered) { answered = true; resolve(v); } };
  const sdr = senderOverride || { id: 'gkoepflfmpdmkhmccekmajlfdnfklmgi', tab: { id: 3 }, url: 'https://www.douyin.com/user/x' };
  for (const f of env.listeners) {
    const keep = f(msg, sdr, done);
    if (keep === true) return;
  }
  setTimeout(() => done(undefined), 50);
});

(async () => {
  // [1]
  const port = env.ports[0];
  assert(port && port.name === 'xyz.tinhgon.vidgrab', '[1] connects to the VidGrab host at start');
  const hello = env.posted.find((m) => m.t === 'hello');
  assert(hello && hello.ext === '5.22.0' && hello.brand === 'Microsoft Edge', '[1] hello names version + browser');
  port.deliver({ t: 'app', up: true, version: '0.20.0' });

  // [2]
  env.posted.length = 0;
  port.deliver({ t: 'req', id: 41, op: 'open_relay', url: 'https://www.douyin.com/user/MS4wLjABAAAAabcdefghij' });
  await tick(); await tick();
  const tab = env.tabs[0];
  const m = tab && tab.url.match(/^https:\/\/www\.douyin\.com\/user\/MS4wLjABAAAAabcdefghij#vg-relay=([A-Za-z0-9_-]{32})$/);
  assert(!!m && tab.active === true, '[2] opens the channel with a 32-char relay token');
  const res = env.posted.find((x) => x.t === 'res' && x.id === 41);
  assert(res && res.ok === true && res.data.signedIn === false, '[2] answers ok, not signed in to Douyin');
  env.setCookie({ name: 'sessionid', host: 'douyin.com' });
  port.deliver({ t: 'req', id: 42, op: 'open_relay', url: 'https://www.douyin.com/user/MS4wLjABAAAAabcdefghij' });
  await tick(); await tick();
  const res2 = env.posted.find((x) => x.t === 'res' && x.id === 42);
  assert(res2 && res2.data.signedIn === true && !JSON.stringify(env.posted).includes('secret'), '[2] signed in: yes, and the cookie value never leaves');

  // [3]
  const token = m[1];
  env.posted.length = 0; env.fetches.length = 0;
  const body = { items: [{ url: 'https://www.douyin.com/video/7412345678901234567', title: 'A' }], channelTitle: 'K', done: false, skipped: 0, loginWall: false };
  const r1 = await send({ type: 'VG_RELAY_PUT', token, body });
  const evt = env.posted.find((x) => x.t === 'evt');
  assert(r1 && r1.ok && r1.data.count === 1, '[3] the page hears "sent 1"');
  assert(evt && evt.id === 41 && evt.data.items.length === 1, '[3] progress goes to the app as evt for that request');
  assert(env.fetches.length === 0, '[3] the server is not called');
  await send({ type: 'VG_RELAY_PUT', token, body: { ...body, done: true } });
  assert(!(env.session.vg_local_relays || {})[token], '[3] token forgotten once done');

  // [4]
  env.fetches.length = 0;
  const serverToken = 'S'.repeat(32);
  const r2 = await send({ type: 'VG_RELAY_PUT', token: serverToken, body });
  const f = env.fetches[0];
  assert(r2 && r2.ok && f && f.url.endsWith(`/api/v1/channel-relay/${serverToken}`) && f.init.method === 'PUT', '[4] other tokens: PUT to the server like before');
  const r3 = await send({ type: 'VG_RELAY_PUT', token: '../x', body });
  assert(r3 && r3.ok === false, '[4] a malformed token is refused');

  // [5]
  env.posted.length = 0; env.tabs.length = 0;
  port.deliver({ t: 'req', id: 50, op: 'open_relay', url: 'https://evil.example/user/MS4wLjABAAAAabcdefghij' });
  port.deliver({ t: 'req', id: 51, op: 'open_relay', url: 'javascript:alert(1)' });
  port.deliver({ t: 'req', id: 52, op: 'run_anything' });
  await tick(); await tick();
  const errs = env.posted.filter((x) => x.t === 'res' && !x.ok).map((x) => `${x.id}:${x.error}`);
  assert(env.tabs.length === 0, '[5] no tab opened for a link the relay does not know');
  assert(errs.includes('50:unsupported_url') && errs.includes('51:unsupported_url') && errs.includes('52:unknown_op'), `[5] errors answered (${errs})`);

  // [6]
  env.posted.length = 0; env.fetches.length = 0;
  const ok = await vm.runInContext('vgSendToApp({ url: "https://cdn.x/a.mpd", title: "T", pageUrl: "https://site.x/" })', sandbox);
  assert(ok.ok && env.posted.some((x) => x.t === 'download' && x.url === 'https://cdn.x/a.mpd') && env.fetches.length === 0, '[6] download over the link');
  port.deliver({ t: 'app', up: false });
  env.posted.length = 0;
  await vm.runInContext('vgSendToApp({ url: "https://cdn.x/b.mpd" })', sandbox);
  assert(!env.posted.some((x) => x.t === 'download') && env.fetches.some((x) => x.url.includes('/vidgrab/ping')), '[6] app off the link: HTTP bridge as before');

  // [7]
  port.drop('Specified native messaging host not found.');
  const later = vm.runInContext('vgNativeRetryAt', sandbox) - Date.now();
  assert(vm.runInContext('vgNativePort', sandbox) === null && later > 9 * 60_000, '[7] no host: retry in ~10 minutes');
  vm.runInContext('vgNativeConnect()', sandbox);
  assert(env.ports.length === 1, '[7] … and not before');
  vm.runInContext('vgNativeRetryAt = 0; vgNativeConnect()', sandbox);
  const p2 = env.ports[1];
  p2.drop('Native host has exited.');
  const soon = vm.runInContext('vgNativeRetryAt', sandbox) - Date.now();
  assert(soon > 0 && soon <= 30_000, '[7] host ended: retry in 30 s');

  // [8]
  assert(manifest.permissions.includes('nativeMessaging'), '[8] nativeMessaging permission');
  assert(typeof manifest.key === 'string' && manifest.key.startsWith('MIIBIjAN'), '[8] pinned key (id gkoepflfmpdmkhmccekmajlfdnfklmgi)');
  const pkg = fs.readFileSync(path.join(dir, '..', 'scripts', 'package-extension.py'), 'utf8');
  assert(/m\.pop\("key", None\)/.test(pkg) && pkg.includes('still has the manifest key'), '[8] store build removes the key and checks it');

  // [9] Universal HLS: highest-tier gate (guest/pro refused, team allowed) + AES-128 + clean title + DRM block
  vm.runInContext('vgNativeRetryAt = 0; vgNativeConnect()', sandbox);
  const p3 = env.ports[env.ports.length - 1];
  p3.deliver({ t: 'app', up: true, version: '0.22.3' });
  sandbox.vgSniffList = async () => [
    { id: 'aes1', kind: 'hls', encryption: 'aes128', live: false, url: 'https://cdn.x/hls/index.txt', title: '(2) Xem phim Hành Trình Tập 01 Full HD Vietsub - cinema.example', pageUrl: 'https://cinema.example/watch/1', refererUrl: 'https://player.embed.x/v/1' },
    { id: 'sa1', kind: 'hls', encryption: 'sample_aes', live: false, url: 'https://cdn.x/hls/sa.m3u8', title: 'Episode 02', pageUrl: 'https://cinema.example/watch/2', refererUrl: 'https://player.embed.x/v/2' },
    { id: 'live1', kind: 'hls', encryption: 'none', live: true, url: 'https://cdn.x/live/ch1.m3u8', title: 'Live Channel 1', pageUrl: 'https://cinema.example/live/1' },
    { id: 'drm1', kind: 'dash', encryption: 'drm', drmSystem: 'Widevine', live: false, url: 'https://cdn.x/drm.mpd', title: 'DRM', pageUrl: 'https://cinema.example/watch/3' },
    { id: 'livedrm1', kind: 'hls', encryption: 'drm', drmSystem: 'FairPlay', live: true, url: 'https://cdn.x/livedrm.m3u8', title: 'Live DRM', pageUrl: 'https://cinema.example/live/2' },
  ];
  sandbox.VGSniff = require(path.join(dir, 'media-sniffer.js'));

  // 9a: Guest (no auth token) -> refused before touching native port
  env.posted.length = 0;
  delete env.local.vg_auth_token;
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const rGuest = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'aes1', height: 720 });
  assert(rGuest && rGuest.ok === false && rGuest.error === 'signin_required' && !env.posted.some((x) => x.t === 'download'),
    '[9a] VG_APP_SEND refuses unauthenticated guest with signin_required');

  // 9b: Starter user -> refused with tier_required; Pro user -> refused on VG_APP_SEND with team_required (Desktop App handoff is Team-exclusive)
  env.local.vg_auth_token = 'tok-starter-user';
  env.setUsagePayload({ tier: 'starter', universal_stream: false, universal_stream_mode: 'none' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const rStarter = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'aes1', height: 720 });
  assert(rStarter && rStarter.ok === false && rStarter.error === 'tier_required' && rStarter.tier === 'starter' && !env.posted.some((x) => x.t === 'download'),
    '[9b] VG_APP_SEND refuses Starter tier with tier_required');

  env.local.vg_auth_token = 'tok-pro-user';
  env.setUsagePayload({ tier: 'pro', universal_stream: false, universal_stream_mode: 'pro' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const rPro = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'aes1', height: 720 });
  const rProHls720 = await send({ type: 'VG_HLS_START', tabId: 3, id: 'aes1', height: 720 });
  const rProHls4k = await send({ type: 'VG_HLS_START', tabId: 3, id: 'aes1', height: 2160 });
  const rProLive = await send({ type: 'VG_HLS_START', tabId: 3, id: 'live1' });
  assert(
    rPro && rPro.ok === false && rPro.error === 'team_required' && rPro.tier === 'pro' && !env.posted.some((x) => x.t === 'download')
      && rProHls720 && rProHls720.ok === true
      && rProHls4k && rProHls4k.ok === false && rProHls4k.error === 'team_required'
      && rProLive && rProLive.ok === false && rProLive.error === 'team_required',
    '[9b] Pro tier allows standard Browser VOD HLS <=1080p while gating Desktop App, 4K, and Live to Team (team_required)',
  );

  // 9c: Team user (highest purchasable tier) -> allowed for AES-128, SAMPLE-AES identity, non-DRM Live, and audioLang forwarding
  env.local.vg_auth_token = 'tok-team-user';
  env.setUsagePayload({ tier: 'team', universal_stream: true, universal_stream_mode: 'team' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  env.posted.length = 0;
  const rAes = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'aes1', height: 720, audioLang: 'vi' });
  const dlMsg = env.posted.find((x) => x.t === 'download');
  assert(rAes && rAes.ok === true && dlMsg && dlMsg.referer === 'https://player.embed.x/v/1'
    && dlMsg.title === 'Hành Trình Tập 01'
    && dlMsg.height === 720 && dlMsg.audioLang === 'vi'
    && dlMsg.stream === true && typeof dlMsg.userAgent === 'string' && dlMsg.userAgent.includes('Chrome'),
    '[9c] VG_APP_SEND allows Team tier, cleans movie title, and forwards iframe refererUrl + height + audioLang + userAgent + stream');

  env.posted.length = 0;
  const rSa = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'sa1', height: 1080 });
  const rLive = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'live1' });
  assert(rSa && rSa.ok === true && rLive && rLive.ok === true && env.posted.filter((x) => x.t === 'download').length === 2,
    '[9d] VG_APP_SEND allows SAMPLE-AES (identity) and non-DRM live streams for Team tier');

  env.posted.length = 0;
  const rDrm = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'drm1' });
  const rLiveDrm = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'livedrm1' });
  assert(
    rDrm && rDrm.ok === false && rDrm.error === 'drm_protected' && rDrm.drmSystem === 'Widevine'
      && rLiveDrm && rLiveDrm.ok === false && rLiveDrm.error === 'drm_protected' && rLiveDrm.drmSystem === 'FairPlay'
      && env.posted.length === 0,
    '[9e] VG_APP_SEND strictly refuses VOD DRM (Widevine) and Live DRM (FairPlay) even for Team tier',
  );

  // 9f: VG_HLS_START allows SAMPLE-AES (identity) and non-DRM Live, blocks Live DRM, and VG_HLS_STOP marks stopping
  const hSa = await send({ type: 'VG_HLS_START', tabId: 3, id: 'sa1', height: 720 });
  const hLive = await send({ type: 'VG_HLS_START', tabId: 3, id: 'live1' });
  const hLiveDrm = await send({ type: 'VG_HLS_START', tabId: 3, id: 'livedrm1' });
  const hStop = hLive && hLive.job ? await send({ type: 'VG_HLS_STOP', id: hLive.job.id }) : null;
  const hJobs = await send({ type: 'VG_HLS_JOBS' });
  const stoppedJob = hJobs && hJobs.jobs && hJobs.jobs.find((j) => hLive && hLive.job && j.id === hLive.job.id);
  assert(
    hSa && hSa.ok === true && hLive && hLive.ok === true && hLive.job.live === true
      && hLiveDrm && hLiveDrm.ok === false && hLiveDrm.error === 'drm_protected' && hLiveDrm.drmSystem === 'FairPlay'
      && hStop && hStop.ok === true && stoppedJob && stoppedJob.stopping === true,
    '[9f] VG_HLS_START allows SAMPLE-AES & non-DRM live, blocks live DRM, and VG_HLS_STOP sets stopping=true',
  );

  // [10] Upgrades #1, #2, #4, #5: ephemeral cookies + companion subtitles + series folder + auto-episode + HLS resume
  env.setCookieJar([
    { matchHost: 'cdn.x', domain: '.cdn.x', path: '/', secure: true, expirationDate: 1900000000, name: 'cf_clearance', value: 'tok_123' },
    { matchHost: 'player.embed.x', domain: 'player.embed.x', path: '/', secure: true, expirationDate: 0, name: 'sid', value: 'sess_456' },
  ]);
  sandbox.vgSniffList = async () => [
    {
      id: 'ep-hls-1',
      kind: 'hls',
      encryption: 'none',
      live: false,
      url: 'https://cdn.x/anime/ep01/master.m3u8',
      title: 'Hành Trình Biển Đông - Tập 01',
      pageUrl: 'https://cinema.example/watch/ep-01',
      refererUrl: 'https://player.embed.x/e/1',
      subtitles: [{ url: 'https://subs.cdn.x/ep01.vi.vtt', lang: 'vi', format: 'vtt' }],
    },
    {
      id: 'ep-mp4-2',
      kind: 'file',
      encryption: 'none',
      live: false,
      url: 'https://cdn.x/anime/ep02/video.mp4',
      title: 'Hành Trình Biển Đông - Tập 02',
      pageUrl: 'https://cinema.example/watch/ep-02',
      refererUrl: 'https://player.embed.x/e/2',
      subtitles: [{ url: 'https://subs.cdn.x/ep02.vi.vtt', lang: 'vi', format: 'vtt' }],
    },
  ];

  // 10a: Ephemeral Netscape cookieJar + subtitles sent when vg_send_cookies !== false, omitted when vg_send_cookies === false
  env.sync.vg_send_cookies = true;
  env.posted.length = 0;
  const rWithCk = await send({ type: 'VG_APP_SEND', tabId: 3, id: 'ep-hls-1', height: 1080 });
  const msgWithCk = env.posted.find((x) => x.t === 'download');
  assert(
    rWithCk && rWithCk.ok === true && msgWithCk
      && typeof msgWithCk.cookieJar === 'string'
      && msgWithCk.cookieJar.startsWith('# Netscape HTTP Cookie File')
      && msgWithCk.cookieJar.includes('cf_clearance\ttok_123')
      && msgWithCk.cookieJar.includes('sid\tsess_456')
      && Array.isArray(msgWithCk.subtitles) && msgWithCk.subtitles.length === 1 && msgWithCk.subtitles[0].lang === 'vi',
    '[10a] VG_APP_SEND forwards Netscape cookieJar + companion subtitles when vg_send_cookies is enabled',
  );

  env.sync.vg_send_cookies = false;
  env.posted.length = 0;
  await send({ type: 'VG_APP_SEND', tabId: 3, id: 'ep-hls-1', height: 1080 });
  const msgNoCk = env.posted.find((x) => x.t === 'download');
  assert(
    msgNoCk && msgNoCk.cookieJar === undefined && Array.isArray(msgNoCk.subtitles) && msgNoCk.subtitles.length === 1,
    '[10a] VG_APP_SEND omits cookieJar when vg_send_cookies === false while keeping companion subtitles',
  );
  env.sync.vg_send_cookies = true;

  // 10b: VG_SNIFF_DOWNLOAD saves video + converted .vi.srt into VidGrab/<Series>/<Title>
  const origSandboxFetch = sandbox.fetch;
  sandbox.fetch = async (url, init = {}) => {
    if (String(url) === 'https://subs.cdn.x/ep02.vi.vtt') {
      const vtt = 'WEBVTT\n\n00:01.000 --> 00:03.000\nXin chào tập 2\n';
      return { ok: true, status: 200, text: async () => vtt };
    }
    return origSandboxFetch(url, init);
  };
  env.downloads.length = 0;
  const rFileDl = await send({ type: 'VG_SNIFF_DOWNLOAD', tabId: 3, id: 'ep-mp4-2' });
  await tick(); await tick();
  const dlFilenames = env.downloads.map((d) => d.filename);
  const subDl = env.downloads.find((d) => d.filename.endsWith('.vi.srt'));
  assert(
    rFileDl && rFileDl.ok === true
      && dlFilenames.includes('VidGrab/Hành Trình Biển Đông/Hành Trình Biển Đông - Tập 02.mp4')
      && dlFilenames.includes('VidGrab/Hành Trình Biển Đông/Hành Trình Biển Đông - Tập 02.vi.srt')
      && subDl && String(subDl.url).startsWith('data:application/octet-stream;charset=utf-8,'),
    `[10b] VG_SNIFF_DOWNLOAD groups series into VidGrab/<Series>/ and saves companion .vi.srt as octet-stream so Chrome keeps .srt (${JSON.stringify(dlFilenames)})`,
  );
  sandbox.fetch = origSandboxFetch;

  // 10c: Auto-queue next episode toggle (Team gated: Starter -> tier_required, Pro -> team_required, Team -> ok + deduplicated)
  env.local.vg_auth_token = 'tok-starter-user';
  env.setUsagePayload({ tier: 'starter', universal_stream: false, universal_stream_mode: 'none' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const autoDeniedStarter = await send({ type: 'VG_SNIFF_AUTO_EP_SET', tabId: 9, enabled: true });

  env.local.vg_auth_token = 'tok-pro-user';
  env.setUsagePayload({ tier: 'pro', universal_stream: false, universal_stream_mode: 'pro' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const autoDenied = await send({ type: 'VG_SNIFF_AUTO_EP_SET', tabId: 9, enabled: true });
  assert(
    autoDeniedStarter && autoDeniedStarter.ok === false && autoDeniedStarter.error === 'tier_required'
      && autoDenied && autoDenied.ok === false && autoDenied.error === 'team_required',
    '[10c] VG_SNIFF_AUTO_EP_SET refuses Starter (tier_required) and Pro (team_required)',
  );

  env.local.vg_auth_token = 'tok-team-user';
  env.setUsagePayload({ tier: 'team', universal_stream: true, universal_stream_mode: 'team' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const autoOk = await send({ type: 'VG_SNIFF_AUTO_EP_SET', tabId: 9, enabled: true });
  const autoState = await send({ type: 'VG_SNIFF_AUTO_EP_GET', tabId: 9 });
  env.posted.length = 0;
  const ep3Item = {
    id: 'ep-hls-3',
    kind: 'hls',
    encryption: 'none',
    live: false,
    height: 1080,
    url: 'https://cdn.x/anime/ep03/master.m3u8',
    title: 'Hành Trình Biển Đông - Tập 03',
    pageUrl: 'https://cinema.example/watch/ep-03',
    refererUrl: 'https://player.embed.x/e/3',
  };
  await sandbox.vgOnSniffStreamReady(9, ep3Item);
  await sandbox.vgOnSniffStreamReady(9, ep3Item); // duplicate call for same URL on same tab
  assert(
    autoOk && autoOk.ok === true && autoState && autoState.enabled === true
      && env.posted.filter((x) => x.t === 'download' && x.url === ep3Item.url).length === 1,
    '[10c] vgOnSniffStreamReady auto-sends next episode to connected Desktop App exactly once',
  );

  // 10d: VG_HLS_RESUME refuses Starter tier, allows Pro standard resume, and auto-rebinds fresh signed URL on live tab for Team tier
  const extPageSender = { id: 'gkoepflfmpdmkhmccekmajlfdnfklmgi', origin: 'chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi', url: 'chrome-extension://gkoepflfmpdmkhmccekmajlfdnfklmgi/offscreen.html' };
  const hStartRes = await send({ type: 'VG_HLS_START', tabId: 3, id: 'ep-hls-1', height: 720 }, extPageSender);
  const rJobId = hStartRes && hStartRes.job && hStartRes.job.id;
  await send({
    target: 'sw',
    type: 'HLS_FAILED',
    id: rJobId,
    reason: 'http',
    message: 'HTTP 403',
    resumable: true,
    done: 12,
    total: 40,
    bytes: 409600,
    checkpoint: { mediaUrl: 'https://cdn.x/anime/ep01/720.m3u8?token=expired_1', written: 12, total: 40, bytes: 409600, seq: 13, fmp4Mode: false },
  }, extPageSender);
  await tick(); await tick();
  const keepResp = await send({ type: 'VG_HLS_KEEP_FILES' }, extPageSender);
  assert(keepResp && keepResp.ok === true && keepResp.keep.includes(`hls-${rJobId}.mp4`),
    '[10d] VG_HLS_KEEP_FILES preserves OPFS file of resumable failed HLS job');

  env.local.vg_auth_token = 'tok-starter-user';
  env.setUsagePayload({ tier: 'starter', universal_stream: false, universal_stream_mode: 'none' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const resumeStarter = await send({ type: 'VG_HLS_RESUME', id: rJobId }, extPageSender);
  assert(resumeStarter && resumeStarter.ok === false && resumeStarter.error === 'tier_required',
    '[10d] VG_HLS_RESUME refuses Starter tier with tier_required');

  // Simulate live tab refreshing its signed HLS token while keeping the same episode stream identity
  sandbox.vgSniffList = async () => [
    {
      id: 'ep-hls-1-fresh',
      kind: 'hls',
      encryption: 'none',
      live: false,
      url: 'https://cdn.x/anime/ep01/master.m3u8?token=fresh_2',
      title: 'Hành Trình Biển Đông - Tập 01',
      pageUrl: 'https://cinema.example/watch/ep-01',
      refererUrl: 'https://player.embed.x/e/1?token=fresh_2',
    },
  ];

  env.local.vg_auth_token = 'tok-team-user';
  env.setUsagePayload({ tier: 'team', universal_stream: true, universal_stream_mode: 'team' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const resumeTeam = await send({ type: 'VG_HLS_RESUME', id: rJobId }, extPageSender);
  assert(
    resumeTeam && resumeTeam.ok === true && resumeTeam.rebound === true
      && resumeTeam.job && resumeTeam.job.state === 'running' && resumeTeam.job.done === 12
      && resumeTeam.job.url === 'https://cdn.x/anime/ep01/master.m3u8?token=fresh_2',
    '[10d] VG_HLS_RESUME auto-rebinds fresh signed URL from live tab and restarts checkpointed job at done=12 for Team tier',
  );

  // 10e: VG_PILL_CLICK routes Team user directly to connected Desktop App, and Pro user to Browser HLS
  env.posted.length = 0;
  const tabSender = { id: 'gkoepflfmpdmkhmccekmajlfdnfklmgi', tab: { id: 3, url: 'https://cinema.example/watch/ep-01' }, origin: 'https://cinema.example', url: 'https://cinema.example/watch/ep-01' };
  const pillTeam = await send({ type: 'VG_PILL_CLICK', itemId: 'ep-hls-1-fresh' }, tabSender);
  env.local.vg_auth_token = 'tok-pro-user';
  env.setUsagePayload({ tier: 'pro', universal_stream: false, universal_stream_mode: 'pro' });
  vm.runInContext('vgResetUniversalTierCache()', sandbox);
  const pillPro = await send({ type: 'VG_PILL_CLICK', itemId: 'ep-hls-1-fresh' }, tabSender);
  assert(
    pillTeam && pillTeam.ok === true && pillTeam.via === 'app'
      && env.posted.some((x) => x.t === 'download' && x.url === 'https://cdn.x/anime/ep01/master.m3u8?token=fresh_2')
      && pillPro && pillPro.ok === true && pillPro.via === 'browser',
    '[10e] VG_PILL_CLICK sends stream to Desktop App for Team tier and starts Browser HLS <=1080p for Pro tier',
  );

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
