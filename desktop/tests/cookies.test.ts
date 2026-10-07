// Run: npm test   (pure logic only: no Tauri, no network)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  COOKIE_PLATFORMS, DOUYIN_FALLBACK_CODES, cookieErrorCode, cookiePlatformOf, domainMatches, douyinLocalArgs, parseCookiePlatforms, pickNext,
  planRoute, rowState, settleThenFallback, shouldFallbackToServer, toHeaderList, type CookieCtx, type DouyinLocal, type PumpItem, type Route,
} from '../src/lib/cookies-core.ts';

const DY = 'https://www.douyin.com/video/7311111111111111111';
const IG = 'https://www.instagram.com/reel/abc/';
const YT = 'https://www.youtube.com/watch?v=x';

const ctx = (enabled: string[], saved: string[]): CookieCtx => ({ enabled, usable: (s) => saved.includes(s) });

test('platform from host: strict suffix, http(s) only', () => {
  assert.equal(cookiePlatformOf(DY), 'douyin');
  assert.equal(cookiePlatformOf('https://v.douyin.com/AbC/'), 'douyin');
  assert.equal(cookiePlatformOf('https://www.iesdouyin.com/share/video/1/'), 'douyin');
  assert.equal(cookiePlatformOf('https://youtu.be/x'), 'youtube');
  assert.equal(cookiePlatformOf('https://x.com/a/status/1'), 'twitter');
  assert.equal(cookiePlatformOf('https://www.threads.com/@a/post/1'), 'threads');
  for (const bad of [
    'https://douyin.com.evil.com/video/1', 'https://evil-douyin.com/video/1', 'https://user@www.douyin.com/',
    'ftp://www.douyin.com/', 'https://example.com/?u=https://www.douyin.com/', 'https://v26-web.douyinvod.com/x.mp4', 'nope',
  ]) assert.equal(cookiePlatformOf(bad), null, bad);
  assert.ok(domainMatches('a.b.douyin.com.', 'douyin.com'));
  assert.ok(!domainMatches('douyin.com', 'com'));
});

test('platform table matches the server allowlist (client_api.py COOKIE_PLATFORM_SLUGS)', () => {
  assert.deepEqual(COOKIE_PLATFORMS.map((p) => p.slug),
    ['douyin', 'instagram', 'facebook', 'twitter', 'youtube', 'bilibili', 'threads', 'reddit', 'pinterest', 'tiktok', 'vimeo']);
  assert.deepEqual(parseCookiePlatforms(['douyin', 'suno', 'douyin', 3, 'instagram']), ['douyin', 'instagram']);
  assert.deepEqual(parseCookiePlatforms(undefined), []);
  assert.deepEqual(parseCookiePlatforms('douyin'), []);
});

test('Douyin route: L1 only when enabled on the server AND a blob is usable, else the server route', () => {
  assert.deepEqual(planRoute(DY, ctx(['douyin'], ['douyin'])), { route: 'local_cookie', useCookies: true, platform: 'douyin' });
  assert.equal(planRoute(DY, ctx([], ['douyin'])).route, 'server');          // server switch off
  assert.equal(planRoute(DY, ctx(['douyin'], [])).route, 'server');          // no blob / no consent
  assert.equal(planRoute(DY, ctx(['instagram'], ['douyin'])).route, 'server');
});

test('other platforms: L1 when enabled + saved, otherwise unchanged (local, no cookies)', () => {
  assert.deepEqual(planRoute(IG, ctx(['instagram'], ['instagram'])), { route: 'local_cookie', useCookies: true, platform: 'instagram' });
  assert.deepEqual(planRoute(IG, ctx(['douyin'], ['instagram'])), { route: 'local', useCookies: false, platform: 'instagram' });
  assert.deepEqual(planRoute(IG, ctx(['instagram'], [])), { route: 'local', useCookies: false, platform: 'instagram' });
  assert.deepEqual(planRoute('https://vk.com/video1', ctx(['instagram'], ['instagram'])), { route: 'local', useCookies: false, platform: null });
});

test('fallback to the server: Douyin L1 only, login/forbidden only, once', () => {
  const it = { url: DY, route: 'local_cookie' as Route };
  assert.ok(shouldFallbackToServer(it, 'private_or_login')); // includes yt-dlp "Fresh cookies are needed" (Rust maps it)
  assert.ok(shouldFallbackToServer(it, 'forbidden'));
  for (const c of ['not_found', 'unsupported', 'geo_blocked', 'network', 'disk_full', 'cancelled', 'unknown']) {
    assert.ok(!shouldFallbackToServer(it, c), c);
  }
  assert.ok(!shouldFallbackToServer({ ...it, cookieFallback: true }, 'private_or_login'));   // only once
  assert.ok(!shouldFallbackToServer({ url: DY, route: 'server' }, 'forbidden'));            // already on the server
  assert.ok(!shouldFallbackToServer({ url: IG, route: 'local_cookie' }, 'private_or_login')); // other platforms: no server L1 fallback
  assert.deepEqual([...DOUYIN_FALLBACK_CODES].sort(), ['forbidden', 'private_or_login']);
});

test('fallback refunds BEFORE the job is handed to the server route', async () => {
  const log: string[] = [];
  let resolveSettle!: () => void;
  const settle = () => new Promise<void>((r) => { log.push('settle:start'); resolveSettle = () => { log.push('settle:done'); r(); }; });
  const p = settleThenFallback(settle, () => true, () => log.push('requeue'));
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(log, ['settle:start']); // nothing requeued while the refund is in flight
  resolveSettle();
  assert.equal(await p, true);
  assert.deepEqual(log, ['settle:start', 'settle:done', 'requeue']);
});

test('fallback: a failed settle still falls back; a job cancelled meanwhile does not', async () => {
  const log: string[] = [];
  assert.equal(await settleThenFallback(() => Promise.reject(new Error('offline')), () => true, () => log.push('requeue')), true);
  assert.deepEqual(log, ['requeue']);
  assert.equal(await settleThenFallback(async () => {}, () => false, () => log.push('again')), false);
  assert.deepEqual(log, ['requeue']);
});

test('error codes: cookie_expired with cookies, cookie_required on an enabled platform without them', () => {
  assert.equal(cookieErrorCode('private_or_login', { cookiesUsed: true, platform: 'instagram', enabled: ['instagram'] }), 'cookie_expired');
  assert.equal(cookieErrorCode('private_or_login', { cookiesUsed: false, platform: 'instagram', enabled: ['instagram'] }), 'cookie_required');
  assert.equal(cookieErrorCode('private_or_login', { cookiesUsed: false, platform: 'instagram', enabled: [] }), 'private_or_login');
  assert.equal(cookieErrorCode('private_or_login', { cookiesUsed: false, platform: null, enabled: ['instagram'] }), 'private_or_login');
  assert.equal(cookieErrorCode('network', { cookiesUsed: true, platform: 'instagram', enabled: ['instagram'] }), 'network');
});

test('pump: at most one running job per platform that uses cookies', () => {
  const c = ctx(['douyin', 'instagram'], ['douyin', 'instagram']);
  const plan = (i: PumpItem) => planRoute(i.url, c);
  const list: PumpItem[] = [
    { id: 'a', url: DY, state: 'running', route: 'local_cookie' },
    { id: 'b', url: DY + '2', state: 'queued' },  // same account busy -> waits
    { id: 'c', url: IG, state: 'queued' },        // other account -> may start
    { id: 'd', url: YT, state: 'queued' },
  ];
  assert.equal(pickNext(list, 4, plan)?.id, 'c');
  assert.equal(pickNext([...list.slice(0, 2), { id: 'e', url: YT, state: 'queued' }], 4, plan)?.id, 'e'); // no cookies -> no limit
  assert.equal(pickNext(list.slice(0, 2), 4, plan), null);
  assert.equal(pickNext(list, 1, plan), null); // global concurrency still applies
  // A Douyin job on the server route does not hold the Douyin slot.
  const srv: PumpItem[] = [{ id: 'a', url: DY, state: 'running', route: 'server' }, { id: 'b', url: DY + '2', state: 'queued' }];
  assert.equal(pickNext(srv, 4, plan)?.id, 'b');
  // Nothing saved: Douyin goes to the server and is never held back.
  const none = (i: PumpItem) => planRoute(i.url, ctx(['douyin'], []));
  assert.equal(pickNext([{ id: 'a', url: DY, state: 'running', route: 'server' }, { id: 'b', url: DY, state: 'queued' }], 4, none)?.id, 'b');
});

test('settings row state', () => {
  assert.equal(rowState(undefined), 'none');
  assert.equal(rowState({ platform: 'douyin', saved: false, savedAt: null, suspectExpired: false }), 'none');
  assert.equal(rowState({ platform: 'douyin', saved: true, savedAt: 1, suspectExpired: false }), 'saved');
  assert.equal(rowState({ platform: 'douyin', saved: true, savedAt: 1, suspectExpired: true }), 'suspect');
});

test('Douyin local (0.7.2): the resolved direct link is downloaded like the server route, without cookies', () => {
  const r: DouyinLocal = {
    id: '7311111111111111111', url: 'https://v26-web.douyinvod.com/x/?a=1', title: 'Mèo con', author: 'A', durationSec: 15,
    headers: { Referer: 'https://www.douyin.com/', 'User-Agent': 'Mozilla/5.0 Edg/140' },
  };
  const a = douyinLocalArgs(r, { id: 'job-1', title: 'Video Douyin 7311' });
  assert.deepEqual(a, {
    url: 'https://v26-web.douyinvod.com/x/?a=1', formatId: undefined,
    headers: [{ name: 'Referer', value: 'https://www.douyin.com/' }, { name: 'User-Agent', value: 'Mozilla/5.0 Edg/140' }],
    fileTitle: 'Mèo con', fileId: '7311111111111111111', useCookies: false,
  });
  // No title from the page -> the queue title; an odd id -> cleaned, or the job id.
  const b = douyinLocalArgs({ ...r, title: '', id: '73/../11' }, { id: 'job-1', title: 'Video Douyin 7311' });
  assert.equal(b.fileTitle, 'Video Douyin 7311');
  assert.equal(b.fileId, '7311');
  assert.equal(douyinLocalArgs({ ...r, id: '' }, { id: 'ab$c-1', title: 't' }).fileId, 'abc-1');
});

test('header list: Referer / User-Agent only, empty values dropped', () => {
  assert.deepEqual(toHeaderList({ referer: 'https://www.douyin.com/', Cookie: 'a=b', 'User-Agent': '', Authorization: 'x' }),
    [{ name: 'referer', value: 'https://www.douyin.com/' }]);
  assert.deepEqual(toHeaderList(null), []);
});
