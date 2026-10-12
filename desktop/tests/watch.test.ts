// Run: npm test   (pure logic only: no Tauri, no network)
// Channel Watch (Phase 33B, task #6259): watch-core.ts.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_IDS, POLL_MS, applyPoll, batchByChannel, channelLabel, emptyLocal, markDone, mergeItems, nextPollDelay, notifyText, oneTap,
  parseItems, parseStatus, parseSubscriptions, pendingItems, readLocal, serverMessage, toChannelVideo, watchVisible, writeLocal,
  type EnqueueWithClaims, type WatchItem,
} from '../src/lib/watch-core.ts';

const item = (id: string, sub: string, channel: string, at: string, url: string | null = `https://www.tiktok.com/@${channel}/video/${id}`): WatchItem => ({
  id, source_id: `src-${sub}`, subscription_id: sub, channel, platform: 'tiktok', title: `Video ${id}`, url, published_at: null, discovered_at: at,
});

const okStatus = (o: Record<string, unknown> = {}) => ({
  enabled: true, kill_switch: false, admin_only: true, platforms: ['tiktok'], tier: 'pro',
  limits: { max_sources: 3, min_interval_sec: 21600, replace_cooldown_sec: 86400 }, used: 0, ...o,
});

test('watch-core is really loaded (not a skipped import)', () => {
  assert.equal(typeof applyPoll, 'function');
  assert.equal(POLL_MS, 15 * 60_000);
});

// ---- status gating -------------------------------------------------------------------

test('parseStatus: 200 -> ok with limits; 401 login; 403/404/503 off; other error', () => {
  const r = parseStatus(200, okStatus({ used: 2 }));
  assert.equal(r.kind, 'ok');
  if (r.kind === 'ok') {
    assert.equal(r.status.enabled, true);
    assert.equal(r.status.used, 2);
    assert.equal(r.status.limits.max_sources, 3);
    assert.deepEqual(r.status.platforms, ['tiktok']);
  }
  assert.equal(parseStatus(401, { error_code: 'login_required' }).kind, 'login');
  assert.equal(parseStatus(404, { error_code: 'watch_disabled' }).kind, 'off');
  assert.equal(parseStatus(403, { error_code: 'watch_disabled' }).kind, 'off');
  assert.equal(parseStatus(503, null).kind, 'off');
  assert.equal(parseStatus(500, null).kind, 'error');
  assert.equal(parseStatus(0, null).kind, 'error');
  assert.equal(parseStatus(200, null).kind, 'error');
});

test('watchVisible: only signed in AND enabled (or still has subscriptions)', () => {
  const on = parseStatus(200, okStatus());
  const offNoSubs = parseStatus(200, okStatus({ enabled: false, used: 0 }));
  const offWithSubs = parseStatus(200, okStatus({ enabled: false, used: 1 }));
  assert.equal(watchVisible('in', on), true);
  assert.equal(watchVisible('out', on), false, 'guest never sees it');
  assert.equal(watchVisible('loading', on), false);
  assert.equal(watchVisible('in', offNoSubs), false, 'admin-only trial, not this user');
  assert.equal(watchVisible('in', offWithSubs), true, 'can still manage existing subscriptions');
  assert.equal(watchVisible('in', parseStatus(404, null)), false, 'WATCH_ENABLED=false');
  assert.equal(watchVisible('in', parseStatus(401, null)), false);
  assert.equal(watchVisible('in', null), false);
});

test('serverMessage shows the server text verbatim, else the fallback', () => {
  const msg = 'Bạn đã theo dõi tối đa 3 kênh. Hãy bỏ theo dõi một kênh hoặc nâng cấp Pro để theo dõi thêm.';
  assert.equal(serverMessage({ error_code: 'source_limit_reached', message: msg, detail: msg }, 'x'), msg);
  assert.equal(serverMessage({ detail: 'chi tiết' }, 'x'), 'chi tiết');
  assert.equal(serverMessage(null, 'x'), 'x');
  assert.equal(serverMessage({ detail: [{ loc: 1 }] }, 'x'), 'x');
});

test('parseSubscriptions / parseItems drop malformed rows', () => {
  const subs = parseSubscriptions({ subscriptions: [{ id: 's1', mode: 'one_tap', status: 'paused', new_items_7d: 2, source: { platform: 'tiktok', display_name: 'abc', last_scan_at: '2026-10-12T00:00:00+00:00' } }, { nope: 1 }, null] });
  assert.equal(subs.length, 1);
  assert.equal(subs[0].status, 'paused');
  assert.equal(subs[0].source.display_name, 'abc');
  assert.equal(subs[0].new_items_7d, 2);
  assert.deepEqual(parseSubscriptions(null), []);
  const items = parseItems({ items: [{ id: 'i1', subscription_id: 's1', url: 'https://www.tiktok.com/@a/video/1', discovered_at: '2026-10-12T01:00:00Z' }, { id: 5 }] });
  assert.equal(items.length, 1);
  assert.equal(items[0].channel, null);
});

// ---- since / last-seen persistence -------------------------------------------------------

test('first poll is a baseline: records ids + since, notifies nothing', () => {
  const items = [item('1', 's1', 'a', '2026-10-10T00:00:00Z'), item('2', 's1', 'a', '2026-10-11T00:00:00Z')];
  const out = applyPoll(emptyLocal(), items, '2026-10-12T00:00:00Z');
  assert.equal(out.toNotify.length, 0);
  assert.equal(out.local.since, '2026-10-11T00:00:00Z');
  assert.deepEqual(out.local.notified.sort(), ['1', '2']);
});

test('first poll with nothing: since starts at now (next poll does not ask 7 days again)', () => {
  const out = applyPoll(emptyLocal(), [], '2026-10-12T00:00:00.000Z');
  assert.equal(out.local.since, '2026-10-12T00:00:00.000Z');
  // and the next poll's new item IS notified (since is set, so this is no longer a baseline)
  const next = applyPoll(out.local, [item('9', 's1', 'a', '2026-10-12T01:00:00Z')], '2026-10-12T01:10:00Z');
  assert.deepEqual(next.toNotify.map((i) => i.id), ['9']);
});

test('no re-notify after restart: state survives write/read, the server\'s >= since item is not announced again', () => {
  const s0 = applyPoll(emptyLocal(), [], '2026-10-12T00:00:00Z').local;
  const p1 = applyPoll(s0, [item('7', 's1', 'a', '2026-10-12T02:00:00Z')], '2026-10-12T02:05:00Z');
  assert.deepEqual(p1.toNotify.map((i) => i.id), ['7']);
  // persist, "restart", read back
  const raw = writeLocal(null, 'user-1', p1.local);
  const restored = readLocal(raw, 'user-1');
  assert.equal(restored.since, '2026-10-12T02:00:00Z');
  // server filters discovered_at >= since, so item 7 comes back
  const p2 = applyPoll(restored, [item('7', 's1', 'a', '2026-10-12T02:00:00Z')], '2026-10-12T02:20:00Z');
  assert.equal(p2.toNotify.length, 0);
  assert.equal(p2.local.since, '2026-10-12T02:00:00Z');
});

test('local state is per account and survives garbage', () => {
  const a = writeLocal(null, 'u1', { since: '2026-10-01T00:00:00Z', notified: ['x'], done: ['y'] });
  const b = writeLocal(a, 'u2', { since: null, notified: ['z'], done: [] });
  assert.deepEqual(readLocal(b, 'u1'), { since: '2026-10-01T00:00:00Z', notified: ['x'], done: ['y'] });
  assert.deepEqual(readLocal(b, 'u2').notified, ['z']);
  assert.deepEqual(readLocal(b, 'u3'), emptyLocal());
  assert.deepEqual(readLocal('{not json', 'u1'), emptyLocal());
  assert.deepEqual(readLocal(JSON.stringify({ u1: { since: 'not a date', notified: [1, 'a'], done: 'x' } }), 'u1'), { since: null, notified: ['a'], done: [] });
  assert.ok(JSON.parse(writeLocal('[1,2]', 'u1', emptyLocal())).u1, 'array in storage is replaced');
});

test('id lists are capped', () => {
  const many = Array.from({ length: MAX_IDS + 50 }, (_, i) => `id${i}`);
  const l = markDone(emptyLocal(), many);
  assert.equal(l.done.length, MAX_IDS);
  assert.equal(l.done.at(-1), `id${MAX_IDS + 49}`);
  const back = readLocal(writeLocal(null, 'u', l), 'u');
  assert.equal(back.done.length, MAX_IDS);
});

test('items already downloaded/dismissed are not notified and not listed', () => {
  const s0 = { since: '2026-10-12T00:00:00Z', notified: [], done: ['3'] };
  const items = [item('3', 's1', 'a', '2026-10-12T01:00:00Z'), item('4', 's1', 'a', '2026-10-12T01:00:00Z'), item('5', 's1', 'a', '2026-10-12T01:00:00Z', null)];
  const out = applyPoll(s0, items, '2026-10-12T01:05:00Z');
  assert.deepEqual(out.toNotify.map((i) => i.id), ['4'], 'done item skipped, item without link skipped');
  assert.deepEqual(pendingItems(items, out.local).map((i) => i.id), ['4']);
});

// ---- batching ------------------------------------------------------------------------------

test('one notification per channel batch', () => {
  const items = [
    item('1', 's1', 'meo', '2026-10-12T01:00:00Z'), item('2', 's2', '@cho', '2026-10-12T01:01:00Z'),
    item('3', 's1', 'meo', '2026-10-12T01:02:00Z'), item('4', 's1', 'meo', '2026-10-12T01:03:00Z'),
  ];
  const b = batchByChannel(items);
  assert.equal(b.length, 2);
  assert.deepEqual(b.map((x) => [x.subscriptionId, x.items.length]), [['s1', 3], ['s2', 1]]);
  assert.equal(notifyText(b[0]).title, '3 video mới từ @meo');
  assert.equal(notifyText(b[1]).title, '1 video mới từ @cho', 'no double @');
  assert.equal(channelLabel(null), 'kênh bạn theo dõi');
  // a poll that finds new items in 2 channels -> exactly 2 batches
  const s0 = { since: '2026-10-12T00:00:00Z', notified: [], done: [] };
  assert.equal(batchByChannel(applyPoll(s0, items, '2026-10-12T02:00:00Z').toNotify).length, 2);
});

test('mergeItems dedupes by id, newest first', () => {
  const m = mergeItems([item('1', 's', 'a', '2026-10-12T01:00:00Z')], [item('1', 's', 'a', '2026-10-12T01:00:00Z'), item('2', 's', 'a', '2026-10-12T03:00:00Z')]);
  assert.deepEqual(m.map((i) => i.id), ['2', '1']);
});

test('poll delay is 15 min with jitter of at most 2 min', () => {
  assert.equal(nextPollDelay(0.5), POLL_MS);
  assert.equal(nextPollDelay(0), POLL_MS - 120_000);
  assert.equal(nextPollDelay(1), POLL_MS + 120_000);
});

// ---- one-tap ---------------------------------------------------------------------------------

test('one-tap goes through enqueueWithClaims (claim path) per channel; only queued items are done', async () => {
  const calls: { urls: string[]; ch: { platform: string; title: string; quality: string; outDir: string } }[] = [];
  const fake: EnqueueWithClaims = async (videos, ch) => {
    calls.push({ urls: videos.map((v) => v.url), ch });
    // the allowance refuses the last video of channel "meo"
    const refused = ch.title === 'meo' ? videos.slice(-1).map((v) => ({ video: { id: v.id }, detail: 'Hết lượt hôm nay.' })) : [];
    return { enqueued: videos.slice(0, videos.length - refused.length).map((v) => ({ id: v.id })), refused };
  };
  const items = [item('1', 's1', 'meo', 't'), item('2', 's2', 'cho', 't'), item('3', 's1', 'meo', 't'), item('4', 's1', 'meo', 't', null)];
  const r = await oneTap(items, emptyLocal(), { quality: '1080', outDirFor: (c) => `D:\\VidGrab\\${c}` }, fake);
  assert.equal(calls.length, 2, 'one claim batch per channel');
  assert.deepEqual(calls[0].urls, ['https://www.tiktok.com/@meo/video/1', 'https://www.tiktok.com/@meo/video/3'], 'item without link never queued');
  assert.deepEqual(calls[0].ch, { platform: 'tiktok', title: 'meo', quality: '1080', outDir: 'D:\\VidGrab\\meo' });
  assert.equal(r.enqueued, 2);
  assert.deepEqual(r.refused.map((x) => [x.item.id, x.detail]), [['3', 'Hết lượt hôm nay.']]);
  assert.deepEqual(r.local.done.sort(), ['1', '2']);
  assert.deepEqual(pendingItems(items, r.local).map((i) => i.id), ['3'], 'refused stays in "Video mới"');
});

test('toChannelVideo keeps the canonical page URL (the queue downloads it locally)', () => {
  const v = toChannelVideo(item('1', 's', 'a', 't'));
  assert.equal(v.url, 'https://www.tiktok.com/@a/video/1');
  assert.equal(v.id, '1');
  assert.equal(v.title, 'Video 1');
});

test('watch.ts wires one-tap to the real channels.ts enqueueWithClaims (no private queue path)', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../src/lib/watch.ts', import.meta.url), 'utf8');
  assert.match(src, /import \{[^}]*\benqueueWithClaims\b[^}]*\} from '\.\/channels'/);
  assert.match(src, /oneTap\([\s\S]*?\},\s*enqueueWithClaims\)/);
  assert.doesNotMatch(src, /\benqueue\(/, 'never calls the bare queue enqueue (which would skip the claim batch)');
  assert.doesNotMatch(src, /startDownload/, 'never starts yt-dlp directly');
});
