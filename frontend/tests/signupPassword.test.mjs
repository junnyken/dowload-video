// node --test tests/signupPassword.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');

test('signup password minimum is 8 characters (7-char rejected, 8-char accepted)', async () => {
  const modPath = path.join(rootDir, 'src', 'lib', 'signupPassword.js');
  assert.equal(fs.existsSync(modPath), true, 'src/lib/signupPassword.js must exist');
  const { MIN_SIGNUP_PASSWORD_LENGTH, isSignupPasswordValid } = await import('../src/lib/signupPassword.js');

  assert.equal(MIN_SIGNUP_PASSWORD_LENGTH, 8);
  assert.equal(isSignupPasswordValid(''), false);
  assert.equal(isSignupPasswordValid('123456'), false);
  assert.equal(isSignupPasswordValid('1234567'), false, '7-character password must be rejected');
  assert.equal(isSignupPasswordValid('12345678'), true, '8-character password must be accepted');
});

test('AuthModal uses 8-char password check and vi/en i18n strings match 8 characters', () => {
  const modalSrc = fs.readFileSync(path.join(rootDir, 'src', 'components', 'auth', 'AuthModal.jsx'), 'utf8');
  assert.equal(modalSrc.includes('password.length < 6'), false, 'AuthModal must not allow 6-char minimum');
  assert.equal(
    modalSrc.includes('isSignupPasswordValid(password)') || modalSrc.includes('password.length < 8'),
    true,
    'AuthModal must enforce 8-char minimum on sign-up'
  );

  const viAuth = JSON.parse(fs.readFileSync(path.join(rootDir, 'src', 'locales', 'vi', 'auth.json'), 'utf8'));
  const enAuth = JSON.parse(fs.readFileSync(path.join(rootDir, 'src', 'locales', 'en', 'auth.json'), 'utf8'));

  assert.match(viAuth.pwMin, /\b8\b/, `vi auth.pwMin should specify 8 characters, got: ${viAuth.pwMin}`);
  assert.match(enAuth.pwMin, /\b8\b/, `en auth.pwMin should specify 8 characters, got: ${enAuth.pwMin}`);
});
