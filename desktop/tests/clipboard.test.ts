import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decide, normalize, platformOf, DEDUPE_MS } from '../src/lib/clipboard-core.ts';

test('platformOf: supported links', () => {
  assert.equal(platformOf('https://www.tiktok.com/@nba/video/7390205678729579807'), 'TikTok');
  assert.equal(platformOf('https://vt.tiktok.com/ZSabc/'), 'TikTok');
  assert.equal(platformOf('https://youtu.be/jNQXAC9IVRw'), 'YouTube');
  assert.equal(platformOf('https://m.youtube.com/watch?v=jNQXAC9IVRw'), 'YouTube');
  assert.equal(platformOf('https://v.douyin.com/iABC/'), 'Douyin');
  assert.equal(platformOf('https://www.instagram.com/reel/DeJjs2ipNPr/'), 'Instagram');
  assert.equal(platformOf('https://x.com/a/status/1'), 'X');
  assert.equal(platformOf('https://open.spotify.com/track/abc'), 'Spotify');
});

test('platformOf: not ours / not a link / home page', () => {
  assert.equal(platformOf('https://example.com/video.mp4'), null);
  assert.equal(platformOf('https://nottiktok.com/@a/video/1'), null);
  assert.equal(platformOf('https://tiktok.com.evil.io/x'), null);
  assert.equal(platformOf('hello world'), null);
  assert.equal(platformOf('ftp://youtube.com/x'), null);
  assert.equal(platformOf('https://www.youtube.com/'), null);
  assert.equal(platformOf('https://accounts.google.com/reset?token=secret'), null);
});

test('normalize drops tracking params and hash', () => {
  assert.equal(
    normalize('https://www.instagram.com/reel/X/?igsh=abc&utm_source=ig#x'),
    'https://www.instagram.com/reel/X',
  );
  assert.equal(normalize('https://youtu.be/abc?si=zz'), 'https://youtu.be/abc');
  assert.equal(normalize('https://www.youtube.com/watch?v=abc&si=1'), 'https://www.youtube.com/watch?v=abc');
});

test('decide: modes', () => {
  const url = 'https://www.tiktok.com/@a/video/1';
  assert.deepEqual(decide(url, 'off', new Map(), new Set(), 0), { action: 'none' });
  assert.deepEqual(decide(url, 'ask', new Map(), new Set(), 0), { action: 'ask', url, platform: 'TikTok' });
  assert.deepEqual(decide(url, 'auto', new Map(), new Set(), 0), { action: 'auto', url, platform: 'TikTok' });
});

test('decide: same link within 10 minutes is handled once', () => {
  const seen = new Map<string, number>();
  const url = 'https://www.tiktok.com/@a/video/1';
  assert.equal(decide(url, 'ask', seen, new Set(), 1000).action, 'ask');
  assert.equal(decide(url + '?is_from_webapp=1', 'ask', seen, new Set(), 2000).action, 'none');
  assert.equal(decide(url, 'ask', seen, new Set(), 1000 + DEDUPE_MS).action, 'ask');
});

test('decide: a link the app copied itself is ignored once', () => {
  const ignored = new Set([normalize('https://youtu.be/abc')]);
  assert.equal(decide('https://youtu.be/abc', 'auto', new Map(), ignored, 0).action, 'none');
  assert.equal(ignored.size, 0);
  assert.equal(decide('https://youtu.be/abc', 'auto', new Map(), ignored, 0).action, 'auto');
});

test('decide: unsupported link never acts', () => {
  assert.equal(decide('https://bank.example/login?otp=1', 'auto', new Map(), new Set(), 0).action, 'none');
});
