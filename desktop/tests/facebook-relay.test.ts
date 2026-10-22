// Run: npm test   (pure logic only: no Tauri, no network)
// 0.19.3: Facebook pages go through the extension in Chrome (channel relay), like the website.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isFacebookChannelUrl } from '../src/lib/urls.ts';
import { facebookRelayListing } from '../src/lib/douyin-core.ts';

test('Facebook channel links the relay takes', () => {
  for (const u of [
    'https://www.facebook.com/thehecotnhadilam.vn/reels/',
    'https://www.facebook.com/thehecotnhadilam.vn/reels/#vg-relay=lGp0-oGuwEsVxxxxxxxxxxxxxxxxxxxxx', // owner's paste 22/10
    'https://www.facebook.com/NASA',
    'https://m.facebook.com/haidee.burrows/videos/',
    'https://www.facebook.com/profile.php?id=61594798577076&sk=reels_tab',
    'https://www.facebook.com/people/Cole-Cooper/61594798577076/?sk=reels_tab',
  ]) assert.equal(isFacebookChannelUrl(u), true, u);
  for (const u of [
    'https://www.facebook.com/reel/1709544057541378',
    'https://www.facebook.com/watch/?v=123456789',
    'https://www.facebook.com/groups/abc',
    'https://www.facebook.com/people/x/abc/',
    'https://www.facebook.com.evil.example/NASA',
    'https://www.tiktok.com/@a',
  ]) assert.equal(isFacebookChannelUrl(u), false, u);
});

test('relay list -> channel listing', () => {
  const doc = {
    channelUrl: 'https://www.facebook.com/profile.php?id=61594798577076&sk=reels_tab', channelTitle: 'Cole Cooper',
    items: [
      { id: '2009532626388081', url: 'https://www.facebook.com/reel/2009532626388081', title: null },
      { id: '1146790647771213', url: 'https://www.facebook.com/reel/1146790647771213', title: 'Một reel' },
      { id: '1111111111111111', url: 'https://www.facebook.com/reel/1111111111111111', title: null },
    ],
  };
  const l = facebookRelayListing(doc, 2);
  assert.equal(l.channelId, 'fb:61594798577076');
  assert.equal(l.platform, 'facebook');
  assert.equal(l.title, 'Cole Cooper');
  assert.deepEqual(l.videos.map((v) => v.title), ['Facebook 388081', 'Một reel']);
  assert.equal(l.truncated, true);
  const named = facebookRelayListing({ ...doc, channelUrl: 'https://www.facebook.com/thehecotnhadilam.vn/reels/', channelTitle: null }, 500);
  assert.equal(named.channelId, 'fb:thehecotnhadilam.vn');
  assert.equal(named.title, 'Facebook');
  assert.equal(named.truncated, false);
});
