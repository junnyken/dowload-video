/**
 * Cloudflare Turnstile loader (anti-spam for sign-up / sign-in / password
 * reset, task #6039).
 *
 * The site key is public and comes from the build-time env
 * VITE_TURNSTILE_SITE_KEY. When it is empty no script is loaded, no widget is
 * shown and no captchaToken is sent — so this build is safe to deploy BEFORE
 * captcha protection is switched on in Supabase (see
 * docs/runbooks/antispam-signup.md for the order).
 *
 * The script is loaded only when an auth form mounts, from Cloudflare's exact
 * URL: Cloudflare says api.js must not be proxied or cached
 * (https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/),
 * which is also why public/sw.js leaves challenges.cloudflare.com alone.
 */
export const TURNSTILE_SITE_KEY = (import.meta.env.VITE_TURNSTILE_SITE_KEY || '').trim();

const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

let loading = null;

export function loadTurnstile() {
  if (typeof window === 'undefined') return Promise.reject(new Error('no window'));
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = SCRIPT_SRC;
    s.async = true;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error('turnstile missing')));
    s.onerror = () => {
      loading = null;          // allow a retry on the next mount
      s.remove();
      reject(new Error('turnstile failed to load'));
    };
    document.head.appendChild(s);
  });
  return loading;
}
