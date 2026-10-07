// PLAN-32E P1 step 3 (task #6125): when the app must block for an update.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fromApiAnswer, fromVersionInfo, installLink, versionLess } from '../src/lib/update-core.ts';

const HOSTS = ['dvid.vibe1.tinhgon.xyz', 'dvid-api.vibe1.tinhgon.xyz'];
const SITE = 'https://dvid.vibe1.tinhgon.xyz';

test('versionLess', () => {
  assert.equal(versionLess('0.8.0', '0.9.0'), true);
  assert.equal(versionLess('0.10.0', '0.9.0'), false); // numeric, not string order
  assert.equal(versionLess('0.9.0', '0.9.0'), false);
  assert.equal(versionLess('v0.5.9', '0.6.0'), true);
});

test('below minSupported blocks; equal or above does not', () => {
  const d = { minSupported: '0.6.0', latest: '0.9.0', downloadUrl: `${SITE}/download` };
  assert.equal(fromVersionInfo('0.5.9', d).required, true);
  assert.equal(fromVersionInfo('0.6.0', d).required, false);
  assert.equal(fromVersionInfo('0.9.0', d).required, false);
});

test('unknown version or no minimum never blocks', () => {
  assert.equal(fromVersionInfo(null, { minSupported: '9.0.0' }).required, false);
  assert.equal(fromVersionInfo('0.9.0', {}).required, false);
  assert.equal(fromVersionInfo('0.9.0', null).required, false);
});

test('only a 426 update_required answer blocks', () => {
  const body = { error_code: 'update_required', detail: 'cũ', minSupported: '0.6.0', latest: '0.9.0', downloadUrl: '' };
  const u = fromApiAnswer(426, body);
  assert.ok(u && u.required && u.detail === 'cũ' && u.minSupported === '0.6.0');
  assert.equal(fromApiAnswer(200, body), null);
  assert.equal(fromApiAnswer(426, { error_code: 'other' }), null);
  assert.equal(fromApiAnswer(429, body), null);
  assert.equal(fromApiAnswer(426, null), null);
});

test('install link: allowed https host only, else the website download page', () => {
  assert.equal(installLink(`${SITE}/download/VidGrab_0.9.0_x64-setup.exe`, SITE, HOSTS), `${SITE}/download/VidGrab_0.9.0_x64-setup.exe`);
  assert.equal(installLink('https://evil.example/x.exe', SITE, HOSTS), `${SITE}/download`);
  assert.equal(installLink('http://dvid.vibe1.tinhgon.xyz/x.exe', SITE, HOSTS), `${SITE}/download`);
  assert.equal(installLink('', SITE + '/', HOSTS), `${SITE}/download`);
});
