// Run: npm test   (pure logic only: no Tauri, no network)
// 0.18.2: Douyin channel = server's first page + the app's page window; downloads capped at press time.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { listsMore, mergeListings, needsMore } from '../src/lib/douyin-core.ts';
import { splitByCap } from '../src/lib/quota-core.ts';
import type { ChannelListing, ChannelVideo } from '../src/lib/types.ts';

const v = (id: string, extra: Partial<ChannelVideo> = {}): ChannelVideo =>
  ({ id, url: `https://www.douyin.com/video/${id}`, title: `t${id}`, duration: null, uploadDate: null, thumbnail: null, ...extra });
const listing = (videos: ChannelVideo[], extra: Partial<ChannelListing> = {}): ChannelListing => ({
  channelId: 'MS4wX', url: 'https://www.douyin.com/user/MS4wX', title: '凌晨睡不着', platform: 'douyin',
  uploader: '凌晨睡不着', thumbnail: 'https://p3/avatar.jpg', videos, truncated: false, ...extra,
});

test('needsMore: only when Douyin said "more" and fewer than asked', () => {
  assert.equal(needsMore(listing([v('1')], { moreHint: 'douyin_extension' }), 500), true);
  assert.equal(needsMore(listing([v('1')], { moreHint: null }), 500), false);
  assert.equal(needsMore(listing([v('1'), v('2')], { moreHint: 'douyin_extension' }), 2), false);
});

test('merge: page order, server details where both have the video, server-only last, cut to limit', () => {
  const server = listing([v('3', { title: 'server 3', duration: 334, thumbnail: 'https://s/3.jpg' }), v('9', { title: 'server only' })],
    { cap: 41, moreHint: 'douyin_extension' });
  const page = listing([v('1', { title: 'page 1', thumbnail: 'https://p/1.jpg' }), v('3', { title: 'page 3', thumbnail: 'https://p/3.jpg' }), v('1'), v('2')],
    { title: 'page title', thumbnail: null });
  const m = mergeListings(server, page, 500);
  assert.deepEqual(m.videos.map((x) => x.id), ['1', '3', '2', '9']);
  assert.deepEqual([m.videos[1].title, m.videos[1].duration, m.videos[1].thumbnail], ['server 3', 334, 'https://s/3.jpg']);
  assert.equal(m.videos[0].title, 'page 1');
  assert.equal(m.title, '凌晨睡不着');
  assert.equal(m.moreHint, null);
  assert.equal(m.cap, undefined);
  assert.equal(mergeListings(server, page, 2).videos.length, 2);
});

test('listsMore', () => {
  const a = listing([v('1'), v('2')]);
  assert.equal(listsMore(a, null), false);
  assert.equal(listsMore(a, listing([v('1')])), false);
  assert.equal(listsMore(a, listing([v('1'), v('2'), v('3')])), true);
});

test('splitByCap: first `cap` now, the rest later; no cap = all now', () => {
  assert.deepEqual(splitByCap(['a', 'b', 'c'], 2), { now: ['a', 'b'], later: ['c'] });
  assert.deepEqual(splitByCap(['a', 'b'], null), { now: ['a', 'b'], later: [] });
  assert.deepEqual(splitByCap(['a'], 0), { now: [], later: ['a'] });
  assert.deepEqual(splitByCap(['a'], 5), { now: ['a'], later: [] });
});
