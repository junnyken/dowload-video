// Run: npm test   (pure logic only: no Tauri, no network)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  LOGIN_CODES, NO_FALLTHROUGH, PLATFORM_SLUGS, claimRoute, currentStep, downloadLocalUrl, fetchLinkBody, legacyRoute, nextRoute,
  parseFetchLink, parseServerFallback, planRoutes, platformOf, routeLabel, serverQuality, triedBefore, type RouteCtx, type RouteId,
} from '../src/lib/routes-core.ts';

const ALL = ['youtube', 'tiktok', 'instagram', 'facebook', 'twitter', 'threads', 'reddit', 'pinterest', 'vimeo', 'bilibili', 'soundcloud', 'youku', 'mgtv', 'iqiyi', 'kuaishou', 'xiaohongshu'];
const ctx = (cookies: boolean, serverFallback: string[] = ALL): RouteCtx => ({ cookies, serverFallback });

test('routes-core is really loaded (not a skipped import)', () => {
  assert.equal(typeof planRoutes, 'function');
  assert.ok(PLATFORM_SLUGS.length >= 19);
});

test('platformOf mirrors platform_key.py slugs, on the host only', () => {
  const cases: [string, string][] = [
    ['https://www.youtube.com/watch?v=x', 'youtube'], ['https://youtu.be/x', 'youtube'], ['https://m.youtube.com/shorts/x', 'youtube'],
    ['https://www.tiktok.com/@a/video/1', 'tiktok'], ['https://vt.tiktok.com/ZS/', 'tiktok'],
    ['https://www.instagram.com/reel/a/', 'instagram'], ['https://www.facebook.com/watch/?v=1', 'facebook'], ['https://fb.watch/a/', 'facebook'],
    ['https://www.douyin.com/video/1', 'douyin'], ['https://v.douyin.com/a/', 'douyin'], ['https://www.iesdouyin.com/share/video/1/', 'douyin'],
    ['https://www.threads.net/@a/post/1', 'threads'], ['https://www.threads.com/@a/post/1', 'threads'],
    ['https://open.spotify.com/track/1', 'spotify'], ['https://x.com/a/status/1', 'twitter'], ['https://mobile.twitter.com/a/status/1', 'twitter'],
    ['https://www.linkedin.com/posts/a', 'linkedin'], ['https://www.kuaishou.com/short-video/1', 'kuaishou'], ['https://v.kuaishou.com/a', 'kuaishou'],
    ['https://www.xiaohongshu.com/explore/1', 'xiaohongshu'], ['http://xhslink.com/a', 'xiaohongshu'], ['https://www.bilibili.com/video/BV1', 'bilibili'],
    ['https://b23.tv/a', 'bilibili'], ['https://www.iq.com/play/a', 'iqiyi'], ['https://www.iqiyi.com/v_1.html', 'iqiyi'], ['https://v.youku.com/v_show/1', 'youku'],
    ['https://www.mgtv.com/b/1/2.html', 'mgtv'], ['https://www.reddit.com/r/a/comments/1', 'reddit'], ['https://pin.it/a', 'pinterest'],
    ['https://vimeo.com/1', 'vimeo'], ['https://soundcloud.com/a/b', 'soundcloud'], ['https://example.com/v.mp4', 'other'],
  ];
  for (const [u, slug] of cases) assert.equal(platformOf(u), slug, u);
  // Host only: not a substring of another host or the query string.
  assert.equal(platformOf('https://evil-youtube.com/x'), 'other');
  assert.equal(platformOf('https://example.com/?u=https://youtube.com/watch'), 'other');
  assert.equal(platformOf('https://dropbox.com/s/x'), 'other'); // "x.com" inside, like twitter_host.py
  assert.equal(platformOf('https://netflix.com/title/1'), 'other');
  assert.equal(platformOf('https://bilibili.com.evil.cn/x'), 'other');
  assert.equal(platformOf('ftp://youtube.com/x'), 'other');
  assert.equal(platformOf('not a url'), 'other');
  for (const s of ALL) assert.ok(PLATFORM_SLUGS.includes(s), `server fallback slug ${s} is known`);
});

test('parseServerFallback keeps known slugs only', () => {
  assert.deepEqual(parseServerFallback(['tiktok', 'tiktok', 'evil', 3, 'youtube']), ['tiktok', 'youtube']);
  assert.deepEqual(parseServerFallback('tiktok'), []);
  assert.deepEqual(parseServerFallback(undefined), []);
});

test('route table (PLAN-32D §2)', () => {
  const plan = (u: string, c: RouteCtx) => planRoutes(u, c);
  assert.deepEqual(plan('https://youtu.be/x', ctx(true)), ['L0', 'L1', 'S0']);
  assert.deepEqual(plan('https://youtu.be/x', ctx(false)), ['L0', 'S0']);
  assert.deepEqual(plan('https://youtu.be/x', ctx(false, [])), ['L0']);
  assert.deepEqual(plan('https://www.tiktok.com/@a/video/1', ctx(true)), ['L0', 'S0']); // no cookie route for TikTok
  assert.deepEqual(plan('https://www.instagram.com/reel/a/', ctx(true)), ['L1', 'S0']);
  assert.deepEqual(plan('https://www.instagram.com/reel/a/', ctx(false)), ['L0', 'S0']); // public reels: local first
  assert.deepEqual(plan('https://www.instagram.com/reel/a/', ctx(false, [])), ['L0']);
  assert.deepEqual(plan('https://www.facebook.com/watch/?v=1', ctx(true)), ['L0', 'L1', 'S0']);
  assert.deepEqual(plan('https://x.com/a/status/1', ctx(true, ['youtube'])), ['L0', 'L1']);
  assert.deepEqual(plan('https://www.threads.net/@a/post/1', ctx(true)), ['L0', 'S0']);
  assert.deepEqual(plan('https://www.bilibili.com/video/BV1', ctx(true)), ['L0', 'L1', 'S0']);
  assert.deepEqual(plan('https://www.reddit.com/r/a/comments/1', ctx(false)), ['L0', 'S0']);
  assert.deepEqual(plan('https://v.youku.com/v_show/1', ctx(true)), ['L0', 'S0']);
  assert.deepEqual(plan('https://www.mgtv.com/b/1/2.html', ctx(false)), ['L0', 'S0']);
  assert.deepEqual(plan('https://www.kuaishou.com/short-video/1', ctx(false)), ['S0']);
  assert.deepEqual(plan('https://www.kuaishou.com/short-video/1', ctx(false, [])), ['L0']); // switch off: as before P2
  assert.deepEqual(plan('https://www.xiaohongshu.com/explore/1', ctx(false)), ['S0']);
  assert.deepEqual(plan('https://www.iq.com/play/a', ctx(false)), ['S0']);
  assert.deepEqual(plan('https://open.spotify.com/track/1', ctx(false)), ['L0']); // no server fallback slug
  assert.deepEqual(plan('https://example.com/v.mp4', ctx(true)), ['L0', 'L1']); // L1 only when the caller found cookies
  // Douyin: always the existing server route, cookies or not, whatever the switches say.
  for (const c of [ctx(true), ctx(false), ctx(true, []), ctx(false, ['douyin'])]) {
    assert.deepEqual(plan('https://www.douyin.com/video/7311111111111111111', c), ['DOUYIN_SERVER']);
  }
});

test('transitions: each step once, terminal errors stop, login goes to L1, the rest to S0', () => {
  const full: RouteId[] = ['L0', 'L1', 'S0'];
  // L0 login / forbidden -> L1 when available
  for (const c of ['private_or_login', 'forbidden']) assert.equal(nextRoute(full, [], 'L0', c), 'L1', c);
  // ... else S0
  assert.equal(nextRoute(['L0', 'S0'], [], 'L0', 'private_or_login'), 'S0');
  // any other L0 failure skips L1 and goes to S0
  for (const c of ['unknown', 'network', 'timeout']) assert.equal(nextRoute(full, [], 'L0', c), 'S0', c);
  // L1 failure -> S0
  assert.equal(nextRoute(full, ['L0'], 'L1', 'private_or_login'), 'S0');
  assert.equal(nextRoute(full, ['L0'], 'L1', 'unknown'), 'S0');
  // no fallthrough
  for (const c of ['not_found', 'unsupported', 'geo_blocked', 'drm', 'api:quota_exceeded_daily', 'disk_full', 'tool_missing', 'cancelled']) {
    assert.ok(NO_FALLTHROUGH.has(c));
    assert.equal(nextRoute(full, [], 'L0', c), null, c);
    assert.equal(nextRoute(full, ['L0'], 'L1', c), null, c);
  }
  // S0 and Douyin are last
  assert.equal(nextRoute(full, ['L0', 'L1'], 'S0', 'unknown'), null);
  assert.equal(nextRoute(['DOUYIN_SERVER'], [], 'DOUYIN_SERVER', 'forbidden'), null);
  // no S0 available: the job fails
  assert.equal(nextRoute(['L0', 'L1'], ['L0'], 'L1', 'unknown'), null);
  assert.equal(nextRoute(['L0'], [], 'L0', 'unknown'), null);
  // a step already used is never used again
  assert.equal(nextRoute(full, ['L1'], 'L0', 'private_or_login'), 'S0');
  assert.equal(nextRoute(full, ['S0'], 'L0', 'unknown'), null);
  assert.ok(LOGIN_CODES.has('forbidden'));
});

test('walking a job through its plan never repeats a step', () => {
  const plan: RouteId[] = ['L0', 'L1', 'S0'];
  let tried: RouteId[] = [];
  const seen: RouteId[] = [];
  for (const code of ['private_or_login', 'unknown', 'unknown', 'unknown']) {
    const step = currentStep(plan, tried);
    if (!step) break;
    seen.push(step);
    const next = nextRoute(plan, tried, step, code);
    if (!next) break;
    tried = triedBefore(plan, tried, next);
  }
  assert.deepEqual(seen, ['L0', 'L1', 'S0']);
  // L0 network -> S0 marks L1 as used too
  assert.deepEqual(triedBefore(plan, [], 'S0'), ['L0', 'L1']);
  assert.equal(currentStep(plan, triedBefore(plan, [], 'S0')), 'S0');
  assert.equal(currentStep(plan, ['L0', 'L1', 'S0']), null);
  // "Tải qua máy chủ" after a failed local analysis: tried = L0, L1
  assert.equal(currentStep(['L0', 'S0'], ['L0', 'L1']), 'S0');
});

test('route names and labels', () => {
  assert.equal(claimRoute('L0'), 'local');
  assert.equal(claimRoute('L1'), 'local_cookie');
  assert.equal(legacyRoute('L1'), 'local_cookie');
  assert.equal(legacyRoute('S0'), 'server');
  assert.equal(legacyRoute('DOUYIN_SERVER'), 'server');
  assert.equal(routeLabel('L0'), 'Tải trên máy này');
  assert.equal(routeLabel('L1'), 'Tải bằng tài khoản của bạn');
  assert.equal(routeLabel('S0'), 'Qua máy chủ VidGrab');
  assert.equal(routeLabel('DOUYIN_SERVER'), 'Qua máy chủ VidGrab');
  assert.equal(routeLabel(undefined), null);
});

test('server quality from the queue item; body asks for nothing else', () => {
  assert.equal(serverQuality({ audioOnly: true, quality: '1080' }), 'mp3_128');
  assert.equal(serverQuality({ audioOnly: false, quality: '720' }), 'video_720');
  assert.equal(serverQuality({ audioOnly: false, quality: 'best' }), 'video');
  assert.equal(serverQuality({ audioOnly: false, formatId: 'bv*[height<=480]+ba/b[height<=480]/b' }), 'video_480');
  assert.equal(serverQuality({ audioOnly: false, formatId: '137+140', formatLabel: '1080p (Full HD)' }), 'video_1080');
  assert.equal(serverQuality({ audioOnly: false }), 'video');
  assert.deepEqual(fetchLinkBody('https://youtu.be/x', 'video_720'), { url: 'https://youtu.be/x', quality: 'video_720' });
});

const API = 'https://api.example';
test('fetch-link answer: server file first, then direct link', () => {
  const file = parseFetchLink(200, { success: true, title: 'Phở bò / Hà Nội', direct_mp4_url: 'https://cdn.example/x.mp4', local_file_id: 'abc123.mp4', local_mp3_file_id: 'abc123.mp3' }, { apiBase: API, audioOnly: false, fallbackTitle: 't' });
  assert.equal(file.kind, 'ok');
  if (file.kind === 'ok') {
    assert.equal(file.source, 'file');
    assert.equal(file.url, `${API}/api/v1/download-local?file=abc123.mp4&filename=${encodeURIComponent('Phở bò Hà Nội.mp4')}`);
    assert.equal(file.title, 'Phở bò / Hà Nội');
    assert.deepEqual(file.headers, []);
  }
  const audio = parseFetchLink(200, { success: true, title: 'a', local_file_id: 'v.mp4', local_mp3_file_id: 'a b.mp3' }, { apiBase: API, audioOnly: true, fallbackTitle: 't' });
  assert.ok(audio.kind === 'ok' && audio.url.includes('file=a%20b.mp3'));
  const direct = parseFetchLink(200, { success: true, title: null, direct_mp4_url: 'https://cdn.example/x.mp4', local_file_id: null }, { apiBase: API, audioOnly: false, fallbackTitle: 'T' });
  assert.deepEqual(direct, { kind: 'ok', url: 'https://cdn.example/x.mp4', title: null, thumbnail: null, headers: [], source: 'direct' });
  const rel = parseFetchLink(200, { success: true, direct_mp4_url: '/api/v1/download-local?file=z.mp4&filename=z.mp4' }, { apiBase: API, audioOnly: false, fallbackTitle: 'T' });
  assert.ok(rel.kind === 'ok' && rel.url === `${API}/api/v1/download-local?file=z.mp4&filename=z.mp4`);
  for (const bad of ['javascript:alert(1)', '//evil.example/x', 'file:///C:/x', '']) {
    const r = parseFetchLink(200, { success: true, direct_mp4_url: bad }, { apiBase: API, audioOnly: false, fallbackTitle: 'T' });
    assert.equal(r.kind, 'error', bad);
  }
  const hdr = parseFetchLink(200, { success: true, direct_mp4_url: 'https://cdn.example/x.mp4', http_headers: { Referer: 'https://www.tiktok.com/', Cookie: 'x=1' } }, { apiBase: API, audioOnly: false, fallbackTitle: 'T' });
  assert.ok(hdr.kind === 'ok');
  if (hdr.kind === 'ok') assert.deepEqual(hdr.headers, [{ name: 'Referer', value: 'https://www.tiktok.com/' }]);
  assert.equal(downloadLocalUrl(API, 'x.mp4', '').endsWith('filename=video.mp4'), true);
});

test('fetch-link refusals and errors', () => {
  const o = { apiBase: API, audioOnly: false, fallbackTitle: 'T' };
  const q = parseFetchLink(429, { detail: { error_code: 'quota_exceeded_daily', message: 'Hết lượt', daily_limit: 5, downloads_today: 5, reset_time_vn: '07:00' } }, o);
  assert.deepEqual(q, { kind: 'quota', refusal: { detail: 'Hết lượt', upsell: 'signin', limit: 5, usedToday: 5, resetTimeVn: '07:00', reason: null } });
  // the network's guest cap keeps its reason (the app must not zero the person's own counters)
  const ip = parseFetchLink(429, { detail: { error_code: 'quota_exceeded_daily', message: 'Mạng này đã dùng hết', reason: 'ip_limit', daily_limit: 5, downloads_today: 2 } }, o);
  assert.ok(ip.kind === 'quota' && ip.refusal.reason === 'ip_limit');
  const q2 = parseFetchLink(403, { detail: { error_code: 'quota_exceeded_daily', user_message: 'Hết lượt (user)' } }, o);
  assert.ok(q2.kind === 'quota' && q2.refusal.upsell === 'upgrade' && q2.refusal.detail === 'Hết lượt (user)');
  // other 429s are errors, not the allowance
  const busy = parseFetchLink(429, { detail: { error: 'youtube_quota_exceeded', message: 'YouTube hết lượt' } }, o);
  assert.deepEqual(busy, { kind: 'error', code: 'server_fetch_failed', message: 'YouTube hết lượt' });
  assert.deepEqual(parseFetchLink(404, { detail: 'Video không khả dụng', error_code: 'video_unavailable' }, o), { kind: 'error', code: 'not_found', message: 'Video không khả dụng' });
  assert.deepEqual(parseFetchLink(500, { detail: 'Lỗi', error_code: 'processing_failed' }, o), { kind: 'error', code: 'server_fetch_failed', message: 'Lỗi' });
  assert.equal(parseFetchLink(202, { success: false, message: 'Đang xếp hàng' }, o).kind, 'error');
  assert.deepEqual(parseFetchLink(202, { success: false, message: 'Đang xếp hàng' }, o), { kind: 'error', code: 'server_busy', message: 'Đang xếp hàng' });
  assert.deepEqual(parseFetchLink(200, { success: true }, o), { kind: 'error', code: 'server_fetch_failed', message: '' });
  assert.equal(parseFetchLink(200, null, o).kind, 'error');
});
