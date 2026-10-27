// quality #8: "Dễ mở" (H.264/AAC up to 1080p, VP9→AV1 above) vs "Chất lượng gốc".
import test from 'node:test';
import assert from 'node:assert/strict';
import { applyCodecPref, easyFormat, GUEST_FORMAT, GUEST_FORMAT_EASY } from '../src/lib/quality.ts';

const preset = (h: number) => `bv*[height<=${h}]+ba/b[height<=${h}]/b`;

test('easy 1080 asks for H.264 + AAC first, keeps the old fallbacks', () => {
  assert.equal(easyFormat(1080), 'bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b');
});

test('easy 2160: VP9 before AV1 above 1080 (short side), then H.264', () => {
  const f = easyFormat(2160).split('/');
  assert.equal(f[0], 'bv*[height<=2160][width>1080][height>1080][vcodec^=vp9]+ba[acodec^=mp4a]');
  assert.equal(f[1], 'bv*[height<=2160][width>1080][height>1080][vcodec^=vp09]+ba[acodec^=mp4a]');
  assert.equal(f[2], 'bv*[height<=2160][width>1080][height>1080][vcodec^=av01]+ba[acodec^=mp4a]');
  assert.equal(f[3], 'bv*[height<=2160][vcodec^=avc1]+ba[acodec^=mp4a]');
});

test('applyCodecPref', () => {
  assert.equal(applyCodecPref(preset(1080), false, 'easy'), easyFormat(1080));
  assert.equal(applyCodecPref(preset(1920), false, 'easy'), easyFormat(1920)); // portrait 1080p class
  assert.equal(applyCodecPref(undefined, false, 'easy'), easyFormat(null));   // "Tốt nhất"
  assert.equal(applyCodecPref(GUEST_FORMAT, false, 'easy'), GUEST_FORMAT_EASY);
  assert.equal(applyCodecPref('137+140', false, 'easy'), '137+140');            // a chosen format stays
  assert.equal(applyCodecPref(preset(1080), true, 'easy'), preset(1080));       // audio untouched
  assert.equal(applyCodecPref(preset(1080), false, 'original'), preset(1080));  // original = as before
  assert.equal(applyCodecPref(undefined, false, 'original'), undefined);
});

test('every easy selector passes the app format_id rule (≤512, allowed characters)', () => {
  for (const f of [easyFormat(null), easyFormat(2160), easyFormat(1440), easyFormat(720), GUEST_FORMAT_EASY]) {
    assert.ok(f.length <= 512, f);
    assert.match(f, /^[A-Za-z0-9+\-_/.*[\]<>=:,^]+$/);
  }
});
