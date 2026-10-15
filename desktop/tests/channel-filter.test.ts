import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EMPTY_FILTER, fold, isEmptyFilter, judge, parseWords, split, topByViews, unknownFields } from '../src/lib/channel-filter.ts';
import type { ChannelVideo } from '../src/lib/types.ts';

const NOW = Date.UTC(2026, 9, 15);
const v = (id: string, o: Partial<ChannelVideo> = {}): ChannelVideo => ({ id, url: `https://x/${id}`, title: 'Video', duration: 120, uploadDate: '20261014', thumbnail: null, viewCount: 5000, ...o });

test('empty filter passes everything (old behaviour)', () => {
  assert.ok(isEmptyFilter(undefined) && isEmptyFilter(EMPTY_FILTER));
  assert.deepEqual(judge(v('a'), undefined, NOW), { pass: true, reason: null });
});

test('duration, views, age, keywords', () => {
  const f = { ...EMPTY_FILTER, minSeconds: 60, maxSeconds: 600 };
  assert.equal(judge(v('a', { duration: 30 }), f, NOW).reason, 'short');
  assert.equal(judge(v('a', { duration: 900 }), f, NOW).reason, 'long');
  assert.ok(judge(v('a', { duration: 300 }), f, NOW).pass);
  assert.equal(judge(v('a', { viewCount: 10 }), { ...EMPTY_FILTER, minViews: 1000 }, NOW).reason, 'views');
  assert.equal(judge(v('a', { uploadDate: '20260801' }), { ...EMPTY_FILTER, maxAgeDays: 7 }, NOW).reason, 'old');
  assert.ok(judge(v('a', { uploadDate: '20261008' }), { ...EMPTY_FILTER, maxAgeDays: 7 }, NOW).pass);
  const kw = { ...EMPTY_FILTER, includeWords: ['đánh giá'], excludeWords: ['LIVE'] };
  assert.ok(judge(v('a', { title: 'Danh Gia iPhone 18' }), kw, NOW).pass);
  assert.equal(judge(v('a', { title: 'Mở hộp' }), kw, NOW).reason, 'keyword');
  assert.equal(judge(v('a', { title: 'Đánh giá — live stream' }), kw, NOW).reason, 'excluded');
});

test('missing data never drops a video', () => {
  const f = { ...EMPTY_FILTER, minSeconds: 60, minViews: 1000, maxAgeDays: 3 };
  assert.ok(judge(v('a', { duration: null, viewCount: null, uploadDate: null }), f, NOW).pass);
  assert.ok(judge(v('a', { viewCount: undefined }), { ...EMPTY_FILTER, minViews: 1000 }, NOW).pass);
  assert.deepEqual(unknownFields([v('a', { viewCount: null }), v('b', { viewCount: null })], f), ['views']);
});

test('split / topByViews / parseWords / fold', () => {
  const r = split([v('a', { duration: 10 }), v('b')], { ...EMPTY_FILTER, minSeconds: 60 }, NOW);
  assert.deepEqual([r.keep.map((x) => x.id), r.skip.map((x) => x.id)], [['b'], ['a']]);
  assert.deepEqual(topByViews([v('a', { viewCount: 5 }), v('b', { viewCount: 50 }), v('c', { viewCount: null })], 2)?.map((x) => x.id), ['b', 'a']);
  assert.equal(topByViews([v('a', { viewCount: null })], 5), null);
  assert.deepEqual(parseWords(' review, Review ;mở hộp\n\n'), ['review', 'mở hộp']);
  assert.equal(fold('Đánh GIÁ'), 'danh gia');
});
