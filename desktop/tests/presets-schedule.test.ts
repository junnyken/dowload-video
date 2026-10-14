import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cleanPresets, defaultsFor, presetKey } from '../src/lib/presets-core.ts';
import { hasWork, nextAt, shouldShutdown, startAllowed } from '../src/lib/schedule-core.ts';

test('defaultsFor: preset wins, else global', () => {
  const p = { youtube: { quality: '1080' as const, outDir: 'D:\\YT' }, tiktok: { outDir: 'D:\\TT' } };
  assert.deepEqual(defaultsFor('youtube', p, 'best', 'C:\\VG'), { quality: '1080', outDir: 'D:\\YT' });
  assert.deepEqual(defaultsFor('TikTok', p, 'best', 'C:\\VG'), { quality: 'best', outDir: 'D:\\TT' });
  assert.deepEqual(defaultsFor('vimeo', p, '720', 'C:\\VG'), { quality: '720', outDir: 'C:\\VG' });
  assert.deepEqual(defaultsFor('youtube', null, 'best', null), { quality: 'best', outDir: null });
  assert.equal(presetKey('x'), 'twitter');
});

test('cleanPresets drops junk', () => {
  assert.deepEqual(cleanPresets({ youtube: { quality: '9999', outDir: '' }, evil: { outDir: 'x' }, tiktok: { quality: 'audio' } }), { tiktok: { quality: 'audio' } });
  assert.deepEqual(cleanPresets('nope'), {});
});

test('nextAt: today if ahead, else tomorrow', () => {
  const now = new Date(2026, 9, 14, 20, 30);
  assert.equal(new Date(nextAt('23:00', now)!).getDate(), 14);
  assert.equal(new Date(nextAt('06:00', now)!).getDate(), 15);
  assert.equal(new Date(nextAt('20:30', now)!).getDate(), 15);
  assert.equal(nextAt('25:00', now), null);
  assert.equal(nextAt('abc', now), null);
});

test('startAllowed', () => {
  assert.ok(startAllowed(null, 5));
  assert.ok(!startAllowed(10, 5));
  assert.ok(startAllowed(10, 10));
});

test('shouldShutdown only after real work finished', () => {
  const done = [{ state: 'completed' as const }];
  const left = [{ state: 'completed' as const }, { state: 'queued' as const }];
  const paused = [{ state: 'paused' as const }];
  assert.ok(shouldShutdown(true, true, done, 1));
  assert.ok(!shouldShutdown(false, true, done, 1));     // not armed
  assert.ok(!shouldShutdown(true, false, done, 1));     // no transition
  assert.ok(!shouldShutdown(true, true, left, 1));      // still work
  assert.ok(!shouldShutdown(true, true, paused, 1));    // paused counts as work
  assert.ok(!shouldShutdown(true, true, [], 0));        // emptied by hand, nothing finished
  assert.ok(hasWork(paused) && !hasWork(done));
});
