// node --test tests/watchZip.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  sanitizeZipChannelName,
  buildChannelZipFilename,
  groupWatchItemsByChannel,
  canShowWatchBulkZip,
} from '../src/lib/watchZip.js';

test('sanitizeZipChannelName strips forbidden chars, @ prefix, reserved names, trailing dots/spaces, and caps length', () => {
  assert.equal(sanitizeZipChannelName('@vtv24'), 'vtv24');
  assert.equal(sanitizeZipChannelName('Kênh Giải Trí: <Mới> | 2026?*'), 'Kênh Giải Trí_ _Mới_ _ 2026');
  assert.equal(sanitizeZipChannelName('CON'), '_CON');
  assert.equal(sanitizeZipChannelName('nul.txt...   '), '_nul.txt');
  assert.equal(sanitizeZipChannelName('   @@@   '), 'channel');
  const longEmoji = '🔥'.repeat(80);
  const cleaned = sanitizeZipChannelName(longEmoji);
  assert.ok(Array.from(cleaned).length <= 60);
  assert.equal(cleaned.includes('\uFFFD'), false);
});

test('buildChannelZipFilename formats <tên kênh>_<yyyy-mm-dd>.zip', () => {
  const d = new Date('2026-11-12T08:30:00Z');
  assert.equal(buildChannelZipFilename('@vtv24', d), 'vtv24_2026-11-12.zip');
  assert.equal(buildChannelZipFilename('Đường Về Nhà', d), 'Đường Về Nhà_2026-11-12.zip');
  assert.equal(buildChannelZipFilename('A:B/C*D', d), 'A_B_C_D_2026-11-12.zip');
});

test('groupWatchItemsByChannel groups items per channel preserving first-seen channel order', () => {
  const items = [
    { id: '1', subscription_id: 'sub_a', source_id: 'src_a', channel: '@chanA', platform: 'tiktok', url: 'https://tiktok.com/1' },
    { id: '2', subscription_id: 'sub_b', source_id: 'src_b', channel: '@chanB', platform: 'youtube', url: 'https://youtu.be/2' },
    { id: '3', subscription_id: 'sub_a', source_id: 'src_a', channel: '@chanA', platform: 'tiktok', url: 'https://tiktok.com/3' },
  ];
  const groups = groupWatchItemsByChannel(items);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].key, 'sub_a');
  assert.equal(groups[0].channel, '@chanA');
  assert.deepEqual(groups[0].items.map((x) => x.id), ['1', '3']);
  assert.equal(groups[1].key, 'sub_b');
  assert.deepEqual(groups[1].items.map((x) => x.id), ['2']);
});

test('canShowWatchBulkZip respects status.bulk_zip_allowed first, then entitlement fallback', () => {
  assert.equal(canShowWatchBulkZip({ bulk_zip_allowed: false }, true), false);
  assert.equal(canShowWatchBulkZip({ bulk_zip_allowed: true }, false), true);
  assert.equal(canShowWatchBulkZip({}, true), true);
  assert.equal(canShowWatchBulkZip({}, false), false);
  assert.equal(canShowWatchBulkZip(null, false), false);
});
