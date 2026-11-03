/**
 * Strict Sign-Up Email Guard & Gmail Normalization
 * ================================================
 * 1. Blocks 100% of disposable / temporary / unknown email domains by enforcing
 *    a strict 5-provider allowlist (17 trusted domains):
 *    - Gmail:           @gmail.com, @googlemail.com
 *    - Outlook/Hotmail: @outlook.com, @hotmail.com, @live.com, @msn.com, @outlook.com.vn, @hotmail.com.vn
 *    - Apple iCloud:    @icloud.com, @me.com, @mac.com
 *    - Yahoo Mail:      @yahoo.com, @yahoo.com.vn, @ymail.com, @rocketmail.com
 *    - ProtonMail:      @proton.me, @protonmail.com, @pm.me
 * 2. Anti-multi-account Gmail tricks:
 *    - Rejects '+' sub-addresses (ten+1@gmail.com)
 *    - Normalizes Gmail dots & @googlemail.com (a.b.c@gmail.com -> abc@gmail.com)
 */

export const ALLOWED_EMAIL_DOMAINS = new Set([
  // Gmail
  'gmail.com',
  'googlemail.com',
  // Outlook / Hotmail
  'outlook.com',
  'hotmail.com',
  'live.com',
  'msn.com',
  'outlook.com.vn',
  'hotmail.com.vn',
  // Apple iCloud
  'icloud.com',
  'me.com',
  'mac.com',
  // Yahoo Mail
  'yahoo.com',
  'yahoo.com.vn',
  'ymail.com',
  'rocketmail.com',
  // ProtonMail
  'proton.me',
  'protonmail.com',
  'pm.me',
]);

/**
 * Validate and normalize an email address for sign-up.
 * @param {string} rawEmail
 * @returns {{ valid: boolean, normalizedEmail: string, reason: null | 'invalid_format' | 'plus_alias' | 'provider_not_allowed' }}
 */
export function validateAndNormalizeSignupEmail(rawEmail) {
  const e = String(rawEmail || '').trim().toLowerCase();
  const at = e.indexOf('@');
  if (at <= 0 || at !== e.lastIndexOf('@')) {
    return { valid: false, normalizedEmail: '', reason: 'invalid_format' };
  }

  let local = e.slice(0, at).trim();
  let domain = e.slice(at + 1).replace(/\.+$/, '').trim();

  if (!local || !domain) {
    return { valid: false, normalizedEmail: '', reason: 'invalid_format' };
  }

  // Rule 1: Reject '+' alias emails (e.g. ten+1@gmail.com)
  if (local.includes('+')) {
    return { valid: false, normalizedEmail: '', reason: 'plus_alias' };
  }

  // Rule 2: Enforce 5-provider whitelist
  if (!ALLOWED_EMAIL_DOMAINS.has(domain)) {
    return { valid: false, normalizedEmail: '', reason: 'provider_not_allowed' };
  }

  // Rule 3: Normalize Gmail dots (a.b.c@gmail.com -> abc@gmail.com) & googlemail.com
  if (domain === 'gmail.com' || domain === 'googlemail.com') {
    local = local.replace(/\./g, '');
    domain = 'gmail.com';
    if (!local) {
      return { valid: false, normalizedEmail: '', reason: 'invalid_format' };
    }
  }

  return {
    valid: true,
    normalizedEmail: `${local}@${domain}`,
    reason: null,
  };
}

/**
 * Normalize Gmail dots & @googlemail.com for sign-in / password reset fallback.
 */
export function normalizeGmailForAuth(rawEmail) {
  const e = String(rawEmail || '').trim().toLowerCase();
  const at = e.lastIndexOf('@');
  if (at <= 0) return e;
  const local = e.slice(0, at);
  const domain = e.slice(at + 1).replace(/\.+$/, '');
  if (domain === 'gmail.com' || domain === 'googlemail.com') {
    return `${local.replace(/\./g, '')}@gmail.com`;
  }
  return `${local}@${domain}`;
}

/** 'x@a.b.mailinator.com' -> ['a.b.mailinator.com', 'b.mailinator.com', 'mailinator.com', 'com'] */
export function domainSuffixes(email) {
  const e = String(email || '').trim().toLowerCase();
  const at = e.lastIndexOf('@');
  if (at < 0) return [];
  const domain = e.slice(at + 1).replace(/\.+$/, '');
  if (!domain) return [];
  const parts = domain.split('.');
  return parts.map((_, i) => parts.slice(i).join('.'));
}

export async function isDisposableEmail(email) {
  const check = validateAndNormalizeSignupEmail(email);
  return !check.valid;
}
