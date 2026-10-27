// node --test tests/mediaLabel.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { mediaParts, needsModernPlayer } from '../src/lib/mediaLabel.js';

const m = (o) => ({ downloaded_media: { measured: true, ...o } });

test('measured 4K VP9 60 fps HDR', () => {
  const info = m({ vcodec: 'vp9', short_side: 2160, fps: 59.94, bitrate_kbps: 12345, hdr: true });
  assert.deepEqual(mediaParts(info), ['4K', 'VP9', '60 fps', '12.3 Mbps', 'HDR']);
  assert.equal(needsModernPlayer(info), true);
});

test('measured 1080p H.264 opens everywhere', () => {
  const info = m({ vcodec: 'h264', short_side: 1080, fps: 30, bitrate_kbps: 900 });
  assert.deepEqual(mediaParts(info), ['1080p', 'H.264', '30 fps', '900 kbps']);
  assert.equal(needsModernPlayer(info), false);
});

test('measured audio', () => {
  assert.deepEqual(mediaParts(m({ acodec: 'opus', audio_bitrate_kbps: 160 })), ['Opus', '160 kbps']);
});

test('unknown values are left out, never guessed', () => {
  assert.deepEqual(mediaParts(m({ vcodec: 'av1', short_side: 1440 })), ['2K', 'AV1']);
});

test('not measured falls back to the old label', () => {
  assert.deepEqual(mediaParts({ downloaded_media: { measured: false }, downloaded_height: 720 }), ['720p']);
  assert.deepEqual(mediaParts({ is_audio_only: true, quality: 'mp3_320' }), ['MP3 320 kbps']);
  assert.deepEqual(mediaParts({}), []);
  assert.equal(needsModernPlayer({ downloaded_media: { measured: false } }), false);
});

test('history row: a requested MP3 kbps is not shown as if measured', () => {
  const row = { is_audio_only: true, quality: 'mp3_320', selected_quality: 'mp3_320' };
  assert.deepEqual(mediaParts({ ...row, quality: null }), []);
  const measured = { ...row, downloaded_media: { measured: true, acodec: 'mp3', audio_bitrate_kbps: 128 } };
  assert.deepEqual(mediaParts({ ...measured, quality: null }), ['MP3', '128 kbps']);
});
