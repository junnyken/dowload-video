import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sameVideo, shortDate, videoKey } from '../src/lib/dedupe-core.ts';

test('videoKey', () => {
  assert.equal(videoKey('https://www.youtube.com/watch?v=jNQXAC9IVRw&si=x'), 'jNQXAC9IVRw');
  assert.equal(videoKey('https://youtu.be/jNQXAC9IVRw?si=x'), 'jNQXAC9IVRw');
  assert.equal(videoKey('https://www.youtube.com/shorts/abcDEF12345'), 'abcDEF12345');
  assert.equal(videoKey('https://www.tiktok.com/@nba/video/7390205678729579807?is_from_webapp=1'), '7390205678729579807');
  assert.equal(videoKey('https://www.instagram.com/reel/DeJjs2ipNPr/'), 'DeJjs2ipNPr');
  assert.equal(videoKey('https://youtu.be/abc1'), 'abc1');
  assert.equal(videoKey('https://example.com/'), null);
  assert.equal(videoKey('not a url'), null);
});

test('sameVideo', () => {
  assert.ok(sameVideo('https://www.tiktok.com/@nba/video/7390205678729579807', 'https://m.tiktok.com/@nba/video/7390205678729579807?x=1'));
  assert.ok(sameVideo('https://www.youtube.com/watch?v=jNQXAC9IVRw', 'https://m.youtube.com/watch?v=jNQXAC9IVRw&t=3'));
  assert.ok(sameVideo('https://www.youtube.com/watch?v=jNQXAC9IVRw', 'https://youtu.be/jNQXAC9IVRw'));
  assert.ok(sameVideo('https://www.youtube.com/watch?v=abc1', 'https://youtu.be/abc1'));
  assert.ok(!sameVideo('https://www.tiktok.com/@a/video/7390205678729579807', 'https://www.youtube.com/watch?v=7390205678729579807'));
  assert.ok(!sameVideo('https://www.tiktok.com/@a/video/111111', 'https://www.tiktok.com/@a/video/222222'));
  assert.ok(!sameVideo('https://example.com/', 'https://example.com/'));
});

test('shortDate', () => {
  assert.equal(shortDate(null), '');
  assert.equal(shortDate('garbage'), '');
  assert.match(shortDate('2026-10-14T05:00:00Z'), /^\d{2}\/\d{2}\/2026$/);
});
