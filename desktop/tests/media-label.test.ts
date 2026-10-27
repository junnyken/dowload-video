import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mediaLabel, needsModernPlayer } from '../src/lib/media.ts';

test('video label from measured fields only', () => {
  assert.equal(mediaLabel({ shortSide: 2160, vcodec: 'vp9', fps: 59.94, bitrateKbps: 12000, hdr: true, acodec: 'opus' }), '4K · VP9 · 60 fps · 12.0 Mbps · HDR');
  assert.equal(mediaLabel({ shortSide: 1080, vcodec: 'h264', hdr: false }), '1080p · H.264');
  assert.equal(mediaLabel({ shortSide: 1440, vcodec: 'av1', bitrateKbps: 800, hdr: false }), '2K · AV1 · 800 kbps');
});

test('audio label', () => {
  assert.equal(mediaLabel({ acodec: 'mp3', audioKbps: 320, bitrateKbps: 322, hdr: false }), 'MP3 · 320 kbps');
  assert.equal(mediaLabel({ acodec: 'opus', bitrateKbps: 131, hdr: false }), 'Opus · 131 kbps');
});

test('nothing measured keeps the old label', () => {
  assert.equal(mediaLabel(undefined), null);
  assert.equal(mediaLabel(null), null);
  assert.equal(mediaLabel({ hdr: false }), null);
});

test('only non-H.264 video needs a modern player', () => {
  assert.equal(needsModernPlayer({ vcodec: 'vp9', hdr: false }), true);
  assert.equal(needsModernPlayer({ vcodec: 'h264', hdr: false }), false);
  assert.equal(needsModernPlayer({ acodec: 'opus', hdr: false }), false);
  assert.equal(needsModernPlayer(undefined), false);
});
