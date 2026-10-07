// Run: npm test   (pure logic only: no Tauri, no network)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  NO_CLAIM, badgeText, capIds, decideBatch, decideClaim, selectionCap, emptyLedger, flushLedger, gatePaused, graceLeft, isIpLimit, mergeCounters, normalizeLedger, parseHint, parseSnapshot, tryGrace, utcDay,
} from '../src/lib/quota-core.ts';
import { presetFormat, qualityFormat } from '../src/lib/quality.ts';

const T0 = Date.parse('2026-10-07T10:00:00Z');
const OK = { allowed: true, claimId: 'abcdef123456', platform: 'youtube', alreadyCounted: false, overLimit: false, mode: 'enforce', limit: 5, usedToday: 1, remaining: 4, resetTimeVn: '07:00', requester: 'device' };

test('claim 200 -> proceed with the claimId', () => {
  const d = decideClaim(200, OK);
  assert.equal(d.kind, 'proceed');
  assert.ok(d.kind === 'proceed' && d.claimId === 'abcdef123456' && d.counted);
});

test('claim 503 client_quota_disabled -> proceed, nothing counted', () => {
  const d = decideClaim(503, { error_code: 'client_quota_disabled' });
  assert.ok(d.kind === 'proceed' && d.claimId === NO_CLAIM && !d.counted);
  // a plain 503 (server down) is NOT "disabled": it is offline
  assert.equal(decideClaim(503, null).kind, 'offline');
});

test('claim 403 / 429 quota_exceeded_daily -> refused with the server text', () => {
  for (const [status, upsell] of [[403, 'upgrade'], [429, 'signin']] as const) {
    const d = decideClaim(status, { allowed: false, error_code: 'quota_exceeded_daily', reason: 'daily_limit', detail: 'Hết lượt rồi.', upsell, limit: 5, usedToday: 5, remaining: 0, resetTimeVn: '07:00' });
    assert.ok(d.kind === 'refused');
    if (d.kind === 'refused') {
      assert.equal(d.refusal.detail, 'Hết lượt rồi.');
      assert.equal(d.refusal.upsell, upsell);
      assert.equal(d.refusal.resetTimeVn, '07:00');
    }
  }
  // a rate-limit 429 without the quota code is not a refusal
  assert.equal(decideClaim(429, { detail: 'Too many requests' }).kind, 'offline');
});

test('network error, 5xx and unknown answers -> offline; invalid_url -> proceed uncounted', () => {
  assert.equal(decideClaim(0, null).kind, 'offline');
  assert.equal(decideClaim(500, { detail: 'x' }).kind, 'offline');
  assert.equal(decideClaim(502, null).kind, 'offline');
  assert.equal(decideClaim(200, { allowed: true }).kind, 'offline'); // no claimId: do not trust it
  const d = decideClaim(400, { error_code: 'invalid_url' });
  assert.ok(d.kind === 'proceed' && d.claimId === NO_CLAIM);
});

test('the refusal gate holds the queue for the rest of the UTC day only', () => {
  const g = { day: utcDay(T0), refusal: { detail: 'x', upsell: null, reason: null, resetTimeVn: null, limit: null, usedToday: null } };
  assert.equal(gatePaused(g, T0), true);
  assert.equal(gatePaused(g, Date.parse('2026-10-07T23:59:59Z')), true);
  assert.equal(gatePaused(g, Date.parse('2026-10-08T00:00:00Z')), false);
  assert.equal(gatePaused(null, T0), false);
});

test('offline grace: N per UTC day, then refused; resets the next day', () => {
  let l = emptyLedger(T0);
  for (let i = 1; i <= 3; i++) {
    const r = tryGrace(l, 3, `https://x.test/${i}`, T0 + i);
    assert.equal(r.ok, true);
    l = r.ledger;
  }
  assert.equal(graceLeft(l, 3, T0), 0);
  const refused = tryGrace(l, 3, 'https://x.test/4', T0 + 10);
  assert.equal(refused.ok, false);
  assert.equal(refused.ledger.pending.length, 3);
  // next UTC day: the counter resets, the unreported entries stay
  const next = Date.parse('2026-10-08T00:00:01Z');
  assert.equal(graceLeft(l, 3, next), 3);
  const r2 = tryGrace(l, 3, 'https://x.test/5', next);
  assert.ok(r2.ok);
  assert.equal(r2.ledger.used, 1);
  assert.equal(r2.ledger.pending.length, 4);
  // grace 0 never allows
  assert.equal(tryGrace(emptyLedger(T0), 0, 'https://x.test/z', T0).ok, false);
});

test('normalizeLedger survives junk and keeps pending across midnight', () => {
  assert.deepEqual(normalizeLedger(null, T0), emptyLedger(T0));
  assert.deepEqual(normalizeLedger('junk', T0), emptyLedger(T0));
  const n = normalizeLedger({ day: '2026-10-06', used: 3, pending: [{ url: 'https://a.test/1', at: 1 }, { nope: 1 }] }, T0);
  assert.equal(n.used, 0);
  assert.equal(n.pending.length, 1);
  assert.equal(normalizeLedger({ day: utcDay(T0), used: 2, pending: [] }, T0).used, 2);
});

test('retro flush: reported entries leave the ledger, a network error keeps the rest, used stays', async () => {
  let l = emptyLedger(T0);
  for (let i = 1; i <= 3; i++) l = tryGrace(l, 3, `https://x.test/${i}`, T0).ledger;
  const sent: string[] = [];
  const out = await flushLedger(l, async (e) => {
    sent.push(e.url);
    return e.url.endsWith('/2') ? 'retry' : 'done';
  });
  assert.deepEqual(sent, ['https://x.test/1', 'https://x.test/2']);
  assert.deepEqual(out.pending.map((e) => e.url), ['https://x.test/2', 'https://x.test/3']);
  assert.equal(out.used, 3);
  const out2 = await flushLedger(out, async () => 'drop');
  assert.equal(out2.pending.length, 0);
  assert.equal(out2.used, 3);
  assert.equal(graceLeft(out2, 3, T0), 0); // flushing does not hand the grace back
});

test('snapshot parsing and badge text', () => {
  const s = parseSnapshot({ limit: 5, usedToday: 2, remaining: 3, resetTimeVn: '07:00', requester: 'device', deviceCode: 'A1B2C3D4', mode: 'enforce', enforced: true, offlineGrace: 3, refundDailyMax: 10 });
  assert.ok(s);
  assert.equal(badgeText(s!), 'Hôm nay 2/5 lượt');
  assert.equal(badgeText({ limit: -1, usedToday: 9, remaining: -1 }), 'Không giới hạn');
  assert.equal(parseSnapshot({ detail: 'x' }), null);
  const m = mergeCounters(s, { limit: 5, usedToday: 3, remaining: 2, resetTimeVn: '07:00', requester: 'device' });
  assert.equal(m!.usedToday, 3);
  assert.equal(m!.deviceCode, 'A1B2C3D4');
  assert.equal(mergeCounters(s, { refunded: false }), s);
});

test('height presets end with /b so formats without height (MangoTV) still download', () => {
  assert.equal(presetFormat(1080), 'bv*[height<=1080]+ba/b[height<=1080]/b');
  assert.equal(qualityFormat('720').formatId, 'bv*[height<=720]+ba/b[height<=720]/b');
  assert.ok(qualityFormat('360').formatId!.endsWith('/b'));
  assert.equal(qualityFormat('best').formatId, undefined);
  assert.equal(qualityFormat('audio').audioOnly, true);
  assert.ok(presetFormat(1920).length <= 64); // Rust validate::format_id limit
});

// ---- channels: claim-batch + cut to remaining (PLAN-32D §6) ----------------------------

const SNAP = { limit: 5, usedToday: 2, remaining: 3, resetTimeVn: '07:00', requester: 'device', deviceCode: null, mode: 'enforce' as const, enforced: true, offlineGrace: 3, refundDailyMax: 10 };

test('picker cap: remaining when enforced, none otherwise', () => {
  assert.equal(selectionCap({ status: 'enabled', snap: SNAP }), 3);
  assert.equal(selectionCap({ status: 'enabled', snap: { ...SNAP, remaining: 0, usedToday: 5 } }), 0);
  assert.equal(selectionCap({ status: 'enabled', snap: { ...SNAP, enforced: false, mode: 'shadow' } }), null);
  assert.equal(selectionCap({ status: 'enabled', snap: { ...SNAP, limit: -1, remaining: -1 } }), null);
  assert.equal(selectionCap({ status: 'disabled', snap: null }), null);
  assert.equal(selectionCap({ status: 'unknown', snap: null }), null);
});

test('a pick of 8 with 3 remaining keeps the first 3 (newest)', () => {
  const ids = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8'];
  assert.deepEqual(capIds(ids, selectionCap({ status: 'enabled', snap: SNAP })), ['v1', 'v2', 'v3']);
  assert.deepEqual(capIds(ids, null), ids);
  assert.deepEqual(capIds(ids, 0), []);
});

test('claim-batch answer -> per item allowed / refused', () => {
  const urls = ['https://youtu.be/a', 'https://youtu.be/b', 'bad', 'https://youtu.be/c'];
  const d = decideBatch(200, {
    items: [
      { url: urls[0], allowed: true, claimId: 'c1aaaaaaaaaa' },
      { url: urls[1], allowed: true, claimId: 'c2aaaaaaaaaa' },
      { url: 'bad', allowed: false, error_code: 'invalid_url', detail: 'Link không hợp lệ.' },
      { url: urls[3], allowed: false, error_code: 'quota_exceeded_daily', detail: 'Hết lượt hôm nay.' },
    ],
    limit: 5, usedToday: 5, remaining: 0,
  }, urls);
  assert.equal(d.kind, 'ok');
  if (d.kind !== 'ok') return;
  assert.deepEqual(d.items.map((i) => [i.allowed, i.claimId, i.detail]), [
    [true, 'c1aaaaaaaaaa', null], [true, 'c2aaaaaaaaaa', null], [true, NO_CLAIM, null], [false, NO_CLAIM, 'Hết lượt hôm nay.'],
  ]);
  assert.equal(d.items[3].url, urls[3]);
});

test('claim-batch: 503 disabled = before P2; anything odd = offline', () => {
  assert.deepEqual(decideBatch(503, { error_code: 'client_quota_disabled' }, ['u']), { kind: 'disabled' });
  assert.deepEqual(decideBatch(0, null, ['u']), { kind: 'offline' });
  assert.deepEqual(decideBatch(500, { detail: 'x' }, ['u']), { kind: 'offline' });
  assert.deepEqual(decideBatch(200, { items: [] }, ['u']), { kind: 'offline' }); // count mismatch
  assert.deepEqual(decideBatch(429, { detail: 'rate' }, ['u']), { kind: 'offline' });
});

test('426 update_required -> stop without offline grace; a bare 426 is still offline', () => {
  assert.equal(decideClaim(426, { error_code: 'update_required', minSupported: '0.9.1' }).kind, 'update_required');
  assert.equal(decideClaim(426, null).kind, 'offline');
});

test('hint is fail-closed: nothing stored or junk = on; only an explicit false turns it off', () => {
  assert.deepEqual(parseHint(null), { enabled: true, grace: 3 });
  assert.deepEqual(parseHint('junk'), { enabled: true, grace: 3 });
  assert.deepEqual(parseHint({ enabled: true, grace: 5 }), { enabled: true, grace: 5 });
  assert.deepEqual(parseHint({ enabled: false, grace: 2 }), { enabled: false, grace: 2 });
  assert.deepEqual(parseHint({ grace: -4 }), { enabled: true, grace: 0 });
});

test('ip_limit refusals are told apart from the personal daily limit', () => {
  const d = decideClaim(429, { error_code: 'quota_exceeded_daily', reason: 'ip_limit', detail: 'Mạng này…', limit: 5, usedToday: 2 });
  assert.ok(d.kind === 'refused' && isIpLimit(d.refusal));
  const p = decideClaim(429, { error_code: 'quota_exceeded_daily', reason: 'daily_limit', detail: 'Hết', limit: 5, usedToday: 5 });
  assert.ok(p.kind === 'refused' && !isIpLimit(p.refusal));
});
