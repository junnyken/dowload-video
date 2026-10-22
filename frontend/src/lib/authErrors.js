/**
 * Supabase Auth errors -> messages for the auth forms, in the page language
 * (text: locales/<lang>/auth.json "errors"; getters, so read at use time).
 * Wording is listed for BA review in docs/runbooks/antispam-signup.md.
 */
import i18n from '../i18n';
export const MSG = {
  get captchaNeeded() { return i18n.t('errors.captchaNeeded', { ns: 'auth' }); },
  get captchaFailed() { return i18n.t('errors.captchaFailed', { ns: 'auth' }); },
  get captchaLoadFailed() { return i18n.t('errors.captchaLoadFailed', { ns: 'auth' }); },
  get disposableEmail() { return i18n.t('errors.disposableEmail', { ns: 'auth' }); },
  get signupRejected() { return i18n.t('errors.signupRejected', { ns: 'auth' }); },
  get invalidCredentials() { return i18n.t('errors.invalidCredentials', { ns: 'auth' }); },
  get emailNotConfirmed() { return i18n.t('errors.emailNotConfirmed', { ns: 'auth' }); },
  get rateLimited() { return i18n.t('errors.rateLimited', { ns: 'auth' }); },
  get network() { return i18n.t('errors.network', { ns: 'auth' }); },
  get generic() { return i18n.t('errors.generic', { ns: 'auth' }); },
};

export function authErrorMessage(error) {
  if (!error) return '';
  const code = String(error.code || '');
  const msg = String(error.message || '');
  const low = msg.toLowerCase();
  // GoTrue: 400 captcha_failed "captcha protection: request disallowed (...)"
  if (code === 'captcha_failed' || low.includes('captcha')) return MSG.captchaFailed;
  // A BEFORE INSERT trigger on auth.users (migration 036) surfaces as
  // 500 unexpected_failure "Database error saving new user".
  if (low.includes('database error saving new user')) return MSG.signupRejected;
  if (code === 'invalid_credentials' || low.includes('invalid login credentials')) return MSG.invalidCredentials;
  if (code === 'email_not_confirmed' || low.includes('email not confirmed')) return MSG.emailNotConfirmed;
  if (error.status === 429 || code.startsWith('over_') || low.includes('rate limit')) return MSG.rateLimited;
  if (error.name === 'AuthRetryableFetchError' || low.includes('failed to fetch')) return MSG.network;
  return msg || MSG.generic;
}
