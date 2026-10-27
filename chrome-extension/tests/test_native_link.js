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
  let cookie = null;
  let nativeError = null;
  const chrome = new Proxy({
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
        get: async (k, cb) => { const r = {}; if (cb) cb(r); return r; },
        set: async () => {}, remove: async () => {},
      },
      session: {
        get: async (k) => ({ [k]: session[k] }),
        set: async (o) => { Object.assign(session, JSON.parse(JSON.stringify(o))); },
      },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    tabs: new Proxy({
      create: async (o) => { tabs.push(o); return { id: tabs.length, windowId: 7 }; },
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
    cookies: new Proxy({
      get: async (q) => (cookie && cookie.name === q.name && q.url.includes(cookie.host) ? { name: q.name, value: 'secret' } : null),
    }, { get: (t, k) => (k in t ? t[k] : anything()) }),
  }, { get: (t, k) => (k in t ? t[k] : anything()) });
  return {
    chrome, posted, ports, tabs, fetches, listeners, session,
    setCookie: (c) => { cookie = c; },
    setNativeError: (e) => { nativeError = e; },
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
    env.fetches.push({ url: String(url), init });
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
const send = (msg) => new Promise((resolve) => {
  let answered = false;
  const done = (v) => { if (!answered) { answered = true; resolve(v); } };
  for (const f of env.listeners) {
    const keep = f(msg, { id: 'gkoepflfmpdmkhmccekmajlfdnfklmgi', tab: { id: 3 }, url: 'https://www.douyin.com/user/x' }, done);
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

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
