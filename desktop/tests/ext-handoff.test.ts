import { test } from 'node:test';
import assert from 'node:assert/strict';
import { planExtJob, streamFileId } from '../src/lib/ext-handoff-core.ts';
import { GUEST_FORMAT } from '../src/lib/quality.ts';

test('streamFileId: deterministic, <= 64 chars, validate::file_id alphabet only', () => {
  const id1 = streamFileId('https://cdn.example.com/hls/ep-01/index.m3u8?token=abc');
  const id2 = streamFileId('https://cdn.example.com/hls/ep-01/index.m3u8?token=abc');
  const id3 = streamFileId('https://cdn.example.com/hls/ep-02/index.m3u8?token=abc');
  assert.equal(id1, id2);
  assert.notEqual(id1, id3);
  assert.match(id1, /^hls-[0-9a-f]{8,16}$/);
  assert.ok(id1.length <= 64);
});

test('planExtJob: sniffed HLS stream queues directly with Referer, Origin, User-Agent, title & height', () => {
  const plan = planExtJob(
    {
      url: 'https://seg.cdn.example/vod/ep01/index.txt?sig=123',
      title: 'Hành Trình Tập 01',
      referer: 'https://player.embed.example/embed/xyz?autoplay=1',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0',
      height: 720,
      stream: true,
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(plan.mode, 'direct_queue');
  if (plan.mode !== 'direct_queue') return;
  assert.equal(plan.title, 'Hành Trình Tập 01');
  assert.equal(plan.fileTitle, 'Hành Trình Tập 01');
  assert.match(plan.fileId, /^hls-[0-9a-f]+$/);
  assert.equal(plan.formatId, 'bv*[height<=720]+ba/b[height<=720]/b');
  assert.equal(plan.formatLabel, '720p (HD)');
  assert.equal(plan.audioOnly, false);
  assert.deepEqual(plan.headers, [
    { name: 'Referer', value: 'https://player.embed.example/embed/xyz?autoplay=1' },
    { name: 'Origin', value: 'https://player.embed.example' },
    { name: 'User-Agent', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0' },
  ]);
});

test('planExtJob: guest height cap enforced and unsafe headers stripped', () => {
  const plan = planExtJob(
    {
      url: 'https://cdn.example/master.m3u8',
      title: '',
      referer: 'javascript:alert(1)',
      userAgent: 'Bad\r\nHeader: 1',
      height: 2160,
    },
    { defaultQuality: 'best', guest: true },
  );
  assert.equal(plan.mode, 'direct_queue');
  if (plan.mode !== 'direct_queue') return;
  assert.equal(plan.formatId, GUEST_FORMAT);
  assert.equal(plan.headers.length, 0);
  assert.equal(plan.title, 'Video từ trình duyệt');
});

test('planExtJob: regular platform page link uses normal probe flow', () => {
  const plan = planExtJob(
    {
      url: 'https://www.tiktok.com/@creator/video/7400000000000000001',
      title: 'TikTok clip',
      referer: 'https://www.tiktok.com/@creator/video/7400000000000000001',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(plan.mode, 'probe');
});
